"""JSON-lines IPC adapter for an isolated engine worker process."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Sequence

from app.core.process_guard import assign_pid_to_kill_job
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
        env: object = None,
        stderr_log_path: Path | None = None,
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
        # 每个引擎自己的环境变量：worker 用 CHENGUAI_VIDEO_ENGINE_ID 之类的变量
        # 决定自己是哪个引擎，如果靠主进程的全局 os.environ 传递，两个视频引擎
        # （wan-t2v / wan-i2v）会互相覆盖，崩溃后透明重启的 worker 可能变成
        # 另一个引擎。这里改成 spawn 时才合并的一次性字典。
        self.env = dict(env) if env else None
        self.stderr_log_path = stderr_log_path
        self._stderr_handle = None
        self._process: asyncio.subprocess.Process | None = None
        self._sequence = 0
        self._lock = asyncio.Lock()

    @property
    def process_id(self) -> int | None:
        return self._process.pid if self._process is not None else None

    def abort_now(self) -> None:
        """立刻终止 worker 子进程（不拿锁、不给优雅退出的机会）。

        取消一段正在进行的 GPU 生成时，shutdown() 走不通：它要拿同一把 IPC 锁，
        而锁正被这条 generate 占着，只能等生成结束。终止进程之后 readline() 读到
        EOF，_request 走「引擎子进程意外退出」分支抛 EngineProcessError，锁随之
        释放，显存当场归还。
        """
        process = self._process
        if process is not None and process.returncode is None:
            process.terminate()

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
        stderr_target: object = asyncio.subprocess.DEVNULL
        handle = None
        if self.stderr_log_path is not None:
            # worker 的 traceback 以前直接进 DEVNULL：CUDA OOM、权重缺失、
            # tokenizers 编码错误等真实原因全部看不见，UI 只剩一句
            # 「引擎子进程意外退出」。落到日志文件才能定位。
            try:
                self.stderr_log_path.parent.mkdir(parents=True, exist_ok=True)
                handle = self.stderr_log_path.open("ab")
                stderr_target = handle
            except OSError:
                handle = None
        try:
            process = await asyncio.create_subprocess_exec(
                *self.command,
                cwd=str(self.cwd) if self.cwd else None,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=stderr_target,
                env={**os.environ, **(self.env or {}), "CHENGUAI_PARENT_PID": str(os.getpid())},
            )
        except OSError as exc:
            if handle is not None:
                handle.close()
            raise EngineProcessError(f"无法启动引擎子进程：{exc}") from exc
        if self._stderr_handle is not None:
            self._stderr_handle.close()
        self._stderr_handle = handle
        self._process = process
        # 护栏第一层：把 worker 挂进「本进程一结束就被内核收掉」的作业对象。生成途中的
        # worker 卡在 generate 里，既读不到 stdin 关闭的 EOF，也没人来得及调 shutdown()，
        # 显存就一直被占着。挂进作业之后，哪怕我们被任务管理器强行结束，内核也会替我们收掉它。
        assign_pid_to_kill_job(process.pid)
        return process

    async def _request(self, method: str, payload: object = None) -> object:
        async with self._lock:
            process = await self._ensure_process()
            if process.stdin is None or process.stdout is None:
                raise EngineProcessError("engine process pipes are unavailable")
            self._sequence += 1
            request_id = self._sequence
            # ensure_ascii=True：把提示词里的中文/emoji 一律转义成 \uXXXX，帧内字节全为
            # ASCII，任何一侧的流编码配错都不会再破坏内容（纵深防御；真正的编码约定
            # 由子进程 stdin/stdout 显式 UTF-8 保证）。语义不变，json 会自动还原。
            message = json.dumps(
                {"id": request_id, "method": method, "payload": payload},
                ensure_ascii=True,
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
                # 帧错位（读到上一条的响应或残留帧）：这个进程的协议已经失步，
                # 留着它只会一直返回错的答案。终止进程，下一次请求由
                # _ensure_process 透明重启一个新的 worker。
                await self._terminate_locked(process)
                raise EngineProcessError(
                    f"引擎 IPC 响应序号不匹配（期望 {request_id}，收到 "
                    f"{response.get('id')}）；已终止 worker，下次请求会自动重启"
                )
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