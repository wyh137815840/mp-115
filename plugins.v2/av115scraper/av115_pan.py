"""用 115 Cookie 添加离线、搬移正片，并给扫描器列目录。"""

from __future__ import annotations

from av115_scan import VIDEO_EXTS, CloudFile, _is_video, _suffix, join, norm


class Pan115:
    def __init__(self, cookie: str):
        from p115client import P115Client

        self.client = P115Client(cookie, app="web")

    def ensure_dir(self, path: str) -> str:
        folder = path if path.startswith("/") else f"/{path}"
        folder = folder.rstrip("/") or "/"
        found = self._dir_id(folder)
        if found:
            return found
        parent = "0"
        built = ""
        for part in folder.strip("/").split("/"):
            built += "/" + part
            found = self._dir_id(built)
            if found:
                parent = found
                continue
            made = self.client.fs_mkdir(part, pid=parent)
            parent = _id_of(made) or parent
        if parent == "0":
            raise RuntimeError(f"无法创建 115 目录 {path}")
        return parent

    def add_magnet(self, magnet: str, directory_id: str) -> dict:
        result = self.client.clouddownload_task_add_url({
            "url": magnet,
            "wp_path_id": directory_id,
        })
        return result if isinstance(result, dict) else {}

    def relocate_code(self, code: str, library_path: str, cloud_id: str, min_bytes: int) -> tuple[int, int]:
        """正片放进 /片库/番号/。垃圾文件留在云下载，混进片库的再搬回去。"""
        if not code:
            return 0, 0
        code_id = self.ensure_dir(f"{library_path.rstrip('/')}/{code}")
        moved = 0
        junk = 0
        seen = set()
        for item in self.search(code):
            identity = str(item.get("fid") or item.get("cid") or "")
            if not identity or identity in seen or identity == code_id:
                continue
            seen.add(identity)
            if _is_directory(item):
                video_count, junk_count = self._relocate_children(identity, code_id, cloud_id, min_bytes)
                moved += video_count
                junk += junk_count
                continue
            if self._relocate_file(item, code_id, cloud_id, min_bytes):
                if _keeps_video(item, min_bytes):
                    moved += 1
                else:
                    junk += 1
        return moved, junk

    def search(self, keyword: str) -> list[dict]:
        found = self.client.fs_search({"search_value": keyword, "limit": 40})
        data = found.get("data") if isinstance(found, dict) else []
        if isinstance(data, dict):
            data = data.get("list") or data.get("data") or []
        return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []

    def list_children(self, directory_id: str) -> list[dict]:
        found = self.client.fs_files({
            "cid": directory_id,
            "limit": 200,
            "show_dir": 1,
            "cur": 1,
        })
        data = found.get("data") if isinstance(found, dict) else []
        return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []

    def move(self, file_id: str, directory_id: str) -> None:
        result = self.client.fs_move(file_id, pid=directory_id)
        if isinstance(result, dict) and result.get("state") in (False, 0):
            message = result.get("error") or result.get("error_msg") or result.get("message") or "115 拒绝移动"
            raise RuntimeError(str(message))

    def rename(self, file_id: str, new_name: str) -> None:
        result = self.client.fs_rename((file_id, new_name))
        if isinstance(result, dict) and result.get("state") in (False, 0):
            message = result.get("error") or result.get("error_msg") or result.get("message") or "115 拒绝重命名"
            raise RuntimeError(str(message))

    def _dir_id(self, path: str) -> str:
        try:
            found = self.client.fs_dir_getid(path)
        except Exception:
            return ""
        return _id_of(found)

    def _relocate_children(self, directory_id: str, library_id: str, cloud_id: str, min_bytes: int) -> tuple[int, int]:
        moved = 0
        junk = 0
        for child in self.list_children(directory_id):
            if _is_directory(child):
                continue
            if self._relocate_file(child, library_id, cloud_id, min_bytes):
                if _keeps_video(child, min_bytes):
                    moved += 1
                else:
                    junk += 1
        return moved, junk

    def _relocate_file(self, item: dict, library_id: str, cloud_id: str, min_bytes: int) -> bool:
        file_id = str(item.get("fid") or "")
        parent = str(item.get("pid") or item.get("cid") or "")
        if not file_id:
            return False
        keep = _keeps_video(item, min_bytes)
        target = library_id if keep else cloud_id
        if parent == target:
            return False
        self.move(file_id, target)
        return True


class CookieStorage:
    """扫描器要用的列目录和重命名，走 Cookie，不走开放平台。"""

    def __init__(self, pan: Pan115):
        self.pan = pan

    def stat(self, path: str) -> CloudFile | None:
        folder = norm(path)
        name = folder.rsplit("/", 1)[-1]
        parent = folder.rsplit("/", 1)[0] or "/"
        if folder == "/":
            return CloudFile(path="/", name="", is_dir=True, fileid="0")
        for child in self.list_dir(parent):
            if child.name == name:
                return child
        directory_id = self.pan._dir_id(folder)
        if not directory_id:
            return None
        return CloudFile(path=folder, name=name, is_dir=True, fileid=directory_id)

    def list_dir(self, path: str) -> list[CloudFile]:
        folder = norm(path)
        directory_id = "0" if folder == "/" else self.pan.ensure_dir(folder)
        items = []
        for child in self.pan.list_children(directory_id):
            name = str(child.get("n") or child.get("fn") or child.get("name") or "")
            if not name:
                continue
            file_id = str(child.get("fid") or child.get("cid") or "")
            items.append(CloudFile(
                path=join(folder, name),
                name=name,
                is_dir=_is_directory(child),
                size=int(child.get("s") or child.get("size") or 0),
                pickcode=str(child.get("pc") or child.get("pickcode") or ""),
                fileid=file_id,
            ))
        return items

    def rename(self, path: str, new_name: str) -> str:
        item = self.stat(path)
        if item is None or not item.fileid:
            raise FileNotFoundError(path)
        self.pan.rename(item.fileid, new_name)
        parent = path.rsplit("/", 1)[0] or "/"
        return join(parent, new_name)


def _id_of(payload) -> str:
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("id") or payload.get("cid") or payload.get("file_id") or payload.get("fid") or "")


def _is_directory(item: dict) -> bool:
    name = str(item.get("n") or item.get("fn") or item.get("name") or "")
    if "fid" not in item and "cid" in item:
        return True
    return not _suffix(name)


def _keeps_video(item: dict, min_bytes: int) -> bool:
    name = str(item.get("n") or item.get("fn") or item.get("name") or "")
    size = int(item.get("s") or item.get("size") or 0)
    cloud = CloudFile(path=name, name=name, is_dir=False, size=size)
    return _is_video(cloud, VIDEO_EXTS, min_bytes)
