"""公开门面契约：外部工具只依赖 av115_api。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins.v2" / "av115scraper"))

import av115_api  # noqa: E402


class ApiFacadeTests(unittest.TestCase):
    def test_api_version_is_declared(self):
        self.assertGreaterEqual(av115_api.API_VERSION, 1)

    def test_exports_needed_by_av_auto(self):
        for name in ("open_pan", "build_play_url", "destination", "find_existing",
                     "safe_name", "write_library", "item_id", "item_name",
                     "item_is_dir", "item_size", "item_pickcode", "api_version"):
            self.assertTrue(hasattr(av115_api, name), name)

    def test_item_helpers(self):
        row = {"fid": "123", "n": "SSIS-001.mp4", "fc": "1", "s": 1024, "pc": "p" * 17}
        self.assertEqual(av115_api.item_id(row), "123")
        self.assertEqual(av115_api.item_name(row), "SSIS-001.mp4")
        self.assertFalse(av115_api.item_is_dir(row))
        self.assertEqual(av115_api.item_size(row), 1024)
        self.assertEqual(av115_api.item_pickcode(row), "p" * 17)
