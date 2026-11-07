"""生成本地 strm、Emby 用的 nfo 和封面。"""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote

_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def safe_name(name: str) -> str:
    """去掉 Windows 不允许的字符。"""
    cleaned = _INVALID.sub(" ", name or "").strip().rstrip(". ")
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        return "unknown"
    if cleaned.upper() in _RESERVED:
        return f"_{cleaned}"
    return cleaned[:80]


def build_play_url(play_base: str, pickcode: str, filename: str) -> str:
    """生成 strm 内容。模板里可以写 {pickcode} 和 {filename}。"""
    template = (play_base or "").strip()
    code = quote((pickcode or "").strip(), safe=".-_")
    name = quote((filename or "video").strip() or "video", safe=".-_()[] ")
    if "{pickcode}" in template or "{filename}" in template:
        return template.replace("{pickcode}", code).replace("{filename}", name)
    base = template.rstrip("/")
    return f"{base}/redirect?pickcode={code}"


def find_existing(strm_root: Path, labeled: str) -> Path | None:
    """找已经刮过的目录，避免同一番号每跑一次换一个演员文件夹。"""
    if not strm_root.exists():
        return None
    direct = strm_root / safe_name(labeled)
    if (direct / "movie.nfo").is_file():
        return direct
    try:
        children = list(strm_root.iterdir())
    except OSError:
        return None
    for child in children:
        if not child.is_dir():
            continue
        nested = child / safe_name(labeled)
        if (nested / "movie.nfo").is_file():
            return nested
    return None


def destination(strm_root: Path, labeled: str, actress: str = "") -> Path:
    folder = safe_name(labeled)
    if actress:
        return strm_root / safe_name(actress) / folder
    return strm_root / folder


def build_nfo(
    *,
    code: str,
    title: str,
    release: str,
    length: str,
    director: str,
    studio: str,
    publisher: str,
    series: str,
    genres: list[str],
    actresses: list[str],
    tag: str,
) -> str:
    """生成 Emby 电影 nfo。"""
    movie = ET.Element("movie")
    display = f"{code}{tag} {title}".strip()

    def add(name: str, value: str) -> None:
        if not value:
            return
        element = ET.SubElement(movie, name)
        element.text = value

    add("title", display)
    add("originaltitle", code)
    add("sorttitle", code)
    add("num", code)
    unique = ET.SubElement(movie, "uniqueid")
    unique.set("type", "javbus")
    unique.set("default", "true")
    unique.text = code
    if release:
        add("premiered", release)
        add("year", release[:4])
    if length.isdigit():
        add("runtime", length)
    add("director", director)
    add("studio", studio)
    add("maker", studio)
    add("publisher", publisher)
    if series:
        add("set", series)
    extra = []
    if tag == "-C":
        extra.append("中文字幕")
    elif tag == "-UC":
        extra.append("无码破解")
    for genre in [*genres, *extra]:
        add("genre", genre)
        add("tag", genre)
    for name in actresses:
        actor = ET.SubElement(movie, "actor")
        ET.SubElement(actor, "name").text = name
        ET.SubElement(actor, "type").text = "Actor"
    plot_parts = [part for part in (title, studio, "、".join(actresses)) if part]
    add("plot", " / ".join(plot_parts))
    body = ET.tostring(movie, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' + body + "\n"


def crop_poster(data: bytes) -> bytes:
    """JavBus 封面是横图，海报取右侧竖图。没有 Pillow 时原样返回。"""
    try:
        from PIL import Image
    except Exception:
        return data
    try:
        image = Image.open(io.BytesIO(data)).convert("RGB")
    except Exception:
        return data
    width, height = image.size
    if width > height:
        poster_width = min(width, max(1, int(height * 2 / 3)))
        image = image.crop((width - poster_width, 0, width, height))
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=90)
    return output.getvalue()


def write_library(
    dest: Path,
    *,
    strm_url: str,
    strm_name: str,
    nfo_text: str | None,
    cover: bytes | None,
    force: bool,
) -> None:
    """写 strm。nfo 和封面只在缺失或 force 时覆盖。"""
    dest.mkdir(parents=True, exist_ok=True)
    strm_path = dest / f"{safe_name(strm_name)}.strm"
    strm_path.write_text(strm_url.strip() + "\n", encoding="utf-8")
    if nfo_text and (force or not (dest / "movie.nfo").is_file()):
        (dest / "movie.nfo").write_text(nfo_text, encoding="utf-8")
    if cover and (force or not (dest / "poster.jpg").is_file()):
        fanart = dest / "fanart.jpg"
        poster = dest / "poster.jpg"
        fanart.write_bytes(cover)
        poster_bytes = crop_poster(cover)
        poster.write_bytes(poster_bytes)
        (dest / "folder.jpg").write_bytes(poster_bytes)
