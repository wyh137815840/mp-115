"""JavBus 详情页抓取和解析。网络入口可以替换，解析本身不访问网络。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape
from urllib.parse import urljoin

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


@dataclass(frozen=True)
class Movie:
    code: str
    title: str
    cover: str
    release: str
    length: str
    director: str
    studio: str
    publisher: str
    series: str
    genres: tuple[str, ...]
    actresses: tuple[str, ...]


def _clean(value: str) -> str:
    text = unescape(re.sub(r"<[^>]+>", " ", value))
    return re.sub(r"\s+", " ", text).strip()


def _field(html: str, header: str) -> str:
    match = re.search(
        rf'<span class="header">{re.escape(header)}:?</span>\s*(.*?)</p>',
        html,
        re.S,
    )
    if not match:
        return ""
    chunk = match.group(1)
    anchors = re.findall(r"<a [^>]*>(.*?)</a>", chunk, re.S)
    if anchors:
        return _clean(anchors[0])
    return _clean(chunk)


def parse_detail(html: str, page_url: str = "") -> Movie | None:
    """解析 JavBus 影片页。页面里没有识别码时视为失败。"""
    if not html or "識別碼" not in html:
        return None
    code_match = re.search(r"識別碼:</span>\s*<span[^>]*>(.*?)</span>", html, re.S)
    if not code_match:
        return None
    code = _clean(code_match.group(1))
    if not code:
        return None
    title_match = re.search(r"<h3>(.*?)</h3>", html, re.S)
    title = _clean(title_match.group(1)) if title_match else ""
    if title.upper().startswith(code.upper()):
        title = title[len(code):].strip(" -")
    cover_match = re.search(r'<a class="bigImage" href="([^"]+)"', html)
    cover = cover_match.group(1).strip() if cover_match else ""
    if cover.startswith("//"):
        cover = "https:" + cover
    elif cover and page_url:
        cover = urljoin(page_url, cover)
    release_match = re.search(r"(\d{4}-\d{2}-\d{2})", _field(html, "發行日期"))
    length_match = re.search(r"(\d+)", _field(html, "長度"))
    genres = tuple(
        _clean(item)
        for item in re.findall(
            r'<span class="genre">.*?<a [^>]*href="[^"]*/genre/[^"]*"[^>]*>(.*?)</a>',
            html,
            re.S,
        )
        if _clean(item)
    )
    actresses = tuple(
        _clean(item)
        for item in re.findall(r'<div class="star-name">\s*<a [^>]*>(.*?)</a>', html, re.S)
        if _clean(item)
    )
    return Movie(
        code=code,
        title=title,
        cover=cover,
        release=release_match.group(1) if release_match else "",
        length=length_match.group(1) if length_match else "",
        director=_field(html, "導演"),
        studio=_field(html, "製作商"),
        publisher=_field(html, "發行商"),
        series=_field(html, "系列"),
        genres=genres,
        actresses=actresses,
    )


def detail_urls(host: str, code: str, kind: str) -> list[str]:
    """有码先走普通页，FC2 和无码先走无码页。"""
    root = host.rstrip("/")
    normal = f"{root}/{code}"
    uncensored = f"{root}/uncensored/{code}"
    if kind in {"fc2", "uncensored"}:
        return [uncensored, normal]
    return [normal, uncensored]


class JavBusClient:
    """按番号抓详情。fetch(url) 返回 (status, html)。"""

    def __init__(self, host: str, fetch, download=None):
        self.host = (host or "https://www.javbus.com").rstrip("/")
        self._fetch = fetch
        self._download = download

    def download_cover(self, url: str) -> bytes:
        if not self._download:
            return b""
        return self._download(url) or b""

    def scrape(self, code: str, kind: str) -> tuple[Movie | None, str]:
        last = "未找到"
        for url in detail_urls(self.host, code, kind):
            try:
                status, html = self._fetch(url)
            except Exception as exc:
                last = str(exc)
                continue
            if status == 404:
                last = "未找到"
                continue
            if status != 200:
                last = f"HTTP {status}"
                continue
            movie = parse_detail(html, url)
            if movie:
                return movie, ""
            last = "页面没有影片信息"
        return None, last


def default_fetch(url: str, proxy: str = "", timeout: int = 20) -> tuple[int, str]:
    """优先用 MoviePilot 的 RequestUtils，宿主没有时退回 urllib。"""
    headers = {"User-Agent": _UA, "Accept-Language": "zh-CN,zh;q=0.9"}
    cookies = {"existmag": "all", "age": "verified"}
    try:
        from app.utils.http import RequestUtils

        response = RequestUtils(
            headers=headers,
            cookies=cookies,
            proxies=proxy or None,
            timeout=timeout,
        ).get_res(url)
        if response is None:
            return 0, ""
        return int(response.status_code), response.text or ""
    except Exception:
        return _urllib_fetch(url, proxy, timeout, headers)


def download_bytes(url: str, proxy: str = "", timeout: int = 20, referer: str = "") -> bytes:
    """下载封面。失败时返回空字节。"""
    if not url:
        return b""
    headers = {"User-Agent": _UA}
    if referer:
        headers["Referer"] = referer
    try:
        from app.utils.http import RequestUtils

        response = RequestUtils(
            headers=headers,
            proxies=proxy or None,
            timeout=timeout,
        ).get_res(url)
        if response is None or int(response.status_code) != 200:
            return b""
        return response.content or b""
    except Exception:
        return _urllib_bytes(url, proxy, timeout, headers)


def _urllib_fetch(url: str, proxy: str, timeout: int, headers: dict) -> tuple[int, str]:
    import urllib.request

    request = urllib.request.Request(url, headers=headers)
    opener = _opener(proxy)
    with opener.open(request, timeout=timeout) as response:
        body = response.read()
        charset = response.headers.get_content_charset() or "utf-8"
        return int(response.status), body.decode(charset, errors="replace")


def _urllib_bytes(url: str, proxy: str, timeout: int, headers: dict) -> bytes:
    import urllib.request

    request = urllib.request.Request(url, headers=headers)
    try:
        with _opener(proxy).open(request, timeout=timeout) as response:
            if int(response.status) != 200:
                return b""
            return response.read()
    except Exception:
        return b""


def _opener(proxy: str):
    import urllib.request

    if not proxy:
        return urllib.request.build_opener()
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy, "https": proxy})
    )
