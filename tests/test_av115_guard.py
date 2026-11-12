"""共享 115 操作池：全局节奏、熔断、风险判定。"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins.v2" / "av115scraper"))


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["AV115_GUARD_DIR"] = self.tmp.name
        os.environ["AV115_GUARD_GAP"] = "0.05"
        os.environ["AV115_GUARD_COOLDOWN"] = "30"
        import av115_guard
        self.guard = importlib.reload(av115_guard)

    def tearDown(self):
        self.tmp.cleanup()
        for key in ("AV115_GUARD_DIR", "AV115_GUARD_GAP", "AV115_GUARD_COOLDOWN"):
            os.environ.pop(key, None)

    def test_global_gap_is_shared(self):
        start = time.monotonic()
        self.guard.wait()
        self.guard.wait()
        self.assertGreaterEqual(time.monotonic() - start, 0.04)

    def test_trip_blocks_until_cooldown(self):
        self.assertIsNone(self.guard.blocked())
        self.guard.trip("115 风控 911", cooldown=30)
        blocked = self.guard.blocked()
        self.assertIsNotNone(blocked)
        self.assertIn("911", str(blocked))
        with self.assertRaises(self.guard.Blocked):
            self.guard.wait()
        self.guard.clear()
        self.assertIsNone(self.guard.blocked())

    def test_risk_markers(self):
        self.assertTrue(self.guard.looks_like_risk("验证码"))
        self.assertTrue(self.guard.looks_like_risk("115 返回 911"))
        self.assertFalse(self.guard.looks_like_risk("frame_911s @911s"))
        self.assertFalse(self.guard.looks_like_risk("正常完成"))


if __name__ == "__main__":
    unittest.main()
