"""Emby 多版本合并的纯逻辑（不依赖 MoviePilot app，便于单测）。

同一部片子的多个 strm 在 Emby 里会各自成为一个 Movie。Emby 提供
`POST /emby/Videos/MergeVersions?Ids=...` 把它们合并为一个条目下的多个版本；
本模块只负责从 Emby `/Items` 返回中找出「同一番号的多条目」候选。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

_CODE_RE = re.compile(
    r"(FC2[-_]?PPV[-_]?\d{5,8}|[A-Z]{2,10}[-_]\d{2,5}(?:[-_][A-Z0-9]+)*|\d{6}[-_]\d{2,3})",
    re.IGNORECASE,
)


def path_code(path: str) -> str:
    """从 strm 路径里取番号（取最后一段包含番号的名字）。"""
    parts = [part for part in str(path or "").replace("\\", "/").split("/") if part]
    for part in reversed(parts):
        match = _CODE_RE.search(part.upper())
        if match:
            return match.group(1).replace("_", "-")
    return ""


def provider_key(item: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    """返回用于分组的 (provider, id)。优先 javbus/fc2，避免误用 MetaTube 串号。"""
    ids = item.get("ProviderIds") if isinstance(item.get("ProviderIds"), dict) else {}
    for name in ("javbus", "fc2"):
        value = ids.get(name)
        if value:
            return name, str(value).strip().upper()
    metatube = ids.get("MetaTube")
    if metatube:
        # MetaTube 可能是 "FANZA:cawb00001" 这类值；仅当同一组路径番号一致时才用。
        return "metatube", str(metatube).split(":")[-1].strip().upper()
    return None


def group_version_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把同一番号的多条目聚成候选组。

    输出每项：{"provider", "id", "items": [...], "same_code": bool}
    - javbus/fc2 直接按 id 聚合；
    - MetaTube 仅在所有条目 path_code 一致时聚合（避免 MIAD-823/PGD-707 这种串号误合并）。
    """
    buckets: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for item in items or []:
        key = provider_key(item)
        if not key:
            continue
        buckets.setdefault(key, []).append(item)
    groups: List[Dict[str, Any]] = []
    for (provider, value), rows in buckets.items():
        if len(rows) < 2:
            continue
        codes = {path_code(row.get("Path") or "") for row in rows}
        same_code = len(codes) == 1 and "" not in codes
        if provider == "metatube" and not same_code:
            continue
        groups.append({
            "provider": provider,
            "id": value,
            "items": rows,
            "same_code": same_code,
        })
    groups.sort(key=lambda group: (group["provider"], group["id"]))
    return groups
