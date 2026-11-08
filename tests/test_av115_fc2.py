"""FC2 刮削：资料站解析、来源顺序，以及扫描时不走 JavBus。"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1] / "plugins.v2" / "av115scraper"
sys.path.insert(0, str(PLUGIN))

from av115_codes import parse_av
from av115_fc2 import (
    Fc2Client,
    article_id,
    duration_minutes,
    movie_from_cmadb,
    movie_from_official,
    parse_actresses,
    parse_inertia,
    unwrap_fc2_image,
)
from av115_javbus import Movie
from av115_library import build_nfo
from av115_scan import CloudFile, ScanOptions, ScanResult, _handle_video

THUMB = (
    "https://contents-thumbnail2.fc2.com/w276/"
    "storage200000.contents.fc2.com/file/397/39660332/1758279087.11.jpg"
)
FULL = "https://storage200000.contents.fc2.com/file/397/39660332/1758279087.11.jpg"
VERSION = "fcb3b524d4c7f8f3d2c38e437b35b7a9"


def _article(video_id="4751586", duration="01:09:45", image=THUMB):
    return {
        "title": "色白スレンダー美乳",
        "video_id": int(video_id),
        "release_date": "2025-08-29",
        "duration": duration,
        "image_url": image,
        "writer": {"name": "ハメダルマ"},
        "tags": [{"name": "ハメ撮り"}, {"name": "素人"}, {"name": "ハメ撮り"}],
    }


def _page_html(article, version=VERSION):
    page = {
        "component": "Articles/Show",
        "version": version,
        "url": f"/articles/{article['video_id']}",
        "props": {"article": article, "errors": {}},
        "deferredProps": {"default": ["actresses"]},
    }
    body = json.dumps(page, ensure_ascii=False)
    return (
        "<!DOCTYPE html><html><head><title inertia>FC2CMADB</title>"
        f'<script data-page="app" type="application/json">{body}</script>'
        "</head><body>cdn-cgi/challenge-platform beacon</body></html>"
    )


def _actresses(*names):
    listed = [
        {
            "id": index,
            "name": name,
            "alias_name": "葉柴三奈 究極アイドルフェイス",
        }
        for index, name in enumerate(names, 1)
    ]
    return json.dumps(
        {
            "component": "Articles/Show",
            "version": VERSION,
            "props": {"errors": {}, "actresses": listed},
        },
        ensure_ascii=False,
    )


OFFICIAL_HTML = """
<html><head>
<meta property="og:title" content="サンプルタイトル | FC2コンテンツマーケット">
<meta property="og:image" content="https://contents-thumbnail2.fc2.com/w480/storage200000.contents.fc2.com/file/397/39660332/1758279087.11.jpg">
<meta property="og:description" content="説明文">
</head><body></body></html>
"""
AGE_HTML = "<html><title>Age verification is required</title><body>Age verification is required to access Content Market Adult.</body></html>"
DELETED_HTML = "<html><body>お探しの商品が見つかりませんでした</body></html>"
CHALLENGE_HTML = "<html><head><title>Just a moment...</title></head><body></body></html>"


class ParseTest(unittest.TestCase):
    def test_codes_already_recognize_fc2(self):
        for name, ident in (
            ("FC2-PPV-4751586.mp4", "4751586"),
            ("fc2 ppv 3127193", "3127193"),
            ("FC2PPV4751586", "4751586"),
        ):
            hit = parse_av(name)
            self.assertIsNotNone(hit)
            self.assertEqual(hit.kind, "fc2")
            self.assertEqual(hit.code, f"FC2-PPV-{ident}")

    def test_unwrap_thumbnail(self):
        self.assertEqual(unwrap_fc2_image(THUMB), FULL)
        self.assertEqual(unwrap_fc2_image(FULL), FULL)

    def test_duration(self):
        self.assertEqual(duration_minutes("01:09:45"), "69")
        self.assertEqual(duration_minutes("28:38"), "28")

    def test_cmadb_page_keeps_title_cover_and_ignores_aliases(self):
        html = _page_html(_article())
        page = parse_inertia(html)
        movie = movie_from_cmadb(page, "FC2-PPV-4751586", "4751586")
        self.assertIsNotNone(movie)
        self.assertEqual(movie.title, "色白スレンダー美乳")
        self.assertEqual(movie.cover, FULL)
        self.assertEqual(movie.release, "2025-08-29")
        self.assertEqual(movie.length, "69")
        self.assertEqual(movie.studio, "ハメダルマ")
        self.assertEqual(movie.publisher, "FC2")
        self.assertEqual(movie.genres, ("ハメ撮り", "素人"))
        names = parse_actresses(_actresses("音田絵凛"))
        self.assertEqual(names, ("音田絵凛",))

    def test_official_deleted_and_age_gate_are_empty(self):
        self.assertIsNone(movie_from_official(DELETED_HTML, "FC2-PPV-1", "1"))
        self.assertIsNone(movie_from_official(AGE_HTML, "FC2-PPV-1", "1"))
        self.assertIsNone(movie_from_official(CHALLENGE_HTML, "FC2-PPV-1", "1"))
        movie = movie_from_official(OFFICIAL_HTML, "FC2-PPV-4751586", "4751586")
        self.assertEqual(movie.title, "サンプルタイトル")
        self.assertEqual(movie.cover, FULL)

    def test_video_id_mismatch_is_rejected(self):
        page = parse_inertia(_page_html(_article("4751586")))
        self.assertIsNone(movie_from_cmadb(page, "FC2-PPV-3127193", "3127193"))

    def test_article_id(self):
        self.assertEqual(article_id("FC2-PPV-3127193"), "3127193")
        self.assertEqual(article_id("not-a-code"), "")


class ClientOrderTest(unittest.TestCase):
    def test_cmadb_hit_does_not_call_official(self):
        seen = []

        def fetch(url, headers=None):
            seen.append((url, headers))
            if headers and headers.get("X-Inertia-Partial-Data") == "actresses":
                self.assertEqual(headers.get("X-Inertia-Version"), VERSION)
                return 200, _actresses("音田絵凛")
            return 200, _page_html(_article())

        movie, reason = Fc2Client("https://fc2cmadb.com", fetch).scrape("FC2-PPV-4751586", "fc2")
        self.assertEqual(reason, "")
        self.assertEqual(movie.actresses, ("音田絵凛",))
        self.assertEqual(movie.length, "69")
        self.assertTrue(all("fc2cmadb.com" in url for url, _headers in seen))
        self.assertEqual(len(seen), 2)

    def test_deleted_official_uses_wayback_snapshot(self):
        seen = []

        def fetch(url, headers=None):
            seen.append(url)
            if "fc2cmadb.com" in url:
                return 404, ""
            if "web.archive.org/cdx/" in url:
                return 200, '[["timestamp"],["20260217114341"]]'
            if "web.archive.org/web/20260217114341id_/" in url:
                return 200, OFFICIAL_HTML
            if "adult.contents.fc2.com" in url:
                return 200, AGE_HTML
            return 403, CHALLENGE_HTML

        movie, reason = Fc2Client("fc2cmadb.com", fetch).scrape("FC2PPV4751586", "fc2")
        self.assertEqual(reason, "")
        self.assertEqual(movie.title, "サンプルタイトル")
        self.assertEqual(movie.code, "FC2-PPV-4751586")
        self.assertTrue(any("adult.contents.fc2.com/article/4751586/" in url for url in seen))
        self.assertTrue(any(url.startswith("https://web.archive.org/web/20260217114341id_/") for url in seen))
        self.assertFalse(any("fc2ppv-db.com" in url or "missav" in url for url in seen))

    def test_all_sources_fail(self):
        def fetch(url, headers=None):
            if "fc2cmadb.com" in url:
                return 404, ""
            if "adult.contents.fc2.com" in url and "web.archive.org" not in url:
                return 200, DELETED_HTML
            if "cdx/search" in url:
                return 200, "[]"
            return 403, CHALLENGE_HTML

        movie, reason = Fc2Client("https://fc2cmadb.com", fetch).scrape("FC2-PPV-4751586", "fc2")
        self.assertIsNone(movie)
        self.assertIn("资料站没有这部片", reason)
        self.assertIn("官网已删除", reason)
        self.assertIn("Wayback 没有快照", reason)
        self.assertIn("备用站被拦截", reason)


class _Client:
    def __init__(self, movie=None, reason="未找到", cover=b"cover-bytes"):
        self.calls = []
        self.movie = movie
        self.reason = reason
        self.cover = cover

    def scrape(self, code, kind):
        self.calls.append((code, kind))
        if self.movie is None:
            return None, self.reason
        return self.movie, ""

    def download_cover(self, url):
        self.calls.append(("cover", url))
        return self.cover


def _video(name, parent="/AV/FC2/folder"):
    return CloudFile(
        path=f"{parent}/{name}",
        name=name,
        is_dir=False,
        size=800_000_000,
        pickcode="abc123pickcodeXYZ",
    )


def _options(root):
    return ScanOptions(
        roots=["/AV"],
        strm_root=root,
        play_base="https://example/redirect?pickcode={pickcode}&file_name={filename}",
        dry_run=False,
        rename_folder=False,
        interval_seconds=0,
    )


def _run(video, javbus, fc2, root, dry_run=False):
    options = _options(root)
    if dry_run:
        options.dry_run = True
    result = ScanResult(ok=True, dry_run=dry_run)
    item = _handle_video(
        storage=object(),
        video=video,
        options=options,
        counts={},
        protected=set(),
        strm_root=Path(root),
        javbus=javbus,
        result=result,
        fc2=fc2,
    )
    return result, item


def _uniqueid(path):
    root = ET.parse(path).getroot()
    node = root.find("uniqueid")
    return node.get("type"), node.text


class ScanRouteTest(unittest.TestCase):
    def test_fc2_uses_fc2_client_and_uniqueid(self):
        movie = Movie(
            code="FC2-PPV-4751586",
            title="色白スレンダー美乳",
            cover=FULL,
            release="2025-08-29",
            length="69",
            director="",
            studio="ハメダルマ",
            publisher="FC2",
            series="",
            genres=("ハメ撮り",),
            actresses=("音田絵凛",),
        )
        javbus = _Client()
        fc2 = _Client(movie)
        with tempfile.TemporaryDirectory() as root:
            result, item = _run(_video("FC2-PPV-4751586.mp4"), javbus, fc2, root)
            self.assertEqual(javbus.calls, [])
            self.assertEqual(fc2.calls[0], ("FC2-PPV-4751586", "fc2"))
            self.assertEqual(item["status"], "ok")
            self.assertEqual(result.scraped, 1)
            nfo = Path(item["output"]) / "movie.nfo"
            self.assertEqual(_uniqueid(nfo), ("fc2", "FC2-PPV-4751586"))
            text = nfo.read_text(encoding="utf-8")
            self.assertIn("音田絵凛", text)
            self.assertIn("色白スレンダー美乳", text)
            self.assertIn(">69<", text)
            poster = (Path(item["output"]) / "poster.jpg").read_bytes()
            self.assertEqual(poster, b"cover-bytes")

    def test_fc2_cover_is_not_cropped_like_javbus(self):
        movie = Movie(
            code="FC2-PPV-3127193",
            title="片想い",
            cover=FULL,
            release="2022-11-12",
            length="28",
            director="",
            studio="趣味はめ",
            publisher="FC2",
            series="",
            genres=(),
            actresses=("石川祐奈",),
        )
        calls = []
        import av115_library

        original = av115_library.crop_poster

        def spy(data):
            calls.append(data)
            return b"cropped"

        av115_library.crop_poster = spy
        try:
            with tempfile.TemporaryDirectory() as root:
                _result, item = _run(_video("FC2-PPV-3127193.mp4"), _Client(), _Client(movie), root)
                poster = (Path(item["output"]) / "poster.jpg").read_bytes()
        finally:
            av115_library.crop_poster = original
        self.assertEqual(calls, [])
        self.assertEqual(poster, b"cover-bytes")

    def test_censored_still_uses_javbus(self):
        movie = Movie(
            code="SSIS-001",
            title="标题",
            cover="https://example/cover.jpg",
            release="2024-01-02",
            length="120",
            director="",
            studio="S1",
            publisher="S1",
            series="",
            genres=(),
            actresses=("演员",),
        )
        javbus = _Client(movie)
        fc2 = _Client()
        with tempfile.TemporaryDirectory() as root:
            result, item = _run(_video("SSIS-001.mp4", "/AV/有码"), javbus, fc2, root)
            self.assertEqual(fc2.calls, [])
            self.assertEqual(javbus.calls[0], ("SSIS-001", "censored"))
            self.assertEqual(_uniqueid(Path(item["output"]) / "movie.nfo"), ("javbus", "SSIS-001"))
            self.assertEqual(result.scraped, 1)

    def test_fc2_miss_stays_partial_without_javbus(self):
        javbus = _Client()
        fc2 = _Client(None, "资料站没有这部片")
        with tempfile.TemporaryDirectory() as root:
            result, item = _run(_video("FC2-PPV-4751586.mp4"), javbus, fc2, root)
            self.assertEqual(javbus.calls, [])
            self.assertEqual(item["status"], "partial")
            self.assertEqual(result.failed, 1)
            self.assertEqual(_uniqueid(Path(item["output"]) / "movie.nfo"), ("fc2", "FC2-PPV-4751586"))

    def test_missing_fc2_client_does_not_fall_through(self):
        javbus = _Client()
        with tempfile.TemporaryDirectory() as root:
            result, item = _run(_video("FC2-PPV-4751586.mp4"), javbus, None, root)
            self.assertEqual(javbus.calls, [])
            self.assertEqual(item["status"], "failed")
            self.assertIn("没有配置 FC2", item["message"])
            self.assertEqual(result.scraped, 0)

    def test_dry_run_does_not_request_fc2(self):
        fc2 = _Client()
        with tempfile.TemporaryDirectory() as root:
            _result, item = _run(_video("FC2-PPV-4751586.mp4"), _Client(), fc2, root, dry_run=True)
            self.assertEqual(fc2.calls, [])
            self.assertIn("未请求 FC2", item["message"])

    def test_default_uniqueid_remains_javbus(self):
        text = build_nfo(
            code="SSIS-001",
            title="标题",
            release="",
            length="",
            director="",
            studio="",
            publisher="",
            series="",
            genres=[],
            actresses=[],
            tag="",
        )
        self.assertIn('type="javbus"', text)


class Fc2LiveTest(unittest.TestCase):
    """手动核对：FC2-PPV-4751586、FC2-PPV-3127193。设置 AV115_FC2_LIVE=1 才访问网络。"""

    @unittest.skipUnless(os.environ.get("AV115_FC2_LIVE") == "1", "set AV115_FC2_LIVE=1")
    def test_live_sample_codes(self):
        from av115_javbus import default_fetch, download_bytes

        def fetch(url, headers=None):
            return default_fetch(url, timeout=25, headers=headers, cookies={"ageVerified": "true"})

        def download(url):
            return download_bytes(url, timeout=25, referer="https://adult.contents.fc2.com/")

        client = Fc2Client("https://fc2cmadb.com", fetch, download)
        expected = {
            "FC2-PPV-4751586": "音田絵凛",
            "FC2-PPV-3127193": "石川祐奈",
        }
        for code, actress in expected.items():
            movie, reason = client.scrape(code, "fc2")
            self.assertIsNotNone(movie, reason)
            self.assertTrue(movie.title, code)
            self.assertTrue(movie.cover.startswith("https://storage"), movie.cover)
            self.assertIn(actress, movie.actresses)
            self.assertRegex(movie.release, r"\d{4}-\d{2}-\d{2}")
            data = client.download_cover(movie.cover)
            self.assertGreater(len(data), 1000, code)
            self.assertTrue(data.startswith(b"\xff\xd8"), code)


if __name__ == "__main__":
    unittest.main()
