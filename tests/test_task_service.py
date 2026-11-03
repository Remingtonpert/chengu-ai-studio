"""Persistent task lifecycle tests with a test-only, no-output adapter."""

from __future__ import annotations

import asyncio
import sqlite3
import sys
from pathlib import Path

import pytest

from app.database.database import MIGRATIONS, initialize_database
from app.tasks.engine_manager import EngineManager
from app.tasks.models import TaskRequest, TaskResult, TaskStatus
from app.tasks.process_adapter import ProcessEngineAdapter
from app.tasks.service import TaskService


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