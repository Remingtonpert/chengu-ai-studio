"""Phase 1 configuration, database, and navigation tests."""

import socket
import sqlite3
import logging
import subprocess
import time
import urllib.request
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from app.bootstrap import _port_in_use, create_application, start_web_console
from app.core.config import ConfigError, ConfigStore
from app.core.paths import AppPaths
from app.database.database import initialize_database
from app.tasks.engine_manager import EngineManager
from app.tasks.service import TaskService
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


def test_desktop_also_starts_web_console(tmp_path, monkeypatch):
    """桌面端启动必须顺手把网页控制台带起来。

    只开桌面端时 8765 上没有任何监听，浏览器打开就是「连不上」，网页端看起来永远坏了
    （2026-11-11 早上用户实测正是这个样子：桌面端出图成功，网页端打不开）。
    这里既验守门逻辑（测试环境默认不许顺手起服务），也用 CHENGUAI_FORCE_WEB_AUTOSTART=1
    放行一次真起服务：端口取临时空闲端口、应用目录指到 tmp_path（子进程继承环境变量），
    所以不碰用户真正的任务库，也不碰 8765。
    """
    paths = AppPaths.from_environment(app_home=tmp_path / "profile", data_root=tmp_path / "data")
    paths.prepare_app_home()
    initialize_database(paths.database_file)  # 让「队列里有没有活」这道退出守卫能真的查
    store = ConfigStore(paths)
    config = store.load()
    assert config["web_autostart"] is True  # 默认开着

    store.save({**config, "web_autostart": False})
    assert store.load()["web_autostart"] is False  # 想关掉就关掉
    store.save({**config})

    # 端口探活：自己 bind 的端口要认出来，已经关掉的端口要说没有
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        assert _port_in_use("127.0.0.1", port) is True
    assert _port_in_use("127.0.0.1", port) is False

    logger = logging.getLogger("test-web-console-autostart")
    service = TaskService(paths.database_file, EngineManager())
    real_popen = subprocess.Popen

    # 默认（PYTEST_CURRENT_TEST 已经在）与显式关掉：都不许起服务子进程
    def boom(*args, **kwargs):
        raise AssertionError("测试环境里不该起服务子进程")

    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.delenv("CHENGUAI_FORCE_WEB_AUTOSTART", raising=False)
    start_web_console(paths, {**config, "web_autostart": True}, service, logger)()
    monkeypatch.setenv("CHENGUAI_DISABLE_WEB_AUTOSTART", "1")
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    start_web_console(paths, {**config, "web_autostart": True}, service, logger)()
    monkeypatch.setattr(subprocess, "Popen", real_popen)

    # 放行一次：真带起来 → /api/health 应答 → stop() 之后端口干净。
    # 端口要写进这份临时 config.json：子进程自己读配置（应用目录已被 CHENGUAI_APP_HOME 指到
    # tmp_path），只在调用参数里改端口没用，那样子进程还是去绑 8765。
    monkeypatch.delenv("CHENGUAI_DISABLE_WEB_AUTOSTART", raising=False)
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests/test_phase1_foundation.py::x (call)")
    monkeypatch.setenv("CHENGUAI_FORCE_WEB_AUTOSTART", "1")
    monkeypatch.setenv("CHENGUAI_APP_HOME", str(tmp_path / "profile"))
    monkeypatch.setenv("CHENGUAI_DATA_ROOT", str(tmp_path / "data"))
    store.save({**config, "web_port": port})
    child_config = store.load()
    assert int(child_config["web_port"]) == port
    stop = start_web_console(paths, child_config, service, logger)
    try:
        deadline = time.time() + 25
        while time.time() < deadline and not _port_in_use("127.0.0.1", port):
            time.sleep(0.3)
        assert _port_in_use("127.0.0.1", port), "网页端没在临时端口上监听"
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=10) as response:
            assert response.status == 200
        # 自家网页端已经在听 → 第二次调用必须只记一行日志，绝不再起一个、也绝不杀别人家的服务
        monkeypatch.setattr(subprocess, "Popen", boom)
        try:
            again = start_web_console(paths, child_config, service, logger)
        finally:
            monkeypatch.setattr(subprocess, "Popen", real_popen)
        again()
    finally:
        stop()
    deadline = time.time() + 10
    while time.time() < deadline and _port_in_use("127.0.0.1", port):
        time.sleep(0.3)
    assert not _port_in_use("127.0.0.1", port), "桌面端退出后留下了孤儿网页端服务"