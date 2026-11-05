"""跨进程安全测试（2026-11-05 加固）：执行者锁、非持锁进程降级、原子领取。

桌面端与 Web 控制台共用同一个 SQLite 队列时的关键保证：
1. 只有一个进程运行 worker 循环（msvcrt 字节锁，进程崩溃 OS 自动释放）；
2. 另一进程仍可提交/查看任务，任务最迟 5 秒被执行者领走；
3. 非持锁进程绝不运行中断恢复，不会把执行者正在跑的任务误标失败；
4. _claim_task 用 UPDATE ... WHERE status='queued' 原子领取，第二次领取必失败。
"""

from __future__ import annotations

import asyncio

from app.database.database import initialize_database
from app.tasks.engine_manager import EngineManager
from app.tasks.models import TaskRequest, TaskResult, TaskStatus
from app.tasks.service import TaskService


def build_service(database_path, started: asyncio.Event, release: asyncio.Event):
    class BlockingTestAdapter:
        engine_id = "blocking-test"
        task_types = frozenset({"image"})
        test_only = True

        async def health_check(self):
            return True, "ok"

        async def validate_request(self, request):
            return None

        async def generate(self, request):
            started.set()
            await asyncio.wait_for(release.wait(), timeout=30)
            return TaskResult(
                metadata={"test_only": True, "message": "no media was generated"},
                test_only=True,
            )

        async def shutdown(self):
            return None

    manager = EngineManager()
    manager.register_factory("blocking-test", BlockingTestAdapter)
    return TaskService(database_path, manager, task_timeout_seconds=60)


def cross_request():
    return TaskRequest(
        task_type="image",
        engine_id="blocking-test",
        prompt="cross-process queue task",
        parameters={},
    )


def test_only_one_process_executes_the_shared_queue(tmp_path):
    """两进程共用队列：先启动者为执行者；另一进程只提交，≤5 秒被执行者领走。"""
    started = asyncio.Event()
    release = asyncio.Event()

    async def scenario():
        database_path = tmp_path / "studio.sqlite3"
        initialize_database(database_path)

        executor = build_service(database_path, started, release)
        await executor.start()
        assert executor._executor_active is True

        submitter = build_service(database_path, started, release)
        await submitter.start()
        # 抢锁失败 → 本进程降级为只提交/查看，不起 worker，也不做中断恢复。
        assert submitter._executor_active is False
        assert submitter._worker_task is None

        # 让执行者的 worker 循环先完成首扫并进入 5 秒轮询休眠，
        # 之后排入的任务必须靠超时轮询被领走（而不是即时唤醒）。
        await asyncio.sleep(0.3)
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        record = submitter.submit_task(cross_request())
        assert record.status == TaskStatus.QUEUED

        # 执行者靠 5 秒超时轮询领走另一个进程提交的任务。
        await asyncio.wait_for(started.wait(), timeout=15)
        elapsed = loop.time() - t0
        assert 4.0 <= elapsed <= 15.0, (
            "领取必须经由 5 秒轮询超时（非持锁进程的提交不会唤醒执行者）"
        )
        running = submitter.get_task(record.task_id)
        assert running.status == TaskStatus.RUNNING

        release.set()
        final = None
        for _ in range(80):
            final = submitter.get_task(record.task_id)
            if final.status in {TaskStatus.COMPLETED, TaskStatus.FAILED}:
                break
            await asyncio.sleep(0.25)
        assert final is not None and final.status == TaskStatus.COMPLETED

        # 执行者退出后锁由 shutdown（以及崩溃时 OS）释放，另一进程可接管。
        await executor.shutdown()
        await submitter.start()
        assert submitter._executor_active is True
        await submitter.shutdown()

    asyncio.run(scenario())


def test_non_executor_never_marks_executors_running_task_failed(tmp_path):
    """执行者任务运行中，另一进程 start() 不得运行中断恢复、不得误标失败。"""
    started = asyncio.Event()
    release = asyncio.Event()

    async def scenario():
        database_path = tmp_path / "studio.sqlite3"
        initialize_database(database_path)

        executor = build_service(database_path, started, release)
        await executor.start()
        record = executor.submit_task(cross_request())
        await asyncio.wait_for(started.wait(), timeout=12)
        assert executor.get_task(record.task_id).status == TaskStatus.RUNNING

        bystander = build_service(database_path, started, release)
        await bystander.start()
        assert bystander._executor_active is False

        # 旁观进程既没起 worker，也没跑 recover：任务仍在运行。
        still = bystander.get_task(record.task_id)
        assert still.status == TaskStatus.RUNNING
        assert still.error_message is None

        release.set()
        for _ in range(80):
            final = executor.get_task(record.task_id)
            if final.status in {TaskStatus.COMPLETED, TaskStatus.FAILED}:
                break
            await asyncio.sleep(0.25)
        assert final.status == TaskStatus.COMPLETED
        await executor.shutdown()

    asyncio.run(scenario())


def test_claim_task_is_atomic_second_claim_fails(tmp_path):
    """原子领取：同一任务第二次领取必返回 False（另一进程不可能重复执行）。"""
    database_path = tmp_path / "studio.sqlite3"
    initialize_database(database_path)
    service = build_service(database_path, asyncio.Event(), asyncio.Event())

    record = service.submit_task(cross_request())
    assert service._claim_task(record.task_id) is True
    assert service._claim_task(record.task_id) is False
    fresh = service.get_task(record.task_id)
    assert fresh.status == TaskStatus.PREPARING
