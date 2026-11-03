"""Single-worker FIFO task service backed by SQLite."""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.database.database import initialize_database
from app.tasks.engine_manager import EngineManager
from app.tasks.models import TaskRecord, TaskRequest, TaskResult, TaskStatus


_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.QUEUED: frozenset(
        {TaskStatus.PREPARING, TaskStatus.FAILED, TaskStatus.CANCELLED}
    ),
    TaskStatus.PREPARING: frozenset(
        {TaskStatus.RUNNING, TaskStatus.FAILED, TaskStatus.CANCELLED}
    ),
    TaskStatus.RUNNING: frozenset(
        {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}
    ),
    TaskStatus.COMPLETED: frozenset(),
    TaskStatus.FAILED: frozenset(),
    TaskStatus.CANCELLED: frozenset(),
}
_LOGGER = logging.getLogger("chenguai.tasks")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _dump_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


class TaskService:
    def __init__(
        self,
        database_path: Path,
        engine_manager: EngineManager,
        task_timeout_seconds: float = 3600,
    ) -> None:
        if task_timeout_seconds <= 0:
            raise ValueError("task_timeout_seconds 必须大于 0")
        self.database_path = database_path
        self.engine_manager = engine_manager
        self.task_timeout_seconds = task_timeout_seconds
        initialize_database(database_path)
        self._worker_task: asyncio.Task[None] | None = None
        self._active_tasks: dict[str, asyncio.Task[None]] = {}
        self._wake_event: asyncio.Event | None = None
        self._stopping = False

    def submit_task(self, request: TaskRequest) -> TaskRecord:
        self._validate_request(request)
        if not self.engine_manager.has_engine(request.engine_id):
            raise ValueError(f"未注册引擎：{request.engine_id}")
        task_id = uuid.uuid4().hex
        created_at = _now()
        with sqlite3.connect(self.database_path, timeout=10) as connection:
            connection.execute(
                """
                INSERT INTO tasks (
                    task_id, task_type, engine_id, model_id, prompt,
                    input_files_json, parameters_json, status, progress,
                    created_at, output_files_json, result_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, '[]', '{}')
                """,
                (
                    task_id,
                    request.task_type,
                    request.engine_id,
                    request.model_id,
                    request.prompt,
                    _dump_json(request.input_files),
                    _dump_json(request.parameters),
                    TaskStatus.QUEUED.value,
                    created_at,
                ),
            )
        if self._wake_event is not None:
            self._wake_event.set()
        record = self.get_task(task_id)
        assert record is not None
        return record

    @staticmethod
    def _validate_request(request: TaskRequest) -> None:
        if request.task_type not in {"image", "video", "music"}:
            raise ValueError("task_type 必须是 image、video 或 music")
        if not request.engine_id.strip():
            raise ValueError("engine_id 不能为空")
        if not request.prompt.strip():
            raise ValueError("prompt 不能为空")
        if not isinstance(request.parameters, dict):
            raise ValueError("parameters 必须是 JSON 对象")
        _dump_json(request.parameters)
        _dump_json(request.input_files)

    def get_task(self, task_id: str) -> TaskRecord | None:
        with sqlite3.connect(self.database_path, timeout=10) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
        return self._record_from_row(row) if row is not None else None

    def list_tasks(
        self,
        status: TaskStatus | None = None,
        limit: int = 100,
    ) -> tuple[TaskRecord, ...]:
        if not 1 <= limit <= 1000:
            raise ValueError("limit 必须在 1 到 1000 之间")
        with sqlite3.connect(self.database_path, timeout=10) as connection:
            connection.row_factory = sqlite3.Row
            if status is None:
                rows = connection.execute(
                    "SELECT * FROM tasks ORDER BY created_at DESC, rowid DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM tasks WHERE status = ? "
                    "ORDER BY created_at DESC, rowid DESC LIMIT ?",
                    (status.value, limit),
                ).fetchall()
        return tuple(self._record_from_row(row) for row in rows)

    @staticmethod
    def _record_from_row(row: sqlite3.Row) -> TaskRecord:
        return TaskRecord(
            task_id=row["task_id"],
            request=TaskRequest(
                task_type=row["task_type"],
                engine_id=row["engine_id"],
                model_id=row["model_id"],
                prompt=row["prompt"],
                input_files=tuple(json.loads(row["input_files_json"])),
                parameters=json.loads(row["parameters_json"]),
            ),
            status=TaskStatus(row["status"]),
            created_at=row["created_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            progress=row["progress"],
            output_files=tuple(json.loads(row["output_files_json"])),
            error_message=row["error_message"],
            result_metadata=json.loads(row["result_json"]),
            retry_of=row["retry_of"],
        )

    def _oldest_queued_task_id(self) -> str | None:
        with sqlite3.connect(self.database_path, timeout=10) as connection:
            row = connection.execute(
                "SELECT task_id FROM tasks WHERE status = ? "
                "ORDER BY created_at, rowid LIMIT 1",
                (TaskStatus.QUEUED.value,),
            ).fetchone()
        return row[0] if row is not None else None

    async def start(self) -> None:
        if self._worker_task is not None and not self._worker_task.done():
            return
        self._stopping = False
        self.recover_interrupted_tasks()
        self._wake_event = asyncio.Event()
        self._worker_task = asyncio.create_task(
            self._worker_loop(), name="chenguai-task-worker"
        )
        self._wake_event.set()

    def recover_interrupted_tasks(self) -> None:
        finished_at = _now()
        with sqlite3.connect(self.database_path, timeout=10) as connection:
            connection.execute(
                """
                UPDATE tasks
                SET status = ?, finished_at = ?, progress = NULL,
                    error_message = ?
                WHERE status IN (?, ?)
                """,
                (
                    TaskStatus.FAILED.value,
                    finished_at,
                    "应用上次退出时任务仍在执行；任务已停止，用户可重试。",
                    TaskStatus.PREPARING.value,
                    TaskStatus.RUNNING.value,
                ),
            )

    async def _worker_loop(self) -> None:
        assert self._wake_event is not None
        while not self._stopping:
            task_id = self._oldest_queued_task_id()
            if task_id is None:
                self._wake_event.clear()
                if self._oldest_queued_task_id() is not None:
                    continue
                await self._wake_event.wait()
                continue

            runner = asyncio.create_task(
                self._process_task(task_id), name=f"task-{task_id}"
            )
            self._active_tasks[task_id] = runner
            try:
                await runner
            except asyncio.CancelledError:
                if self._stopping:
                    break
            except Exception as exc:
                _LOGGER.exception("任务 worker 捕获未处理异常 task_id=%s", task_id)
                record = self.get_task(task_id)
                if record is not None and record.status in {
                    TaskStatus.QUEUED,
                    TaskStatus.PREPARING,
                    TaskStatus.RUNNING,
                }:
                    self._transition(
                        task_id,
                        TaskStatus.FAILED,
                        finished_at=_now(),
                        error_message=f"WorkerError: {exc}"[:2000],
                    )
            finally:
                self._active_tasks.pop(task_id, None)

    async def _process_task(self, task_id: str) -> None:
        task = self.get_task(task_id)
        if task is None or task.status is not TaskStatus.QUEUED:
            return
        adapter = None
        try:
            self._transition(task_id, TaskStatus.PREPARING)
            adapter = await self.engine_manager.start_engine(task.request.engine_id)
            if task.request.task_type not in adapter.task_types:
                raise ValueError(
                    f"引擎 {adapter.engine_id} 不支持任务类型 {task.request.task_type}"
                )
            await adapter.validate_request(task.request)
            self._transition(task_id, TaskStatus.RUNNING, started_at=_now())

            async with asyncio.timeout(self.task_timeout_seconds):
                result = await adapter.generate(task.request)
            self._validate_result(adapter, result)
            finished_at = _now()
            with sqlite3.connect(self.database_path, timeout=10) as connection:
                connection.execute(
                    "UPDATE tasks SET status = ?, finished_at = ?, progress = NULL, "
                    "output_files_json = ?, result_json = ?, error_message = NULL "
                    "WHERE task_id = ? AND status = ?",
                    (
                        TaskStatus.COMPLETED.value,
                        finished_at,
                        _dump_json(result.output_files),
                        _dump_json(result.metadata),
                        task_id,
                        TaskStatus.RUNNING.value,
                    ),
                )
        except asyncio.CancelledError:
            self._transition(
                task_id,
                TaskStatus.CANCELLED,
                finished_at=_now(),
                error_message="任务已取消",
            )
            if adapter is not None:
                await self._stop_adapter(adapter.engine_id)
            raise
        except TimeoutError:
            self._transition(
                task_id,
                TaskStatus.FAILED,
                finished_at=_now(),
                error_message=f"任务超过 {self.task_timeout_seconds:g} 秒超时限制",
            )
            if adapter is not None:
                self.engine_manager.mark_failed(adapter.engine_id, "任务执行超时")
                await self._stop_adapter(adapter.engine_id)
        except Exception as exc:
            self._transition(
                task_id,
                TaskStatus.FAILED,
                finished_at=_now(),
                error_message=f"{type(exc).__name__}: {exc}"[:2000],
            )
            if adapter is not None:
                self.engine_manager.mark_failed(adapter.engine_id, str(exc))
                await self._stop_adapter(adapter.engine_id)

    async def _stop_adapter(self, engine_id: str) -> None:
        try:
            await self.engine_manager.stop_engine(engine_id)
        except Exception:
            _LOGGER.exception("停止引擎失败 engine_id=%s", engine_id)

    @staticmethod
    def _validate_result(adapter, result: TaskResult) -> None:
        if not isinstance(result, TaskResult):
            raise TypeError("引擎必须返回 TaskResult")
        if adapter.test_only != result.test_only:
            raise ValueError("测试适配器与结果的 test_only 标记不一致")
        if not adapter.test_only and not result.output_files:
            raise ValueError("真实引擎必须返回至少一个输出文件")
        for output_file in result.output_files:
            path = Path(output_file)
            if not path.is_file():
                raise FileNotFoundError(f"引擎输出文件不存在：{output_file}")

    def _transition(
        self,
        task_id: str,
        target: TaskStatus,
        *,
        started_at: str | None = None,
        finished_at: str | None = None,
        error_message: str | None = None,
    ) -> None:
        current = self.get_task(task_id)
        if current is None:
            raise KeyError(f"任务不存在：{task_id}")
        if target not in _TRANSITIONS[current.status]:
            raise RuntimeError(
                f"非法任务状态转换：{current.status.value} -> {target.value}"
            )
        with sqlite3.connect(self.database_path, timeout=10) as connection:
            connection.execute(
                "UPDATE tasks SET status = ?, started_at = COALESCE(?, started_at), "
                "finished_at = COALESCE(?, finished_at), error_message = ?, "
                "progress = NULL WHERE task_id = ? AND status = ?",
                (
                    target.value,
                    started_at,
                    finished_at,
                    error_message,
                    task_id,
                    current.status.value,
                ),
            )

    async def cancel_task(self, task_id: str) -> TaskRecord:
        task = self.get_task(task_id)
        if task is None:
            raise KeyError(f"任务不存在：{task_id}")
        if task.status is TaskStatus.QUEUED:
            self._transition(
                task_id,
                TaskStatus.CANCELLED,
                finished_at=_now(),
                error_message="任务在开始前取消",
            )
        elif task.status in {TaskStatus.PREPARING, TaskStatus.RUNNING}:
            runner = self._active_tasks.get(task_id)
            if runner is not None:
                runner.cancel()
                try:
                    await runner
                except asyncio.CancelledError:
                    pass
            else:
                self._transition(
                    task_id,
                    TaskStatus.FAILED,
                    finished_at=_now(),
                    error_message="找不到任务执行器；任务已停止",
                )
        final_record = self.get_task(task_id)
        assert final_record is not None
        return final_record

    def retry_task(self, task_id: str) -> TaskRecord:
        original = self.get_task(task_id)
        if original is None:
            raise KeyError(f"任务不存在：{task_id}")
        if original.status not in {TaskStatus.FAILED, TaskStatus.CANCELLED}:
            raise ValueError("只有失败或已取消的任务可以重试")
        retried = self.submit_task(original.request)
        with sqlite3.connect(self.database_path, timeout=10) as connection:
            connection.execute(
                "UPDATE tasks SET retry_of = ? WHERE task_id = ?",
                (original.task_id, retried.task_id),
            )
        updated = self.get_task(retried.task_id)
        assert updated is not None
        return updated

    async def wait_until_idle(self, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            pending = self._oldest_queued_task_id() is not None
            if not pending and not self._active_tasks:
                return
            await asyncio.sleep(0.005)
        raise TimeoutError("任务队列在等待时间内未清空")

    async def shutdown(self) -> None:
        self._stopping = True
        if self._wake_event is not None:
            self._wake_event.set()
        for runner in tuple(self._active_tasks.values()):
            runner.cancel()
        if self._worker_task is not None:
            try:
                await asyncio.wait_for(self._worker_task, timeout=10)
            except asyncio.TimeoutError:
                self._worker_task.cancel()
                try:
                    await self._worker_task
                except asyncio.CancelledError:
                    pass
        await self.engine_manager.shutdown()