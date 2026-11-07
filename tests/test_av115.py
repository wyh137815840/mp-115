"""115 AV 刮削插件的纯逻辑测试，不访问网络，也不依赖 MoviePilot。"""

from __future__ import annotations

import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins.v2" / "av115scraper"))

from av115_codes import choose_code, parse_av
from av115_javbus import parse_detail
from av115_library import build_nfo, build_play_url, write_library
from av115_scan import CloudFile, ScanOptions, join, name_of, norm, parent_path, run_scan


class CodeTests(unittest.TestCase):
    def test_censored_and_tags(self):
        self.assertEqual(parse_av("hhd800.com@SSIS-001.mp4").labeled, "SSIS-001")
        self.assertEqual(parse_av("[4K]ssis001-C.mkv").labeled, "SSIS-001-C")
        self.assertEqual(parse_av("ABP-123_UC.mp4").labeled, "ABP-123-UC")
        self.assertEqual(parse_av("ABP123C.mp4").labeled, "ABP-123-C")
        self.assertEqual(parse_av("MIDV-23.mp4").code, "MIDV-023")

    def test_fc2_heyzo_and_uncensored(self):
        self.assertEqual(parse_av("FC2-PPV-1234567.mp4").code, "FC2-PPV-1234567")
        self.assertEqual(parse_av("fc2ppv_123456.avi").kind, "fc2")
        self.assertEqual(parse_av("HEYZO-1234.mp4").kind, "uncensored")
        self.assertEqual(parse_av("Caribbean-010115-001-C.mp4").labeled, "010115-001-C")
        self.assertEqual(parse_av("1pondo_010115_001.mp4").code, "010115_001")

    def test_rejects_noise(self):
        self.assertIsNone(parse_av("movie.2024.1080p.mkv"))
        self.assertIsNone(parse_av("H264.1080p.mp4"))
        self.assertIsNone(parse_av("SSIS-001234.mp4"))
        self.assertIsNone(parse_av("无字幕介绍.txt"))

    def test_folder_tag_merges_when_code_matches(self):
        chosen = choose_code("SSIS-001.mp4", "SSIS-001-C")
        self.assertEqual(chosen.labeled, "SSIS-001-C")
        chosen = choose_code("video.mp4", "SSIS-001-C")
        self.assertEqual(chosen.labeled, "SSIS-001-C")
        self.assertEqual(parse_av("SSIS-001-无字幕.mp4").tag, "")


class JavBusTests(unittest.TestCase):
    def test_parse_detail(self):
        html = """
        <h3>SSIS-001 标题&amp;副题</h3>
        <a class="bigImage" href="https://img.example/cover.jpg"><img src="t.jpg"></a>
        <p><span class="header">識別碼:</span> <span style="color:#CC0000;">SSIS-001</span></p>
        <p><span class="header">發行日期:</span> 2024-05-06</p>
        <p><span class="header">長度:</span> 120分鐘</p>
        <p><span class="header">導演:</span> <a href="https://www.javbus.com/director/1">某导演</a></p>
        <p><span class="header">製作商:</span> <a href="https://www.javbus.com/studio/1">S1</a></p>
        <p><span class="header">發行商:</span> <a href="https://www.javbus.com/label/1">S1 Label</a></p>
        <p><span class="header">系列:</span> <a href="https://www.javbus.com/series/1">系列名</a></p>
        <span class="genre"><a href="https://www.javbus.com/genre/1">剧情</a></span>
        <div class="star-name"><a href="https://www.javbus.com/star/1">演员A</a></div>
        """
        movie = parse_detail(html)
        self.assertEqual(movie.code, "SSIS-001")
        self.assertEqual(movie.title, "标题&副题")
        self.assertEqual(movie.release, "2024-05-06")
        self.assertEqual(movie.length, "120")
        self.assertEqual(movie.studio, "S1")
        self.assertEqual(movie.actresses, ("演员A",))
        self.assertEqual(movie.genres, ("剧情",))


class LibraryTests(unittest.TestCase):
    def test_nfo_and_strm(self):
        xml = build_nfo(
            code="SSIS-001",
            title="标题",
            release="2024-05-06",
            length="120",
            director="导演",
            studio="S1",
            publisher="",
            series="",
            genres=["剧情"],
            actresses=["甲&乙"],
            tag="-C",
        )
        root = ET.fromstring(xml.split("\n", 1)[1])
        self.assertEqual(root.find("actor/name").text, "甲&乙")
        self.assertIn("中文字幕", [item.text for item in root.findall("tag")])
        self.assertEqual(
            build_play_url(
                "http://127.0.0.1:8787/redirect?pickcode={pickcode}",
                "ako4cbdjnckmlhfcl",
                "SSIS-001-C.mp4",
            ),
            "http://127.0.0.1:8787/redirect?pickcode=ako4cbdjnckmlhfcl",
        )

    def test_write_does_not_clobber_nfo(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "SSIS-001"
            write_library(
                dest,
                strm_url="http://play/a",
                strm_name="SSIS-001-C",
                nfo_text="<movie><title>旧</title></movie>\n",
                cover=None,
                force=False,
            )
            write_library(
                dest,
                strm_url="http://play/b",
                strm_name="SSIS-001-C",
                nfo_text="<movie><title>新</title></movie>\n",
                cover=None,
                force=False,
            )
            self.assertIn("旧", (dest / "movie.nfo").read_text(encoding="utf-8"))
            self.assertEqual((dest / "SSIS-001-C.strm").read_text(encoding="utf-8").strip(), "http://play/b")


class MemStorage:
    def __init__(self):
        self.nodes = {}
        self.dirs = {"/": set()}
        self.renames = []
        self._add_dir("/")

    def _add_dir(self, path: str) -> None:
        path = norm(path)
        if path in self.nodes and path != "/":
            return
        if path != "/":
            self._add_dir(parent_path(path))
            self.dirs.setdefault(parent_path(path), set()).add(name_of(path))
        self.nodes[path] = CloudFile(path, name_of(path), True)
        self.dirs.setdefault(path, set())

    def add_file(self, path: str, size: int, pickcode: str) -> None:
        path = norm(path)
        parent = parent_path(path)
        self._add_dir(parent)
        name = name_of(path)
        self.nodes[path] = CloudFile(path, name, False, size, pickcode, "fid")
        self.dirs[parent].add(name)

    def stat(self, path: str):
        return self.nodes.get(norm(path))

    def list_dir(self, path: str):
        path = norm(path)
        if path not in self.dirs:
            raise FileNotFoundError(path)
        return [self.nodes[join(path, name)] for name in self.dirs[path]]

    def rename(self, path: str, new_name: str) -> str:
        path = norm(path)
        self.renames.append((path, new_name))
        node = self.nodes[path]
        parent = parent_path(path)
        new_path = join(parent, new_name)
        self.dirs[parent].remove(node.name)
        self.dirs[parent].add(new_name)
        if node.is_dir:
            self._move_tree(path, new_path)
        else:
            self.nodes.pop(path)
            self.nodes[new_path] = CloudFile(new_path, new_name, False, node.size, node.pickcode, node.fileid)
        return new_path

    def _move_tree(self, old: str, new: str) -> None:
        paths = [item for item in list(self.nodes) if item == old or item.startswith(old + "/")]
        for item in paths:
            node = self.nodes.pop(item)
            dest = new + item[len(old):]
            self.nodes[dest] = CloudFile(
                dest, name_of(dest), node.is_dir, node.size, node.pickcode, node.fileid,
            )
        moved = {}
        for key, value in list(self.dirs.items()):
            if key == old or key.startswith(old + "/"):
                moved[new + key[len(old):]] = self.dirs.pop(key)
        self.dirs.update(moved)


class _Bus:
    def __init__(self):
        self.calls = []

    def scrape(self, code, kind):
        self.calls.append((code, kind))
        from av115_javbus import Movie

        return Movie(
            code, "标题", "http://img/cover.jpg", "2024-05-06", "120",
            "导演", "S1", "", "", ("剧情",), ("演员A",),
        ), ""

    def download_cover(self, url):
        return b"cover-bytes"


class ScanTests(unittest.TestCase):
    def test_rename_scrape_and_skip_rules(self):
        storage = MemStorage()
        storage.add_file("/AV/messy/hhd800.com@SSIS-001-C.mkv", 800 * 1024 * 1024, "pick1")
        storage.add_file("/AV/pack/SSIS-002.mp4", 800 * 1024 * 1024, "pick2")
        storage.add_file("/AV/pack/SSIS-003.mp4", 800 * 1024 * 1024, "pick3")
        storage.add_file("/AV/noise/sample-SSIS-008.mp4", 800 * 1024 * 1024, "pick4")
        storage.add_file("/AV/tiny/SSIS-009.mp4", 10 * 1024 * 1024, "pick5")

        with tempfile.TemporaryDirectory() as tmp:
            bus = _Bus()
            result = run_scan(storage, ScanOptions(
                roots=["/AV"],
                strm_root=tmp,
                play_base="http://127.0.0.1:5000",
                interval_seconds=0,
                dry_run=False,
            ), javbus=bus)
            self.assertTrue(result.ok, result.error)
            self.assertTrue(storage.stat("/AV/SSIS-001-C/SSIS-001-C.mkv"))
            self.assertTrue(storage.stat("/AV/pack/SSIS-002.mp4"))
            self.assertEqual(name_of("/AV/pack"), "pack")
            self.assertFalse(any(item["code"] == "SSIS-009" for item in result.items))
            nfo = Path(tmp) / "演员A" / "SSIS-001-C" / "movie.nfo"
            self.assertTrue(nfo.is_file())
            strm = (Path(tmp) / "演员A" / "SSIS-001-C" / "SSIS-001-C.strm").read_text(encoding="utf-8")
            self.assertEqual(strm.strip(), "http://127.0.0.1:5000/redirect?pickcode=pick1")

    def test_dry_run_does_not_rename(self):
        storage = MemStorage()
        storage.add_file("/AV/box/SSIS-001.mp4", 800 * 1024 * 1024, "pick")
        result = run_scan(storage, ScanOptions(
            roots=["/AV"],
            strm_root="",
            play_base="",
            interval_seconds=0,
            dry_run=True,
        ))
        self.assertEqual(storage.renames, [])
        self.assertEqual(result.renamed, 1)
        self.assertIn("将把文件夹改名为", result.items[0]["message"])

    def test_name_conflict_keeps_original(self):
        storage = MemStorage()
        storage.add_file("/AV/box/SSIS-001.mp4", 800 * 1024 * 1024, "pick-old")
        storage.add_file("/AV/box/messy-SSIS-001.mp4", 800 * 1024 * 1024, "pick-new")
        with tempfile.TemporaryDirectory() as tmp:
            result = run_scan(storage, ScanOptions(
                roots=["/AV"],
                strm_root=tmp,
                play_base="http://127.0.0.1:5000",
                interval_seconds=0,
            ), javbus=_Bus())
            self.assertTrue(storage.stat("/AV/box/messy-SSIS-001.mp4"))
            self.assertTrue(any("目标文件名已存在" in item["message"] for item in result.items))


if __name__ == "__main__":
    unittest.main()
