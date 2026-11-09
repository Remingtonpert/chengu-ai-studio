"""Persistent task lifecycle tests with a test-only, no-output adapter."""

from __future__ import annotations

import asyncio
import sqlite3
import time
import sys
from pathlib import Path

import pytest

from app.database.database import MIGRATIONS, initialize_database
from app.tasks.engine_manager import EngineManager
from app.tasks.models import TaskRequest, TaskResult, TaskStatus
from app.tasks.process_adapter import ProcessEngineAdapter
from app.tasks.service import ENGINE_IDLE_LIMIT_SECONDS, TaskService


class ControlledTestAdapter:
    engine_id = "test-only-controlled"
    task_types = frozenset({"image"})
    test_only = True

    def __init__(self, on_generate=None):
        self.on_generate = on_generate
        self.shutdown_count = 0

    async def health_check(self):
        return True, "test adapter only; no media generation"

    async def validate_request(self, request):
        if request.task_type not in self.task_types:
            raise ValueError("unsupported test task")

    async def generate(self, request):
        if self.on_generate is not None:
            await self.on_generate(request)
        return TaskResult(
            metadata={"test_only": True, "message": "no media was generated"},
            test_only=True,
        )

    async def shutdown(self):
        self.shutdown_count += 1


def make_service(database_path, adapter_factory=None, timeout=1.0):
    manager = EngineManager()
    manager.register_factory(
        ControlledTestAdapter.engine_id,
        adapter_factory or ControlledTestAdapter,
    )
    return TaskService(database_path, manager, task_timeout_seconds=timeout)


def request(prompt):
    return TaskRequest(
        task_type="image",
        engine_id=ControlledTestAdapter.engine_id,
        prompt=prompt,
        parameters={"test_only": True},
    )


def test_fifo_is_persisted_and_never_reports_fake_progress(tmp_path):
    async def scenario():
        observed = []

        async def generate(item):
            observed.append(item.prompt)

        service = make_service(
            tmp_path / "studio.sqlite3",
            lambda: ControlledTestAdapter(generate),
        )
        submitted = [service.submit_task(request(f"prompt-{index}")) for index in range(3)]
        await service.start()
        await service.wait_until_idle()

        records = [service.get_task(item.task_id) for item in submitted]
        assert observed == ["prompt-0", "prompt-1", "prompt-2"]
        assert all(record.status is TaskStatus.COMPLETED for record in records)
        assert all(record.progress is None for record in records)
        assert all(record.output_files == () for record in records)
        assert all(record.result_metadata["test_only"] for record in records)
        await service.shutdown()

    asyncio.run(scenario())


def test_queued_task_can_be_cancelled_without_running(tmp_path):
    async def scenario():
        started = asyncio.Event()
        release = asyncio.Event()
        observed = []

        async def generate(item):
            observed.append(item.prompt)
            if item.prompt == "first":
                started.set()
                await release.wait()

        service = make_service(
            tmp_path / "studio.sqlite3",
            lambda: ControlledTestAdapter(generate),
        )
        first = service.submit_task(request("first"))
        second = service.submit_task(request("second"))
        await service.start()
        await started.wait()

        cancelled = await service.cancel_task(second.task_id)
        release.set()
        await service.wait_until_idle()

        assert cancelled.status is TaskStatus.CANCELLED
        assert observed == ["first"]
        assert service.get_task(first.task_id).status is TaskStatus.COMPLETED
        await service.shutdown()

    asyncio.run(scenario())


def test_running_task_cancellation_is_persisted(tmp_path):
    async def scenario():
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def generate(_request):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        service = make_service(
            tmp_path / "studio.sqlite3",
            lambda: ControlledTestAdapter(generate),
        )
        task = service.submit_task(request("cancel me"))
        await service.start()
        await started.wait()

        record = await service.cancel_task(task.task_id)

        assert cancelled.is_set()
        assert record.status is TaskStatus.CANCELLED
        assert record.finished_at is not None
        await service.shutdown()

    asyncio.run(scenario())


def test_failure_can_be_retried_and_links_to_original(tmp_path):
    async def scenario():
        calls = 0

        async def generate(_request):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("controlled test failure")

        service = make_service(
            tmp_path / "studio.sqlite3",
            lambda: ControlledTestAdapter(generate),
        )
        original = service.submit_task(request("retry me"))
        await service.start()
        await service.wait_until_idle()

        failed = service.get_task(original.task_id)
        assert failed.status is TaskStatus.FAILED
        assert "controlled test failure" in failed.error_message

        retried = service.retry_task(original.task_id)
        assert retried.task_id != original.task_id
        assert retried.retry_of == original.task_id
        await service.wait_until_idle()
        assert service.get_task(retried.task_id).status is TaskStatus.COMPLETED
        await service.shutdown()

    asyncio.run(scenario())


def test_timeout_is_recorded_as_failure(tmp_path):
    async def scenario():
        async def generate(_request):
            await asyncio.Event().wait()

        service = make_service(
            tmp_path / "studio.sqlite3",
            lambda: ControlledTestAdapter(generate),
            timeout=0.02,
        )
        task = service.submit_task(request("timeout"))
        await service.start()
        await service.wait_until_idle()

        record = service.get_task(task.task_id)
        assert record.status is TaskStatus.FAILED
        assert "超时" in record.error_message
        await service.shutdown()

    asyncio.run(scenario())


def test_restart_marks_interrupted_tasks_failed_and_resumes_queue(tmp_path):
    async def scenario():
        database_path = tmp_path / "studio.sqlite3"
        service_before_exit = make_service(database_path)
        interrupted = service_before_exit.submit_task(request("interrupted"))
        queued = service_before_exit.submit_task(request("queued"))
        with sqlite3.connect(database_path) as connection:
            connection.execute(
                "UPDATE tasks SET status = ? WHERE task_id = ?",
                (TaskStatus.RUNNING.value, interrupted.task_id),
            )

        service_after_restart = make_service(database_path)
        await service_after_restart.start()
        await service_after_restart.wait_until_idle()

        failed = service_after_restart.get_task(interrupted.task_id)
        resumed = service_after_restart.get_task(queued.task_id)
        assert failed.status is TaskStatus.FAILED
        assert "上次退出" in failed.error_message
        assert resumed.status is TaskStatus.COMPLETED
        await service_after_restart.shutdown()

    asyncio.run(scenario())


def test_shutdown_cancels_running_task_and_preserves_queued_task(tmp_path):
    async def scenario():
        started = asyncio.Event()

        async def generate(_request):
            started.set()
            await asyncio.Event().wait()

        database_path = tmp_path / "studio.sqlite3"
        service = make_service(
            database_path,
            lambda: ControlledTestAdapter(generate),
        )
        running = service.submit_task(request("running"))
        queued = service.submit_task(request("queued"))
        await service.start()
        await started.wait()
        await service.shutdown()

        assert service.get_task(running.task_id).status is TaskStatus.CANCELLED
        assert service.get_task(queued.task_id).status is TaskStatus.QUEUED

        resumed_service = make_service(database_path)
        await resumed_service.start()
        await resumed_service.wait_until_idle()
        assert resumed_service.get_task(queued.task_id).status is TaskStatus.COMPLETED
        await resumed_service.shutdown()

    asyncio.run(scenario())


def test_engine_manager_can_start_stop_and_restart_independently():
    async def scenario():
        created = []

        def factory():
            adapter = ControlledTestAdapter()
            created.append(adapter)
            return adapter

        manager = EngineManager()
        manager.register_factory(ControlledTestAdapter.engine_id, factory)

        first = await manager.start_engine(ControlledTestAdapter.engine_id)
        assert manager.list_statuses()[0].state == "ready"
        await manager.stop_engine(ControlledTestAdapter.engine_id)
        assert manager.list_statuses()[0].state == "stopped"
        assert created[0].shutdown_count == 1

        second = await manager.restart_engine(ControlledTestAdapter.engine_id)
        assert second is not first
        assert len(created) == 2
        assert manager.list_statuses()[0].state == "ready"
        await manager.shutdown()

    asyncio.run(scenario())


def test_health_check_failure_cleans_adapter_and_keeps_failed_status():
    class UnhealthyAdapter(ControlledTestAdapter):
        async def health_check(self):
            return False, "controlled health failure"

    async def scenario():
        instance = UnhealthyAdapter()
        manager = EngineManager()
        manager.register_factory(
            instance.engine_id,
            lambda: instance,
        )

        with pytest.raises(RuntimeError, match="controlled health failure"):
            await manager.start_engine(instance.engine_id)

        assert instance.shutdown_count == 1
        status = manager.list_statuses()[0]
        assert status.state == "failed"
        assert "controlled health failure" in status.message

    asyncio.run(scenario())


def test_non_test_engine_without_existing_output_fails(tmp_path):
    class InvalidProductionAdapter(ControlledTestAdapter):
        test_only = False

        async def generate(self, _request):
            return TaskResult(test_only=False)

    async def scenario():
        service = make_service(
            tmp_path / "studio.sqlite3",
            InvalidProductionAdapter,
        )
        task = service.submit_task(request("must create a real file"))
        await service.start()
        await service.wait_until_idle()

        record = service.get_task(task.task_id)
        assert record.status is TaskStatus.FAILED
        assert "输出文件" in record.error_message
        await service.shutdown()

    asyncio.run(scenario())


def test_subprocess_adapter_round_trip_and_shutdown():
    async def scenario():
        project_root = Path(__file__).resolve().parents[1]
        adapter = ProcessEngineAdapter(
            engine_id="test-process-only",
            command=(
                sys.executable,
                "-m",
                "app.tasks.process_worker",
                "--adapter-factory",
                "tests.process_worker_fixture:create_adapter",
            ),
            task_types=frozenset({"image"}),
            test_only=True,
            cwd=project_root,
        )
        healthy, message = await adapter.health_check()
        process_id = adapter.process_id
        assert healthy is True
        assert "test worker" in message
        assert process_id is not None

        await adapter.validate_request(
            TaskRequest(
                task_type="image",
                engine_id=adapter.engine_id,
                prompt="protocol check",
            )
        )
        result = await adapter.generate(
            TaskRequest(
                task_type="image",
                engine_id=adapter.engine_id,
                prompt="protocol check",
            )
        )
        assert result.test_only is True
        assert result.output_files == ()
        assert result.metadata["prompt_received"] is True

        await adapter.shutdown()
        assert adapter.process_id is None

    asyncio.run(scenario())


def test_subprocess_adapter_non_ascii_prompt_is_not_mojibake():
    """回归测试（2026-11-06 中文文生图 4 连失败）：中文/多行/emoji 提示词经
    子进程 JSON-lines IPC 往返后必须逐字符相同，且不残留孤立代理码位。

    曾经的根因：worker 只把 stdout/stderr 配成 UTF-8，stdin 仍是系统代码页
    （中文 Windows 的 gbk + surrogateescape），UTF-8 字节被解成带 \\udcXX 的坏
    字符串；这种 str 进不了 tokenizers 的 Rust 入参校验，表现为
    "TextEncodeInput must be Union[TextInputSequence, ...]"。英文提示词因为两种
    代码页解出的字节完全相同而一直正常，所以只有中文批次全批失败。
    """
    zh_prompt = (
        "一位三十五岁左右的东亚男性工程师，坐在深夜的办公室里，\n"
        "桌上一台亮着的笔记本，屏幕光打在他侧脸上，\n"
        "黑色卫衣，胡茬清晰，眼神疲惫但专注，\n"
        "背景是虚化的城市夜景，冷色调，浅景深 🐈"
    )

    async def scenario():
        project_root = Path(__file__).resolve().parents[1]
        adapter = ProcessEngineAdapter(
            engine_id="test-process-only",
            command=(
                sys.executable,
                "-m",
                "app.tasks.process_worker",
                "--adapter-factory",
                "tests.process_worker_fixture:create_adapter",
            ),
            task_types=frozenset({"image"}),
            test_only=True,
            cwd=project_root,
        )
        result = await adapter.generate(
            TaskRequest(
                task_type="image",
                engine_id=adapter.engine_id,
                prompt=zh_prompt,
            )
        )
        await adapter.shutdown()

        assert result.metadata["prompt_type"] == "str"
        assert result.metadata["prompt_has_lone_surrogate"] is False
        assert result.metadata["prompt_text"] == zh_prompt

    asyncio.run(scenario())


def test_subprocess_adapter_recycles_worker_only_after_the_response_is_written():
    """回归测试（AI 音乐引擎进程轮换竞态）：worker 请求自我更换时，退出必须发生在
    「本次响应已经写回主进程」之后，下一个任务由 adapter 透明重启并从头计数。

    曾经的写法是 music_worker 在生成结束时启动 threading.Timer(2.0, os._exit(0))：
    计时器和下一个任务毫无关系，max_tasks=4 时第 5 首曲子刚开始生成（甚至还在加载
    权重）就被 os._exit 杀掉，表现为「每 4 首之后必有一首失败」。
    """

    async def scenario():
        project_root = Path(__file__).resolve().parents[1]
        adapter = ProcessEngineAdapter(
            engine_id="test-process-only",
            command=(
                sys.executable,
                "-m",
                "app.tasks.process_worker",
                "--adapter-factory",
                "tests.process_worker_fixture:create_recycling_adapter",
            ),
            task_types=frozenset({"image"}),
            test_only=True,
            cwd=project_root,
        )
        first = await adapter.generate(
            TaskRequest(
                task_type="image",
                engine_id=adapter.engine_id,
                prompt="第一首曲子 🎵",
            )
        )
        assert first.metadata["prompt_text"] == "第一首曲子 🎵"
        assert first.metadata["worker_generations_served"] == 1
        first_process = adapter._process  # 测试内直接引用，用于观察退出时机
        assert first_process is not None
        # 响应已回到本进程 → worker 此刻才退出；协作式退出，没有任何计时器。
        await asyncio.wait_for(first_process.wait(), timeout=15)
        assert first_process.returncode == 0

        second = await adapter.generate(
            TaskRequest(
                task_type="image",
                engine_id=adapter.engine_id,
                prompt="第二首曲子",
            )
        )
        assert second.metadata["prompt_text"] == "第二首曲子"
        assert second.metadata["worker_generations_served"] == 1
        assert adapter.process_id is not None
        assert adapter.process_id != first_process.pid
        await adapter.shutdown()

    asyncio.run(scenario())


def test_cross_process_cancel_does_not_write_fake_failure(tmp_path):
    """桌面端与 Web 控制台共用同一个任务库时的跨进程取消。

    曾经的根因：非执行者进程（执行者锁在另一个进程手里，本地 _active_tasks 里
    没有这条任务）把 RUNNING/PREPARING 直接写成 FAILED，error_message 是
    "找不到任务执行器；任务已停止"。执行者其实还在真跑：它完成后的
    UPDATE ... WHERE status='running' 打不中，产物变成没人认领的孤儿文件，
    用户看到的是一条假的失败记录，还会去点重试把同一张图再跑一遍。
    """
    database_path = tmp_path / "studio.sqlite3"
    started = asyncio.Event()
    release = asyncio.Event()
    executor_adapters: list[ControlledTestAdapter] = []

    async def slow_generate(item):
        if item.prompt == "长任务":
            started.set()
            await release.wait()

    def make_executor_adapter() -> ControlledTestAdapter:
        adapter = ControlledTestAdapter(slow_generate)
        executor_adapters.append(adapter)
        return adapter

    async def scenario():
        executor_manager = EngineManager()
        executor_manager.register_factory(
            ControlledTestAdapter.engine_id, make_executor_adapter
        )
        executor = TaskService(database_path, executor_manager, task_timeout_seconds=30)

        peer_manager = EngineManager()
        peer_manager.register_factory(
            ControlledTestAdapter.engine_id,
            lambda: ControlledTestAdapter(None),
        )
        # 第二个进程（模拟 Web 控制台）不启动 worker 循环：执行者锁在 executor 手里。
        peer = TaskService(database_path, peer_manager, task_timeout_seconds=30)

        await executor.start()
        task = executor.submit_task(request("长任务"))
        await asyncio.wait_for(started.wait(), timeout=10)
        assert executor.get_task(task.task_id).status is TaskStatus.RUNNING

        cancelled = await peer.cancel_task(task.task_id)
        assert cancelled.status is TaskStatus.CANCELLED
        assert "找不到任务执行器" not in (cancelled.error_message or "")

        # 执行者侧的取消监视器按 1.5 秒轮询，等它先看到 cancelled。
        await asyncio.sleep(2.0)
        release.set()
        await executor.wait_until_idle(timeout=20)

        final = executor.get_task(task.task_id)
        assert final.status is TaskStatus.CANCELLED
        assert final.error_message
        assert "另一端" in final.error_message
        # 执行者确实把引擎 worker 停掉了（释放显存），而不是继续跑完。
        assert executor_adapters and executor_adapters[0].shutdown_count >= 1
        await executor.shutdown()
        await peer.shutdown()

    asyncio.run(scenario())


def test_switching_modality_releases_idle_engine_workers(tmp_path):
    """跨模态显存换载：跑音乐/视频之前必须释放还占着显存的空闲图片 worker。

    曾经的根因：成功路径故意不卸载引擎（省 1-2 分钟重载），但没有任何空闲卸载，
    12GB 单卡上 FLUX worker 常驻约 7GB 显存 → 紧随其后的 ACE-Step health_check
    因空闲显存不足 6000MB 直接拒绝启动、Wan2.1 则 CUDA OOM，UI 里表现为
    「图片刚跑完，音乐/视频必定失败」。
    """

    class SubprocessLikeAdapter:
        """带 process_id 的测试适配器：模拟真实引擎持有 worker 子进程。"""

        test_only = True

        def __init__(self, engine_id: str, task_types) -> None:
            self.engine_id = engine_id
            self.task_types = frozenset(task_types)
            self.process_id = 4242
            self.shutdown_count = 0

        async def health_check(self):
            return True, f"{self.engine_id} ready"

        async def validate_request(self, task_request):
            if task_request.task_type not in self.task_types:
                raise ValueError("unsupported test task")

        async def generate(self, task_request):
            return TaskResult(metadata={"test_only": True}, test_only=True)

        async def shutdown(self):
            self.shutdown_count += 1

    async def scenario():
        image_adapter = SubprocessLikeAdapter("test-image-engine", ("image",))
        music_adapter = SubprocessLikeAdapter("test-music-engine", ("music",))
        manager = EngineManager()
        manager.register_factory("test-image-engine", lambda: image_adapter)
        manager.register_factory("test-music-engine", lambda: music_adapter)
        service = TaskService(database_path, manager, task_timeout_seconds=30)
        image_task = service.submit_task(
            TaskRequest(task_type="image", engine_id="test-image-engine", prompt="一张图")
        )
        music_task = service.submit_task(
            TaskRequest(task_type="music", engine_id="test-music-engine", prompt="一首曲子")
        )
        await service.start()
        await service.wait_until_idle(timeout=20)

        assert service.get_task(image_task.task_id).status is TaskStatus.COMPLETED
        assert service.get_task(music_task.task_id).status is TaskStatus.COMPLETED
        # 图片 worker 在音乐任务开始前被释放（释放显存）……
        assert image_adapter.shutdown_count == 1
        # ……而音乐引擎本身保持热态（成功路径不卸载，同模态连发不用重载权重）。
        assert music_adapter.shutdown_count == 0
        await service.shutdown()

    database_path = tmp_path / "studio.sqlite3"
    asyncio.run(scenario())


def test_engine_manager_contains_subprocess_start_failure():
    async def scenario():
        project_root = Path(__file__).resolve().parents[1]
        manager = EngineManager()
        manager.register_factory(
            "crash-test-engine",
            lambda: ProcessEngineAdapter(
                engine_id="crash-test-engine",
                command=(sys.executable, "-c", "raise SystemExit(17)"),
                task_types=frozenset({"image"}),
                test_only=True,
                cwd=project_root,
            ),
        )

        with pytest.raises(RuntimeError, match="意外退出"):
            await manager.start_engine("crash-test-engine")

        status = manager.list_statuses()[0]
        assert status.state == "failed"
        assert "exit code=17" in status.message
        await manager.shutdown()

    asyncio.run(scenario())


def test_adapter_shutdown_error_does_not_block_fifo_worker(tmp_path):
    async def scenario():
        calls = 0

        class ShutdownFailureAdapter(ControlledTestAdapter):
            async def generate(self, request):
                nonlocal calls
                calls += 1
                if request.prompt == "fail and cleanup error":
                    raise RuntimeError("generation failed")
                return await super().generate(request)

            async def shutdown(self):
                if calls == 1:
                    raise RuntimeError("controlled cleanup failure")

        service = make_service(
            tmp_path / "studio.sqlite3",
            ShutdownFailureAdapter,
        )
        failed = service.submit_task(request("fail and cleanup error"))
        next_task = service.submit_task(request("continue queue"))
        await service.start()
        await service.wait_until_idle()

        assert service.get_task(failed.task_id).status is TaskStatus.FAILED
        assert service.get_task(next_task.task_id).status is TaskStatus.COMPLETED
        assert calls == 2
        await service.shutdown()

    asyncio.run(scenario())


def test_existing_v1_database_migrates_without_losing_queued_tasks(tmp_path):
    database_path = tmp_path / "existing-v1.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "CREATE TABLE schema_migrations "
            "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        MIGRATIONS[1](connection)
        connection.execute(
            """
            INSERT INTO tasks (
                task_id, task_type, engine_id, prompt, status, created_at
            ) VALUES ('existing-task', 'image', 'test-only-controlled', 'keep me', 'queued', '2026-10-10T00:00:00+00:00')
            """
        )
        connection.execute("INSERT INTO schema_migrations(version) VALUES (1)")

    initialize_database(database_path)

    with sqlite3.connect(database_path) as connection:
        versions = connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
        task = connection.execute(
            "SELECT prompt, status, retry_of, result_json FROM tasks "
            "WHERE task_id = 'existing-task'"
        ).fetchone()

    assert versions == [(1,), (2,)]
    assert task == ("keep me", "queued", None, "{}")
def test_idle_reaper_releases_engines_then_calls_registered_hooks(tmp_path):
    """十分钟没任务：先把自己拉起的引擎 worker 停掉（显存当场归还），再叫注册进来的钩子。

    五个生成模块的模型与生成逻辑一行没动：这里只验「空闲了就让路」这层调度。
    """

    async def scenario():
        service = make_service(tmp_path / "studio.sqlite3")
        kept: list = []
        hook_notes: list = []

        async def fake_release(keep_engine_id):
            kept.append(keep_engine_id)
            return ("test-only-controlled",)

        service.engine_manager.release_idle_engines = fake_release  # type: ignore[method-assign]
        service.register_idle_hook(
            lambda idle: hook_notes.append(int(idle)) or "已卸载大模型"
        )
        await service.start()
        assert service._reaper_task is not None, "收割循环要跟着服务一起起来"

        await service._reap_idle(ENGINE_IDLE_LIMIT_SECONDS + 5)
        assert kept == [None], "空闲收割一个引擎都不留（None＝不是「保留某一个引擎」）"
        assert hook_notes and hook_notes[0] >= ENGINE_IDLE_LIMIT_SECONDS

        # 前端拦任务之前的「先自己让路」走同一条路，并把空闲时钟拨回去
        released = await service.release_all_idle_engines()
        assert released == ("test-only-controlled",)
        assert service._last_task_activity > time.monotonic() - 5
        await service.shutdown()

    asyncio.run(scenario())


def test_idle_reaper_does_not_teardown_a_running_task(tmp_path, monkeypatch):
    """任务正在跑（或队列里还有货）＝不空闲：收割循环不许停引擎、也不许卸载大模型。"""
    monkeypatch.setattr("app.tasks.service.IDLE_REAPER_POLL_SECONDS", 0.05)
    monkeypatch.setattr("app.tasks.service.ENGINE_IDLE_LIMIT_SECONDS", 0.0)

    started = asyncio.Event()
    release = asyncio.Event()
    kept: list = []
    hooked: list = []

    async def generate(item):
        started.set()
        await release.wait()

    async def scenario():
        service = make_service(
            tmp_path / "studio.sqlite3",
            lambda: ControlledTestAdapter(generate),
            timeout=30.0,
        )

        async def fake_release(keep_engine_id):
            # keep_engine_id 不是 None＝提交任务前「保留要用的那个引擎、别的先让路」，
            # 那是原有调度；只有 None（一个都不留）才是空闲收割动的手。
            kept.append(keep_engine_id)
            return ()

        service.engine_manager.release_idle_engines = fake_release  # type: ignore[method-assign]
        service.register_idle_hook(lambda idle: hooked.append(idle))
        first = service.submit_task(request("running-task"))
        try:
            await service.start()
            await started.wait()
            # 任务正在跑：让收割循环多转几轮（限制已被压到 0 秒，真有空它马上就会动手）
            await asyncio.sleep(0.4)
            assert None not in kept, "任务在跑，不许做空闲收割（一个引擎都不留的那种）"
            assert hooked == [], "任务在跑，不许卸载大模型"
            assert service._last_task_activity > time.monotonic() - 5, "跑任务要把空闲时钟拨回去"

            release.set()
            await service.wait_until_idle()
            assert service.get_task(first.task_id).status is TaskStatus.COMPLETED
            # 任务结束后队列空了，下一轮收割就会正常动手（限制 0 秒）
            deadline = time.monotonic() + 3.0
            while None not in kept and time.monotonic() < deadline:
                await asyncio.sleep(0.05)
            assert None in kept, "任务跑完、队列空了，收割就该正常放行"
            assert hooked, "收完引擎还要把自带大模型一并卸载"
        finally:
            # 断言万一失败也要把在跑的任务放开并关掉服务：不然 pytest 收尾时会被
            # 还在等 Event 的适配器拖住，整条测试卡到天荒地老。
            release.set()
            await service.shutdown()

    asyncio.run(scenario())