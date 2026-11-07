"""扫描 MoviePilot 已登录的 115，刮削 JavBus，并生成本地 strm。"""

from __future__ import annotations

import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_DIR = str(Path(__file__).resolve().parent)
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)

from fastapi import Request

from app.chain.storage import StorageChain
from app.log import logger
from app.plugins import _PluginBase

from av115_intake import classify_line
from av115_javbus import JavBusClient, default_fetch, download_bytes
from av115_pan import CookieStorage, Pan115
from av115_scan import CloudFile, ScanOptions, join, name_of, norm, parent_path, run_scan

_VIDEO_NOTE = (
    "先保持「只预览」跑一遍。正式运行结束后，用 MoviePilot 里已保存的 Emby 密钥刷新媒体库，"
    "目标是 Unraid 上的 Emby：http://192.168.1.9:8096。"
    "strm 目录填这台 Emby 媒体库正在扫描的路径。"
    "播放地址也由这台 Emby 打开，不能写 127.0.0.1；跳转服务在 192.168.1.10:8787。"
)


class Av115Scraper(_PluginBase):
    plugin_name = "115 AV刮削"
    plugin_desc = "接收番号或磁力，扫描 115 里的有码、FC2 和无码番号，刮削 JavBus，重命名并生成本地 strm。"
    plugin_icon = "Moviepilot_A.png"
    plugin_version = "1.4.3"
    plugin_author = "local"
    author_url = ""
    plugin_config_prefix = "av115scraper_"
    plugin_order = 20
    auth_level = 1

    _enabled = False
    _onlyonce = False
    _dry_run = True
    _cron = ""
    _storage = "u115"
    _cloud_paths = ""
    _strm_root = "/media/AV"
    _play_base = "http://192.168.1.10:8787/redirect?pickcode={pickcode}"
    _javbus_host = "https://www.javbus.com"
    _refresh_emby = True
    _emby_library = "AV"
    _proxy = ""
    _min_size_mb = 300
    _interval_seconds = 3
    _overwrite = False
    _rename_folder = True

    def init_plugin(self, config: dict = None):
        if not hasattr(self, "_lock"):
            self._lock = threading.Lock()
        previous = getattr(self, "_cancel", None)
        if previous is not None:
            previous.set()
        self._cancel = threading.Event()
        config = dict(config or {})
        self._enabled = bool(config.get("enabled"))
        self._onlyonce = bool(config.get("onlyonce"))
        self._dry_run = bool(config.get("dry_run", True))
        self._cron = str(config.get("cron") or "").strip()
        self._storage = str(config.get("storage") or "u115").strip() or "u115"
        self._cloud_paths = str(config.get("cloud_paths") or "")
        self._strm_root = str(config.get("strm_root") or "/media/AV").strip()
        self._play_base = str(
            config.get("play_base") or "http://192.168.1.10:8787/redirect?pickcode={pickcode}"
        ).strip()
        self._javbus_host = str(config.get("javbus_host") or "https://www.javbus.com").strip()
        self._refresh_emby = bool(config.get("refresh_emby", True))
        self._emby_library = str(config.get("emby_library") or "AV").strip() or "AV"
        self._proxy = str(config.get("proxy") or "").strip()
        self._min_size_mb = _as_int(config.get("min_size_mb"), 300)
        self._interval_seconds = _as_int(config.get("interval_seconds"), 3)
        self._overwrite = bool(config.get("overwrite"))
        self._rename_folder = bool(config.get("rename_folder", True))
        self._offline_dir = str(config.get("offline_dir") or "").strip()
        self._cookie = str(config.get("cookie") or "").strip()
        pending = str(config.get("pending_text") or "").strip()
        if pending:
            config["pending_text"] = ""
            self.update_config(config)
            self._accept_text(pending)
        if self._onlyonce:
            self._onlyonce = False
            config["onlyonce"] = False
            self.update_config(config)
            if self._enabled:
                threading.Thread(target=self.run, name="av115-once", daemon=True).start()
        threading.Thread(target=self._watch_offline, name="av115-offline", daemon=True).start()

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        return []

    def get_api(self) -> List[Dict[str, Any]]:
        return [
            {
                "path": "/run",
                "endpoint": self.api_run,
                "methods": ["POST"],
                "auth": "apikey",
                "summary": "立即刮削",
                "description": "按当前配置扫描 115 并刮削",
            },
            {
                "path": "/status",
                "endpoint": self.api_status,
                "methods": ["GET"],
                "auth": "apikey",
                "summary": "最近一次结果",
                "description": "返回最近一次刮削结果",
            },
            {
                "path": "/task",
                "endpoint": self.api_add_task,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "提交番号或磁力",
                "description": "收下番号或磁力并排队。JSON 正文为 {\"text\":\"SSIS-001\"}，多条用换行。",
            },
            {
                "path": "/task",
                "endpoint": self.api_list_tasks,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "查看已收下的任务",
                "description": "返回排队中的番号和磁力",
            },
        ]

    def get_service(self) -> List[Dict[str, Any]]:
        if not self.get_state() or not self._cron:
            return []
        try:
            from apscheduler.triggers.cron import CronTrigger

            trigger = CronTrigger.from_crontab(self._cron)
        except Exception as exc:
            logger.warning(f"115 AV刮削的定时表达式无效：{self._cron} ({exc})")
            return []
        return [{
            "id": "Av115Scraper",
            "name": "115 AV刮削",
            "trigger": trigger,
            "func": self.run,
            "kwargs": {},
        }]

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        return [
            {
                "component": "VForm",
                "content": [
                    {
                        "component": "VAlert",
                        "props": {
                            "type": "info",
                            "variant": "tonal",
                            "text": _VIDEO_NOTE,
                            "class": "mb-4",
                        },
                    },
                    {
                        "component": "VRow",
                        "content": [
                            _area(
                                "pending_text",
                                "番号或磁力，保存后加入任务",
                                "SSIS-001\nmagnet:?xt=urn:btih:...",
                            ),
                            _switch(4, "enabled", "启用插件"),
                            _switch(4, "onlyonce", "保存后立即运行一次"),
                            _switch(4, "dry_run", "只预览，不改名不写文件"),
                            _field(6, "cron", "定时", "0 3 * * *，留空则只手动运行"),
                            _field(6, "storage", "存储", "MoviePilot 里的 115 存储 ID，默认 u115"),
                            _area(
                                "cookie",
                                "115 Cookie",
                                "和 115网盘订阅助手一样，填写 UID=...; CID=...; SEID=...; KID=...",
                            ),
                            _field(12, "offline_dir", "离线下载目录", "默认 /云下载，完成后只把正片移到扫描目录"),
                            _area("cloud_paths", "115 目录，每行一个", "/AV/有码\n/AV/FC2"),
                            _field(6, "strm_root", "strm 目录", "/media/AV"),
                            _field(12, "play_base", "播放地址模板", "http://192.168.1.10:8787/redirect?pickcode={pickcode}"),
                            _field(6, "javbus_host", "JavBus 地址", "https://www.javbus.com"),
                            _field(6, "proxy", "JavBus 代理", "http://127.0.0.1:7890，留空则直连"),
                            _field(4, "min_size_mb", "最小体积(MB)", "小于此值视为预告，默认 300"),
                            _field(4, "interval_seconds", "刮削间隔(秒)", "每次请求 JavBus 的间隔"),
                            _switch(4, "rename_folder", "只有一部片的文件夹也改名"),
                            _switch(4, "overwrite", "覆盖已有 nfo 和封面"),
                            _switch(4, "refresh_emby", "完成后刷新 Emby"),
                            _field(4, "emby_library", "Emby 媒体库名", "AV"),
                        ],
                    },
                ],
            }
        ], {
            "pending_text": "",
            "enabled": False,
            "onlyonce": False,
            "dry_run": True,
            "cron": "",
            "storage": "u115",
            "cookie": "",
            "offline_dir": "/云下载",
            "cloud_paths": "",
            "strm_root": "/media/AV",
            "play_base": "http://192.168.1.10:8787/redirect?pickcode={pickcode}",
            "refresh_emby": True,
            "emby_library": "AV",
            "javbus_host": "https://www.javbus.com",
            "proxy": "",
            "min_size_mb": 300,
            "interval_seconds": 3,
            "rename_folder": True,
            "overwrite": False,
        }

    def get_page(self) -> List[dict]:
        running = hasattr(self, "_lock") and self._lock.locked()
        text = ("正在运行。\n" if running else "") + self._page_text()
        return [
            {
                "component": "VAlert",
                "props": {
                    "type": "info",
                    "variant": "tonal",
                    "text": (
                        "把番号或磁力贴在下面，点「加入任务」。"
                        "如果列表没变化，就填到配置里的「番号或磁力」再点保存。"
                        "机器人调用 POST /api/v1/plugin/Av115Scraper/task ，"
                        "请求头带 API 令牌，正文 {\"text\":\"SSIS-001\"}。"
                    ),
                    "class": "mb-3",
                },
            },
            {
                "component": "VTextarea",
                "props": {
                    "model": "text",
                    "label": "番号或磁力",
                    "placeholder": "SSIS-001\nmagnet:?xt=urn:btih:...",
                    "rows": 4,
                    "variant": "outlined",
                },
            },
            {
                "component": "VBtn",
                "text": "加入任务",
                "props": {"color": "primary", "class": "mt-3"},
                "events": {
                    "click": {
                        "api": "plugin/Av115Scraper/task",
                        "method": "post",
                    }
                },
            },
            {
                "component": "VAlert",
                "props": {
                    "type": "info",
                    "variant": "tonal",
                    "text": text,
                    "class": "mt-4",
                    "style": "white-space: pre-line;",
                },
            },
        ]

    def stop_service(self):
        cancel = getattr(self, "_cancel", None)
        if cancel is not None:
            cancel.set()

    def api_run(self) -> dict:
        if not self._enabled:
            return {"ok": False, "error": "插件未启用"}
        threading.Thread(target=self.run, name="av115-api", daemon=True).start()
        return {"ok": True, "message": "已开始"}

    def api_status(self) -> dict:
        return self.get_data("last_run") or {"ok": False, "error": "还没有运行记录"}

    async def api_add_task(self, request: Request) -> dict:
        payload = await _read_body(request)
        query_text = request.query_params.get("text") or ""
        chunks = _payload_text(payload)
        if query_text.strip():
            chunks.append(query_text)
        logger.info("115 AV刮削收到提交，文本段 %s", len(chunks))
        return self._accept_text("\n".join(chunks))

    def _accept_text(self, text: str) -> dict:
        accepted = []
        rejected = []
        for line in (text or "").splitlines():
            item = classify_line(line)
            if item is None:
                continue
            if item["kind"] == "invalid":
                rejected.append({"text": item["text"][:120], "error": item["error"]})
                continue
            task = self._enqueue(item)
            accepted.append(task)
        if accepted:
            logger.info("115 AV刮削收下 %s 条，拒绝 %s 条", len(accepted), len(rejected))
        else:
            logger.warning("115 AV刮削没有收下任务，拒绝 %s 条", len(rejected))
        return {
            "ok": bool(accepted),
            "accepted": accepted,
            "rejected": rejected,
            "error": "" if accepted else "没有收下任何番号或磁力",
        }

    def api_list_tasks(self) -> dict:
        tasks = self._load_tasks()
        return {"ok": True, "tasks": tasks}

    def _enqueue(self, item: dict) -> dict:
        if not hasattr(self, "_intake_lock"):
            self._intake_lock = threading.Lock()
        task = {
            "id": datetime.now().strftime("%Y%m%d%H%M%S%f"),
            "kind": item["kind"],
            "text": item["text"],
            "code": item["code"],
            "status": "queued",
            "created_at": _now(),
        }
        with self._intake_lock:
            tasks = self._load_tasks()
            tasks.insert(0, task)
            self.save_data("tasks", tasks[:50])
        return task

    def _load_tasks(self) -> list:
        try:
            tasks = self.get_data("tasks") or []
        except Exception:
            return []
        return tasks if isinstance(tasks, list) else []

    def _watch_offline(self) -> None:
        cancel = getattr(self, "_cancel", None)
        while cancel is None or not cancel.is_set():
            try:
                self._flush_offline()
            except Exception as exc:
                logger.warning("115 AV刮削离线监视失败：%s", exc)
            if cancel is not None and cancel.wait(20):
                return

    def _flush_offline(self) -> None:
        for task in list(self._load_tasks()):
            if task.get("kind") == "magnet" and task.get("status") == "queued":
                self._push_offline(task)
        self._settle_downloads()

    def _settle_downloads(self) -> None:
        cookie = self._cookie_value()
        if not cookie:
            return
        pending = [
            task for task in self._load_tasks()
            if task.get("kind") == "magnet"
            and task.get("code")
            and not task.get("packed")
            and task.get("status") in ("offline", "ready")
        ]
        if not pending:
            return
        try:
            pan = Pan115(cookie)
            library = self._library_path()
            cloud_id = pan.ensure_dir(self._offline_save_path())
            min_bytes = max(0, self._min_size_mb) * 1024 * 1024
        except Exception as exc:
            logger.warning("115 AV刮削无法整理离线文件：%s", exc)
            return
        ready = False
        for task in pending:
            code = task.get("code") or ""
            folder = f"{library.rstrip('/')}/{code}"
            try:
                moved, junk, already = pan.relocate_code(code, library, cloud_id, min_bytes)
                if moved + already <= 0:
                    task["status"] = "offline"
                    task["message"] = "115 还在下载，正片还没出现"
                    logger.info("115 AV刮削等待 %s 下完", code)
                    self._update_task(task)
                    continue
                task["settled"] = True
                task["packed"] = True
                task["status"] = "ready"
                task["message"] = f"已把 {moved or already} 个正片放进 {folder}，{junk} 个垃圾文件留在云下载"
                ready = True
                logger.info("115 AV刮削整理 %s：%s", code, task["message"])
            except Exception as exc:
                task["message"] = str(exc)
                logger.warning("115 AV刮削整理失败：%s", exc)
            self._update_task(task)
        if ready:
            self.run()

    def _push_offline(self, task: dict) -> dict:
        if not self._cookie_value():
            task["status"] = "queued"
            task["message"] = "没有填写 115 Cookie"
            self._update_task(task)
            return task
        try:
            task["message"] = self._add_offline(task.get("text") or "")
            task["status"] = "offline"
            logger.info("115 AV刮削已提交离线：%s", task.get("code") or task.get("id"))
        except Exception as exc:
            task["status"] = "failed"
            task["message"] = str(exc)
            logger.warning("115 AV刮削提交离线失败：%s", exc)
        self._update_task(task)
        return task

    def _add_offline(self, magnet: str) -> str:
        cookie = self._cookie_value()
        if not cookie:
            raise RuntimeError("没有填写 115 Cookie")
        pan = Pan115(cookie)
        directory_id = pan.ensure_dir(self._offline_save_path())
        return _offline_message(pan.add_magnet(magnet, directory_id))

    def _cookie_value(self) -> str:
        own = (getattr(self, "_cookie", "") or "").strip()
        if "UID=" in own:
            return own
        try:
            from app.core.plugin import PluginManager
            config = PluginManager().get_plugin_config("P115StrmHelper") or {}
        except Exception:
            return ""
        if not isinstance(config, dict):
            return ""
        for key in ("cookies", "cookie", "pan_cookie", "pan_cookies"):
            value = config.get(key)
            if isinstance(value, str) and "UID=" in value:
                return value.strip()
        return ""

    def _offline_save_path(self) -> str:
        path = (getattr(self, "_offline_dir", "") or "").strip() or "/云下载"
        return path if path.startswith("/") else f"/{path}"

    def _library_path(self) -> str:
        for line in (getattr(self, "_cloud_paths", "") or "").splitlines():
            text = line.strip()
            if text and not text.startswith("#"):
                return text if text.startswith("/") else f"/{text}"
        return "/AV"

    def _offline_path_id(self, client) -> str:
        path = self._offline_save_path()
        if not path or path == "/":
            return "0"
        folder = path if path.startswith("/") else f"/{path}"
        try:
            found = client.fs_dir_getid(folder.rstrip("/"))
        except Exception as exc:
            raise RuntimeError(f"找不到 115 目录 {path}：{exc}") from exc
        file_id = ""
        if isinstance(found, dict):
            file_id = str(found.get("id") or found.get("cid") or "")
        if not file_id or file_id == "0":
            raise RuntimeError(f"找不到 115 目录 {path}")
        return file_id

    def _update_task(self, task: dict) -> None:
        if not hasattr(self, "_intake_lock"):
            self._intake_lock = threading.Lock()
        with self._intake_lock:
            tasks = self._load_tasks()
            for index, old in enumerate(tasks):
                if old.get("id") == task.get("id"):
                    tasks[index] = task
                    break
            else:
                tasks.insert(0, task)
            self.save_data("tasks", tasks[:50])

    def run(self) -> dict:
        if not hasattr(self, "_lock"):
            self._lock = threading.Lock()
        if not self._lock.acquire(blocking=False):
            logger.info("115 AV刮削已有任务在运行")
            return {"ok": False, "error": "已有任务在运行"}
        flag = getattr(self, "_cancel", None) or threading.Event()
        try:
            flag.clear()
            payload = self._execute(flag.is_set)
            self.save_data("last_run", payload)
            logger.info(
                "115 AV刮削结束：重命名 %s，刮削 %s，跳过 %s，失败 %s",
                payload.get("renamed", 0),
                payload.get("scraped", 0),
                payload.get("skipped", 0),
                payload.get("failed", 0),
            )
            return payload
        except Exception as exc:
            logger.exception(f"115 AV刮削失败：{exc}")
            payload = {"ok": False, "error": str(exc), "items": [], "finished_at": _now()}
            self.save_data("last_run", payload)
            return payload
        finally:
            self._lock.release()

    def _execute(self, cancel) -> dict:
        roots = []
        for line in self._cloud_paths.splitlines():
            text = line.strip()
            if text and not text.startswith("#"):
                roots.append(text)
        options = ScanOptions(
            roots=roots,
            strm_root=self._strm_root,
            play_base=self._play_base,
            min_size_mb=self._min_size_mb,
            interval_seconds=self._interval_seconds,
            overwrite=self._overwrite,
            dry_run=self._dry_run,
            rename_folder=self._rename_folder,
        )
        mode = "预览" if self._dry_run else "正式"
        logger.info(f"115 AV刮削开始（{mode}），目录 {len(roots)} 个")
        cookie = self._cookie_value()
        storage = CookieStorage(Pan115(cookie)) if cookie else _U115Storage(self._storage)
        outcome = run_scan(
            storage,
            options,
            javbus=self._client(),
            cancel=cancel,
        )
        payload = outcome.as_dict()
        payload["finished_at"] = _now()
        payload["items"] = payload["items"][:200]
        wrote = any(item.get("wrote") for item in payload["items"])
        if self._refresh_emby and not self._dry_run and wrote:
            payload["emby"] = self._refresh_emby_library()
        elif self._dry_run:
            payload["emby"] = "预览不刷新 Emby"
        return payload

    def _refresh_emby_library(self) -> str:
        """用 MoviePilot 里已经保存的 Emby 密钥，刷新指定媒体库。"""
        try:
            from app.core.module import ModuleManager
            from app.schemas.types import ModuleType
        except Exception as exc:
            return f"无法加载媒体服务器模块：{exc}"
        wanted = self._emby_library.casefold()
        found = False
        for module in ModuleManager().get_running_type_modules(ModuleType.MediaServer):
            if module.__class__.__name__ != "EmbyModule":
                continue
            for name, server in (module.get_instances() or {}).items():
                found = True
                libraries = server.get_librarys() or []
                target = next(
                    (
                        library for library in libraries
                        if str(getattr(library, "name", "") or "").casefold() == wanted
                    ),
                    None,
                )
                refresh_one = getattr(server, "_Emby__refresh_emby_library_by_id", None)
                if target is not None and refresh_one:
                    if refresh_one(str(target.id)):
                        return f"已通知 {name} 刷新媒体库 {target.name}"
                    return f"{name} 刷新媒体库 {target.name} 失败"
                if server.refresh_root_library():
                    return f"没有找到名为 {self._emby_library} 的媒体库，已通知 {name} 刷新全部媒体库"
                return f"{name} 刷新失败"
        if not found:
            return "MoviePilot 里没有可用的 Emby"
        return "Emby 刷新失败"

    def _client(self) -> JavBusClient:
        proxy = self._proxy
        host = self._javbus_host

        def fetch(url: str):
            return default_fetch(url, proxy=proxy, timeout=20)

        def download(url: str) -> bytes:
            return download_bytes(url, proxy=proxy, timeout=20, referer=host.rstrip("/") + "/")

        return JavBusClient(host, fetch, download)

    def _page_text(self) -> str:
        try:
            data = self.get_data("last_run") or {}
        except Exception:
            data = {}
        lines = ["已收下的任务："]
        tasks = self._load_tasks()
        if not tasks:
            lines.append("还没有番号或磁力。")
        for task in tasks[:20]:
            label = task.get("code") or task.get("kind") or ""
            lines.append(
                f"{task.get('status') or ''}  {label}  {task.get('message') or ''}  {task.get('created_at') or ''}"
            )
        lines.append("")
        if not data:
            lines.append("还没有运行记录。建议先打开「只预览」。")
            return "\n".join(lines)
        lines.extend([
            f"时间：{data.get('finished_at') or ''}",
            f"模式：{'预览' if data.get('dry_run') else '正式'}",
            "重命名 {renamed}，刮削 {scraped}，跳过 {skipped}，失败 {failed}".format(
                renamed=data.get("renamed", 0),
                scraped=data.get("scraped", 0),
                skipped=data.get("skipped", 0),
                failed=data.get("failed", 0),
            ),
        ])
        if data.get("emby"):
            lines.append(f"Emby：{data['emby']}")
        if data.get("error"):
            lines.append(f"错误：{data['error']}")
        items = data.get("items") or []
        for item in items[:40]:
            lines.append(
                f"{item.get('status') or ''}  {item.get('code') or '-'}  "
                f"{item.get('message') or ''}  {item.get('path') or ''}"
            )
        if len(items) > 40:
            lines.append(f"... 还有 {len(items) - 40} 条")
        return "\n".join(lines)


class _U115Storage:
    """把 MoviePilot 的 115 存储收成扫描器要用的三个方法。"""

    def __init__(self, storage: str):
        self.storage = storage or "u115"
        self.chain = StorageChain()

    def stat(self, path: str) -> Optional[CloudFile]:
        item = self.chain.get_file_item(storage=self.storage, path=Path(norm(path)))
        if item is None:
            return None
        return _to_cloud(item)

    def list_dir(self, path: str) -> List[CloudFile]:
        item = self.chain.get_file_item(storage=self.storage, path=Path(norm(path)))
        if item is None:
            raise FileNotFoundError(path)
        children = self.chain.list_files(item, recursion=False) or []
        return [_to_cloud(child) for child in children]

    def rename(self, path: str, new_name: str) -> str:
        item = self.chain.get_file_item(storage=self.storage, path=Path(norm(path)))
        if item is None:
            raise FileNotFoundError(path)
        if not self.chain.rename_file(item, new_name):
            raise RuntimeError(f"115 拒绝重命名 {path}")
        return join(parent_path(path), new_name)


def _to_cloud(item) -> CloudFile:
    path = norm(item.path or "")
    return CloudFile(
        path=path,
        name=item.name or name_of(path),
        is_dir=item.type == "dir",
        size=int(item.size or 0),
        pickcode=item.pickcode or "",
        fileid=str(item.fileid or ""),
    )


def _switch(cols: int, model: str, label: str) -> dict:
    return {
        "component": "VCol",
        "props": {"cols": 12, "md": cols},
        "content": [{
            "component": "VSwitch",
            "props": {"model": model, "label": label},
        }],
    }


def _field(cols: int, model: str, label: str, placeholder: str) -> dict:
    return {
        "component": "VCol",
        "props": {"cols": 12, "md": cols},
        "content": [{
            "component": "VTextField",
            "props": {"model": model, "label": label, "placeholder": placeholder},
        }],
    }


def _area(model: str, label: str, placeholder: str) -> dict:
    return {
        "component": "VCol",
        "props": {"cols": 12},
        "content": [{
            "component": "VTextarea",
            "props": {"model": model, "label": label, "placeholder": placeholder, "rows": 4},
        }],
    }


async def _read_body(request) -> dict:
    try:
        payload = await request.json()
    except Exception:
        payload = None
    if isinstance(payload, dict):
        return payload
    try:
        raw = (await request.body()).decode("utf-8", "replace").strip()
    except Exception:
        raw = ""
    if raw:
        return {"text": raw}
    return {}


def _offline_message(result) -> str:
    if not isinstance(result, dict):
        raise RuntimeError("115 离线接口没有返回")
    message = str(result.get("error_msg") or result.get("error") or result.get("message") or "")
    state = result.get("state")
    errno = result.get("errno", result.get("errcode", result.get("code")))
    if state in (True, 1, "1") or errno in (0, "0") or "已存在" in message:
        return message or "已添加到 115 离线下载"
    raise RuntimeError(message or "115 拒绝了这条磁力")


def _payload_text(payload) -> list:
    chunks = []
    if isinstance(payload, dict):
        values = payload.values()
    elif isinstance(payload, list):
        values = payload
    elif isinstance(payload, str):
        values = [payload]
    else:
        return chunks
    for value in values:
        if isinstance(value, str):
            text = value.strip()
            if text and text != "{text}":
                chunks.append(text)
        elif isinstance(value, (dict, list)):
            chunks.extend(_payload_text(value))
    return chunks


def _as_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
