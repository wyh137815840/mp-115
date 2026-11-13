"""历史 strm：外网入口 + 严格编码迁移。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1] / "plugins.v2" / "av115scraper"
sys.path.insert(0, str(PLUGIN))

from av115_strm_migrate import migrate, normalize_url  # noqa: E402

BASE = "http://192.168.1.9:3000/api/v1/plugin/Av115Scraper/redirect?pickcode=abc&file_name="


class NormalizeUrlTest(unittest.TestCase):
    def test_lan_base_and_spaces_are_fixed(self):
        old = BASE + "[LoliHouse] Show - 01 [WebRip 1080p].mkv"
        new = normalize_url(old)
        self.assertTrue(new.startswith("https://mp.yuanhot.top:8443/api/v1/plugin/Av115Scraper/redirect?"))
        name = new.split("file_name=", 1)[1]
        self.assertNotIn(" ", name)
        self.assertNotIn("[", name)
        self.assertNotIn("]", name)
        self.assertIn("%5BLoliHouse%5D%20Show%20-%2001%20%5BWebRip%201080p%5D.mkv", name)

    def test_ampersand_in_filename_is_encoded_from_tail(self):
        old = BASE + "A&B.mp4"
        new = normalize_url(old)
        self.assertTrue(new.endswith("file_name=A%26B.mp4"))

    def test_double_encoding_is_normalized_once(self):
        old = BASE + "%255BLoliHouse%255D%2520Show%2520-%252001.mkv"
        new = normalize_url(old)
        self.assertTrue(new.endswith("file_name=%5BLoliHouse%5D%20Show%20-%2001.mkv"))
        self.assertNotIn("%25", new.split("file_name=", 1)[1])

    def test_url_without_file_name_keeps_path(self):
        old = "https://mp.yuanhot.top:8443/api/v1/plugin/Av115Scraper/redirect?pickcode=abc"
        self.assertEqual(normalize_url(old), old)


class MigrateTest(unittest.TestCase):
    def test_only_changed_files_are_rewritten(self):
        with tempfile.TemporaryDirectory() as root:
            a = Path(root) / "a.strm"
            b = Path(root) / "b.strm"
            a.write_text(BASE + "A B.mkv\n", encoding="utf-8")
            b.write_text("not a url\n", encoding="utf-8")
            out = migrate(root)
            self.assertEqual(out["total"], 2)
            self.assertEqual(out["changed"], 1)
            self.assertIn("%20", a.read_text(encoding="utf-8"))
            self.assertEqual(b.read_text(encoding="utf-8"), "not a url\n")


if __name__ == "__main__":
    unittest.main()
