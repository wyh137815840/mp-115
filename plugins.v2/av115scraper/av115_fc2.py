"""FC2 详情。优先 fc2cmadb，失败再试官网、Wayback 和备用站。

不访问 JavBus。解析函数本身不发请求。fc2cmadb 是 Inertia 页面，
作品在 `application/json` 里，演员要带上页面给出的版本号再问一次。
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from html import unescape
from urllib.parse import quote, urljoin

from av115_javbus import Movie

OFFICIAL_HOST = "https://adult.contents.fc2.com"
DEFAULT_HOST = "https://fc2cmadb.com"
_COMPONENT = "Articles/Show"
_ID = re.compile(r"(\d{5,8})")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_STORAGE = re.compile(r"(storage\d+\.contents\.fc2\.com/file/[^\s\"'<>]+)")


def article_id(code: str) -> str:
    """FC2-PPV-4751586 / FC2PPV4751586 → 4751586。"""
    match = _ID.search(code or "")
    return match.group(1) if match else ""


def clean_text(value: str) -> str:
    text = unescape(value or "").replace("\x00", "")
    return re.sub(r"\s+", " ", text).strip()


def clean_title(title: str, ident: str) -> str:
    text = clean_text(title)
    text = re.sub(r"\s*[|｜]\s*FC2.*$", "", text).strip()
    if ident:
        text = re.sub(
            rf"^(?:FC2[\s\-_]*PPV[\s\-_]*)?{re.escape(ident)}\s*",
            "",
            text,
            flags=re.I,
        ).strip(" -")
    return text


def unwrap_fc2_image(url: str) -> str:
    """缩略图外层和 Wayback 前缀都还原成 storage 原图。"""
    text = (url or "").strip()
    if text.startswith("//"):
        text = "https:" + text
    match = _STORAGE.search(text)
    if not match:
        return text
    return "https://" + match.group(1).rstrip(").,]")


def duration_minutes(value: str) -> str:
    """01:09:45 → 69，28:38 → 28。两位按分:秒，三位按时:分:秒。"""
    parts = [int(item) for item in re.findall(r"\d+", str(value or ""))[:3]]
    if not parts:
        return ""
    if len(parts) >= 3:
        total = parts[0] * 60 + parts[1]
    else:
        total = parts[0]
    return str(total) if total else ""


def _date(value: str) -> str:
    match = _DATE.search(str(value or ""))
    return match.group(0) if match else ""


def is_challenge(html: str) -> bool:
    """Cloudflare 验证页或 Wayback 的机器人拦截。正常资料页里的统计脚本不算。"""
    if not html:
        return False
    head = html[:1500]
    if re.search(r"<title[^>]*>\s*Just a moment", head, re.I):
        return True
    if "<title>Attention Required!" in head or "cf-browser-verification" in head:
        return True
    return "suspected abusive bot traffic" in html


def is_age_gate(html: str) -> bool:
    return "Age verification is required" in html or "ekyc_auth" in html or "error-ipblock__title" in html


def is_deleted(html: str) -> bool:
    return "お探しの商品が見つかりませんでした" in html or "この商品は削除" in html


def parse_og(html: str, prop: str) -> str:
    first = rf'property=["\']og:{re.escape(prop)}["\'][^>]+content=["\']([^"\']+)["\']'
    second = rf'content=["\']([^"\']+)["\'][^>]+property=["\']og:{re.escape(prop)}["\']'
    match = re.search(first, html, re.I) or re.search(second, html, re.I)
    return match.group(1).strip() if match else ""


def parse_inertia(text: str) -> dict:
    """整页 HTML 或 Inertia JSON 都收成 page 对象。"""
    raw = (text or "").lstrip()
    if raw.startswith("{"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict) and "component" in data:
            return data
    for match in re.finditer(
        r'<script[^>]*type="application/json"[^>]*>(.*?)</script>',
        text or "",
        re.S,
    ):
        body = match.group(1).strip()
        if not body.startswith("{"):
            continue
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and "component" in data:
            return data
    return {}


def parse_actresses(text: str) -> tuple[str, ...]:
    """只取 name。alias_name 是一串曾用名，放进演员栏会变成十几个人。"""
    try:
        data = json.loads(text or "")
    except json.JSONDecodeError:
        return ()
    props = data.get("props") if isinstance(data, dict) else None
    if not isinstance(props, dict):
        return ()
    names: list[str] = []
    seen: set[str] = set()
    for item in props.get("actresses") or []:
        if not isinstance(item, dict):
            continue
        name = clean_text(str(item.get("name") or ""))
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return tuple(names)


def actress_headers(version: str) -> dict[str, str]:
    return {
        "Accept": "text/html, application/xhtml+xml",
        "X-Requested-With": "XMLHttpRequest",
        "X-Inertia": "true",
        "X-Inertia-Version": version,
        "X-Inertia-Partial-Component": _COMPONENT,
        "X-Inertia-Partial-Data": "actresses",
    }


def _movie(
    code: str,
    title: str,
    cover: str,
    release: str = "",
    length: str = "",
    studio: str = "",
    genres: tuple[str, ...] = (),
    actresses: tuple[str, ...] = (),
) -> Movie:
    return Movie(
        code=code,
        title=title,
        cover=cover,
        release=release,
        length=length,
        director="",
        studio=studio,
        publisher="FC2",
        series="",
        genres=genres,
        actresses=actresses,
    )


def movie_from_cmadb(page: dict, code: str, ident: str) -> Movie | None:
    if str(page.get("component") or "") != _COMPONENT:
        return None
    props = page.get("props") if isinstance(page.get("props"), dict) else {}
    article = props.get("article") if isinstance(props, dict) else None
    if not isinstance(article, dict):
        return None
    if str(article.get("video_id") or "") != ident:
        return None
    title = clean_title(str(article.get("title") or ""), ident)
    cover = unwrap_fc2_image(str(article.get("image_url") or ""))
    if not title and not cover:
        return None
    writer = article.get("writer") if isinstance(article.get("writer"), dict) else {}
    studio = clean_text(str(writer.get("name") or ""))
    genres: list[str] = []
    seen: set[str] = set()
    for tag in article.get("tags") or []:
        name = clean_text(str(tag.get("name") or "")) if isinstance(tag, dict) else clean_text(str(tag or ""))
        if name and name not in seen:
            seen.add(name)
            genres.append(name)
    return _movie(
        code,
        title,
        cover,
        release=_date(str(article.get("release_date") or "")),
        length=duration_minutes(str(article.get("duration") or "")),
        studio=studio,
        genres=tuple(genres),
    )


def _absolute(base: str, url: str) -> str:
    text = (url or "").strip()
    if not text:
        return ""
    if text.startswith("//"):
        return "https:" + text
    if text.startswith(("http://", "https://")):
        return text
    return urljoin(base, text)


def movie_from_official(html: str, code: str, ident: str, page_url: str = "") -> Movie | None:
    if not html or is_challenge(html) or is_age_gate(html) or is_deleted(html):
        return None
    title = clean_title(parse_og(html, "title"), ident)
    description = clean_text(parse_og(html, "description"))
    if not title:
        title = clean_title(description, ident)
    cover = unwrap_fc2_image(_absolute(page_url, parse_og(html, "image")))
    if title in {"FC2", "FC2コンテンツマーケット", "FC2 Content Market"}:
        title = ""
    if not title and not cover:
        return None
    return _movie(code, title, cover)


def latest_timestamp(payload: str) -> str:
    try:
        rows = json.loads(payload or "")
    except json.JSONDecodeError:
        return ""
    if not isinstance(rows, list):
        return ""
    stamps = [
        str(row[0])
        for row in rows[1:]
        if isinstance(row, list) and row and str(row[0]).isdigit()
    ]
    return stamps[-1] if stamps else ""


class Fc2Client:
    """按番号抓详情。fetch(url, headers) 返回 (status, html)。"""

    def __init__(self, host: str, fetch, download=None):
        self.host = _normalize_host(host)
        self._fetch = fetch
        self._download = download

    def download_cover(self, url: str) -> bytes:
        if not self._download or not url:
            return b""
        return self._download(url) or b""

    def scrape(self, code: str, kind: str = "fc2") -> tuple[Movie | None, str]:
        if kind != "fc2":
            return None, "不是 FC2 番号"
        ident = article_id(code)
        if not ident:
            return None, "不是 FC2 番号"
        labeled = f"FC2-PPV-{ident}"
        errors: list[str] = []

        movie, reason = self._cmadb(labeled, ident)
        if movie:
            return movie, ""
        if reason:
            errors.append(reason)

        movie, reason = self._official(labeled, ident)
        if movie:
            return movie, ""
        if reason:
            errors.append(reason)

        movie, reason = self._wayback(labeled, ident)
        if movie:
            return movie, ""
        if reason:
            errors.append(reason)

        movie, reason = self._backups(labeled, ident)
        if movie:
            return movie, ""
        if reason:
            errors.append(reason)
        return None, "；".join(errors) or "未找到"

    def _get(self, url: str, headers: dict | None = None) -> tuple[int, str, str]:
        try:
            result = self._fetch(url, headers)
        except Exception as exc:
            return 0, "", str(exc)
        if not isinstance(result, tuple) or len(result) < 2:
            return 0, "", "刮削请求没有返回页面"
        status, html = result[0], result[1]
        return int(status or 0), html or "", ""

    def _cmadb(self, code: str, ident: str) -> tuple[Movie | None, str]:
        url = f"{self.host}/articles/{ident}"
        status, html, error = self._get(url)
        if status == 404:
            return None, "资料站没有这部片"
        if status != 200 or not html:
            if html and is_challenge(html):
                return None, "资料站被拦截"
            return None, error or f"资料站 HTTP {status or '失败'}"
        if is_challenge(html):
            return None, "资料站被拦截"
        page = parse_inertia(html)
        movie = movie_from_cmadb(page, code, ident)
        if movie is None:
            return None, "资料站没有影片信息"
        version = str(page.get("version") or "").strip()
        if not version:
            return movie, ""
        status, body, _error = self._get(url, actress_headers(version))
        if status == 200 and body and not is_challenge(body):
            names = parse_actresses(body)
            if names:
                movie = replace(movie, actresses=names)
        return movie, ""

    def _official(self, code: str, ident: str) -> tuple[Movie | None, str]:
        movie, reason = self._html_page(code, ident, f"{OFFICIAL_HOST}/article/{ident}/")
        return movie, f"官网{reason}" if reason else ""

    def _wayback(self, code: str, ident: str) -> tuple[Movie | None, str]:
        original = f"{OFFICIAL_HOST}/article/{ident}/"
        query = quote(original, safe=":/")
        cdx = (
            "https://web.archive.org/cdx/search/cdx?url="
            + query
            + "&output=json&filter=statuscode:200&fl=timestamp&limit=-1"
        )
        status, html, error = self._get(cdx)
        if status != 200 or not html or is_challenge(html):
            return None, error or "Wayback 没有快照"
        stamp = latest_timestamp(html)
        if not stamp:
            return None, "Wayback 没有快照"
        movie, reason = self._html_page(code, ident, f"https://web.archive.org/web/{stamp}id_/{original}")
        if movie:
            return movie, ""
        if reason == "被拦截":
            return None, "Wayback 快照被拒绝"
        return None, f"Wayback {reason}" if reason else "Wayback 没有影片信息"

    def _backups(self, code: str, ident: str) -> tuple[Movie | None, str]:
        urls = (
            f"https://fc2ppv-db.com/ja/videos/{ident}",
            f"https://missav.ws/ja/fc2-ppv-{ident}",
        )
        blocked = False
        last = ""
        for url in urls:
            movie, reason = self._html_page(code, ident, url)
            if movie:
                return movie, ""
            if reason == "被拦截":
                blocked = True
                continue
            last = reason
        if blocked and not last:
            return None, "备用站被拦截"
        return None, f"备用站{last}" if last else ""

    def _html_page(self, code: str, ident: str, url: str) -> tuple[Movie | None, str]:
        status, html, error = self._get(url)
        if html and is_challenge(html):
            return None, "被拦截"
        if status == 404:
            return None, "未找到"
        if status != 200 or not html:
            return None, error or f"HTTP {status or '失败'}"
        if is_age_gate(html):
            return None, "需要年龄验证"
        if is_deleted(html):
            return None, "已删除"
        movie = movie_from_official(html, code, ident, url)
        if movie is None:
            return None, "页面没有影片信息"
        return movie, ""


def _normalize_host(host: str) -> str:
    text = (host or "").strip().rstrip("/")
    if not text:
        text = DEFAULT_HOST
    if not text.startswith(("http://", "https://")):
        text = "https://" + text
    return text
