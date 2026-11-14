"""离线等待的退避与超时判断，不依赖 MoviePilot，便于单测。

115 离线任务从提交到「正片出现在 /AV」可能要几分钟。同一个任务每 20 秒去问一次
会把账号撞进风控，所以这里按检查次数逐步拉长间隔，并在等太久时判定任务丢失。
"""
from __future__ import annotations

from datetime import datetime, timedelta

# 第 1..N 次检查后的等待秒数：20s, 1m, 2m, 5m, 10m（之后都 10m）。
WAIT_STEPS = (20, 60, 120, 300, 600)
# 超过这个时长还只报「没下完」，就当离线任务已经丢了。
WAIT_TIMEOUT = 6 * 3600
STAMP = "%Y-%m-%d %H:%M:%S"


def parse_stamp(text) -> datetime | None:
    """任务里的时间戳（%Y-%m-%d %H:%M:%S，容忍 ISO 的 T）。解析不了就当没有。"""
    raw = str(text or "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw[:19].replace("T", " "), STAMP)
    except ValueError:
        return None


def stamp(moment: datetime) -> str:
    return moment.strftime(STAMP)


def wait_seconds(checks: int) -> int:
    """第 checks 次检查后应该等多久。"""
    if checks <= 0:
        return WAIT_STEPS[0]
    return WAIT_STEPS[min(checks - 1, len(WAIT_STEPS) - 1)]


def is_due(task: dict, now: datetime) -> bool:
    """到了下次检查时间（或从没检查过）才值得再发 115 请求。"""
    nxt = parse_stamp(task.get("next_settle_at"))
    return nxt is None or nxt <= now


def waiting_since(task: dict) -> datetime | None:
    """这个任务从什么时候开始在等离线。优先 offline_since，旧任务退回 created_at。"""
    return parse_stamp(task.get("offline_since")) or parse_stamp(task.get("created_at"))


def timed_out(task: dict, now: datetime, limit: int = WAIT_TIMEOUT) -> bool:
    started = waiting_since(task)
    return started is not None and (now - started).total_seconds() > limit


def mark_waiting(task: dict, now: datetime) -> int:
    """记一次「还没下完」，返回本次退避秒数。"""
    checks = int(task.get("settle_checks") or 0) + 1
    delay = wait_seconds(checks)
    task["settle_checks"] = checks
    task["last_settle_at"] = stamp(now)
    task["next_settle_at"] = stamp(now + timedelta(seconds=delay))
    return delay


def mark_timeout(task: dict, limit: int = WAIT_TIMEOUT) -> str:
    """等待超时：标 failed 并清掉退避字段，免得再被离线监视反复撞。"""
    message = f"离线等待超过 {limit // 3600} 小时仍未出正片，疑似离线任务丢失；重新提交番号即可"
    task["status"] = "failed"
    task["message"] = message
    task.pop("next_settle_at", None)
    task.pop("settle_checks", None)
    return message


def clear_wait(task: dict) -> None:
    """任务已经出正片，清掉等待状态。"""
    for key in ("settle_checks", "next_settle_at", "last_settle_at"):
        task.pop(key, None)