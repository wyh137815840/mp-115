"""把历史 strm 统一成外网入口 + 严格百分号编码（从 Anime115 1.0.60 移植）。

只改 `file_name=` 的值；URL 其余部分不动。file_name 是最后一个参数，必须
取到字符串结尾，不能按 `&` 切（历史未编码的文件名可能含 `&`）。
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import quote, unquote

WAN_PREFIX = "https://mp.yuanhot.top"
LAN_PREFIX = "http://192.168.1.9:3000"


def normalize_url(text: str) -> str:
    """返回规范化后的 strm 内容；不修改模板/其他 query 参数。"""
    url = (text or "").strip()
    if not url.startswith("http"):
        return url
    if LAN_PREFIX in url:
        url = url.replace(LAN_PREFIX, WAN_PREFIX, 1)
    if "file_name=" not in url:
        return url
    head, raw = url.split("file_name=", 1)
    for _ in range(4):
        decoded = unquote(raw)
        if decoded == raw:
            break
        raw = decoded
    return f"{head}file_name={quote(raw, safe='.-_')}"


def migrate(root: str | Path, dry_run: bool = False) -> dict:
    root_path = Path(root)
    changed = total = 0
    changed_paths: list[str] = []
    for path in root_path.rglob("*.strm"):
        total += 1
        try:
            old = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        new = normalize_url(old)
        if new == old:
            continue
        changed += 1
        changed_paths.append(str(path))
        if not dry_run:
            path.write_text(new + "\n", encoding="utf-8")
    return {"total": total, "changed": changed, "dry_run": dry_run, "paths": changed_paths}
