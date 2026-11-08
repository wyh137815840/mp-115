"""把一行字收成番号、磁力或 ed2k。"""

from __future__ import annotations

import re
from urllib.parse import unquote

from av115_codes import parse_av

_MAGNET = re.compile(r"magnet:\?", re.IGNORECASE)
# ed2k://|file|文件名|大小|32 位哈希| 后面还可以带 p=、h= 等字段。
_ED2K = re.compile(
    r"ed2k://\|file\|(?P<name>[^|]+)\|\d+\|[0-9a-f]{32}\|",
    re.IGNORECASE,
)
OFFLINE_KINDS = frozenset({"magnet", "ed2k"})


def parse_intake(text: str) -> list[dict]:
    """一行一个。空行和 # 开头的行丢掉。"""
    items = []
    for line in (text or "").splitlines():
        item = classify_line(line)
        if item is not None:
            items.append(item)
    return items


def is_offline_kind(kind: str) -> bool:
    """磁力和 ed2k 走同一条 115 离线流程。"""
    return kind in OFFLINE_KINDS


def is_offline_task(task: dict) -> bool:
    """排队中的磁力或 ed2k，以及上次卡在失效接口上的失败任务，交给离线提交。"""
    if not isinstance(task, dict) or not is_offline_kind(str(task.get("kind") or "")):
        return False
    status = task.get("status")
    message = task.get("message") or ""
    retry = status == "failed" and "_get_id" in message
    return status == "queued" or retry


def classify_line(line: str) -> dict | None:
    raw = (line or "").strip()
    if not raw or raw.startswith("#"):
        return None
    if _MAGNET.search(raw):
        return _accepted("magnet", raw, _code_of(raw))
    if _ED2K.search(raw):
        return _accepted("ed2k", raw, _code_of(_ed2k_filename(raw)))
    parsed = parse_av(raw)
    if parsed:
        return _accepted("code", raw, parsed.labeled)
    return {"kind": "invalid", "text": raw, "code": "", "error": "不是番号、磁力或 ed2k"}


def _ed2k_filename(line: str) -> str:
    match = _ED2K.search(line or "")
    if not match:
        return ""
    return unquote(match.group("name")).strip()


def _code_of(text: str) -> str:
    parsed = parse_av(text)
    return parsed.labeled if parsed else ""


def _accepted(kind: str, text: str, code: str) -> dict:
    return {"kind": kind, "text": text, "code": code, "error": ""}
