"""把一行字收成番号或磁力。"""

from __future__ import annotations

import re

from av115_codes import parse_av

_MAGNET = re.compile(r"magnet:\?", re.IGNORECASE)


def parse_intake(text: str) -> list[dict]:
    """一行一个。空行和 # 开头的行丢掉。"""
    items = []
    for line in (text or "").splitlines():
        item = classify_line(line)
        if item is not None:
            items.append(item)
    return items


def classify_line(line: str) -> dict | None:
    raw = (line or "").strip()
    if not raw or raw.startswith("#"):
        return None
    parsed = parse_av(raw)
    code = parsed.labeled if parsed else ""
    if _MAGNET.search(raw):
        return {"kind": "magnet", "text": raw, "code": code, "error": ""}
    if parsed:
        return {"kind": "code", "text": raw, "code": code, "error": ""}
    return {"kind": "invalid", "text": raw, "code": "", "error": "不是番号，也不是磁力"}
