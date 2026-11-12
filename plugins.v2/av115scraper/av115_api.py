"""稳定对外接口：给 av_auto 等外部工具使用，不直接依赖内部模块。

外部工具只应 import 本模块。改了这里暴露的名字/行为时，请同时递增
API_VERSION；调用方会检查它，避免静默错配。
"""

from __future__ import annotations

API_VERSION = 1

from av115_library import build_play_url, destination, find_existing, safe_name, write_library
from av115_pan import Pan115
from av115_pan import (
    _file_id as item_id,
    _is_directory as item_is_dir,
    _name as item_name,
    _size as item_size,
)


def open_pan(cookie: str) -> Pan115:
    """用 115 Cookie 建一个目录/离线操作客户端。"""
    return Pan115(cookie or "")


def item_pickcode(item: dict) -> str:
    """115 列表行的 pickcode（兼容多种字段名）。"""
    item = item or {}
    return str(item.get("pc") or item.get("pickcode") or item.get("pick_code") or "")


def api_version() -> int:
    return API_VERSION
