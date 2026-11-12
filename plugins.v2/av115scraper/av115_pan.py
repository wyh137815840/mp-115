"""用 115 Cookie 添加离线、搬移正片，并给扫描器列目录。"""

from __future__ import annotations

import threading
import time

import httpx

try:  # 插件外单独跑（测试/脚本）时也能用
    from app.log import logger
except Exception:  # pragma: no cover - MoviePilot 外运行
    import logging

    logger = logging.getLogger("av115_pan")

from av115_scan import VIDEO_EXTS, CloudFile, _is_video, _suffix, join, norm

# 列目录走 aps。搜索、移动、重命名仍走 App。
# 建目录走网页 files/add（p115client.fs_mkdir）。App 的 /folder/update 在验证码之后会连续失败。
# 路径已在时用网页 getid；返回的 id 为 0 表示还没看见，不当成目录。扫描列目录不走 getid。
_APP = "https://proapi.115.com/android"
_APS_LIST = "https://aps.115.com/natsort/files.php"
# 大目录兜底：web /files 支持大 limit 与正常 offset 分页，count 是目录总条目数。
_WEB_LIST = "https://webapi.115.com/files"
_WEB_PAGE = 1000
_PAGE = 500
# aps 回绕兜底：正常目录远小于这个页数（500*200=10 万条）
_APS_MAX_PAGES = 200
# 同一进程内重复列同一目录（扫描多个 root 时最常见）的短缓存，写操作会清空。
_LISTING_TTL = 60.0
# App 接口连续请求过快也会回 405，全量扫描时宁可慢，避免 115 风控。
_GAP_SECONDS = 2.5
_RETRY_WAITS = (5, 20, 40)
_RETRY_STATUS = {405, 429, 500, 502, 503, 504}
_MKDIR_TRIES = 3
_GETID_RACE_WAITS = (0.4, 0.8)
# 网页 files/add 在目录已存在时返回这个 errno。
_EXISTS_ERRNO = {"20004"}
_pace_lock = threading.Lock()
_last_call = [0.0]
try:  # 跨插件共享的 115 操作池（与 Anime115 共用状态）
    from av115_guard import looks_like_risk as _guard_risk
    from av115_guard import trip as _guard_trip
    from av115_guard import wait as _guard_wait
except Exception:  # pragma: no cover
    _guard_risk = _guard_trip = _guard_wait = None


class Pan115:
    def __init__(self, cookie: str):
        self.cookie = cookie
        uid = ""
        for part in cookie.split(";"):
            key, _, value = part.strip().partition("=")
            if key == "UID":
                uid = value.split("_", 1)[0]
        self.user_id = uid
        self._dirs = {"/": "0"}
        self._children_cache = {}
        self._children_lock = threading.Lock()
        self.http = httpx.Client(
            timeout=20,
            follow_redirects=True,
            headers={
                "Cookie": cookie,
                "User-Agent": "Mozilla/5.0 (Linux; Android 14) 115wangpan_android/36.2.0",
            },
        )

    def ensure_dir(self, path: str) -> str:
        """逐级保证目录存在。创建走网页 fs_mkdir，已存在则用 getid 取回 id。"""
        folder = norm(path)
        parent = "0"
        built = ""
        for part in folder.strip("/").split("/") if folder != "/" else []:
            built += "/" + part
            parent = self._ensure_level(built, parent, part)
            if not parent:
                raise RuntimeError(f"无法创建 115 目录 {built}")
            self._dirs[built] = parent
        self._invalidate_listing_cache()
        return parent

    def _ensure_level(self, built: str, parent: str, name: str) -> str:
        cached = self._dirs.get(built) or ""
        if cached and cached != "0":
            return cached
        found = self._web_getid(built)
        if found:
            return found
        found = self._web_mkdir(parent, name, built)
        if found:
            return found
        return self._child_dir(built, parent, name, fresh=True)

    def _web(self):
        client = getattr(self, "_p115", None)
        if client is None:
            from p115client import P115Client

            client = P115Client(self.cookie, app="web")
            self._p115 = client
        return client

    def _web_getid(self, path: str) -> str:
        """网页 files/getid。id 为 0、失败或被风控时返回空，交给 mkdir 或列目录。"""
        try:
            payload = self._web().fs_dir_getid(path)
        except Exception:
            return ""
        if not isinstance(payload, dict):
            return ""
        if payload.get("state") in (False, 0) and not _exists_errno(payload):
            errno = payload.get("errno")
            if errno not in (None, 0, "0", ""):
                return ""
        return _real_id(payload)

    def _web_mkdir(self, parent: str, name: str, built: str) -> str:
        """网页 files/add。最多试三次；已存在或响应里没有 id 时，用 getid 把竞态里的 0 等掉。"""
        saw_exists = False
        for attempt in range(_MKDIR_TRIES):
            made = self._call_fs_mkdir(parent, name)
            found = _real_id(made)
            if found:
                return found
            saw_exists = saw_exists or _folder_exists(made)
            found = self._web_getid(built)
            if found:
                return found
            if saw_exists or attempt + 1 == _MKDIR_TRIES:
                return self._retry_getid(built)
            time.sleep(_GETID_RACE_WAITS[min(attempt, len(_GETID_RACE_WAITS) - 1)])
        return ""

    def _retry_getid(self, path: str) -> str:
        for wait in _GETID_RACE_WAITS:
            time.sleep(wait)
            found = self._web_getid(path)
            if found:
                return found
        return ""

    def _call_fs_mkdir(self, parent: str, name: str) -> dict:
        """pid + cname 是网页 files/add 的参数。旧客户端只收名字加 pid 时再退一次。"""
        client = self._web()
        calls = (
            lambda: client.fs_mkdir({"pid": parent, "cname": name}),
            lambda: client.fs_mkdir(name, pid=parent),
        )
        last = "fs_mkdir 不可用"
        for call in calls:
            try:
                made = call()
            except TypeError as exc:
                last = str(exc)
                continue
            except Exception as exc:
                return {"state": False, "error": str(exc)}
            return made if isinstance(made, dict) else {}
        return {"state": False, "error": last}

    def dir_id(self, path: str) -> str:
        folder = norm(path)
        parent = "0"
        built = ""
        for part in folder.strip("/").split("/") if folder != "/" else []:
            built += "/" + part
            parent = self._child_dir(built, parent, part)
            if not parent:
                return ""
        return parent

    def _child_dir(self, path: str, parent: str, name: str, fresh: bool = False) -> str:
        if not fresh and path in self._dirs:
            return self._dirs[path]
        try:
            children = self.list_children(parent)
        except RuntimeError:
            children = []
        for child in children:
            if _is_directory(child) and _name(child) == name:
                self._dirs[path] = _file_id(child)
                return self._dirs[path]
        for item in self.search(name):
            if _is_directory(item) and _name(item) == name and str(item.get("pid") or "") == parent:
                self._dirs[path] = _file_id(item)
                return self._dirs[path]
        return ""

    def add_magnet(self, url: str, directory_id: str) -> dict:
        """115 离线添加。url 原样提交，磁力链和 ed2k 都走这个接口。"""
        from p115client import P115Client

        client = P115Client(self.cookie, app="web")
        result = client.clouddownload_task_add_url({"url": url, "wp_path_id": directory_id})
        return result if isinstance(result, dict) else {}

    def relocate_code(self, code: str, library_path: str, cloud_id: str, min_bytes: int) -> tuple[int, int, int]:
        """正片放进 /片库/番号/。垃圾文件留在原处，误进片库的搬回云下载。"""
        if not code:
            return 0, 0, 0
        code_id = self.ensure_dir(f"{library_path.rstrip('/')}/{code}")
        moved = 0
        junk = 0
        ready = 0
        seen = set()
        for item in self.search(code):
            identity = _file_id(item)
            if not identity or identity in seen or identity == code_id:
                continue
            seen.add(identity)
            files = self.list_children(identity) if _is_directory(item) else [item]
            for child in files:
                if _is_directory(child):
                    continue
                kind = self._relocate_file(child, code_id, cloud_id, min_bytes)
                if kind == "video":
                    moved += 1
                elif kind == "junk":
                    junk += 1
                elif kind == "ready":
                    ready += 1
        return moved, junk, ready

    def search(self, keyword: str) -> list[dict]:
        found = self._get("/2.0/ufile/search", {"search_value": keyword, "limit": 100, "offset": 0, "aid": 1})
        return _items(found)

    def list_children(self, directory_id: str) -> list[dict]:
        """列目录。

        aps 的 offset 到边界时会从头返回一整页（响应 offset 回显请求值），而且
        每种排序只开放前约 1200 项；只用 len(page) < _PAGE 终止会无限循环（生产
        /AV 目录 503 项时发生过）。这里做三件事：
        1) 按 id 去重，本页没有新条目视为回绕，立即停止；
        2) 用 aps 返回的 count（目录总条目数）判断是否漏项；
        3) 漏项时用 web /files（limit=1000、offset 正常分页、count 为总数）补齐。
        """
        with self._children_lock:
            cached = self._children_cache.get(str(directory_id))
            if cached is not None and cached[0] > time.monotonic():
                return list(cached[1])
        items = []
        seen = set()
        offset = 0
        pages = 0
        total = None
        while pages < _APS_MAX_PAGES:
            pages += 1
            found = self._call("GET", "", url=_APS_LIST, params={
                "cid": directory_id,
                "limit": _PAGE,
                "offset": offset,
                "show_dir": 1,
                "aid": 1,
            })
            raw = _items(found)
            count = found.get("count")
            if total is None and isinstance(count, int) and count >= 0:
                total = count
            page = [_from_aps(row, directory_id) for row in raw]
            new = []
            for row in page:
                key = _file_id(row) or _name(row)
                if key:
                    if key in seen:
                        continue
                    seen.add(key)
                new.append(row)
            items.extend(new)
            if not raw:
                break
            if not new:
                # 服务端回绕到第一页，再往后也是重复数据。
                break
            if len(raw) < _PAGE:
                break
            offset += _PAGE
        if pages >= _APS_MAX_PAGES:
            logger.warning("115 aps 目录 %s 分页超过 %s 页，已截断", directory_id, _APS_MAX_PAGES)
        missing = total is not None and len(items) < total
        if missing:
            try:
                web = self._list_children_web(directory_id)
            except Exception as exc:
                logger.warning("115 目录 %s 漏项（%s/%s），web 兜底失败：%s",
                               directory_id, len(items), total, exc)
                web = []
            if len(web) > len(items):
                logger.info("115 目录 %s aps 只取到 %s 项，web 兜底取到 %s 项",
                            directory_id, len(items), len(web))
                items = web
        if total is not None and len(items) < total:
            logger.warning("115 目录 %s 只列出 %s/%s 项，可能被接口上限截断",
                           directory_id, len(items), total)
        with self._children_lock:
            self._children_cache[str(directory_id)] = (time.monotonic() + _LISTING_TTL, list(items))
        return items

    def _invalidate_listing_cache(self) -> None:
        with self._children_lock:
            self._children_cache.clear()

    def _list_children_web(self, directory_id: str) -> list[dict]:
        """web /files 兜底列目录。limit 用 1000，offset 正常分页，count 是总数。"""
        items = []
        seen = set()
        offset = 0
        while offset <= _APS_MAX_PAGES * _WEB_PAGE:
            _pace()
            response = self.http.get(_WEB_LIST, params={
                "cid": directory_id,
                "offset": offset,
                "limit": _WEB_PAGE,
                "show_dir": 1,
                "aid": 1,
            }, timeout=20)
            payload = _checked(response, "/files")
            raw = _items(payload)
            if not raw:
                break
            page = [_from_aps(row, directory_id) for row in raw]
            new = 0
            for row in page:
                key = _file_id(row) or _name(row)
                if key:
                    if key in seen:
                        continue
                    seen.add(key)
                items.append(row)
                new += 1
            count = payload.get("count")
            if isinstance(count, int) and count >= 0 and len(items) >= count:
                break
            if len(raw) < _WEB_PAGE or not new:
                break
            offset += _WEB_PAGE
        return items

    def move(self, file_id: str, directory_id: str) -> None:
        self._post("/files/move", {"ids": file_id, "to_cid": directory_id, "user_id": self.user_id})
        self._invalidate_listing_cache()

    def rename(self, file_id: str, new_name: str) -> None:
        self._post("/files/batch_rename", {f"files_new_name[{file_id}]": new_name})
        self._invalidate_listing_cache()

    def _relocate_file(self, item: dict, code_id: str, cloud_id: str, min_bytes: int) -> str:
        file_id = _file_id(item)
        parent = str(item.get("pid") or item.get("cid") or "")
        if not file_id:
            return ""
        if _keeps_video(item, min_bytes):
            if parent == code_id:
                return "ready"
            self.move(file_id, code_id)
            return "video"
        if parent == code_id:
            self.move(file_id, cloud_id)
            return "junk"
        return ""

    def _get(self, path: str, params: dict) -> dict:
        return self._call("GET", path, params=params)

    def _post(self, path: str, data: dict) -> dict:
        return self._call("POST", path, data=data)

    def _call(self, method: str, path: str, url: str = "", **kwargs) -> dict:
        target = url or _APP + path
        path = path or target
        last = ""
        for wait in (0, *_RETRY_WAITS):
            if wait:
                time.sleep(wait)
            _pace()
            try:
                response = self.http.request(method, target, **kwargs)
            except httpx.TransportError as exc:
                last = f"115 {path} 连接失败：{exc}"
                continue
            if response.status_code in _RETRY_STATUS:
                last = f"115 {path} 返回 {response.status_code}"
                continue
            text = ""
            try:
                text = response.text
            except Exception:
                text = ""
            if _guard_risk is not None and (_guard_risk(text) or response.status_code == 403):
                try:
                    _guard_trip(f"115 {path} 风控（HTTP {response.status_code}）")
                except Exception:
                    pass
                raise RuntimeError(f"115 {path} 触发风控（HTTP {response.status_code}），已熔断 115 操作池")
            return _checked(response, path)
        raise RuntimeError(last)


class CookieStorage:
    """扫描器要用的列目录和重命名，走 Cookie，不走开放平台。"""

    def __init__(self, pan: Pan115):
        self.pan = pan

    def stat(self, path: str) -> CloudFile | None:
        target = norm(path)
        if target == "/":
            return CloudFile(path="/", name="", is_dir=True, fileid="0")
        name = target.rsplit("/", 1)[-1]
        parent = target.rsplit("/", 1)[0] or "/"
        try:
            siblings = self.list_dir(parent)
        except FileNotFoundError:
            return None
        return next((child for child in siblings if child.name == name), None)

    def list_dir(self, path: str) -> list[CloudFile]:
        folder = norm(path)
        directory_id = self.pan.dir_id(folder)
        if not directory_id:
            raise FileNotFoundError(folder)
        items = []
        for child in self.pan.list_children(directory_id):
            name = _name(child)
            if not name:
                continue
            items.append(CloudFile(
                path=join(folder, name),
                name=name,
                is_dir=_is_directory(child),
                size=_size(child),
                pickcode=str(child.get("pc") or child.get("pickcode") or ""),
                fileid=_file_id(child),
            ))
        return items

    def rename(self, path: str, new_name: str) -> str:
        item = self.stat(path)
        if item is None or not item.fileid:
            raise FileNotFoundError(path)
        self.pan.rename(item.fileid, new_name)
        parent = norm(path).rsplit("/", 1)[0] or "/"
        return join(parent, new_name)


def _pace() -> None:
    if _guard_wait is not None:
        _guard_wait()
        return
    with _pace_lock:
        delay = _last_call[0] + _GAP_SECONDS - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        _last_call[0] = time.monotonic()


def _checked(response: httpx.Response, path: str) -> dict:
    if response.status_code != 200:
        raise RuntimeError(f"115 {path} 返回 {response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(f"115 {path} 返回的不是 JSON") from exc
    if not isinstance(payload, dict):
        return {}
    if payload.get("state") in (False, 0) and payload.get("errno") not in (None, 0, "0"):
        message = payload.get("error") or payload.get("error_msg") or payload.get("message") or payload.get("errno")
        raise RuntimeError(f"115 {path}：{message}")
    return payload


def _items(payload: dict) -> list[dict]:
    data = payload.get("data") if isinstance(payload, dict) else []
    if isinstance(data, dict):
        data = data.get("list") or data.get("data") or []
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def _from_aps(row: dict, directory_id: str) -> dict:
    """aps 的目录用 cid 当自己的 id，文件用 fid，文件的 cid 才是父目录。"""
    if row.get("fid"):
        return {
            "fid": str(row.get("fid")),
            "fc": "1",
            "fn": row.get("n") or "",
            "pid": str(row.get("cid") or directory_id),
            "fs": row.get("s") or 0,
            "pc": row.get("pc") or "",
        }
    return {
        "fid": str(row.get("cid") or ""),
        "fc": "0",
        "fn": row.get("n") or "",
        "pid": str(row.get("pid") or directory_id),
        "pc": row.get("pc") or "",
    }


def _id_of(payload) -> str:
    if not isinstance(payload, dict):
        return ""
    # 网页 files/add 把 id 放在 file_id 或 cid；旧的 App 响应放在 category_id。
    return str(
        payload.get("category_id")
        or payload.get("id")
        or payload.get("cid")
        or payload.get("file_id")
        or payload.get("fid")
        or ""
    )


def _real_id(payload) -> str:
    """目录 id。0 表示 getid 还没看见，或这不是一个目录 id。"""
    if not isinstance(payload, dict):
        return ""
    found = _id_of(payload)
    if not found or found == "0":
        data = payload.get("data")
        found = _id_of(data) if isinstance(data, dict) else ""
    if found and found != "0":
        return found
    return ""


def _folder_exists(payload: dict) -> bool:
    if not payload:
        return False
    text = str(payload.get("error") or payload.get("error_msg") or payload.get("message") or "")
    if "已存在" in text:
        return True
    return _exists_errno(payload)


def _exists_errno(payload: dict) -> bool:
    return str(payload.get("errno") or payload.get("errNo") or "") in _EXISTS_ERRNO


def _file_id(item: dict) -> str:
    return str(item.get("fid") or item.get("file_id") or item.get("cid") or "")


def _name(item: dict) -> str:
    return str(item.get("fn") or item.get("n") or item.get("file_name") or item.get("name") or "")


def _size(item: dict) -> int:
    try:
        return int(item.get("fs") or item.get("s") or item.get("size") or item.get("file_size") or 0)
    except (TypeError, ValueError):
        return 0


def _is_directory(item: dict) -> bool:
    if "fc" in item:
        return str(item.get("fc")) == "0"
    if "fid" not in item and "cid" in item:
        return True
    return not _suffix(_name(item))


def _keeps_video(item: dict, min_bytes: int) -> bool:
    name = _name(item)
    cloud = CloudFile(path=name, name=name, is_dir=False, size=_size(item))
    return _is_video(cloud, VIDEO_EXTS, min_bytes)
