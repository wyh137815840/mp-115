"""离线等待的退避/超时判断：不访问网络，也不依赖 MoviePilot。"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins.v2" / "av115scraper"))

import av115_offline as offline  # noqa: E402


class WaitStepsTests(unittest.TestCase):
    def test_backoff_steps(self):
        self.assertEqual(offline.wait_seconds(0), 20)
        self.assertEqual(offline.wait_seconds(1), 20)
        self.assertEqual(offline.wait_seconds(2), 60)
        self.assertEqual(offline.wait_seconds(3), 120)
        self.assertEqual(offline.wait_seconds(4), 300)
        self.assertEqual(offline.wait_seconds(5), 600)
        self.assertEqual(offline.wait_seconds(99), 600)


class StampTests(unittest.TestCase):
    def test_parses_plain_and_iso(self):
        self.assertEqual(offline.parse_stamp("2026-11-14 11:00:00"), datetime(2026, 11, 14, 11, 0, 0))
        self.assertEqual(offline.parse_stamp("2026-11-14T11:00:00"), datetime(2026, 11, 14, 11, 0, 0))

    def test_bad_values(self):
        for value in (None, "", "昨天", "2026-13-40 99:99:99"):
            self.assertIsNone(offline.parse_stamp(value))


class DueTests(unittest.TestCase):
    def test_due_rules(self):
        now = datetime(2026, 11, 14, 11, 0, 0)
        self.assertTrue(offline.is_due({}, now))
        self.assertFalse(offline.is_due({"next_settle_at": "2026-11-14 11:05:00"}, now))
        self.assertTrue(offline.is_due({"next_settle_at": "2026-11-14 10:59:00"}, now))

    def test_mark_waiting_pushes_next_check_out(self):
        now = datetime(2026, 11, 14, 11, 0, 0)
        task = {"code": "ABF-042"}
        delay = offline.mark_waiting(task, now)
        self.assertEqual(delay, 20)
        self.assertEqual(task["settle_checks"], 1)
        self.assertEqual(task["next_settle_at"], "2026-11-14 11:00:20")
        self.assertFalse(offline.is_due(task, now))
        self.assertTrue(offline.is_due(task, now + timedelta(seconds=21)))
        # 第二次检查退避到 60 秒
        self.assertEqual(offline.mark_waiting(task, now), 60)


class TimeoutTests(unittest.TestCase):
    def test_old_task_times_out(self):
        now = datetime(2026, 11, 14, 11, 0, 0)
        task = {"created_at": "2026-11-14 04:00:00"}
        self.assertTrue(offline.timed_out(task, now))
        self.assertFalse(offline.timed_out({"created_at": "2026-11-14 10:30:00"}, now))
        self.assertFalse(offline.timed_out({}, now))

    def test_offline_since_wins_over_created_at(self):
        now = datetime(2026, 11, 14, 11, 0, 0)
        task = {"created_at": "2026-11-09 20:00:00", "offline_since": "2026-11-14 10:59:00"}
        self.assertFalse(offline.timed_out(task, now))

    def test_mark_timeout_marks_failed_and_clears_backoff(self):
        task = {"code": "DA-9164-C", "status": "offline", "settle_checks": 4,
                "next_settle_at": "2026-11-14 11:05:00"}
        message = offline.mark_timeout(task)
        self.assertEqual(task["status"], "failed")
        self.assertIn("小时", message)
        self.assertNotIn("next_settle_at", task)
        self.assertNotIn("settle_checks", task)

    def test_clear_wait(self):
        task = {"settle_checks": 3, "next_settle_at": "x", "last_settle_at": "y", "status": "ready"}
        offline.clear_wait(task)
        self.assertEqual(task, {"status": "ready"})


if __name__ == "__main__":
    unittest.main()