"""JSON-lines IPC adapter for an isolated engine worker process."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Sequence

from app.tasks.models import TaskRequest, TaskResult


class EngineProcessError(RuntimeError):
    """Raised when an isolated engine process exits or violates its protocol."""


class ProcessEngineAdapter:
    """Adapter whose engine implementation runs in its own Python process.

    ``command`` must be a trusted, application-defined argument vector. User input
    is sent as JSON over stdin and is never interpreted as a shell command.
    """

    def __init__(
        self,
        engine_id: str,
        command: Sequence[str],
        task_types: frozenset[str],
        test_only: bool = False,
        cwd: Path | None = None,
        request_timeout_seconds: float = 30.0,
        shutdown_timeout_seconds: float = 3.0,
    ) -> None:
        if not command or any(not part for part in command):
            raise ValueError("engine process command must be a non-empty argv")
        self.engine_id = engine_id
        self.command = tuple(command)
        self.task_types = task_types
        self.test_only = test_only
        self.cwd = cwd
        self.request_timeout_seconds = request_timeout_seconds
        self.shutdown_timeout_seconds = shutdown_timeout_seconds
        self._process: asyncio.subprocess.Process | None = None
        self._sequence = 0
        self._lock = asyncio.Lock()

    @property
    def process_id(self) -> int | None:
        return self._process.pid if self._process is not None else None

    async def health_check(self) -> tuple[bool, str]:
        try:
            response = await self._request("health_check")
        except (OSError, EngineProcessError, TimeoutError) as exc:
            return False, str(exc)
        if not isinstance(response, list) or len(response) != 2:
            return False, "engine health_check returned an invalid response"
        return bool(response[0]), str(response[1])

    async def validate_request(self, request: TaskRequest) -> None:
        await self._request("validate_request", self._request_to_json(request))

    async def generate(self, request: TaskRequest) -> TaskResult:
        try:
            response = await self._request("generate", self._request_to_json(request))
        except asyncio.CancelledError:
            await self.shutdown()
            raise
        if not isinstance(response, dict):
            raise EngineProcessError("engine generate returned an invalid response")
        try:
            return TaskResult(
                output_files=tuple(response.get("output_files", ())),
                metadata=response.get("metadata", {}),
                test_only=bool(response.get("test_only", False)),
            )
        except (TypeError, ValueError) as exc:
            raise EngineProcessError("engine generate returned malformed metadata") from exc

    @staticmethod
    def _request_to_json(request: TaskRequest) -> dict[str, object]:
        return {
            "task_type": request.task_type,
            "engine_id": request.engine_id,
            "model_id": request.model_id,
            "prompt": request.prompt,
            "input_files": list(request.input_files),
            "parameters": request.parameters,
        }

    async def _ensure_process(self) -> asyncio.subprocess.Process:
        process = self._process
        if process is not None and process.returncode is None:
            return process
        try:
            process = await asyncio.create_subprocess_exec(
                *self.command,
                cwd=str(self.cwd) if self.cwd else None,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except OSError as exc:
            raise EngineProcessError(f"无法启动引擎子进程：{exc}") from exc
        self._process = process
        return process

    async def _request(self, method: str, payload: object = None) -> object:
        async with self._lock:
            process = await self._ensure_process()
            if process.stdin is None or process.stdout is None:
                raise EngineProcessError("engine process pipes are unavailable")
            self._sequence += 1
            request_id = self._sequence
            message = json.dumps(
                {"id": request_id, "method": method, "payload": payload},
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8") + b"\n"
            try:
                process.stdin.write(message)
                await process.stdin.drain()
                response_line = await asyncio.wait_for(
                    process.stdout.readline(), timeout=self.request_timeout_seconds
                )
            except TimeoutError as exc:
                await self._terminate_locked(process)
                raise EngineProcessError(
                    f"引擎 IPC {method} 请求超时"
                ) from exc
            except (BrokenPipeError, ConnectionError) as exc:
                await self._terminate_locked(process)
                raise EngineProcessError(f"引擎 IPC 连接失败：{exc}") from exc

            if not response_line:
                exit_code = await process.wait()
                self._process = None
                raise EngineProcessError(
                    f"引擎子进程意外退出，exit code={exit_code}"
                )
            try:
                response = json.loads(response_line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                await self._terminate_locked(process)
                raise EngineProcessError("引擎子进程输出了无效 IPC 数据") from exc
            if response.get("id") != request_id:
                raise EngineProcessError("engine IPC response id mismatch")
            if not response.get("ok"):
                error = response.get("error", "unknown engine error")
                raise EngineProcessError(str(error)[:2000])
            return response.get("result")

    async def _terminate_locked(
        self,
        process: asyncio.subprocess.Process,
    ) -> None:
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=self.shutdown_timeout_seconds)
            except TimeoutError:
                process.kill()
                await process.wait()
        if self._process is process:
            self._process = None

    async def shutdown(self) -> None:
        async with self._lock:
            process = self._process
            if process is None:
                return
            if process.returncode is None and process.stdin is not None:
                self._sequence += 1
                message = json.dumps(
                    {"id": self._sequence, "method": "shutdown", "payload": None}
                ).encode("utf-8") + b"\n"
                try:
                    process.stdin.write(message)
                    await process.stdin.drain()
                    await asyncio.wait_for(
                        process.wait(), timeout=self.shutdown_timeout_seconds
                    )
                except (BrokenPipeError, ConnectionError, TimeoutError):
                    await self._terminate_locked(process)
            if self._process is process:
                self._process = None