"""Phase 1 configuration, database, and navigation tests."""

import sqlite3
import logging
from pathlib import Path

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
    # 权重目录默认与代码同目录树（项目根 / 便携包根的 models\），
    # 不跟随 data_root：engine_registration 的 FLUX/Wan/ACE-Step 默认权重路径同源。
    project_root = Path(__file__).resolve().parents[1]
    assert defaults["models_dir"] == str(project_root / "models")
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


def test_main_window_creation_and_system_navigation(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    application = QApplication.instance() or QApplication([])
    paths = AppPaths.from_environment(
        app_home=tmp_path / "profile",
        data_root=tmp_path / "data",
    )
    window = MainWindow(
        ConfigStore(paths), logging.getLogger("test"), str(tmp_path / "db" / "studio.sqlite3")
    )

    # 创作页（MJ 同款三栏 + 五模式 Tab）常驻 stack 首屏；
    # PAGES 里三个生成模式已并入 Tab，其余系统页保留在「系统功能」入口。
    from app.ui.pages.creation_page import CreationPage

    assert isinstance(window.creation, CreationPage)
    assert window.stack.widget(0) is window.creation
    assert len(window._system_shells) + 1 == window.stack.count()

    system_keys = [key for key, _, _, is_generator in PAGES if not is_generator]
    assert set(system_keys) == set(window._system_shells)
    for key in system_keys:
        window._show_system(key)
        assert window.stack.currentWidget() is window._system_shells[key]
        assert not window.param_panel.isVisibleTo(window)
    window._show_creation()
    assert window.stack.currentWidget() is window.creation
    # 首屏默认落在「AI 智能体」（六板块排第一）：参数面板此时让位给智能体页；
    # 切到任一生成模式后参数面板才回来。
    assert not window.param_panel.isVisibleTo(window)
    window.creation.switch_mode("t2v")
    assert window.param_panel.isVisibleTo(window)

    # 会话 CRUD（非交互部分）：新建 → 激活 → 持久化
    before = len(window.store.sessions)
    window._new_session()
    assert len(window.store.sessions) == before + 1
    assert window.store.active_id == window.store.sessions[0]["id"]

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