"""用 115 Cookie 添加离线、搬移正片，并给扫描器列目录。"""

from __future__ import annotations

import threading
import time

import httpx

from av115_scan import VIDEO_EXTS, CloudFile, _is_video, _suffix, join, norm

# webapi.115.com 和 App 的 getid、列目录会被风控成 405；列目录改走 aps，搜索和写操作走 App。
_APP = "https://proapi.115.com/android"
_APS_LIST = "https://aps.115.com/natsort/files.php"
_PAGE = 500
# App 接口连续请求过快也会回 405，容器里的 DNS 偶尔解析失败，都要隔一会儿再试。
_GAP_SECONDS = 0.6
_RETRY_WAITS = (2, 5, 10)
_RETRY_STATUS = {405, 429, 500, 502, 503, 504}
_pace_lock = threading.Lock()
_last_call = [0.0]


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
        self.http = httpx.Client(
            timeout=20,
            follow_redirects=True,
            headers={
                "Cookie": cookie,
                "User-Agent": "Mozilla/5.0 (Linux; Android 14) 115wangpan_android/36.2.0",
            },
        )

    def ensure_dir(self, path: str) -> str:
        folder = norm(path)
        parent = "0"
        built = ""
        for part in folder.strip("/").split("/") if folder != "/" else []:
            built += "/" + part
            found = self._child_dir(built, parent, part)
            if found:
                parent = found
                continue
            made = self._post("/folder/update", {"name": part, "pid": parent, "user_id": self.user_id})
            parent = _id_of(made) or _id_of(made.get("data")) or self._child_dir(built, parent, part, fresh=True)
            if not parent:
                raise RuntimeError(f"无法创建 115 目录 {built}")
            self._dirs[built] = parent
        return parent

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

    def add_magnet(self, magnet: str, directory_id: str) -> dict:
        from p115client import P115Client

        client = P115Client(self.cookie, app="web")
        result = client.clouddownload_task_add_url({"url": magnet, "wp_path_id": directory_id})
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
        items = []
        offset = 0
        while True:
            found = self._call("GET", "", url=_APS_LIST, params={
                "cid": directory_id,
                "limit": _PAGE,
                "offset": offset,
                "show_dir": 1,
                "aid": 1,
            })
            page = [_from_aps(row, directory_id) for row in _items(found)]
            items.extend(page)
            if len(page) < _PAGE:
                return items
            offset += _PAGE

    def move(self, file_id: str, directory_id: str) -> None:
        self._post("/files/move", {"ids": file_id, "to_cid": directory_id, "user_id": self.user_id})

    def rename(self, file_id: str, new_name: str) -> None:
        self._post("/files/batch_rename", {f"files_new_name[{file_id}]": new_name})

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
    return str(payload.get("id") or payload.get("cid") or payload.get("file_id") or payload.get("fid") or "")


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
