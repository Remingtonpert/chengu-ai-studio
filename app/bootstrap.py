"""Application startup, local services, and Qt lifecycle."""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
from collections.abc import Callable, Sequence

from PySide6.QtWidgets import QApplication

from app.core.config import ConfigStore
from app.core.logging_setup import configure_logging
from app.core.paths import AppPaths
from app.engine_registration import register_production_engines
from app.tasks.engine_manager import EngineManager
from app.tasks.service import TaskService
from app.ui.main_window import MainWindow


def start_task_service_loop(
    task_service: TaskService, logger
) -> tuple[Callable[[], None], asyncio.AbstractEventLoop | None]:
    """在后台线程运行 asyncio 事件循环，驱动与 Web 控制台完全相同的 TaskService。

    桌面端不再自带独立的任务执行逻辑：worker 循环、SQLite FIFO 队列、引擎
    子进程协议与 Web 模式一行不差；若另一个进程（Web 控制台）已经是任务
    执行者，TaskService 会自动降级为「只提交」模式，队列 5 秒内被执行者领走。
    返回 (优雅停止回调, 服务事件循环)：GUI 用该 loop 回投 cancel_task /
    restart_engine 等协程（asyncio.run_coroutine_threadsafe）。
    """
    loop_holder: dict[str, asyncio.AbstractEventLoop | None] = {"loop": None}
    loop_ready = threading.Event()

    async def _boot() -> None:
        try:
            await task_service.start()
        except Exception:
            logger.exception("任务服务启动失败")

    async def _graceful_shutdown() -> None:
        try:
            await task_service.shutdown()
        finally:
            asyncio.get_running_loop().stop()

    def _runner() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop_holder["loop"] = loop
        loop.create_task(_boot())
        loop_ready.set()
        loop.run_forever()

    thread = threading.Thread(target=_runner, name="chenguai-task-loop", daemon=True)
    thread.start()
    loop_ready.wait(timeout=10)
    logger.info("任务服务事件循环已在后台线程启动")

    def stop() -> None:
        loop = loop_holder["loop"]
        if loop is None:
            return
        try:
            future = asyncio.run_coroutine_threadsafe(_graceful_shutdown(), loop)
            future.result(timeout=15)
            logger.info("任务服务已随应用优雅停止")
        except Exception:
            logger.exception("停止任务服务失败")

    return stop, loop_holder["loop"]


def create_application(
    paths: AppPaths | None = None,
    argv: Sequence[str] | None = None,
) -> tuple[QApplication, MainWindow]:
    paths = paths or AppPaths.from_environment()
    paths.prepare_app_home()
    logger = configure_logging(paths)
    config_store = ConfigStore(paths)
    config = config_store.load()
    engine_manager = EngineManager()
    register_production_engines(engine_manager, config, paths, logger)
    task_service = TaskService(paths.database_file, engine_manager)
    # 中断任务恢复与 worker 循环统一由 task_service.start() 完成，
    # 并由跨进程执行者锁保证只有「首个启动的进程」执行恢复，
    # 不会把另一个进程正在运行的任务误标为失败。
    stop_task_loop, service_loop = start_task_service_loop(task_service, logger)

    application = QApplication.instance() or QApplication(list(argv or sys.argv))
    window = MainWindow(
        config_store,
        logger,
        str(paths.database_file),
        task_service,
        engine_manager,
        service_loop,
    )
    application.aboutToQuit.connect(
        lambda: logger.info("应用正常退出")
    )
    application.aboutToQuit.connect(stop_task_loop)
    return application, window


def main() -> int:
    logging.raiseExceptions = False
    application, window = create_application()
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
