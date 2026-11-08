"""115 AV 刮削的收任务测试，不访问网络，也不依赖 MoviePilot。"""

from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins.v2" / "av115scraper"))

from av115_intake import classify_line, is_offline_kind, is_offline_task, parse_intake

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "ed2k_uris.txt"
_CODES = {
    "|ADN-396.mp4|": "ADN-396",
    "|%40MTALL-061_Jasna0.11.1-LTX.mp4|": "MTALL-061",
    "|fsdss-304-C (1)_restored.mp4|": "FSDSS-304-C",
    "|IPZZ-081C.mp4|": "IPZZ-081-C",
    "|@star-820_restored_wm.mp4|": "STAR-820",
    "|@IPZ-676-C_restored~1.mp4|": "IPZ-676-C",
    "|@0619snis933FHD_restored_wm.mp4|": "",
    "|PPPD-451LTX.mp4|": "PPPD-451",
    "|@SSNI454.FHD_restored.mp4|": "SSNI-454",
}


def _sample_lines() -> list[str]:
    return [line for line in FIXTURE.read_text(encoding="utf-8").splitlines() if line.strip()]


class IntakeTests(unittest.TestCase):
    def test_sample_ed2k_uris_are_offline_tasks(self):
        lines = _sample_lines()
        self.assertEqual(len(lines), 71)
        for line in lines:
            item = classify_line(line)
            self.assertEqual(item["kind"], "ed2k", line)
            self.assertEqual(item["text"], line)
            self.assertEqual(item["error"], "")
            self.assertTrue(is_offline_kind(item["kind"]))
            queued = {
                "kind": item["kind"],
                "text": item["text"],
                "status": "queued",
                "message": "",
            }
            self.assertTrue(is_offline_task(queued))

    def test_filename_codes_come_from_parse_av(self):
        lines = _sample_lines()
        for needle, code in _CODES.items():
            matched = [line for line in lines if needle in line]
            self.assertEqual(len(matched), 1, needle)
            self.assertEqual(classify_line(matched[0])["code"], code, needle)

    def test_ed2k_case_extra_fields_and_whitespace(self):
        uri = "ED2K://|FILE|ADN-396.mp4|7189253963|4DB24F6B8D063B5A90BD6E396E206A3D|h=ABCDEF|/"
        item = classify_line("  " + uri + "  ")
        self.assertEqual(item["kind"], "ed2k")
        self.assertEqual(item["text"], uri)
        self.assertEqual(item["code"], "ADN-396")

    def test_magnets_and_codes_stay_the_same(self):
        magnet = "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567&dn=SSIS-001.mp4"
        self.assertEqual(
            classify_line(magnet),
            {"kind": "magnet", "text": magnet, "code": "SSIS-001", "error": ""},
        )
        bare = classify_line("magnet:?xt=urn:btih:abc")
        self.assertEqual(bare["kind"], "magnet")
        self.assertEqual(bare["code"], "")
        self.assertEqual(bare["text"], "magnet:?xt=urn:btih:abc")

        self.assertEqual(
            classify_line("  ssis001  "),
            {"kind": "code", "text": "ssis001", "code": "SSIS-001", "error": ""},
        )
        self.assertEqual(classify_line("SSIS-001")["code"], "SSIS-001")
        self.assertEqual(classify_line("ssis-12")["code"], "SSIS-012")
        tagged = classify_line("ABP-123C")
        self.assertEqual(tagged["kind"], "code")
        self.assertEqual(tagged["code"], "ABP-123-C")
        self.assertEqual(classify_line("ABP123C.mp4")["code"], "ABP-123-C")
        self.assertEqual(classify_line("[4K]ssis001-C.mkv")["code"], "SSIS-001-C")
        self.assertEqual(classify_line("FC2-PPV-1234567")["kind"], "code")
        self.assertEqual(classify_line("movie.2024.1080p.mkv")["kind"], "invalid")

        self.assertIsNone(classify_line(""))
        self.assertIsNone(classify_line("   "))
        self.assertIsNone(classify_line("# SSIS-001"))
        self.assertIsNone(classify_line("# ed2k://|file|SSIS-001.mp4|1|0123456789abcdef0123456789abcdef|/"))

        mentioned = classify_line("SSIS-001 不是 ed2k 链接")
        self.assertEqual(mentioned["kind"], "code")
        self.assertEqual(mentioned["code"], "SSIS-001")
        self.assertEqual(classify_line("notes about ed2k")["kind"], "invalid")

        junk = classify_line("不是链接")
        self.assertEqual(junk["kind"], "invalid")
        self.assertEqual(junk["error"], "不是番号、磁力或 ed2k")

    def test_parse_intake_keeps_order(self):
        uri = _sample_lines()[0]
        text = "\n".join(["SSIS-001", "# skip", "", "magnet:?xt=urn:btih:abc", uri, "nope"])
        items = parse_intake(text)
        self.assertEqual([item["kind"] for item in items], ["code", "magnet", "ed2k", "invalid"])

    def test_offline_queue_treats_ed2k_like_magnet(self):
        uri = _sample_lines()[0]
        self.assertTrue(is_offline_task({
            "kind": "ed2k", "text": uri, "status": "queued", "message": "",
        }))
        self.assertTrue(is_offline_task({
            "kind": "magnet", "text": "magnet:?xt=urn:btih:abc", "status": "queued", "message": "",
        }))
        self.assertFalse(is_offline_task({
            "kind": "code", "text": "SSIS-001", "status": "queued", "message": "",
        }))
        self.assertTrue(is_offline_task({
            "kind": "ed2k",
            "text": uri,
            "status": "failed",
            "message": "module has no attribute _get_id",
        }))
        self.assertFalse(is_offline_task({
            "kind": "magnet",
            "text": "magnet:?xt=urn:btih:abc",
            "status": "failed",
            "message": "115 拒绝了",
        }))
        self.assertFalse(is_offline_task({
            "kind": "ed2k",
            "text": uri,
            "status": "offline",
            "message": "已添加到 115 离线下载",
        }))
        self.assertTrue(is_offline_kind("magnet"))
        self.assertTrue(is_offline_kind("ed2k"))
        self.assertFalse(is_offline_kind("code"))

    def test_add_url_posts_the_raw_ed2k_and_magnet(self):
        calls = []

        class P115Client:
            def __init__(self, cookie, app=None):
                calls.append(("init", cookie, app))

            def clouddownload_task_add_url(self, payload):
                calls.append(payload)
                return {"state": True}

        previous = sys.modules.get("p115client")
        sys.modules["p115client"] = types.SimpleNamespace(P115Client=P115Client)
        from av115_pan import Pan115

        pan = Pan115("UID=1_A; CID=2; SEID=3")
        try:
            uri = _sample_lines()[0]
            magnet = "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567"
            self.assertEqual(pan.add_magnet(uri, "7")["state"], True)
            self.assertEqual(pan.add_magnet(magnet, "7")["state"], True)
        finally:
            pan.http.close()
            if previous is None:
                sys.modules.pop("p115client", None)
            else:
                sys.modules["p115client"] = previous
        self.assertEqual(calls[1], {"url": uri, "wp_path_id": "7"})
        self.assertEqual(calls[3], {"url": magnet, "wp_path_id": "7"})
        self.assertEqual(calls[0][2], "web")

    def test_user_copy_mentions_ed2k(self):
        init = (ROOT / "plugins.v2" / "av115scraper" / "__init__.py").read_text(encoding="utf-8")
        intake = (ROOT / "plugins.v2" / "av115scraper" / "av115_intake.py").read_text(encoding="utf-8")
        package = (ROOT / "package.v2.json").read_text(encoding="utf-8")
        self.assertNotIn("不是番号，也不是磁力", intake)
        self.assertNotIn("番号或磁力", init)
        self.assertNotIn("番号和磁力", init)
        self.assertNotIn("这条磁力", init)
        self.assertIn("plugin_version = \"1.5.7\"", init)
        self.assertIn('"version": "1.5.7"', package)
        self.assertIn("ed2k 链接", package)


if __name__ == "__main__":
    unittest.main()
