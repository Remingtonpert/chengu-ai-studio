"""Single-worker FIFO task service backed by SQLite."""

from __future__ import annotations

import asyncio
import json
import logging
import msvcrt
import os
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.artifacts import copy_artifacts
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

# ---- 空闲收割：谁拉起的引擎，谁就负责让它退场 ----------------------------------
# 三分钟没有任何任务（生成、聊天、文档都算）就把还挂着 worker 子进程的引擎全部停掉，
# 显存当场归还；同时叫一声注册进来的空闲钩子（智能体用它卸载自带大模型服务）。
# 这里只结束自家 worker 进程与它加载的模型，在岗引擎的模型与生成逻辑一行都不动。
# 2026-11-09：原来定的是十分钟（600 秒），单卡显存金贵，用户定的档是三分钟。
ENGINE_IDLE_LIMIT_SECONDS = 180.0
IDLE_REAPER_POLL_SECONDS = 30.0


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
        # 跨进程「唯一任务执行者」锁：桌面端与 Web 控制台共用同一个 SQLite
        # 队列时，只有先持有锁的进程运行 worker 循环，另一个进程只提交任务，
        # 队列会在 5 秒内被执行者领走（见 _worker_loop 的超时轮询）。
        self._executor_lock_handle = None
        self._executor_active = False
        # 跨进程取消：本进程正在执行的任务被另一端（Web 控制台或桌面端）改成
        # cancelled 时，_watch_external_cancel 记下这个标记并中止 worker，
        # _process_task 的失败分支据此把「中止产生的异常」定性为已取消而不是失败。
        self._externally_cancelled: set[str] = set()
        # 执行期间轮询任务状态的间隔（秒）：足够快释放显存，代价是每 1.5 秒一次
        # 极小的只读查询。
        self._cancel_poll_seconds = 1.5
        # 自动另存目录（配置项 auto_save_dir）：留空＝不做额外复制，
        # 非空＝每次生成完成后再复制一份到该目录（用户可以选本地硬盘任意位置）。
        # 启动时由 bootstrap / Web 控制台写入，设置页改完即时生效（两端共用同一份配置）。
        self.auto_save_dir = ""
        # 「工具内 output\」＝项目根（便携包则是代码包根）下的 output 目录。用户明确
        # 要求产物除了工具里即时显示，还要固定存一份到这里，哪怕 output_dir 被改到别的
        # 盘；产物本来就写在这个目录时复制会自动跳过，不会生成 art-1.png 这种重复文件。
        self.project_output_dir = ""
        # 空闲收割（见模块顶部常量）：最后一次「有任务」的时间戳 + 注册进来的空闲钩子。
        # 每个进程只收割自己拉起的引擎与自己的大模型服务，桌面端与网页端同时开着也互不干扰。
        self._last_task_activity = time.monotonic()
        self._idle_hooks: list[Any] = []
        self._reaper_task: asyncio.Task[None] | None = None

    def register_idle_hook(self, callback: Any) -> None:
        """注册「空闲到点」的回调（参数＝已空闲秒数），返回本次做的事的说明文字。

        智能体用它把自带大模型服务在空闲十分钟时卸载掉。注册时若已经空闲过一轮，
        下次收割照样叫到（钩子列表只在 shutdown 时清空）。
        """
        if callable(callback) and callback not in self._idle_hooks:
            self._idle_hooks.append(callback)

    def set_auto_save_dir(self, dest_dir: str) -> None:
        """更新自动另存目录；传空字符串表示关掉（不额外复制）。"""
        self.auto_save_dir = str(dest_dir or "").strip()

    def set_project_output_dir(self, dest_dir: str) -> None:
        """更新「工具内 output\」镜像目录（一般由启动流程固定写入，不用用户配置）。"""
        self.project_output_dir = str(dest_dir or "").strip()

    def _copy_to_extra_dirs(self, output_files: list[str]) -> list[dict[str, str]]:
        """按「工具内 output\」→ 自选自动另存目录依次复制（去重、失败不牵连任务）。"""
        entries: list[tuple[str, str]] = []
        for raw in (self.project_output_dir, self.auto_save_dir):
            text = str(raw or "").strip()
            if not text:
                continue
            key = os.path.normcase(os.path.abspath(text))
            if not any(key == seen for seen, _ in entries):
                entries.append((key, text))
        saved: list[dict[str, str]] = []
        for _key, text in entries:
            saved.extend(copy_artifacts(output_files, text))
        return saved

    def _executor_lock_path(self) -> Path:
        return Path(str(self.database_path) + ".worker.lock")

    def _acquire_executor_lock(self) -> bool:
        """非阻塞尝试持有执行者锁；进程退出（含崩溃）时操作系统自动释放。"""
        if self._executor_lock_handle is not None:
            return True
        try:
            handle = open(self._executor_lock_path(), "a+b")
        except OSError:
            _LOGGER.exception("无法创建任务执行者锁文件")
            return False
        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            handle.close()
            return False
        handle.seek(0)
        handle.write(str(os.getpid()).encode("ascii"))
        handle.flush()
        self._executor_lock_handle = handle
        return True

    def _release_executor_lock(self) -> None:
        handle = self._executor_lock_handle
        if handle is None:
            return
        self._executor_lock_handle = None
        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        handle.close()

    def submit_task(self, request: TaskRequest) -> TaskRecord:
        self._validate_request(request)
        if not self.engine_manager.has_engine(request.engine_id):
            raise ValueError(f"未注册引擎：{request.engine_id}")
        # 有任务＝有活动：空闲收割的十分钟从这里重新数。
        self._last_task_activity = time.monotonic()
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
        # 2026-11-09 重新选型后恢复三类任务：图片走 Wan2.1 1.3B 单帧（Apache-2.0、
        # UMT5-XXL 原生中英文、与视频共用权重、6GB 档逐层换入策略同视频——视频档
        # 真机实测峰值约 2GB，单帧 token 只有其零头），音乐走 ACE-Step 1.5
        # （MIT、原生中英文歌词、LM 与 DiT 全部 CPU 换入，目标 6GB 档）。
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
        # 空闲收割循环两条路都要跑：即使本进程不是队列执行者（另一个客户端正在跑任务），
        # 它自己拉起的大模型服务与引擎 worker 一样得在空闲十分钟后交还显存。
        if self._reaper_task is None or self._reaper_task.done():
            self._reaper_task = asyncio.create_task(
                self._idle_reaper_loop(), name="chenguai-idle-reaper"
            )
        if not self._acquire_executor_lock():
            self._executor_active = False
            _LOGGER.warning(
                "另一个进程（Web 控制台或另一实例）正在执行任务队列；"
                "本进程只提交与查看任务，队列会在 5 秒内被执行者领走。"
            )
            return
        self._executor_active = True
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
                try:
                    await asyncio.wait_for(self._wake_event.wait(), timeout=5.0)
                except TimeoutError:
                    # 超时唤醒：定期重新扫描队列，使另一个进程提交的
                    # 任务（桌面端或另一控制台）最迟 5 秒内被本执行者领走。
                    pass
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

    # ------------------------------------------------------------ 空闲收割
    async def _idle_reaper_loop(self) -> None:
        """每 30 秒看一眼：十分钟没任务了就把空闲引擎与自带大模型整个收掉。"""
        while not self._stopping:
            try:
                await asyncio.sleep(IDLE_REAPER_POLL_SECONDS)
            except asyncio.CancelledError:
                return
            if self._stopping:
                return
            if self._active_tasks:
                # 本进程正在跑任务：显存是任务在用，不算空闲。
                self._last_task_activity = time.monotonic()
                continue
            if self._oldest_queued_task_id() is not None:
                # 队列里还有货（可能是另一个进程刚提交的）：马上要用，别去拆台。
                self._last_task_activity = time.monotonic()
                continue
            idle = time.monotonic() - self._last_task_activity
            if idle < ENGINE_IDLE_LIMIT_SECONDS:
                continue
            await self._reap_idle(idle)

    async def _reap_idle(self, idle_seconds: float) -> None:
        """释放本进程所有空闲引擎 worker（当场交还显存），再叫一遍注册进来的空闲钩子。"""
        self._last_task_activity = time.monotonic()
        try:
            released = await self.engine_manager.release_idle_engines(None)  # type: ignore[arg-type]
        except Exception:  # noqa: BLE001 - 这一轮没收成，下一轮 30 秒后再来
            _LOGGER.exception("空闲收割引擎 worker 失败")
            released = ()
        notes: list[str] = []
        for hook in list(self._idle_hooks):
            try:
                note = hook(idle_seconds)
            except Exception:  # noqa: BLE001 - 一个钩子失败不牵连别的钩子与队列
                _LOGGER.exception("空闲钩子执行失败")
                continue
            if note:
                notes.append(str(note))
        if released or notes:
            _LOGGER.info(
                "空闲 %.0f 分钟没有任务：显存已交还（引擎 worker：%s%s）",
                idle_seconds / 60.0,
                ", ".join(released) if released else "无（本来就没有常驻的）",
                "；" + "；".join(notes) if notes else "",
            )

    async def release_all_idle_engines(self) -> tuple[str, ...]:
        """立刻释放本进程所有空闲引擎 worker。

        显存判定不够时，界面前端先调它让路（把上一个任务留下的「热缓存」引擎停掉），
        再重新探一次显存——先自己动手清干净，判定还不够才提示用户，而不是张口就叫用户
        去双击脚本。测试适配器没有 process_id，不会被这里动到。
        """
        self._last_task_activity = time.monotonic()
        return await self.engine_manager.release_idle_engines(None)  # type: ignore[arg-type]

    def _claim_task(self, task_id: str) -> bool:
        """跨进程原子领取排队任务：UPDATE ... WHERE status='queued' 只成功一次。

        桌面端与 Web 控制台同时运行时，同一个任务不可能被两个进程重复执行；
        领取失败（另一进程已领走或任务刚被取消）就诚实跳过。
        """
        with sqlite3.connect(self.database_path, timeout=10) as connection:
            cursor = connection.execute(
                "UPDATE tasks SET status = ? WHERE task_id = ? AND status = ?",
                (TaskStatus.PREPARING.value, task_id, TaskStatus.QUEUED.value),
            )
            return cursor.rowcount == 1

    async def _process_task(self, task_id: str) -> None:
        if not self._claim_task(task_id):
            # 已被另一进程领取（或刚被取消）：跳过，不重复执行。
            return
        # 任务开跑＝有活动：空闲收割的十分钟从这一刻重新数（跑完十分钟没新任务才收引擎）。
        self._last_task_activity = time.monotonic()
        task = self.get_task(task_id)
        if task is None:
            return
        adapter = None
        cancel_watch: asyncio.Task[None] | None = None
        try:
            # 跨模态显存换载：本进程是唯一执行者、队列严格串行，此刻其它引擎的
            # worker 只可能是上一个任务留下的「热缓存」。12GB 单卡上图像引擎常驻
            # 约 7GB 显存，ACE-Step 的 health_check 在空闲显存不足 6000MB 时直接
            # 拒绝启动、Wan2.1 更会 CUDA OOM，换模态之前必须先释放别人。
            released = await self.engine_manager.release_idle_engines(
                task.request.engine_id
            )
            if released:
                _LOGGER.info(
                    "跨模态显存换载：已释放空闲引擎 %s（即将启动 %s）",
                    ", ".join(released),
                    task.request.engine_id,
                )
            adapter = await self.engine_manager.start_engine(task.request.engine_id)
            if task.request.task_type not in adapter.task_types:
                raise ValueError(
                    f"引擎 {adapter.engine_id} 不支持任务类型 {task.request.task_type}"
                )
            await adapter.validate_request(task.request)
            self._transition(task_id, TaskStatus.RUNNING, started_at=_now())

            # 跨进程取消的执行端：另一端（桌面端或 Web 控制台）把任务改成
            # cancelled 之后，本进程必须真的把 worker 停下来，而不是把十几分钟的
            # 生成跑完、把显存一直占着、最后把产物写成没人认领的孤儿文件。
            cancel_watch = asyncio.create_task(
                self._watch_external_cancel(task_id, adapter)
            )
            async with asyncio.timeout(self.task_timeout_seconds):
                result = await adapter.generate(task.request)
            self._validate_result(adapter, result)
            finished_at = _now()
            # 自动另存：产物本来就已经写进 output_dir（工具里即时显示、也归队列管），
            # 这里按用户要求再固定复制一份到项目内 output\，如果还配了自动另存目录就
            # 再复制一份过去。复制失败不改写任务状态，只记日志；副本路径记进 result_json，
            # 两端界面都能看到「已另存 N 份 → …」。
            saved_copies = self._copy_to_extra_dirs(result.output_files)
            if saved_copies:
                result.metadata["saved_copies"] = [entry["target"] for entry in saved_copies]
            with sqlite3.connect(self.database_path, timeout=10) as connection:
                cursor = connection.execute(
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
            if cursor.rowcount == 0:
                current = self.get_task(task_id)
                _LOGGER.warning(
                    "生成已完成，但任务状态已被另一端改写为 %s；产物保留在磁盘：%s",
                    current.status.value if current is not None else "未知",
                    ", ".join(result.output_files),
                )
        except asyncio.CancelledError:
            self._transition_if_open(
                task_id,
                TaskStatus.CANCELLED,
                finished_at=_now(),
                error_message="任务已取消",
            )
            if adapter is not None:
                await self._stop_adapter(adapter.engine_id)
            raise
        except TimeoutError:
            if task_id in self._externally_cancelled:
                _LOGGER.info("任务在另一端已被取消，超时中止不再改写状态：task_id=%s", task_id)
            else:
                self._transition_if_open(
                    task_id,
                    TaskStatus.FAILED,
                    finished_at=_now(),
                    error_message=f"任务超过 {self.task_timeout_seconds:g} 秒超时限制",
                )
                if adapter is not None:
                    self.engine_manager.mark_failed(adapter.engine_id, "任务执行超时")
            if adapter is not None:
                await self._stop_adapter(adapter.engine_id)
        except Exception as exc:
            if task_id in self._externally_cancelled:
                # 另一端取消并中止了 worker：这里的异常是急停的副产品，
                # 既不能覆盖 cancelled 状态，也不能把引擎标成 failed。
                _LOGGER.info(
                    "任务已在另一端取消，中止产生的异常不记为失败：task_id=%s error=%s",
                    task_id,
                    f"{type(exc).__name__}: {exc}"[:500],
                )
            else:
                wrote = self._transition_if_open(
                    task_id,
                    TaskStatus.FAILED,
                    finished_at=_now(),
                    error_message=f"{type(exc).__name__}: {exc}"[:2000],
                )
                if wrote and adapter is not None:
                    self.engine_manager.mark_failed(adapter.engine_id, str(exc))
            if adapter is not None:
                await self._stop_adapter(adapter.engine_id)
        finally:
            self._externally_cancelled.discard(task_id)
            if cancel_watch is not None:
                cancel_watch.cancel()

    async def _watch_external_cancel(self, task_id: str, adapter: Any) -> None:
        """轮询任务行：另一端把任务取消后，本进程立刻中止 worker 释放显存。

        没有这个 watcher，另一端只能改数据库状态，本进程照样把整段生成跑完
        （音乐/视频可达十几分钟），期间显存一直被占着，重试也排不上队。
        """
        while True:
            await asyncio.sleep(self._cancel_poll_seconds)
            current = self.get_task(task_id)
            if current is not None and current.status is TaskStatus.CANCELLED:
                self._externally_cancelled.add(task_id)
                _LOGGER.info(
                    "检测到另一端已取消任务，正在中止 worker：task_id=%s engine_id=%s",
                    task_id,
                    getattr(adapter, "engine_id", "?"),
                )
                abort_now = getattr(adapter, "abort_now", None)
                if abort_now is not None:
                    # 正在生成时 shutdown() 走不通：它要拿同一把 IPC 锁，
                    # 而锁被这条 generate 占着。直接终止进程才是可靠的急停。
                    abort_now()
                else:
                    await self._stop_adapter(getattr(adapter, "engine_id", ""))
                return

    def _transition_if_open(
        self,
        task_id: str,
        target: TaskStatus,
        *,
        started_at: str | None = None,
        finished_at: str | None = None,
        error_message: str | None = None,
    ) -> bool:
        """状态机转换，但绝不覆盖另一端已经写下的终态。

        桌面端与 Web 控制台共用同一张任务表，两边都可能改写状态。原来的
        _transition 只做本地读-改-写，既不检查 UPDATE 是否命中（rowcount=0 就是
        竞争输了），也会在状态已被改写时抛 RuntimeError 把真正的失败原因盖掉。
        """
        current = self.get_task(task_id)
        if current is None:
            return False
        if current.status in {
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }:
            _LOGGER.info(
                "任务已是终态 %s，忽略 %s -> %s：task_id=%s",
                current.status.value,
                current.status.value,
                target.value,
                task_id,
            )
            return False
        try:
            self._transition(
                task_id,
                target,
                started_at=started_at,
                finished_at=finished_at,
                error_message=error_message,
            )
        except RuntimeError:
            latest = self.get_task(task_id)
            _LOGGER.info(
                "任务状态已被另一端改写为 %s，忽略本次 %s：task_id=%s",
                latest.status.value if latest is not None else "已删除",
                target.value,
                task_id,
            )
            return False
        return True

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
            cursor = connection.execute(
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
        if cursor.rowcount == 0:
            # 条件 UPDATE 没命中：本进程读到的旧状态已经过期，另一个进程
            # （Web 控制台或桌面端的另一端）在同一瞬间改写了这一行。
            raise RuntimeError(
                f"任务状态已被其他进程改写，本次转换未生效：{task_id} "
                f"({current.status.value} -> {target.value})"
            )

    async def cancel_task(self, task_id: str) -> TaskRecord:
        task = self.get_task(task_id)
        if task is None:
            raise KeyError(f"任务不存在：{task_id}")
        if task.status is TaskStatus.QUEUED:
            try:
                self._transition(
                    task_id,
                    TaskStatus.CANCELLED,
                    finished_at=_now(),
                    error_message="任务在开始前取消",
                )
            except RuntimeError:
                # 就在这一瞬间被执行者领走了：按「执行中」重新处理。
                refreshed = self.get_task(task_id)
                if refreshed is None:
                    raise
                task = refreshed
        if task.status in {TaskStatus.PREPARING, TaskStatus.RUNNING}:
            runner = self._active_tasks.get(task_id)
            if runner is not None:
                runner.cancel()
                try:
                    await runner
                except asyncio.CancelledError:
                    pass
            else:
                self._request_peer_cancellation(task_id)
        final_record = self.get_task(task_id)
        assert final_record is not None
        return final_record

    def _request_peer_cancellation(self, task_id: str) -> None:
        """执行者是另一个进程时：把任务标为已取消，并要求执行者中止 worker。

        桌面端与 Web 控制台共用同一张任务表，执行者锁只在一个进程手里。原来的
        写法是「本进程找不到 runner 就把任务写成 FAILED（找不到任务执行器）」，
        三个后果都很难看：执行者还在真跑，显存一直被占；它完成后的
        UPDATE ... WHERE status='running' 打不中，产物变成没人认领的孤儿文件；
        用户看到的是一条假的「失败」记录，还会去点重试，把同一张图再跑一遍。

        正确路径是合法的状态机转换 PREPARING/RUNNING -> CANCELLED：执行者侧的
        _watch_external_cancel 会在 _cancel_poll_seconds 内看到 cancelled，
        终止 worker（立刻释放显存），并且不再把中止产生的异常改写成 failed。
        """
        now = _now()
        with sqlite3.connect(self.database_path, timeout=10) as connection:
            row = connection.execute(
                "SELECT result_json FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            try:
                metadata = json.loads(row[0]) if row and row[0] else {}
            except (TypeError, json.JSONDecodeError):
                metadata = {}
            if not isinstance(metadata, dict):
                metadata = {}
            # 留一个跨进程可见的取消标记，便于排查与前端呈现「取消中」。
            metadata["cancel_requested"] = True
            connection.execute(
                "UPDATE tasks SET result_json = ? WHERE task_id = ?",
                (_dump_json(metadata), task_id),
            )
            cursor = connection.execute(
                "UPDATE tasks SET status = ?, finished_at = ?, progress = NULL, "
                "error_message = ? WHERE task_id = ? AND status IN (?, ?)",
                (
                    TaskStatus.CANCELLED.value,
                    now,
                    "已在另一端请求取消；执行者进程会在数秒内中止 worker 并释放显存",
                    task_id,
                    TaskStatus.PREPARING.value,
                    TaskStatus.RUNNING.value,
                ),
            )
        if cursor.rowcount == 0:
            _LOGGER.info("任务状态已被改写，取消请求按现状返回：task_id=%s", task_id)
        else:
            _LOGGER.info(
                "已在非执行者进程发起跨进程取消：task_id=%s（执行者轮询间隔 %.1fs）",
                task_id,
                self._cancel_poll_seconds,
            )

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
        if self._reaper_task is not None:
            self._reaper_task.cancel()
            try:
                await self._reaper_task
            except asyncio.CancelledError:
                pass
            self._reaper_task = None
        self._idle_hooks.clear()
        await self.engine_manager.shutdown()
        self._release_executor_lock()