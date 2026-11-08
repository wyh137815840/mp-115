"""内外网播放地址：strm 写哪一侧，以及 /redirect 按客户端改入口。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1] / "plugins.v2" / "av115scraper"
sys.path.insert(0, str(PLUGIN))

from av115_play import (  # noqa: E402
    LAN_PLAY_BASE,
    LEGACY_WAN_PLAY_BASE,
    WAN_PLAY_BASE,
    choose_strm_template,
    normalize_play_base,
    redirect_location,
)
from av115_scan import CloudFile, ScanOptions, ScanResult, _handle_video, run_scan  # noqa: E402

PICK = "abc123pickcodeXYZ"


def _redirect(**overrides) -> str | None:
    values = {
        "host": "mp.yuanhot.top",
        "forwarded_host": "",
        "peer": "",
        "forwarded_for": "",
        "real_ip": "",
        "pickcode": PICK,
        "filename": "SSIS-001.mp4",
        "play_base": WAN_PLAY_BASE,
        "play_base_lan": LAN_PLAY_BASE,
        "networks": "",
    }
    values.update(overrides)
    return redirect_location(**values)


class TemplateTest(unittest.TestCase):
    def test_legacy_default_points_at_av115_redirect(self):
        self.assertEqual(normalize_play_base(LEGACY_WAN_PLAY_BASE), WAN_PLAY_BASE)
        self.assertEqual(normalize_play_base(""), WAN_PLAY_BASE)
        self.assertEqual(normalize_play_base("https://example/play"), "https://example/play")

    def test_strm_uses_lan_template_when_asked(self):
        chosen = choose_strm_template(WAN_PLAY_BASE, LAN_PLAY_BASE, True)
        self.assertEqual(chosen, LAN_PLAY_BASE)

    def test_strm_keeps_wan_template_when_switch_off(self):
        chosen = choose_strm_template(WAN_PLAY_BASE, LAN_PLAY_BASE, False)
        self.assertEqual(chosen, WAN_PLAY_BASE)

    def test_strm_falls_back_when_chosen_side_is_empty(self):
        self.assertEqual(choose_strm_template(WAN_PLAY_BASE, "", True), WAN_PLAY_BASE)
        self.assertEqual(choose_strm_template("", LAN_PLAY_BASE, False), LAN_PLAY_BASE)


class RedirectTest(unittest.TestCase):
    def test_lan_client_on_public_host_goes_to_lan_moviepilot(self):
        url = _redirect(forwarded_for="192.168.1.20")
        self.assertIsNotNone(url)
        self.assertTrue(url.startswith("http://192.168.1.9:3000/api/v1/plugin/Av115Scraper/redirect?"))
        self.assertIn(f"pickcode={PICK}", url)
        self.assertIn("file_name=SSIS-001.mp4", url)
        self.assertNotIn("mp.yuanhot.top", url)

    def test_wan_client_on_public_host_stays_for_115(self):
        self.assertIsNone(_redirect(forwarded_for="8.8.8.8"))

    def test_lan_client_already_on_lan_host_stays_for_115(self):
        self.assertIsNone(_redirect(host="192.168.1.9:3000", peer="192.168.1.20"))

    def test_public_peer_that_dialed_lan_host_goes_to_public_domain(self):
        url = _redirect(host="192.168.1.9:3000", peer="8.8.8.8")
        self.assertIsNotNone(url)
        self.assertTrue(url.startswith("https://mp.yuanhot.top/api/v1/plugin/Av115Scraper/redirect?"))

    def test_proxy_without_forwarded_client_is_not_treated_as_lan(self):
        self.assertIsNone(_redirect(host="mp.yuanhot.top", peer="172.17.0.1"))

    def test_forwarded_lan_client_on_public_name_behind_proxy(self):
        url = _redirect(
            host="192.168.1.9:3000",
            forwarded_host="mp.yuanhot.top",
            forwarded_for="192.168.1.8, 172.17.0.1",
        )
        self.assertIsNotNone(url)
        self.assertTrue(url.startswith("http://192.168.1.9:3000/"))

    def test_rewritten_host_without_forwarded_host_does_not_loop(self):
        self.assertIsNone(_redirect(host="192.168.1.9:3000", forwarded_for="1.1.1.1"))

    def test_same_origin_does_not_bounce(self):
        self.assertIsNone(_redirect(
            forwarded_for="192.168.1.20",
            play_base=WAN_PLAY_BASE,
            play_base_lan=WAN_PLAY_BASE,
        ))

    def test_private_ranges_and_mapped_ipv4(self):
        self.assertIsNotNone(_redirect(forwarded_for="10.1.2.3"))
        self.assertIsNotNone(_redirect(forwarded_for="172.16.5.5"))
        self.assertIsNotNone(_redirect(real_ip="::ffff:192.168.1.9"))
        self.assertIsNone(_redirect(forwarded_for="2001:db8::1"))

    def test_invalid_networks_fall_back_to_private_ranges(self):
        url = _redirect(forwarded_for="192.168.1.9", networks="not-a-cidr")
        self.assertIsNotNone(url)
        self.assertIn("192.168.1.9:3000", url)


class StrmWriteTest(unittest.TestCase):
    def test_existing_nfo_rewrites_strm_to_lan_or_wan(self):
        video = CloudFile(
            path="/AV/SSIS-001/SSIS-001.mp4",
            name="SSIS-001.mp4",
            is_dir=False,
            size=800_000_000,
            pickcode=PICK,
        )
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root) / "SSIS-001"
            folder.mkdir()
            (folder / "movie.nfo").write_text("<movie></movie>", encoding="utf-8")
            lan = ScanOptions(
                roots=["/AV"],
                strm_root=root,
                play_base=WAN_PLAY_BASE,
                play_base_lan=LAN_PLAY_BASE,
                strm_use_lan=True,
                interval_seconds=0,
            )
            _handle_video(
                storage=object(),
                video=video,
                options=lan,
                counts={},
                protected=set(),
                strm_root=Path(root),
                javbus=None,
                result=ScanResult(ok=True),
            )
            text = (folder / "SSIS-001.strm").read_text(encoding="utf-8")
            self.assertIn("http://192.168.1.9:3000/api/v1/plugin/Av115Scraper/redirect", text)
            self.assertNotIn("mp.yuanhot.top", text)
            self.assertIn(f"pickcode={PICK}", text)

            wan = ScanOptions(
                roots=["/AV"],
                strm_root=root,
                play_base=WAN_PLAY_BASE,
                play_base_lan=LAN_PLAY_BASE,
                strm_use_lan=False,
                interval_seconds=0,
            )
            _handle_video(
                storage=object(),
                video=video,
                options=wan,
                counts={},
                protected=set(),
                strm_root=Path(root),
                javbus=None,
                result=ScanResult(ok=True),
            )
            text = (folder / "SSIS-001.strm").read_text(encoding="utf-8")
            self.assertIn("https://mp.yuanhot.top/api/v1/plugin/Av115Scraper/redirect", text)
            self.assertNotIn("192.168.1.9", text)

    def test_scan_requires_a_play_template(self):
        result = run_scan(None, ScanOptions(roots=["/AV"], strm_root="/media/AV", play_base="  "))
        self.assertFalse(result.ok)
        self.assertEqual(result.error, "没有填写播放地址")


if __name__ == "__main__":
    unittest.main()
