"""跨插件共享的 115 操作池：全局节奏 + 风控熔断。

Av115Scraper 和 Anime115 各自有 115 客户端，但共用同一个 115 账号。本模块用
文件锁 + state.json 在所有客户端之间共享「上次调用时间」和「熔断截止时间」：

- :func:`wait`：每次 115 请求前调用，保证全局至少 ``gap`` 秒一次；
- :func:`trip`：检测到验证码/风控/账号异常/911/406 等，写入冷却截止时间；
- :func:`blocked`：返回当前熔断状态（冷却中则抛 :class:`Blocked`）。

状态目录默认 ``/config/scripts/av-auto-fs/115_pool``（两个插件都挂载了 /config），
可用 ``AV115_GUARD_DIR`` 覆盖；``AV115_GUARD_GAP`` 默认 2.5 秒，
``AV115_GUARD_COOLDOWN`` 默认 1800 秒。
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - 非 Linux 环境只做单进程降级
    fcntl = None

DEFAULT_DIR = Path(os.environ.get("AV115_GUARD_DIR") or "/config/scripts/av-auto-fs/115_pool")
DEFAULT_GAP = float(os.environ.get("AV115_GUARD_GAP") or "2.5")
DEFAULT_COOLDOWN = float(os.environ.get("AV115_GUARD_COOLDOWN") or "1800")

RISK_RE = re.compile(
    r"(?<![\w\]])911(?![\w])|验证码|风控|账号异常|checking_info|violation|访问上限",
    re.IGNORECASE,
)


class Blocked(RuntimeError):
    """115 操作池处于熔断冷却中。"""

    def __init__(self, reason: str, retry_after: float = 0.0):
        self.reason = reason or "115 风控冷却中"
        self.retry_after = max(0.0, float(retry_after or 0))
        super().__init__(f"115 操作池冷却中（剩余 {int(self.retry_after)}s）：{self.reason}")


def looks_like_risk(text: str) -> bool:
    return bool(RISK_RE.search(str(text or "")))


def _state_dir() -> Path:
    path = Path(os.environ.get("AV115_GUARD_DIR") or DEFAULT_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _paths():
    root = _state_dir()
    return root / "state.json", root / "lock"


def _read_state() -> dict:
    path, _ = _paths()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_state(data: dict) -> None:
    path, _ = _paths()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


class _Lock:
    def __enter__(self):
        _, lock_path = _paths()
        self.fh = open(lock_path, "a+", encoding="utf-8")
        if fcntl is not None:
            fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, *_exc):
        try:
            if fcntl is not None:
                fcntl.flock(self.fh.fileno(), fcntl.LOCK_UN)
        finally:
            self.fh.close()


def blocked() -> Blocked | None:
    """冷却中返回 Blocked，否则 None。"""
    state = _read_state()
    until = float(state.get("blocked_until") or 0)
    now = time.time()
    if until > now:
        return Blocked(str(state.get("reason") or "115 风控冷却中"), until - now)
    return None


def status() -> dict:
    state = _read_state()
    now = time.time()
    until = float(state.get("blocked_until") or 0)
    return {
        "blocked": until > now,
        "reason": state.get("reason") or "",
        "retry_after": max(0.0, until - now),
        "gap": DEFAULT_GAP,
        "last_call": float(state.get("last_call") or 0),
    }


def wait(gap: float | None = None) -> None:
    """在共享池里占一个调用槽；冷却中抛 Blocked，否则睡到全局节奏允许。"""
    gap = DEFAULT_GAP if gap is None else max(0.0, float(gap))
    now = time.time()
    with _Lock():
        state = _read_state()
        until = float(state.get("blocked_until") or 0)
        if until > now:
            raise Blocked(str(state.get("reason") or "115 风控冷却中"), until - now)
        slot = max(now, float(state.get("last_call") or 0) + gap)
        state["last_call"] = slot
        state["updated_at"] = now
        _write_state(state)
    delay = slot - now
    if delay > 0:
        time.sleep(delay)


def trip(reason: str, cooldown: float | None = None) -> float:
    """写入熔断；返回冷却秒数。多个插件重复触发会延长到最晚的截止时间。"""
    cooldown = DEFAULT_COOLDOWN if cooldown is None else max(0.0, float(cooldown))
    now = time.time()
    until = now + cooldown
    reason = str(reason or "115 风控")[:300]
    with _Lock():
        state = _read_state()
        state["blocked_until"] = max(float(state.get("blocked_until") or 0), until)
        state["reason"] = reason
        state["tripped_at"] = now
        state["updated_at"] = now
        _write_state(state)
    return max(0.0, float(state["blocked_until"]) - now)


def clear() -> None:
    """人工解除熔断（冷却结束后也可手动清）。"""
    with _Lock():
        state = _read_state()
        state.pop("blocked_until", None)
        state.pop("reason", None)
        state["updated_at"] = time.time()
        _write_state(state)
