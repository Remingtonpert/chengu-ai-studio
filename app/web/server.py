"""辰鹄AI创作中心 本地 Web 服务：纯标准库 asyncio HTTP 服务器。

与 Qt 桌面端共用同一套服务对象（ConfigStore / EngineManager / TaskService），
只监听本机地址（默认 127.0.0.1:8765），不引入任何第三方依赖。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from app.core.config import ConfigError, ConfigStore
from app.core.logging_setup import configure_logging
from app.core.paths import AppPaths
from app.engine_registration import register_production_engines
from app.tasks.engine_manager import EngineManager
from app.tasks.models import TaskRecord, TaskRequest, TaskStatus
from app.tasks.service import TaskService

STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_BODY_BYTES = 256 * 1024
MAX_HEADER_BYTES = 16 * 1024

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
}

_STATUS_PATH = re.compile(r"^/api/tasks/([^/]+)$")
_ACTION_PATH = re.compile(r"^/api/tasks/([^/]+)/(cancel|retry)$")
_ENGINE_RESTART_PATH = re.compile(r"^/api/engines/([^/]+)/restart$")
_OUTPUT_PATH = re.compile(r"^/outputs/(.+)$")


def _hardware_snapshot() -> dict[str, Any]:
    """只读硬件快照：NVIDIA 显卡显存（nvidia-smi）与物理内存（GlobalMemoryStatusEx）。

    为 AI 音乐提交前置检查（显存门槛拦截）与启动自检展示服务的纯信息端点，
    不参与任何生成/排队逻辑。nvidia-smi 不可用（无 NVIDIA 显卡）时如实返回
    gpu=None，前端据此诚实提示，绝不伪造数值。
    """
    gpu: dict[str, Any] | None = None
    try:
        proc = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.free",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=8,
            check=True,
        )
        line = (proc.stdout.strip().splitlines() or [""])[0]
        name, total_mb, free_mb = [part.strip() for part in line.split(",")[:3]]
        gpu = {
            "name": name,
            "memory_total_mb": int(total_mb),
            "memory_free_mb": int(free_mb),
        }
    except Exception:  # noqa: BLE001 - 探测失败时如实降级
        gpu = None

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
# 垫图（init_image_path）允许的扩展名；worker 侧 flux 解码同样按这些格式校验。
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

    # ------------------------------------------------------------------ 路由

    async def handle(self, request: _Request) -> tuple[int, str, bytes, dict[str, str]]:
        method, path = request.method, request.path
        try:
            if method == "GET":
                return self._handle_get(path, request.query)
            if method == "POST":
                return await self._handle_post(path, request)
            raise HttpError(405, "仅支持 GET 与 POST")
        except HttpError:
            raise
        except KeyError as exc:
            raise HttpError(404, str(exc).strip("'")) from exc
        except ValueError as exc:
            raise HttpError(400, str(exc)) from exc

    def _handle_get(
        self, path: str, query: dict[str, list[str]]
    ) -> tuple[int, str, bytes, dict[str, str]]:
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
        if path == "/api/hardware":
            return self._json(200, _hardware_snapshot())
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
            return self._serve_output_file(match.group(1))
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
        if path == "/api/tasks":
            payload = self._parse_json_body(request.body)
            record = self._submit_task(payload)
            return self._json(201, _record_to_dict(record, self.output_dir))
        raise HttpError(404, f"未找到接口：{path}")

    # ------------------------------------------------------------- 业务辅助

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
            parameters["init_image_path"] = str(ref_path)
        request = TaskRequest(
            task_type=task_type,
            engine_id=engine_id.strip(),
            prompt=prompt,
            model_id=model_id,
            input_files=tuple(input_files),
            parameters=parameters,
        )
        return self.services.task_service.submit_task(request)

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

    def _serve_output_file(self, relative: str) -> tuple[int, str, bytes, dict[str, str]]:
        candidate = self._read_guarded(self.output_dir, relative)
        content = candidate.read_bytes()
        content_type = _MEDIA_TYPES.get(
            candidate.suffix.lower(), "application/octet-stream"
        )
        return (
            200,
            content_type,
            content,
            {"Cache-Control": "no-cache", "Content-Disposition": "inline"},
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
                    request.body = await self._read_body(reader, head.decode("latin-1"))
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
        return _Request(method=method, path=path, query=query, body=b""), keep_alive

    @staticmethod
    async def _read_body(reader: asyncio.StreamReader, head_text: str) -> bytes:
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
        if length < 0 or length > MAX_BODY_BYTES:
            raise HttpError(413, "请求体过大")
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
    400: "Bad Request",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    413: "Payload Too Large",
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
    protocol = _HttpServerProtocol(app_obj, services.logger)
    server = await asyncio.start_server(protocol.serve, host, port)
    services.logger.info("辰鹄AI创作中心 Web 服务已启动：http://%s:%d", host, port)
    try:
        async with server:
            await server.serve_forever()
    finally:
        await services.task_service.shutdown()
        services.logger.info("辰鹄AI创作中心 Web 服务已停止")


def main() -> int:
    logging.raiseExceptions = False
    try:
        asyncio.run(run_web_server())
    except KeyboardInterrupt:
        print("已收到退出信号，服务已停止。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
