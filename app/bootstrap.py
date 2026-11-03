"""Application startup, local services, and Qt lifecycle."""

from __future__ import annotations

import logging
import sys
from collections.abc import Sequence

from PySide6.QtWidgets import QApplication

from app.core.config import ConfigStore
from app.core.logging_setup import configure_logging
from app.core.paths import AppPaths
from app.engine_registration import register_production_engines
from app.tasks.engine_manager import EngineManager
from app.tasks.service import TaskService
from app.ui.main_window import MainWindow


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
    task_service.recover_interrupted_tasks()

    application = QApplication.instance() or QApplication(list(argv or sys.argv))
    window = MainWindow(
        config_store,
        logger,
        str(paths.database_file),
        task_service,
        engine_manager,
    )
    application.aboutToQuit.connect(
        lambda: logger.info("应用正常退出")
    )
    return application, window


def main() -> int:
    logging.raiseExceptions = False
    application, window = create_application()
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())