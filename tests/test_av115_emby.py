"""Emby 多版本候选分组的纯逻辑测试。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins.v2" / "av115scraper"))

import av115_emby  # noqa: E402


def _item(iid, path, provider):
    return {"Id": iid, "Path": path, "ProviderIds": provider}


class GroupVersionItemsTests(unittest.TestCase):
    def test_javbus_base_and_uc_grouped(self):
        items = [
            _item("1", "/media/AV/清野咲/CAWB-001/CAWB-001.strm", {"javbus": "CAWB-001"}),
            _item("2", "/media/AV/清野咲/CAWB-001-UC/CAWB-001-UC.strm", {"javbus": "CAWB-001"}),
        ]
        groups = av115_emby.group_version_items(items)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["provider"], "javbus")
        self.assertEqual(len(groups[0]["items"]), 2)

    def test_metatube_same_code_grouped(self):
        items = [
            _item("1", "/media/AV/MIDV-459/MIDV-459.strm", {"MetaTube": "FANZA:midv00459"}),
            _item("2", "/media/AV/MIDV-459/MIDV-459.restored.strm", {"MetaTube": "FANZA:midv00459"}),
        ]
        groups = av115_emby.group_version_items(items)
        self.assertEqual(len(groups), 1)
        self.assertTrue(groups[0]["same_code"])

    def test_metatube_different_codes_skipped(self):
        items = [
            _item("1", "/media/AV/MIAD-823/MIAD-823.strm", {"MetaTube": "FANZA:firststar-1968"}),
            _item("2", "/media/AV/PGD-707/PGD-707.strm", {"MetaTube": "FANZA:firststar-1968"}),
        ]
        self.assertEqual(av115_emby.group_version_items(items), [])

    def test_single_items_ignored(self):
        items = [_item("1", "/media/AV/CAWB-001/CAWB-001.strm", {"javbus": "CAWB-001"})]
        self.assertEqual(av115_emby.group_version_items(items), [])


if __name__ == "__main__":
    unittest.main()
