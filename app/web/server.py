"""辰鹄AI创作中心 本地 Web 服务：纯标准库 asyncio HTTP 服务器。

与 Qt 桌面端共用同一套服务对象（ConfigStore / EngineManager / TaskService），
只监听本机地址（默认 127.0.0.1:8765），不引入任何第三方依赖。
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import subprocess
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from app.agent.service import AgentService
from app.core.config import ConfigError, ConfigStore
from app.core.console import ensure_utf8_console
from app.core.hardware import (
    GpuInfo,
    effective_free_mib,
    forget_vram_cache,
    holders_summary,
    vram_holders_cached,
)
from app.core.logging_setup import configure_logging
from app.core.paths import AppPaths
from app.engine_registration import register_production_engines
from app.tasks.engine_manager import EngineManager
from app.tasks.models import TaskRecord, TaskRequest, TaskStatus
from app.tasks.service import TaskService

STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_BODY_BYTES = 256 * 1024
# 上传垫图（图生图 / 图生视频）走单独的上限：base64 后最大 12MB（原图约 8MB）。
MAX_UPLOAD_BYTES = 12 * 1024 * 1024
MAX_UPLOAD_RAW_BYTES = 8 * 1024 * 1024
MAX_HEADER_BYTES = 16 * 1024
# 硬件快照缓存：nvidia-smi 可能耗时数秒，缓存后不再每次请求都卡事件循环。
HARDWARE_TTL_SECONDS = 5.0

_TEXT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}

_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
    # AI 智能体生成的办公文件：类型写对，浏览器才知道是 Word/Excel/PPT，
    # 下载后的文件名与扩展名也不会被改写（这三行是 OOXML 的标准 MIME）。
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}

_STATUS_PATH = re.compile(r"^/api/tasks/([^/]+)$")
_ACTION_PATH = re.compile(r"^/api/tasks/([^/]+)/(cancel|retry)$")
_ENGINE_RESTART_PATH = re.compile(r"^/api/engines/([^/]+)/restart$")
_OUTPUT_PATH = re.compile(r"^/outputs/(.+)$")
_UPLOAD_PATH = re.compile(r"^/uploads/([^/]+)$")

# 图片文件签名：上传时校验内容与扩展名一致，不只看后缀。
_IMAGE_SIGNATURES = {
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".webp": (b"RIFF",),
    ".bmp": (b"BM",),
}


def _hardware_snapshot(*, detailed: bool = False) -> dict[str, Any]:
    """只读硬件快照：NVIDIA 显卡显存（nvidia-smi）与物理内存（GlobalMemoryStatusEx）。

    为 AI 音乐提交前置检查（显存门槛拦截）与启动自检展示服务的纯信息端点，
    不参与任何生成/排队逻辑。nvidia-smi 不可用（无 NVIDIA 显卡）时如实返回
    gpu=None，前端据此诚实提示，绝不伪造数值。

    ``detailed=True`` 才多做一件事：按进程实测「显存到底被谁占着」（要起一次 PowerShell，
    几百毫秒，内部缓存 20 秒）。仪表盘每几秒轮询一次的默认路径绝不做这件事——
    nvidia-smi 的 used 在 Windows 上虚高，只能当参考；真正拦任务前要点名占用者，
    就得用按进程实测的数，那只在「门槛差一点就要拦下来」时查一次。
    """
    gpu: dict[str, Any] | None = None
    try:
        proc = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.free,memory.used",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=8,
            check=True,
        )
        line = (proc.stdout.strip().splitlines() or [""])[0]
        # 显卡名里可能带逗号，数字在末尾三项。
        parts = [part.strip() for part in line.split(",")]
        if len(parts) >= 4:
            gpu = {
                "name": ",".join(parts[:-3]),
                "memory_total_mb": int(parts[-3]),
                "memory_free_mb": int(parts[-2]),
                "memory_used_mb": int(parts[-1]),
            }
    except Exception:  # noqa: BLE001 - 探测失败时如实降级
        gpu = None

    if detailed and gpu is not None:
        try:
            holders = vram_holders_cached(max_age_seconds=20.0)
            info = GpuInfo(
                str(gpu["name"]),
                int(gpu["memory_total_mb"]),
                int(gpu["memory_free_mb"]),
                int(gpu.get("memory_used_mb", 0)),
            )
            gpu["memory_free_effective_mb"] = effective_free_mib(info, holders)
            gpu["memory_holders"] = holders_summary(holders)
            gpu["memory_holders_list"] = [
                {"pid": holder.pid, "name": holder.name, "memory_mb": holder.memory_mib}
                for holder in (holders or ())[:5]
            ]
        except Exception:  # noqa: BLE001 - 按进程实测失败就退回 nvidia-smi 的 free
            gpu["memory_free_effective_mb"] = int(gpu["memory_free_mb"])
            gpu["memory_holders"] = ""
            gpu["memory_holders_list"] = []
    elif gpu is not None:
        # 不做按进程实测时，effective 就等于 nvidia-smi 的 free（前端不至于拿到 undefined）。
        gpu["memory_free_effective_mb"] = int(gpu["memory_free_mb"])

    total_gb: float | None = None
    free_gb: float | None = None
    try:
        import ctypes

        class _MemoryStatusEx(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        stat = _MemoryStatusEx()
        stat.dwLength = ctypes.sizeof(_MemoryStatusEx)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            total_gb = round(stat.ullTotalPhys / (1024**3), 1)
            free_gb = round(stat.ullAvailPhys / (1024**3), 1)
    except Exception:  # noqa: BLE001 - 非 Windows 或 API 失败时省略该字段
        pass

    return {"gpu": gpu, "memory_total_gb": total_gb, "memory_free_gb": free_gb}

_VALID_TASK_TYPES = ("image", "video", "music")
# 垫图（init_image_path）允许的扩展名；worker 侧图像解码同样按这些格式校验。
_VALID_INPUT_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".bmp"})


@dataclass
class WebServices:
    """Web 层组装出来的服务对象（与桌面端完全同构，只是不含 Qt）。"""

    paths: AppPaths
    config: dict[str, Any]
    config_store: ConfigStore
    engine_manager: EngineManager
    task_service: TaskService
    logger: logging.Logger


def build_services() -> WebServices:
    """按桌面端相同方式组装服务（Qt-free；恢复中断任务由 task_service.start() 完成）。"""
    paths = AppPaths.from_environment()
    paths.prepare_app_home()
    logger = configure_logging(paths)
    config_store = ConfigStore(paths)
    config = config_store.load()
    engine_manager = EngineManager()
    register_production_engines(engine_manager, config, paths, logger)
    task_service = TaskService(paths.database_file, engine_manager)
    # 自动另存目录（配置项 auto_save_dir）：与桌面端读同一份 config.json；
    # 设置页改动后由 _handle_post 即时更新（本进程与桌面端进程各自持有自己的实例，
    # 所以谁在执行任务谁就按最新配置复制副本）。
    task_service.set_auto_save_dir(str(config.get("auto_save_dir") or ""))
    # 「工具内 output\」固定镜像（与桌面端 bootstrap 完全一致）：产物写进 output_dir 之后
    # 再复制一份到项目根下的 output\；output_dir 本来就指着这里时不重复复制。
    task_service.set_project_output_dir(str(paths.project_output_dir))
    return WebServices(paths, config, config_store, engine_manager, task_service, logger)


def _file_to_output_url(path_text: str, output_dir: Path) -> str | None:
    """任务产物若位于输出目录内，则换算成 /outputs/... 的预览链接。"""
    try:
        resolved = Path(path_text).resolve()
        base = output_dir.resolve()
        relative = resolved.relative_to(base)
    except (OSError, ValueError):
        return None
    return "/outputs/" + "/".join(
        urllib.parse.quote(part) for part in relative.parts
    )


def _record_to_dict(record: TaskRecord, output_dir: Path) -> dict[str, Any]:
    return {
        "task_id": record.task_id,
        "task_type": record.request.task_type,
        "engine_id": record.request.engine_id,
        "prompt": record.request.prompt,
        "model_id": record.request.model_id,
        "parameters": record.request.parameters,
        "status": record.status.value,
        "created_at": record.created_at,
        "started_at": record.started_at,
        "finished_at": record.finished_at,
        "progress": record.progress,
        "error_message": record.error_message,
        "retry_of": record.retry_of,
        "output_files": [
            {
                "path": item,
                "name": Path(item).name,
                "url": _file_to_output_url(item, output_dir),
            }
            for item in record.output_files
        ],
        # 自动另存目录里那份副本的路径（未配置自动另存时为空列表）。
        "saved_copies": [
            str(item) for item in (record.result_metadata.get("saved_copies") or [])
        ],
    }


class HttpError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass
class _Request:
    method: str
    path: str
    query: dict[str, list[str]]
    body: bytes
    headers: dict[str, str] = field(default_factory=dict)


class WebApplication:
    """把 HTTP 请求路由到 TaskService / EngineManager 的应用对象。"""

    def __init__(self, services: WebServices) -> None:
        self.services = services
        # 与 worker 端 CHENGUAI_OUTPUT_DIR 同源：优先 config["output_dir"]，
        # 否则回退 AppPaths 默认。两侧不一致就会出现“产物不在输出目录”。
        configured = str(services.config.get("output_dir", "")).strip()
        self.output_dir = Path(configured).expanduser() if configured else services.paths.output_dir
        # 启动即确保输出目录存在（生成开始前的前置检查）。
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
        except OSError:  # 输出目录不可写时不阻塞启动，仅记录
            services.logger.exception("无法创建输出目录：%s", self.output_dir)
        self.logger = services.logger
        # 上传的垫图目录：图生图 / 图生视频在网页端选本机文件后落到这里，
        # worker 用的是文件系统绝对路径，因此返回 path（绝对）+ url（预览）。
        configured_upload_dir = str(services.config.get("upload_dir", "")).strip()
        self.upload_dir = (
            Path(configured_upload_dir).expanduser()
            if configured_upload_dir
            else services.paths.data_root / "uploads"
        )
        try:
            self.upload_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            services.logger.exception("无法创建上传目录：%s", self.upload_dir)
        # 第六板块「AI 智能体」：与桌面端同一个服务层，共用同一串行队列。
        self.agent = AgentService(
            task_service=services.task_service,
            engine_manager=services.engine_manager,
            config=services.config,
            logger=services.logger,
        )
        self._hardware_cache: dict[str, Any] | None = None
        self._hardware_at = 0.0
        self._bound_host = str(services.config.get("web_host", "127.0.0.1"))

    # ------------------------------------------------------------------ 路由

    async def handle(self, request: _Request) -> tuple[int, str, bytes, dict[str, str]]:
        method, path = request.method, request.path
        # 本机服务也挡 DNS 重绑定 / 跨站写请求：只接受指向本机地址的 Host 与同源 Origin。
        self._check_local_only(request)
        try:
            if method == "GET":
                if path == "/api/hardware":
                    # ?detailed=1 才会多做一次「按进程实测谁占着显存」（要起 PowerShell，
                    # 几百毫秒）：仪表盘默认轮询不要它，只有想看点名清单时才要。
                    detailed = ((request.query.get("detailed") or [""])[0].strip().lower() not in ("", "0", "false"))
                    return self._json(200, await self._hardware_async(detailed=detailed))
                if path == "/api/agent/capabilities":
                    # 能力探测要连大模型端点、跑一次 PowerShell，放线程池里跑，不卡事件循环。
                    return self._json(200, await asyncio.to_thread(self.agent.capabilities))
                return self._handle_get(request)
            if method == "POST":
                return await self._handle_post(path, request)
            raise HttpError(405, "仅支持 GET 与 POST")
        except HttpError:
            raise
        except KeyError as exc:
            raise HttpError(404, str(exc).strip("'")) from exc
        except ValueError as exc:
            raise HttpError(400, str(exc)) from exc

    # --------------------------------------------------------- 本机访问约束
    def _check_local_only(self, request: _Request) -> None:
        """Host / Origin 校验。

        默认只监听 127.0.0.1，但浏览器仍可能被引导到本机端口（DNS 重绑定、
        恶意网页发 POST）。这里要求 Host 指向本机，且带 Origin 时必须同源。
        配置成 0.0.0.0/:: 这类全网卡监听时（局域网自用），跳过 Host 白名单，
        但仍要求 Origin 同源，避免任意网页提交任务。
        """
        host_header = (request.headers.get("host") or "").strip()
        bind_host = (self._bound_host or "").strip().lower().strip("[]")
        wildcard = bind_host in ("", "0.0.0.0", "::", "*")
        host_name = self._host_of(host_header)
        if not host_header:
            raise HttpError(400, "缺少 Host 头：请用浏览器打开本机地址访问")
        if not wildcard and not self._is_local_host(host_name, bind_host):
            self.logger.warning(
                "拒绝访问：Host=%r（解析为 %s）不是本机地址；Origin=%r",
                host_header,
                host_name,
                request.headers.get("origin") or "-",
            )
            raise HttpError(
                403,
                "Host 不匹配：本服务只接受指向本机的地址（收到 "
                f"{host_name}）。请在浏览器里用 "
                f"http://127.0.0.1:{self._port_of(host_header)}/ 打开；"
                "若浏览器走了代理，请把 127.0.0.1/localhost 加入不代理名单。",
            )
        origin = (request.headers.get("origin") or "").strip()
        if origin and origin.lower() != "null":
            origin_netloc = urllib.parse.urlsplit(origin).netloc.lower()
            origin_host = self._host_of(origin_netloc)
            # 本机服务的关键约束是「来源必须是本机」，而不是 Host 与 Origin 字符串严格相等：
            # 浏览器/代理常把 localhost 与 127.0.0.1 互换（本机代理改写 Host 时尤其如此），
            # 严格相等会把正常页面误判成跨站、整个界面拿不到数据。全网卡监听时仍要求同源。
            same_source = bool(origin_netloc) and origin_netloc == host_header.lower()
            if wildcard:
                if origin_netloc and not same_source:
                    self.logger.warning(
                        "拒绝跨站：Origin=%s 与 Host=%s 不同源", origin_netloc, host_header
                    )
                    raise HttpError(403, "跨站请求已拒绝：Origin 与本机服务不同源")
            elif not self._is_local_host(origin_host, bind_host):
                self.logger.warning(
                    "拒绝访问：Origin=%r 的宿主 %s 不是本机地址（Host=%r）",
                    origin,
                    origin_host,
                    host_header,
                )
                raise HttpError(
                    403,
                    "跨站请求已拒绝：Origin 不是本机地址"
                    f"（收到 {origin_host}），请用 http://127.0.0.1:{self._port_of(host_header)}/ 打开",
                )

    @staticmethod
    def _host_of(value: str) -> str:
        """从 Host / netloc 里取出主机名（处理 [::1]:8765 这类带端口的写法）。"""
        value = (value or "").strip().lower()
        if not value:
            return ""
        if value.startswith("["):
            end = value.find("]")
            return value[1:end] if end > 0 else value
        return value.split(":")[0]

    @staticmethod
    def _port_of(host_header: str) -> int:
        for candidate in reversed((host_header or "").rsplit(":", 1)):
            if candidate.isdigit():
                return int(candidate)
        return 8765

    @staticmethod
    def _is_local_host(name: str, bind_host: str = "") -> bool:
        if not name:
            return False
        return name in {
            "localhost",
            "127.0.0.1",
            "::1",
            "ip6-localhost",
            "ip6-loopback",
            (bind_host or "").strip("[]").lower(),
        } or name.startswith("127.")

    async def _hardware_async(self, *, detailed: bool = False) -> dict[str, Any]:
        """硬件快照（线程池 + TTL 缓存），避免 nvidia-smi 阻塞事件循环。

        ``detailed=True``（提交前的显存判定）不吃 TTL 缓存：刚把自己拉起的空闲引擎
        停掉，显存数必须现查，不然前端会拿着旧数继续报「显存不够」。
        """
        now = time.monotonic()
        if (
            not detailed
            and self._hardware_cache is not None
            and now - self._hardware_at < HARDWARE_TTL_SECONDS
        ):
            return self._hardware_cache
        snapshot = await asyncio.to_thread(lambda: _hardware_snapshot(detailed=detailed))
        self._hardware_cache = snapshot
        self._hardware_at = now
        return snapshot

    def _handle_get(
        self, request: _Request
    ) -> tuple[int, str, bytes, dict[str, str]]:
        path, query = request.path, request.query
        if path in ("/", "/index.html"):
            return self._serve_static("index.html")
        if path.startswith("/static/"):
            return self._serve_static(path[len("/static/"):])
        if path == "/api/health":
            return self._json(200, {"status": "ok", "service": "strata-web"})
        if path == "/api/engines":
            statuses = self.services.engine_manager.list_statuses()
            return self._json(
                200,
                {
                    "engines": [
                        {
                            "engine_id": s.engine_id,
                            "state": s.state,
                            "message": s.message,
                        }
                        for s in statuses
                    ]
                },
            )
        if path == "/api/settings":
            # 产物保存相关设置：项目内 output\ 目录 + 可选的「自动另存目录」。
            return self._json(200, self._settings_payload())
        if path == "/api/tasks":
            status_value = (query.get("status") or [""])[0]
            limit_value = (query.get("limit") or ["100"])[0]
            status: TaskStatus | None = None
            if status_value:
                try:
                    status = TaskStatus(status_value)
                except ValueError as exc:
                    raise HttpError(400, f"未知任务状态：{status_value}") from exc
            try:
                limit = int(limit_value)
            except ValueError as exc:
                raise HttpError(400, "limit 必须是整数") from exc
            records = self.services.task_service.list_tasks(status=status, limit=limit)
            return self._json(
                200,
                {
                    "tasks": [
                        _record_to_dict(record, self.output_dir) for record in records
                    ]
                },
            )
        match = _STATUS_PATH.match(path)
        if match:
            record = self.services.task_service.get_task(match.group(1))
            if record is None:
                raise HttpError(404, f"任务不存在：{match.group(1)}")
            return self._json(200, _record_to_dict(record, self.output_dir))
        match = _OUTPUT_PATH.match(path)
        if match:
            # ?download=1 → 浏览器弹「另存为」对话框，用户可以选本地硬盘任意位置；
            # 不带这个参数就是原来的内联预览（图片/视频/音频直接播放）。
            return self._serve_output_file(
                match.group(1),
                request.headers,
                download=bool((query.get("download") or [""])[0].strip()),
            )
        match = _UPLOAD_PATH.match(path)
        if match:
            return self._serve_upload_file(match.group(1))
        raise HttpError(404, f"未找到资源：{path}")

    async def _handle_post(
        self, path: str, request: _Request
    ) -> tuple[int, str, bytes, dict[str, str]]:
        match = _ACTION_PATH.match(path)
        if match:
            task_id, action = match.group(1), match.group(2)
            if action == "cancel":
                record = await self.services.task_service.cancel_task(task_id)
            else:
                record = self.services.task_service.retry_task(task_id)
            return self._json(
                200, _record_to_dict(record, self.output_dir)
            )
        match = _ENGINE_RESTART_PATH.match(path)
        if match:
            engine_id = urllib.parse.unquote(match.group(1))
            try:
                await self.services.engine_manager.restart_engine(engine_id)
            except KeyError:
                raise
            except Exception as exc:  # 引擎启动失败：把原因原样反馈给界面
                raise HttpError(502, f"引擎重启失败：{exc}") from exc
            return self._json(200, {"status": "ok", "engine_id": engine_id})
        if path == "/api/engines/release-idle":
            # 显存不够时的正确顺序：先把自己拉起的空闲引擎 worker 全停掉（显存当场交还），
            # 再重新实测一次；还不够才拦，并且点名是谁占着（按进程实测，不拿 nvidia-smi
            # 虚高的 used 说事）。绝不甩一句「请自行释放显存」就把用户支走。
            try:
                released = await self.services.task_service.release_all_idle_engines()
            except Exception as exc:  # noqa: BLE001 - 释放失败也要继续给出判定依据
                self.services.logger.warning("释放空闲引擎 worker 失败：%s", exc)
                released = ()
            forget_vram_cache()
            snapshot = await self._hardware_async(detailed=True)
            return self._json(
                200,
                {
                    "status": "ok",
                    "released": list(released),
                    "gpu": (snapshot or {}).get("gpu"),
                },
            )
        if path == "/api/agent/command":
            payload = self._parse_json_body(request.body)
            message = payload.get("message")
            if not isinstance(message, str):
                raise HttpError(400, "message 必须是字符串")
            history = payload.get("history", [])
            if not isinstance(history, list):
                raise HttpError(400, "history 必须是消息对象数组")
            action_hint = str(payload.get("action_hint", "") or "")
            ref_image = payload.get("ref_image", "")
            if ref_image not in (None, ""):
                ref_image = self._validate_init_image(ref_image)
            else:
                ref_image = ""
            try:
                # 大模型与语音都会阻塞：放到线程池里，保证任务轮询与页面不被拖慢。
                result = await asyncio.to_thread(
                    self.agent.handle,
                    message,
                    history=history,
                    ref_image=ref_image,
                    action_hint=action_hint,
                )
            except HttpError:
                raise
            except Exception as exc:  # noqa: BLE001 - 界面需要可读回执而非 500 堆栈
                self.logger.exception("智能体处理失败")
                raise HttpError(500, f"智能体处理失败：{exc}") from exc
            return self._json(200, result)
        if path == "/api/settings":
            payload = self._parse_json_body(request.body)
            return self._json(200, self._update_settings(payload))
        if path == "/api/uploads":
            payload = self._parse_json_body(request.body)
            return self._json(201, self._save_upload(payload))
        if path == "/api/tasks":
            payload = self._parse_json_body(request.body)
            record = self._submit_task(payload)
            return self._json(201, _record_to_dict(record, self.output_dir))
        raise HttpError(404, f"未找到接口：{path}")

    # ------------------------------------------------------------- 业务辅助

    def _settings_payload(self) -> dict[str, Any]:
        """产物保存相关设置（两端共用同一份 config.json）。

        产物先写进 output_dir（网页/桌面里即时显示的那一份），再固定复制一份到项目内
        output\\（project_output_dir，用户明确要求的位置），auto_save_dir 非空时最后再
        复制一份到那个自选目录（本地硬盘任意位置），留空＝不做这次额外复制。
        """
        return {
            "output_dir": str(self.output_dir),
            "project_output_dir": str(self.services.paths.project_output_dir),
            "auto_save_dir": str(self.services.config.get("auto_save_dir") or ""),
        }

    def _update_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        raw = payload.get("auto_save_dir")
        if raw is None:
            raise HttpError(400, "请求体需要 auto_save_dir（留空＝关闭自动另存）")
        if not isinstance(raw, str):
            raise HttpError(400, "auto_save_dir 必须是字符串")
        text = raw.strip().strip('"')
        if text:
            candidate = Path(text).expanduser()
            if not candidate.is_absolute():
                raise HttpError(400, "自动另存目录必须是绝对路径，例如 D:\\AI作品")
            try:
                candidate.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise HttpError(400, f"自动另存目录无法创建：{exc}") from exc
            if not candidate.is_dir():
                raise HttpError(400, f"该路径不是文件夹：{candidate}")
            text = str(candidate)
        config = dict(self.services.config)
        config["auto_save_dir"] = text
        try:
            self.services.config_store.save(config)
        except ConfigError as exc:
            raise HttpError(400, str(exc)) from exc
        self.services.config = config
        # 本进程立刻生效；桌面端进程在保存设置时同样会刷新它自己的实例。
        self.services.task_service.set_auto_save_dir(text)
        self.logger.info("自动另存目录已更新：%s", text or "（已关闭）")
        return {"status": "ok", **self._settings_payload()}

    def _parse_json_body(self, body: bytes) -> dict[str, Any]:
        if not body:
            raise HttpError(400, "请求体不能为空")
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HttpError(400, f"请求体不是合法的 JSON：{exc}") from exc
        if not isinstance(payload, dict):
            raise HttpError(400, "请求体必须是 JSON 对象")
        return payload

    def _submit_task(self, payload: dict[str, Any]) -> TaskRecord:
        task_type = payload.get("task_type")
        engine_id = payload.get("engine_id")
        prompt = payload.get("prompt")
        model_id = payload.get("model_id")
        input_files = payload.get("input_files", [])
        parameters = payload.get("parameters", {})
        if not isinstance(task_type, str) or task_type not in _VALID_TASK_TYPES:
            raise HttpError(400, "task_type 必须是 image / video / music 之一")
        if not isinstance(engine_id, str) or not engine_id.strip():
            raise HttpError(400, "engine_id 不能为空")
        if not isinstance(prompt, str):
            raise HttpError(400, "prompt 必须是字符串")
        if model_id is not None and not isinstance(model_id, str):
            raise HttpError(400, "model_id 必须是字符串或 null")
        if not isinstance(input_files, list) or not all(
            isinstance(item, str) for item in input_files
        ):
            raise HttpError(400, "input_files 必须是字符串数组")
        if not isinstance(parameters, dict):
            raise HttpError(400, "parameters 必须是 JSON 对象")
        # 图生图/图生视频垫图必须是本机已存在的具体图片文件：
        # 把目录、不存在的相对路径在进入 worker 之前就挡下，给出明确提示。
        init_image = parameters.get("init_image_path")
        if init_image not in (None, ""):
            parameters["init_image_path"] = self._validate_init_image(init_image)
        request = TaskRequest(
            task_type=task_type,
            engine_id=engine_id.strip(),
            prompt=prompt,
            model_id=model_id,
            input_files=tuple(input_files),
            parameters=parameters,
        )
        return self.services.task_service.submit_task(request)

    def _validate_init_image(self, init_image: Any) -> str:
        """垫图校验：必须是本机已存在的具体图片文件，返回绝对路径字符串。"""
        if not isinstance(init_image, str) or not init_image.strip():
            raise HttpError(400, "init_image_path 必须是非空字符串（垫图文件的本机绝对路径）")
        ref_path = Path(init_image.strip())
        if not ref_path.is_absolute():
            raise HttpError(
                400,
                f"垫图必须是绝对路径，例如 D:\\ChenguAI\\uploads\\photo.png；收到的是：{init_image}",
            )
        if ref_path.is_dir():
            raise HttpError(
                400,
                f"垫图必须选具体图片文件，不能是文件夹：{init_image}",
            )
        if not ref_path.is_file():
            raise HttpError(400, f"垫图文件不存在：{init_image}")
        if ref_path.suffix.lower() not in _VALID_INPUT_IMAGE_SUFFIXES:
            raise HttpError(
                400,
                "垫图格式需为 "
                + " / ".join(sorted(_VALID_INPUT_IMAGE_SUFFIXES))
                + f"；收到的是：{ref_path.name}",
            )
        return str(ref_path)

    # ------------------------------------------------------------- 上传垫图

    def _save_upload(self, payload: dict[str, Any]) -> dict[str, Any]:
        """接收网页端选的本机图片（base64），落盘到上传目录并返回绝对路径。

        图生图 / 图生视频的 worker 读的是文件系统路径，因此必须给出 path；
        url 只用于界面预览。文件名只取 basename，并加时间戳前缀避免互相覆盖。
        """
        raw_name = str(payload.get("name", "") or "")
        data = payload.get("data", "")
        if not isinstance(data, str) or not data:
            raise HttpError(400, "data 必须是图片内容的 base64 字符串")
        safe_name = Path(raw_name.replace("\\", "/")).name
        if not safe_name or safe_name in (".", "..") or "/" in raw_name.replace("\\", "/"):
            raise HttpError(400, f"name 只能是文件名，不能包含路径：{raw_name}")
        suffix = Path(safe_name).suffix.lower()
        if suffix not in _VALID_INPUT_IMAGE_SUFFIXES:
            raise HttpError(
                400,
                "上传图片格式需为 "
                + " / ".join(sorted(_VALID_INPUT_IMAGE_SUFFIXES))
                + f"；收到的是：{safe_name}",
            )
        try:
            blob = base64.b64decode(data, validate=True)
        except (ValueError, TypeError) as exc:
            raise HttpError(400, f"data 不是合法的 base64：{exc}") from exc
        if not blob:
            raise HttpError(400, "上传的图片内容为空")
        if len(blob) > MAX_UPLOAD_RAW_BYTES:
            raise HttpError(413, f"图片超过 {MAX_UPLOAD_RAW_BYTES // (1024 * 1024)}MB，请先缩小")
        signatures = _IMAGE_SIGNATURES.get(suffix, ())
        if signatures and not any(blob.startswith(sign) for sign in signatures):
            raise HttpError(400, f"图片内容与扩展名 {suffix} 不符，可能是改过后缀的文件")
        stem = Path(safe_name).stem[:48] or "image"
        target = self.upload_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-{stem}{suffix}"
        counter = 1
        while target.exists():
            target = self.upload_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-{stem}-{counter}{suffix}"
            counter += 1
        try:
            target.write_bytes(blob)
        except OSError as exc:
            raise HttpError(500, f"保存上传文件失败：{exc}") from exc
        return {
            "path": str(target),
            "name": target.name,
            "url": "/uploads/" + urllib.parse.quote(target.name),
            "bytes": len(blob),
        }

    def _serve_upload_file(self, relative: str) -> tuple[int, str, bytes, dict[str, str]]:
        """预览上传的垫图（限定在上传目录内）。"""
        candidate = self._read_guarded(self.upload_dir, relative)
        content = candidate.read_bytes()
        content_type = _MEDIA_TYPES.get(candidate.suffix.lower(), "application/octet-stream")
        return (
            200,
            content_type,
            content,
            {"Cache-Control": "no-cache", "Content-Disposition": "inline"},
        )

    # ------------------------------------------------------------- 静态资源

    def _read_guarded(
        self, base_dir: Path, relative: str
    ) -> Path:
        try:
            candidate = (base_dir / urllib.parse.unquote(relative)).resolve()
            base = base_dir.resolve()
            candidate.relative_to(base)
        except (OSError, ValueError):
            raise HttpError(403, "禁止访问该路径") from None
        if not candidate.is_file():
            raise HttpError(404, "文件不存在")
        return candidate

    def _serve_static(self, relative: str) -> tuple[int, str, bytes, dict[str, str]]:
        if not relative or ".." in Path(relative).parts:
            raise HttpError(403, "禁止访问该路径")
        try:
            candidate = (STATIC_DIR / relative).resolve()
            candidate.relative_to(STATIC_DIR.resolve())
        except ValueError:
            raise HttpError(403, "禁止访问该路径") from None
        if not candidate.is_file():
            raise HttpError(404, "文件不存在")
        content = candidate.read_bytes()
        content_type = _TEXT_TYPES.get(
            candidate.suffix.lower(), "application/octet-stream"
        )
        return (
            200,
            content_type,
            content,
            {"Cache-Control": "no-cache"},
        )

    def _serve_output_file(
        self,
        relative: str,
        headers: dict[str, str] | None = None,
        download: bool = False,
    ) -> tuple[int, str, bytes, dict[str, str]]:
        candidate = self._read_guarded(self.output_dir, relative)
        content = candidate.read_bytes()
        content_type = _MEDIA_TYPES.get(
            candidate.suffix.lower(), "application/octet-stream"
        )
        if download:
            # RFC 5987：中文文件名要按 UTF-8 百分号转义，否则浏览器另存出来的名字乱码。
            disposition = (
                "attachment; filename*=UTF-8''"
                + urllib.parse.quote(candidate.name, safe="")
            )
        else:
            disposition = "inline"
        extra = {
            "Cache-Control": "no-cache",
            "Content-Disposition": disposition,
            "Accept-Ranges": "bytes",
        }
        # 支持单段 Range 请求：浏览器播放 mp4/wav 时会用 Range 拖动进度条，
        # 只回 200 全量体的话视频无法 seek（Chrome 尤其明显）。
        range_header = ((headers or {}).get("range") or "").strip().lower()
        if not range_header.startswith("bytes=") or range_header == "bytes=0-":
            return (200, content_type, content, extra)
        size = len(content)
        spec = range_header[len("bytes="):].split(",")[0].strip()
        start_text, _, end_text = spec.partition("-")
        try:
            if not start_text:
                if not end_text:
                    raise ValueError("空 Range")
                length = min(int(end_text), size)
                start, end = size - length, size - 1
            else:
                start = int(start_text)
                end = min(int(end_text), size - 1) if end_text else size - 1
        except ValueError:
            raise HttpError(416, f"无法解析 Range：{spec}") from None
        if start < 0 or start >= size or end < start:
            return (
                416,
                content_type,
                b"",
                {**extra, "Content-Range": f"bytes */{size}"},
            )
        partial = content[start:end + 1]
        return (
            206,
            content_type,
            partial,
            {**extra, "Content-Range": f"bytes {start}-{end}/{size}"},
        )

    def _json(
        self, status: int, payload: Any
    ) -> tuple[int, str, bytes, dict[str, str]]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        return (
            status,
            "application/json; charset=utf-8",
            body,
            {"Cache-Control": "no-store"},
        )


class _HttpServerProtocol:
    """极简 HTTP/1.1：逐连接顺序解析请求，支持 keep-alive。"""

    def __init__(
        self,
        app_obj: WebApplication,
        logger: logging.Logger,
    ) -> None:
        self.app = app_obj
        self.logger = logger

    async def serve(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peer = writer.get_extra_info("peername")
        try:
            keep_alive = True
            while keep_alive:
                try:
                    head = await reader.readuntil(b"\r\n\r\n")
                except asyncio.IncompleteReadError:
                    return  # 客户端已关闭连接
                except asyncio.LimitOverrunError:
                    await self._send_error(writer, 431, "请求头过大")
                    return
                if len(head) > MAX_HEADER_BYTES:
                    await self._send_error(writer, 431, "请求头过大")
                    return
                try:
                    request, keep_alive = self._parse_head(head.decode("latin-1"))
                    request.body = await self._read_body(
                        reader, head.decode("latin-1"), request.path
                    )
                except HttpError as exc:
                    await self._send_error(writer, exc.status, exc.message)
                    return
                try:
                    status, content_type, body, extra = await self.app.handle(request)
                except HttpError as exc:
                    status, content_type, body = (
                        exc.status,
                        "application/json; charset=utf-8",
                        json.dumps(
                            {"error": exc.message}, ensure_ascii=False
                        ).encode("utf-8"),
                    )
                    extra = {"Cache-Control": "no-store"}
                except Exception:
                    self.logger.exception("处理请求失败：%s %s", request.method, request.path)
                    status, content_type, body = (
                        500,
                        "application/json; charset=utf-8",
                        json.dumps({"error": "服务器内部错误"}, ensure_ascii=False).encode("utf-8"),
                    )
                    extra = {"Cache-Control": "no-store"}
                await self._send(writer, status, content_type, body, extra, keep_alive)
                self.logger.info("%s %s -> %s", request.method, request.path, status)
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    @staticmethod
    def _parse_head(head_text: str) -> tuple[_Request, bool]:
        lines = head_text.split("\r\n")
        request_line = lines[0]
        parts = request_line.split(" ")
        if len(parts) != 3 or not parts[1].startswith("/"):
            raise HttpError(400, "无法解析请求行")
        method, target = parts[0].upper(), parts[1]
        keep_alive = True
        headers: dict[str, str] = {}
        for line in lines[1:]:
            if not line:
                continue
            name, _, value = line.partition(":")
            headers[name.strip().lower()] = value.strip()
        if headers.get("connection", "").lower() == "close":
            keep_alive = False
        parsed = urllib.parse.urlsplit(target)
        query = urllib.parse.parse_qs(parsed.query)
        path = urllib.parse.unquote(parsed.path)
        return (
            _Request(method=method, path=path, query=query, body=b"", headers=headers),
            keep_alive,
        )

    @staticmethod
    async def _read_body(reader: asyncio.StreamReader, head_text: str, path: str) -> bytes:
        length_value = None
        for line in head_text.split("\r\n")[1:]:
            name, _, value = line.partition(":")
            if name.strip().lower() == "content-length":
                length_value = value.strip()
        if length_value is None:
            return b""
        try:
            length = int(length_value)
        except ValueError as exc:
            raise HttpError(400, "Content-Length 无效") from exc
        # 上传垫图的接口单独放宽上限，其余接口仍保持 256KB。
        limit = MAX_UPLOAD_BYTES if path.startswith("/api/uploads") else MAX_BODY_BYTES
        if length < 0 or length > limit:
            raise HttpError(413, f"请求体过大（上限 {limit // 1024} KB）")
        if length == 0:
            return b""
        return await reader.readexactly(length)

    async def _send(
        self,
        writer: asyncio.StreamWriter,
        status: int,
        content_type: str,
        body: bytes,
        extra: dict[str, str],
        keep_alive: bool,
    ) -> None:
        reason = _REASONS.get(status, "OK")
        headers = [
            f"HTTP/1.1 {status} {reason}",
            f"Content-Type: {content_type}",
            f"Content-Length: {len(body)}",
            f"Connection: {'keep-alive' if keep_alive else 'close'}",
        ]
        headers.extend(f"{name}: {value}" for name, value in extra.items())
        writer.write(("\r\n".join(headers) + "\r\n\r\n").encode("latin-1") + body)
        await writer.drain()

    async def _send_error(
        self, writer: asyncio.StreamWriter, status: int, message: str
    ) -> None:
        body = json.dumps({"error": message}, ensure_ascii=False).encode("utf-8")
        await self._send(
            writer, status, "application/json; charset=utf-8", body, {}, False
        )


_REASONS = {
    200: "OK",
    201: "Created",
    206: "Partial Content",
    400: "Bad Request",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    413: "Payload Too Large",
    416: "Requested Range Not Satisfiable",
    431: "Request Header Fields Too Large",
    500: "Internal Server Error",
    502: "Bad Gateway",
}


async def run_web_server(
    services: WebServices | None = None,
    host: str | None = None,
    port: int | None = None,
) -> None:
    """启动任务后台 worker 与 HTTP 服务，直到收到 Ctrl+C。"""
    services = services or build_services()
    app_obj = WebApplication(services)
    host = host or str(services.config.get("web_host", "127.0.0.1"))
    port = port or int(services.config.get("web_port", 8765))

    await services.task_service.start()
    # 启动清扫：上一代客户端被强行结束（或被杀）时留下的 llama-server 孤儿先收掉。
    # 只认镜像名 llama-server.exe 且父进程已经不在了的那些——别人家的程序一个都不碰。
    try:
        await asyncio.to_thread(app_obj.agent.sweep_orphan_llm)
    except Exception as exc:  # noqa: BLE001 - 清扫失败不影响网页端本身能用
        services.logger.warning("启动清扫大模型孤儿进程失败：%s", exc)
    protocol = _HttpServerProtocol(app_obj, services.logger)
    server = await asyncio.start_server(protocol.serve, host, port)
    services.logger.info("辰鹄AI创作中心 Web 服务已启动：http://%s:%d", host, port)
    try:
        async with server:
            await server.serve_forever()
    finally:
        await services.task_service.shutdown()
        # 退出收尾：网页端关掉，它拉起的大模型服务也得跟着结束，显存当场归还。
        # 被强行杀掉时这段来不及跑，那由内核层护栏（作业对象 + 父进程看门狗）代收。
        try:
            await asyncio.to_thread(app_obj.agent.stop_llm_for_exit)
        except Exception as exc:  # noqa: BLE001 - 收尾失败不能再拖住退出
            services.logger.warning("退出时停止自带大模型服务失败：%s", exc)
        services.logger.info("辰鹄AI创作中心 Web 服务已停止")


def main() -> int:
    logging.raiseExceptions = False
    ensure_utf8_console()
    try:
        asyncio.run(run_web_server())
    except KeyboardInterrupt:
        print("已收到退出信号，服务已停止。")
    except OSError as exc:
        # 端口被占用是「网页端打不开」最常见的样子：给可执行的下一步，而不是甩堆栈。
        if getattr(exc, "winerror", None) in (10048, 10049) or exc.errno in (98, 99):
            print(
                "网页控制台启动失败：端口已被占用，说明已经有一个网页端在跑。\n"
                "  · 想直接用：浏览器打开 http://127.0.0.1:8765 （端口可在 config.json 的 web_port 改）；\n"
                "  · 想用新代码重启：powershell -NoProfile -File scripts\\run_web.ps1 -Restart；\n"
                "  · 想换端口：改 config.json 的 web_port 后重新启动。\n"
                f"原始错误：{exc}"
            )
            return 3
        print(f"网页控制台启动失败：{exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
