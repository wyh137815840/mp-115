"""扫描云盘目录、重命名，并调用刮削和本地落盘。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from av115_codes import AvCode, choose_code
from av115_javbus import Movie
from av115_library import build_nfo, build_play_url, destination, find_existing, safe_name, write_library

VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".wmv", ".ts", ".m2ts", ".iso", ".rmvb", ".flv", ".mov", ".m4v"}
_PREVIEW = ("预告", "sample", "trailer", "preview")
_MAX_DEPTH = 10


@dataclass(frozen=True)
class CloudFile:
    path: str
    name: str
    is_dir: bool
    size: int = 0
    pickcode: str = ""
    fileid: str = ""


@dataclass
class ScanOptions:
    roots: list[str]
    strm_root: str
    play_base: str
    min_size_mb: int = 300
    interval_seconds: float = 3
    overwrite: bool = False
    dry_run: bool = False
    rename_folder: bool = True
    extensions: tuple[str, ...] = tuple(sorted(VIDEO_EXTS))


@dataclass
class ScanResult:
    ok: bool
    error: str = ""
    items: list[dict] = field(default_factory=list)
    renamed: int = 0
    scraped: int = 0
    failed: int = 0
    skipped: int = 0
    dry_run: bool = False

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "error": self.error,
            "items": self.items,
            "renamed": self.renamed,
            "scraped": self.scraped,
            "failed": self.failed,
            "skipped": self.skipped,
            "dry_run": self.dry_run,
        }


def norm(path: str) -> str:
    text = (path or "").replace("\\", "/").strip()
    if not text.startswith("/"):
        text = "/" + text
    if len(text) > 1:
        text = text.rstrip("/")
    return text


def name_of(path: str) -> str:
    path = norm(path)
    if path == "/":
        return ""
    return path.rsplit("/", 1)[-1]


def parent_path(path: str) -> str:
    path = norm(path)
    if path == "/":
        return "/"
    parent = path.rsplit("/", 1)[0]
    return parent or "/"


def join(parent: str, name: str) -> str:
    parent = norm(parent)
    if parent == "/":
        return "/" + name
    return parent + "/" + name


def _suffix(name: str) -> str:
    base = name.rsplit("/", 1)[-1]
    if "." not in base:
        return ""
    return "." + base.rsplit(".", 1)[-1]


def _is_video(item: CloudFile, extensions: set[str], min_bytes: int) -> bool:
    if item.is_dir:
        return False
    suffix = _suffix(item.name).lower()
    if suffix not in extensions:
        return False
    if item.size and item.size < min_bytes:
        return False
    lower = item.name.lower()
    if any(word in lower for word in _PREVIEW):
        return False
    return True


def _collect(storage, root: str, options: ScanOptions, cancel, progress=None) -> tuple[list[CloudFile], str]:
    root = norm(root)
    try:
        node = storage.stat(root)
    except Exception as exc:
        return [], f"读取失败：{exc}"
    if node is None:
        return [], "目录不存在"
    extensions = {ext if ext.startswith(".") else f".{ext}" for ext in options.extensions}
    min_bytes = max(0, options.min_size_mb) * 1024 * 1024
    if not node.is_dir:
        return ([node] if _is_video(node, extensions, min_bytes) else []), ""
    found: list[CloudFile] = []
    stack = [(node.path, 0)]
    listed = 0
    while stack:
        if cancel():
            return found, "已取消"
        current, depth = stack.pop()
        if depth > _MAX_DEPTH:
            continue
        try:
            children = storage.list_dir(current) or []
        except Exception as exc:
            return found, f"列出 {current} 失败：{exc}"
        listed += 1
        child_dirs = 0
        for child in children:
            if child.is_dir:
                child_dirs += 1
                stack.append((norm(child.path), depth + 1))
            elif _is_video(child, extensions, min_bytes):
                found.append(child)
        if progress:
            waiting = len(stack)
            seconds = max(waiting, 1) * 3 if waiting else 0
            progress(
                f"列目录 {listed} 个，还要列 {waiting} 个，已发现正片 {len(found)} 个，"
                f"当前 {current}（本层 {child_dirs} 个子目录）。"
                f"按每个目录约 3 秒，列完大约还要 {seconds // 60} 分 {seconds % 60} 秒"
            )
    return found, ""


def _sibling_names(storage, parent: str, skip: str) -> set[str]:
    names = set()
    for child in storage.list_dir(parent) or []:
        if norm(child.path) == norm(skip):
            continue
        names.add(child.name)
    return names


def run_scan(storage, options: ScanOptions, javbus=None, cancel=None, sleep=time.sleep, progress=None) -> ScanResult:
    """执行一次扫描。dry_run 只报告将要发生的事，不改云盘也不写本地文件。"""
    stopped = cancel or (lambda: False)
    result = ScanResult(ok=True, dry_run=options.dry_run)
    roots = [norm(item) for item in options.roots if str(item).strip()]
    if not roots:
        result.ok = False
        result.error = "没有填写 115 目录"
        return result
    if not options.dry_run:
        if not options.strm_root.strip():
            result.ok = False
            result.error = "没有填写本地 strm 目录"
            return result
        if not options.play_base.strip():
            result.ok = False
            result.error = "没有填写播放服务地址"
            return result
    protected = set(roots)
    videos: list[CloudFile] = []
    for root in roots:
        if stopped():
            result.error = "已取消"
            break
        found, error = _collect(storage, root, options, stopped, progress)
        if error and error != "已取消":
            result.failed += 1
            result.items.append({"status": "failed", "path": root, "code": "", "message": error, "output": ""})
        videos.extend(found)
    counts: dict[str, int] = {}
    for video in videos:
        parent = parent_path(video.path)
        counts[parent] = counts.get(parent, 0) + 1

    strm_root = Path(options.strm_root) if options.strm_root.strip() else None
    total = len(videos)
    if progress:
        progress(f"目录列完，正片 {total} 个，开始刮削")
    armed = False
    for index, video in enumerate(videos, 1):
        if stopped():
            result.error = "已取消"
            break
        if armed and options.interval_seconds > 0:
            sleep(options.interval_seconds)
        item = _handle_video(
            storage=storage,
            video=video,
            options=options,
            counts=counts,
            protected=protected,
            strm_root=strm_root,
            javbus=javbus,
            result=result,
        )
        result.items.append(item)
        armed = bool(item.get("scraped"))
        if progress:
            progress(
                f"刮削 {index}/{total}：{item.get('code') or video.path}，{item.get('status') or ''}。"
                f"已重命名 {result.renamed}，已刮削 {result.scraped}，跳过 {result.skipped}，失败 {result.failed}"
            )
    if result.failed and not result.error:
        result.ok = True
    return result


def _handle_video(storage, video, options, counts, protected, strm_root, javbus, result: ScanResult) -> dict:
    parent = parent_path(video.path)
    parsed = choose_code(video.name, name_of(parent))
    if parsed is None:
        result.skipped += 1
        return {
            "status": "skipped",
            "path": video.path,
            "code": "",
            "message": "没有识别出番号",
            "output": "",
        }
    suffix = _suffix(video.name) or ".mp4"
    new_file_name = f"{parsed.labeled}{suffix}"
    actual_path = norm(video.path)
    actual_name = video.name
    messages = []
    renamed = _rename_file(
        storage, options, parent, actual_path, actual_name, new_file_name, messages, result
    )
    if renamed:
        actual_path, actual_name = renamed
    folder_renamed = _rename_folder(
        storage, options, parent, actual_name, parsed, counts, protected, messages, result
    )
    if folder_renamed:
        actual_path = folder_renamed

    output = ""
    status = "dry_run" if options.dry_run else "ok"
    if options.dry_run:
        if strm_root is not None:
            output = str(destination(strm_root, parsed.labeled))
        messages.append("预览：未请求 JavBus，也未写文件")
        return _row(status, actual_path, parsed.labeled, "；".join(messages), output)

    if strm_root is None:
        result.failed += 1
        return _row("failed", actual_path, parsed.labeled, "没有本地 strm 目录", "")

    existing = find_existing(strm_root, parsed.labeled)
    dest = existing or destination(strm_root, parsed.labeled)
    pickcode = video.pickcode
    if not pickcode:
        try:
            again = storage.stat(actual_path)
        except Exception:
            again = None
        pickcode = again.pickcode if again else ""
    if not pickcode:
        result.failed += 1
        return _row("failed", actual_path, parsed.labeled, "缺少 pickcode，无法生成播放地址", str(dest))

    movie = None
    nfo_text = None
    cover = None
    need_meta = existing is None or options.overwrite or not (existing / "movie.nfo").is_file()
    if need_meta:
        if javbus is None:
            result.failed += 1
            return _row("failed", actual_path, parsed.labeled, "没有配置 JavBus", "")
        movie, reason = javbus.scrape(parsed.code, parsed.kind)
        item_scraped = True
        if movie is None:
            messages.append(f"JavBus：{reason}，已写仅含番号的 nfo")
            nfo_text = _nfo(parsed, None)
            result.failed += 1
            status = "partial"
        else:
            actress = movie.actresses[0] if movie.actresses else ""
            if existing is None:
                dest = destination(strm_root, parsed.labeled, actress)
            nfo_text = _nfo(parsed, movie)
            cover = _cover(javbus, movie)
            if movie.cover and not cover:
                messages.append("封面下载失败")
            result.scraped += 1
            status = "ok"
    else:
        item_scraped = False
        messages.append("已有 nfo，只更新 strm")
        result.skipped += 1
        status = "skipped"

    try:
        write_library(
            dest,
            strm_url=build_play_url(options.play_base, pickcode, actual_name),
            strm_name=Path(actual_name).stem,
            nfo_text=nfo_text,
            cover=cover,
            force=options.overwrite,
        )
    except OSError as exc:
        result.failed += 1
        return _row("failed", actual_path, parsed.labeled, f"写入本地文件失败：{exc}", str(dest), item_scraped)

    output = str(dest)
    if not messages:
        messages.append("完成")
    row = _row(status, actual_path, parsed.labeled, "；".join(messages), output, item_scraped)
    row["wrote"] = True
    return row


def _rename_file(storage, options, parent, path, name, new_name, messages, result: ScanResult):
    if name == new_name:
        return None
    try:
        taken = new_name in _sibling_names(storage, parent, path)
    except Exception as exc:
        messages.append(f"检查重名失败：{exc}")
        return None
    if taken:
        messages.append(f"目标文件名已存在，保留 {name}")
        return None
    if options.dry_run:
        messages.append(f"将把文件改名为 {new_name}")
        result.renamed += 1
        return join(parent, new_name), new_name
    try:
        new_path = norm(storage.rename(path, new_name))
    except Exception as exc:
        messages.append(f"重命名文件失败：{exc}")
        return None
    result.renamed += 1
    messages.append(f"文件已改为 {new_name}")
    return new_path, new_name


def _rename_folder(storage, options, parent, file_name, parsed: AvCode, counts, protected, messages, result):
    if not options.rename_folder:
        return None
    if counts.get(parent, 0) != 1 or parent in protected or parent == "/":
        return None
    desired = parsed.labeled
    if name_of(parent) == desired:
        return None
    grand = parent_path(parent)
    try:
        taken = desired in _sibling_names(storage, grand, parent)
    except Exception as exc:
        messages.append(f"检查文件夹重名失败：{exc}")
        return None
    if taken:
        messages.append(f"目标文件夹已存在，保留 {name_of(parent)}")
        return None
    if options.dry_run:
        messages.append(f"将把文件夹改名为 {desired}")
        result.renamed += 1
        return join(join(grand, desired), file_name)
    try:
        new_parent = norm(storage.rename(parent, desired))
    except Exception as exc:
        messages.append(f"重命名文件夹失败：{exc}")
        return None
    result.renamed += 1
    messages.append(f"文件夹已改为 {desired}")
    return join(new_parent, file_name)


def _nfo(parsed: AvCode, movie: Movie | None) -> str:
    if movie is None:
        return build_nfo(
            code=parsed.code,
            title="",
            release="",
            length="",
            director="",
            studio="",
            publisher="",
            series="",
            genres=[],
            actresses=[],
            tag=parsed.tag,
        )
    return build_nfo(
        code=parsed.code,
        title=movie.title,
        release=movie.release,
        length=movie.length,
        director=movie.director,
        studio=movie.studio,
        publisher=movie.publisher,
        series=movie.series,
        genres=list(movie.genres),
        actresses=list(movie.actresses),
        tag=parsed.tag,
    )


def _cover(javbus, movie: Movie) -> bytes | None:
    download = getattr(javbus, "download_cover", None)
    if not download or not movie.cover:
        return None
    data = download(movie.cover)
    return data or None


def _row(status, path, code, message, output, scraped=False) -> dict:
    return {
        "status": status,
        "path": path,
        "code": code,
        "message": message,
        "output": output,
        "scraped": scraped,
    }
