"""Phase 1 configuration, database, and navigation tests."""

import sqlite3
import logging

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from app.bootstrap import create_application
from app.core.config import ConfigError, ConfigStore
from app.core.paths import AppPaths
from app.database.database import initialize_database
from app.ui.main_window import MainWindow, PAGES


def test_config_defaults_and_round_trip(tmp_path):
    paths = AppPaths.from_environment(
        app_home=tmp_path / "profile",
        data_root=tmp_path / "data",
    )
    store = ConfigStore(paths)

    defaults = store.load()
    assert defaults["models_dir"] == str(tmp_path / "data" / "Models")
    assert defaults["output_dir"] == str(tmp_path / "data" / "Output")

    changed = {
        "models_dir": str(tmp_path / "other-models"),
        "output_dir": str(tmp_path / "renders"),
    }
    store.save(changed)
    # 保存只写显式提供的键；未提供的可选键（engines_enabled/image_venv_python）
    # 由 load() 补回默认值，因此逐项比较已知路径键。
    loaded = store.load()
    assert {key: loaded[key] for key in changed} == changed


def test_config_rejects_empty_path(tmp_path):
    store = ConfigStore(AppPaths.from_environment(app_home=tmp_path))
    try:
        store.save({"models_dir": "", "output_dir": "renders"})
    except ConfigError:
        pass
    else:
        raise AssertionError("empty paths must be rejected")


def test_database_migration_is_idempotent(tmp_path):
    database_path = tmp_path / "db" / "studio.sqlite3"
    initialize_database(database_path)
    initialize_database(database_path)

    with sqlite3.connect(database_path) as connection:
        versions = connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(tasks)")
        }

    assert versions == [(1,), (2,)]
    assert {
        "task_id",
        "task_type",
        "engine_id",
        "status",
        "output_files_json",
        "retry_of",
        "result_json",
    } <= columns


def test_main_window_navigation_and_unverified_generation_is_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    application = QApplication.instance() or QApplication([])
    paths = AppPaths.from_environment(
        app_home=tmp_path / "profile",
        data_root=tmp_path / "data",
    )
    window = MainWindow(ConfigStore(paths), logging.getLogger("test"), "test.db")

    assert window.navigation.count() == 10
    for row, page in enumerate(PAGES):
        window.navigation.setCurrentRow(row)
        assert window.stack.currentIndex() == row
        if page[3]:
            assert window.stack.currentWidget().generate_button.isEnabled() is False

    window.close()
    application.quit()


def test_application_bootstraps_database_and_exits(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    paths = AppPaths.from_environment(
        app_home=tmp_path / "profile",
        data_root=tmp_path / "data",
    )
    application, window = create_application(paths=paths, argv=["chenguai-test"])
    QTimer.singleShot(0, window.close)

    window.show()
    assert application.exec() == 0
    assert paths.database_file.exists()
    assert paths.log_file.exists()