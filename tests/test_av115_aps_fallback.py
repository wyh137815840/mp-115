"""aps 列目录被单独风控时：只停用 aps 改走 web /files，不锁整个 115 池。"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins.v2" / "av115scraper"))

import av115_pan  # noqa: E402


class FakeResponse:
    def __init__(self, payload: dict, status: int = 200, text: str | None = None):
        self.status_code = status
        self._payload = payload
        self.text = text if text is not None else json.dumps(payload, ensure_ascii=False)

    def json(self) -> dict:
        return self._payload


class ApsRiskFallbackTests(unittest.TestCase):
    def setUp(self):
        self.pan = av115_pan.Pan115("UID=1_A; CID=2; SEID=3")
        self.trips: list[str] = []
        self.aps_calls = 0
        self.web_calls = 0
        self._orig_trip = av115_pan._guard_trip
        self._orig_pace = av115_pan._pace
        av115_pan._guard_trip = lambda reason: self.trips.append(reason)
        av115_pan._pace = lambda: None
        self.pan.http.request = self._request
        self.pan.http.get = self._web_get

    def tearDown(self):
        av115_pan._guard_trip = self._orig_trip
        av115_pan._pace = self._orig_pace

    def _request(self, method, url, **kwargs):
        if "aps.115.com" in str(url):
            self.aps_calls += 1
            return FakeResponse({"state": False, "error": "风控"}, text="风控")
        raise AssertionError(f"不应请求 {url}")

    def _web_get(self, url, **kwargs):
        self.web_calls += 1
        return FakeResponse({
            "count": 2,
            "data": [
                {"cid": "11", "pid": "0", "n": "ABF-001", "m": 0},
                {"cid": "12", "pid": "0", "n": "ABF-002", "m": 0},
            ],
        })

    def test_aps_risk_falls_back_to_web_without_tripping_pool(self):
        items = self.pan.list_children("0")
        self.assertEqual([row["fn"] for row in items], ["ABF-001", "ABF-002"])
        self.assertEqual(self.trips, [])  # 没有锁整个 115 池
        self.assertGreater(self.pan._aps_skip_until, 0)
        self.assertEqual(self.aps_calls, 1)

    def test_next_listing_skips_aps(self):
        self.pan.list_children("0")
        self.pan._children_cache.clear()
        self.pan.list_children("0")
        self.assertEqual(self.aps_calls, 1)  # 第二次不再撞 aps
        self.assertEqual(self.web_calls, 2)

    def test_non_aps_risk_still_trips_pool(self):
        def request(method, url, **kwargs):
            return FakeResponse({"state": False, "error": "风控"}, text="风控")

        self.pan.http.request = request
        with self.assertRaises(RuntimeError):
            self.pan._call("GET", "/2.0/ufile/search", params={"search_value": "x"})
        self.assertEqual(len(self.trips), 1)
        self.assertIn("风控", self.trips[0])


if __name__ == "__main__":
    unittest.main()