"""Main window for Chengu AI Studio — the MJ-style creation centre.

Three columns like the web console: session sidebar (left), the creation
page with five mode tabs + feed + composer (centre), parameter panel
(right). The ten legacy system pages are all kept: 首页概览 / 任务队列 /
生成历史 / 模型管理 / 引擎管理 / 系统设置 / 日志与诊断 are reachable from
the sidebar "系统功能" entries and return to creation via their top bar.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.core import i18n
from app.core.config import ConfigStore
from app.core.hardware import (
    cached_gpu_info,
    get_disk_free_gib,
    nvidia_smi_available,
    prime_gpu_cache,
)
from app.engine_registration import (
    VIDEO_I2V_ENGINE_ID,
    VIDEO_T2V_ENGINE_ID,
)
from app.tasks.engine_manager import EngineManager
from app.tasks.models import TaskStatus
from app.tasks.service import TaskService
from app.ui.pages.creation_page import CreationPage
from app.ui.pages.engine_status_page import EngineStatusPage
from app.ui.pages.settings_page import SettingsPage
from app.ui.pages.task_list_page import TaskListPage
from app.ui.session_store import SessionStore
from app.ui.theme import build_stylesheet, palette, system_prefers_dark
from app.ui.widgets.param_panel import ParamPanel, normalize_settings

PAGES: tuple[tuple[str, str, str, bool], ...] = (
    ("home", "首页概览", "系统概览", False),
    ("image", "AI 图片", "图片创作", True),
    ("video", "AI 视频", "视频创作", True),
    ("music", "AI 音乐", "音乐创作", True),
    ("queue", "任务队列", "查看任务", False),
    ("history", "生成历史", "本地历史", False),
    ("models", "模型管理", "本地模型", False),
    ("engines", "引擎管理", "引擎状态", False),
    ("settings", "系统设置", "本地配置", False),
    ("diagnostics", "日志与诊断", "诊断信息", False),
)

# 系统功能入口（image/video/music 三页已并入创作页五模式 Tab）
_SYSTEM_KEYS = ("home", "queue", "history", "models", "engines", "settings", "diagnostics")


class _SessionRow(QWidget):
    clicked = Signal()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
        else:
            super().mousePressEvent(event)


class SystemPageShell(QWidget):
    """Wrap a legacy system page with a '← 返回创作' top bar."""

    backRequested = Signal()

    def __init__(
        self, title: str, description: str, content: QWidget, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        bar = QFrame(self)
        bar.setObjectName("sysTopBar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(16, 10, 16, 10)
        row.setSpacing(12)
        back = QPushButton("← 返回创作", bar)
        back.setObjectName("btnMini")
        back.clicked.connect(self.backRequested)
        row.addWidget(back)
        heading = QLabel(title, bar)
        heading.setObjectName("sysTitle")
        row.addWidget(heading)
        row.addStretch(1)
        desc = QLabel(description, bar)
        desc.setObjectName("sysDesc")
        row.addWidget(desc)
        layout.addWidget(bar)
        layout.addWidget(content, 1)
        # 语言切换时按源串重设顶栏（页面正文不在这里翻译，避免动态内容被改写）。
        self._back_source = back.text()
        self._title_source = title
        self._desc_source = description
        self.chrome_widgets = (back, heading, desc)

    def retranslate(self) -> None:
        self.chrome_widgets[0].setText(i18n.tr(self._back_source))
        self.chrome_widgets[1].setText(i18n.tr(self._title_source))
        self.chrome_widgets[2].setText(i18n.tr(self._desc_source))


class _OverviewPage(QWidget):
    """首页概览：与旧版 dashboard 相同的诚实摘要，可随打开刷新。"""

    def __init__(self, window: "MainWindow") -> None:
        super().__init__()
        self.window_ref = window
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(32, 24, 32, 32)
        self._layout.setSpacing(12)
        self.refresh()

    def _clear(self) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def refresh(self) -> None:
        self._clear()
        window = self.window_ref
        heading = QLabel(i18n.tr("首页概览"))
        heading.setObjectName("pageHeading")
        self._layout.addWidget(heading)
        self._layout.addWidget(QLabel("辰鹄 AI 创作中心 · 本地工作区（网页端同款界面）"))
        gpu_info = cached_gpu_info()
        if gpu_info is None:
            gpu_summary = (
                "GPU：显存信息探测中（nvidia-smi 在后台跑），稍后重开本页即可看到"
                if nvidia_smi_available()
                else "GPU：未检测到 NVIDIA GPU 或 nvidia-smi 不可用"
            )
        else:
            total_gib = gpu_info.memory_total_mib / 1024
            free_gib = gpu_info.memory_free_mib / 1024
            gpu_summary = (
                f"GPU：{gpu_info.name} · 显存 {total_gib:.1f} GiB，"
                f"可用 {free_gib:.1f} GiB"
            )
        self._layout.addWidget(QLabel(gpu_summary))
        config = window.config_store.load()
        output_free = get_disk_free_gib(Path(config["output_dir"]))
        self._layout.addWidget(
            QLabel(
                f"输出盘剩余空间：{output_free:.1f} GiB"
                if output_free is not None
                else "输出盘剩余空间：不可用"
            )
        )
        names = window._engine_names()
        self._layout.addWidget(
            QLabel(
                "引擎：" + " / ".join(names) + "已接入，真机验收进行中"
                if names
                else "引擎：尚无引擎通过真实推理与离线验证"
            )
        )
        model_parts = []
        if window._video_ready:
            model_parts.append(
                "Wan2.1 视频引擎已接入（T2V + I2V）"
                if Path(config.get("video_model_dir", "")).exists()
                else "Wan2.1 视频引擎已接入，权重待下载（models/wan2.1）"
            )
        else:
            model_parts.append(
                "视频引擎未注册 · 需 .venv-video 与 Wan2.1 权重"
                "（scripts/setup_video_venv.ps1、scripts/download_video_weights.py）"
            )
        model_parts.append("文生图 / 图生图 / AI 音乐已下架（不满足 6GB 峰值 + 中英文 Prompt + 可商用）")
        self._layout.addWidget(
            QLabel(
                "模型：" + "；".join(model_parts)
                if model_parts
                else "模型：未安装 · 本阶段不会自动下载"
            )
        )
        if window.task_service is not None:
            try:
                records = window.task_service.list_tasks(limit=1000)
            except Exception:  # pragma: no cover - defensive
                records = ()
            active_count = sum(
                record.status
                in {TaskStatus.QUEUED, TaskStatus.PREPARING, TaskStatus.RUNNING}
                for record in records
            )
            self._layout.addWidget(
                QLabel(f"本地任务记录：{len(records)} · 活动任务：{active_count}")
            )
        self._layout.addWidget(QLabel(f"任务数据库：{window.database_path}"))
        self._layout.addWidget(QLabel("界面：深色/浅色自动跟随系统（CHENGUAI_THEME 可强制）"))
        self._layout.addStretch(1)


class MainWindow(QMainWindow):
    def __init__(
        self,
        config_store: ConfigStore,
        logger,
        database_path: str,
        task_service: TaskService | None = None,
        engine_manager: EngineManager | None = None,
        service_loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        super().__init__()
        self.config_store = config_store
        self.logger = logger
        self.database_path = database_path
        self.task_service = task_service
        self.engine_manager = engine_manager
        self.service_loop = service_loop
        registered = {
            status.engine_id
            for status in (engine_manager.list_statuses() if engine_manager else ())
        }
        self._video_ready = bool(
            {VIDEO_T2V_ENGINE_ID, VIDEO_I2V_ENGINE_ID} & registered
        )
        self._theme_dark: bool | None = None
        # 界面语言：中文源码 + 词典换壳（词典在 app/web/static/i18n，与网页端同一份）。
        self._ui_language = i18n.set_language(self._configured_language())
        # 显存探测起守护线程：nvidia-smi 在被占满的卡上可能跑一秒以上，
        # 不提前预热的话，第一次刷新首页会把界面冻在那里。
        prime_gpu_cache()
        # 打开程序先落在「AI 智能体」：顶部 Tab 也是它排在最前，两端保持一致。
        self._creation_mode = "agent"
        self._chrome: list[tuple[QWidget, str, str]] = []
        self._nav_entries: dict[str, QPushButton] = {}
        self.setWindowTitle(self._window_title())
        self.resize(1280, 800)
        self.setMinimumSize(980, 640)
        self._apply_current_theme()

        self.data_dir = Path(database_path).parent
        self.store = SessionStore(self.data_dir, logger)
        self.store.settings = normalize_settings(self.store.settings)

        root = QWidget()
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # ------------------------------------------------------------- 侧栏
        sidebar = QFrame()
        sidebar.setObjectName("sideColumn")
        sidebar.setFixedWidth(250)
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(14, 18, 14, 14)
        side_layout.setSpacing(8)
        brand = QLabel("辰鹄AI创作中心", sidebar)
        brand.setObjectName("brand")
        side_layout.addWidget(brand)
        footer = QLabel(self._phase_footer_text(), sidebar)
        footer.setObjectName("brandSub")
        footer.setWordWrap(True)
        side_layout.addWidget(footer)

        # 🌏 语言菜单：一次只显示一种语言，选择写回 config.json 的 ui_language。
        self._brand = brand
        self._footer = footer
        self.lang_button = QToolButton(sidebar)
        self.lang_button.setObjectName("iconBtn")
        self.lang_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.lang_button.setText(self._language_button_text())
        self.lang_button.setToolTip("界面语言（与网页端各存各的，互不覆盖）")
        self._lang_menu = QMenu(self.lang_button)
        for entry in i18n.languages():
            action = self._lang_menu.addAction(f"{entry['native']} · {entry['english']}")
            action.setCheckable(True)
            action.setChecked(entry["code"] == i18n.current_language())
            action.triggered.connect(
                lambda _checked=False, code=entry["code"]: self._set_ui_language(code)
            )
        self.lang_button.setMenu(self._lang_menu)
        side_layout.addWidget(self.lang_button)

        new_button = QPushButton("＋ 新建会话", sidebar)
        new_button.setObjectName("btnNew")
        new_button.clicked.connect(self._new_session)
        side_layout.addWidget(new_button)

        sessions_caption = QLabel("会话", sidebar)
        sessions_caption.setObjectName("sectionLabel")
        side_layout.addWidget(sessions_caption)
        self._new_button = new_button
        self._sessions_caption = sessions_caption
        session_scroll = QScrollArea(sidebar)
        session_scroll.setObjectName("sessionScroll")
        session_scroll.setWidgetResizable(True)
        session_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        rows_host = QWidget()
        rows_host.setObjectName("sessionRowsHost")
        self._rows_layout = QVBoxLayout(rows_host)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(2)
        session_scroll.setWidget(rows_host)
        side_layout.addWidget(session_scroll, 3)

        nav_caption = QLabel("系统功能", sidebar)
        nav_caption.setObjectName("sectionLabel")
        side_layout.addWidget(nav_caption)
        self._nav_caption = nav_caption
        nav_scroll = QScrollArea(sidebar)
        nav_scroll.setObjectName("navScroll")
        nav_scroll.setWidgetResizable(True)
        nav_host = QWidget()
        nav_host.setObjectName("navHost")
        self._nav_layout = QVBoxLayout(nav_host)
        self._nav_layout.setContentsMargins(0, 0, 0, 0)
        self._nav_layout.setSpacing(2)
        nav_scroll.setWidget(nav_host)
        side_layout.addWidget(nav_scroll, 2)

        # ------------------------------------------------------------ 中栏
        self.stack = QStackedWidget()

        # ------------------------------------------------------------ 右栏
        self.param_panel = ParamPanel(self.store.settings, self.store.persist)
        self.param_panel.setFixedWidth(320)

        refs_dir = self.data_dir / "refs"
        try:
            refs_dir.mkdir(parents=True, exist_ok=True)
        except OSError:  # pragma: no cover
            pass
        self.agent_service = self._build_agent_service(task_service, engine_manager, logger)
        self.creation = CreationPage(
            self.store,
            self.param_panel,
            task_service,
            engine_manager,
            service_loop,
            refs_dir,
            logger,
            palette(self._theme_dark or system_prefers_dark()),
            self.agent_service,
        )
        self.stack.addWidget(self.creation)
        self.creation.systemRequested.connect(self._show_system)
        self.creation.sessionsChanged.connect(self._render_sessions)
        self.creation.modeChanged.connect(self._on_mode_changed)
        self._creation_mode = str(self.store.settings.get("mode") or "t2i")

        # -------------------------------------------------------- 系统页面
        self._system_shells: dict[str, SystemPageShell] = {}
        labels = {key: label for key, label, _, _ in PAGES}
        descriptions = {key: desc for key, _, desc, _ in PAGES}
        for key in _SYSTEM_KEYS:
            content = self._build_system_page(key)
            shell = SystemPageShell(labels[key], descriptions[key], content)
            shell.backRequested.connect(self._show_creation)
            self._system_shells[key] = shell
            self.stack.addWidget(shell)
            entry = QPushButton(labels[key], nav_host)
            entry.setObjectName("navEntry")
            entry.clicked.connect(lambda _checked=False, k=key: self._show_system(k))
            self._nav_layout.addWidget(entry)
            self._nav_entries[key] = entry
        self._nav_layout.addStretch(1)

        root_layout.addWidget(sidebar)
        root_layout.addWidget(self.stack, 1)
        root_layout.addWidget(self.param_panel)
        self.setCentralWidget(root)
        self._render_sessions()
        self.creation.refresh()
        # 记录一次中文源串，之后切换语言都从源串重设（zh 时是原样返回，等于不做事）。
        self._collect_chrome()
        self._retranslate_chrome()

        self._theme_timer = QTimer(self)
        self._theme_timer.setInterval(4000)
        self._theme_timer.timeout.connect(self._refresh_theme)
        self._theme_timer.start()
        self.logger.info("主窗口初始化完成（MJ 同款三栏创作界面）")

    def _apply_runtime_settings(self, config: dict) -> None:
        r"""设置页保存后把「自动另存目录」立刻同步给任务服务（不用重启程序）。

        产物写进「输出目录」（本工具里即时显示的那一份），并固定复制一份到项目内 output\；
        auto_save_dir 非空时再复制一份到用户自选的目录。两端读同一份 config.json，所以
        网页端改了设置，这里提交的任务也会按新目录复制（反之亦然）。
        """
        self.task_service.set_auto_save_dir(str(config.get("auto_save_dir") or ""))

    # ------------------------------------------------------------ 系统页面
    def _build_system_page(self, key: str) -> QWidget:
        if key == "home":
            self._overview_page = _OverviewPage(self)
            return self._overview_page
        if key == "queue":
            return TaskListPage(self.task_service, active=True)
        if key == "history":
            return TaskListPage(self.task_service, active=False)
        if key == "engines":
            return EngineStatusPage(self.engine_manager)
        if key == "settings":
            return SettingsPage(
                self.config_store,
                lambda message, is_error: (
                    self.creation.toast.show(message, "error" if is_error else "info"),
                    self.logger.error(message)
                    if is_error
                    else self.logger.info(message),
                ),
                on_saved=self._apply_runtime_settings,
            )
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(32, 24, 32, 32)
        layout.setSpacing(10)
        text = (
            "模型清单、校验与下载确认将在后续阶段实现；模型不会自动下载，"
            "所有权重必须经 SHA-256 校验后按许可协议手动放置。"
            if key == "models"
            else "日志保存在本机应用数据目录（与任务数据库同目录），无远程错误上报。"
        )
        body = QLabel(text)
        body.setWordWrap(True)
        layout.addWidget(body)
        if key == "diagnostics":
            layout.addWidget(QLabel(f"数据目录：{self.data_dir}"))
            layout.addWidget(QLabel(f"任务数据库：{self.database_path}"))
            layout.addWidget(QLabel("桌面端与网页端共用同一任务服务：先启动的一方持有执行锁。"))
        layout.addStretch(1)
        return page

    def _show_system(self, key: str) -> None:
        shell = self._system_shells.get(key)
        if shell is None:
            return
        self.stack.setCurrentWidget(shell)
        self.param_panel.setVisible(False)
        if key == "home":
            self._overview_page.refresh()
        content = shell.layout().itemAt(1).widget()
        refresh = getattr(content, "refresh", None)
        if callable(refresh):
            refresh()

    def _show_creation(self) -> None:
        self.stack.setCurrentWidget(self.creation)
        self.param_panel.setVisible(self._creation_mode != "agent")

    # ------------------------------------------------------------ 界面语言
    def _configured_language(self) -> str:
        try:
            return str(self.config_store.load().get("ui_language") or "zh")
        except Exception as exc:  # pragma: no cover - 配置坏了也要能把窗口开起来
            self.logger.warning("读取 ui_language 失败，按简体中文启动：%s", exc)
            return "zh"

    def _language_button_text(self) -> str:
        return f"🌏 {i18n.native_name()}"

    def _set_ui_language(self, code: str) -> None:
        applied = i18n.set_language(code)
        self._ui_language = applied
        saved = True
        try:
            config = self.config_store.load()
            config["ui_language"] = applied
            self.config_store.save(config)
        except Exception as exc:
            saved = False
            self.logger.warning("ui_language 写入 config.json 失败：%s", exc)
        codes = [entry["code"] for entry in i18n.languages()]
        for index, action in enumerate(self._lang_menu.actions()):
            action.setChecked(index < len(codes) and codes[index] == applied)
        self.lang_button.setText(self._language_button_text())
        self.setWindowTitle(self._window_title())
        self._retranslate_chrome()
        for shell in self._system_shells.values():
            shell.retranslate()
        self.creation.retranslate_tabs()
        self.creation.retranslate_agent()
        translated, total = i18n.coverage()
        self.logger.info(
            "界面语言切换为 %s（词典 %d/%d 条；词典外的正文保持中文）", applied, translated, total
        )
        if applied != "zh" and translated == 0:
            self.creation.toast.show(
                f"语言词典缺失或损坏：{i18n.load_error()}",
                "error",
            )
        elif not saved:
            self.creation.toast.show(
                f"{i18n.language_label(applied)}：本次会话生效，但没能写入 config.json",
                "error",
            )
        else:
            self.creation.toast.show(f"界面语言：{i18n.language_label(applied)}", "info")

    def _register_text(self, widget: QWidget | None, kind: str = "text") -> None:
        if widget is None:
            return
        getter, setter = ("text", "setText") if kind == "text" else ("placeholderText", "setPlaceholderText")
        read = getattr(widget, getter, None)
        write = getattr(widget, setter, None)
        if not callable(read) or not callable(write):
            return
        current = read()
        if isinstance(current, str) and current:
            self._chrome.append((widget, kind, current))

    def _walk_chrome(self, host: QWidget | None) -> None:
        """把 host 里已登记在 source-zh.txt 的静态文案登记进词典表。

        判定用的是「是不是登记过的源串」，不是「是不是中文」，所以会话标题、
        GPU 摘要、任务正文这类动态内容不会被登记，也就不会被语言切换改写。
        """
        if host is None:
            return
        skip = {"pillOk", "pillBad", "pill", "tabBadge"}
        for widget in (host, *host.findChildren(QWidget)):
            if widget.objectName() in skip:
                continue
            for kind, getter, setter in (
                ("text", "text", "setText"),
                ("placeholder", "placeholderText", "setPlaceholderText"),
            ):
                read = getattr(widget, getter, None)
                write = getattr(widget, setter, None)
                if not callable(read) or not callable(write):
                    continue
                try:
                    current = read()
                except RuntimeError:  # pragma: no cover - 控件正在被销毁
                    continue
                if isinstance(current, str) and i18n.is_source(current):
                    self._chrome.append((widget, kind, current))
                    break

    def _collect_chrome(self) -> None:
        self._chrome = []
        for widget in (self._brand, self._new_button, self._sessions_caption, self._nav_caption):
            self._register_text(widget)
        for entry in self._nav_entries.values():
            self._register_text(entry)
        self._walk_chrome(self.param_panel)
        self._walk_chrome(self.creation.findChild(QFrame, "topBar"))
        self._walk_chrome(self.creation.findChild(QFrame, "tabBar"))

    def _window_title(self) -> str:
        """窗口标题：中文保持原有写法，其它语言用词典里的品牌译名。"""
        if i18n.current_language() == "zh":
            return "辰鹄 AI 创作中心"
        return i18n.tr("辰鹄AI创作中心")

    def _retranslate_chrome(self) -> None:
        alive: list[tuple[QWidget, str, str]] = []
        for widget, kind, source in self._chrome:
            value = i18n.tr(source)
            try:
                if kind == "title":
                    widget.setWindowTitle(value)
                else:
                    setter = getattr(
                        widget,
                        "setPlaceholderText" if kind == "placeholder" else "setText",
                        None,
                    )
                    if not callable(setter):
                        raise RuntimeError("控件不再支持文本设置")
                    setter(value)
            except RuntimeError:  # 控件已被替换，丢掉这条登记
                continue
            alive.append((widget, kind, source))
        self._chrome = alive

    def _on_mode_changed(self, mode: str) -> None:
        self._creation_mode = mode
        # 规则 5：一个模块一套外观。这里换强调色（composerBar 顶边、主按钮、进度条都跟着走）。
        if self._theme_dark is not None:
            self.setStyleSheet(build_stylesheet(self._theme_dark, mode))
        if self.stack.currentWidget() is self.creation:
            # AI 智能体自己带输入区，右侧参数栏在这种模式下没有意义。
            self.param_panel.setVisible(mode != "agent")

    # --------------------------------------------------------- 会话侧栏
    def _clear_layout(self, layout: QVBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _new_session(self) -> None:
        self._show_creation()
        self.creation.new_session()

    def _render_sessions(self) -> None:
        self._clear_layout(self._rows_layout)
        active = self.store.active_id
        for session in self.store.sessions:
            row = _SessionRow()
            row.setObjectName("sideRow")
            row.setProperty("active", "true" if session["id"] == active else "false")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(8, 5, 6, 5)
            row_layout.setSpacing(8)
            thumb = QLabel(row)
            thumb.setObjectName("sideThumb")
            thumb.setFixedSize(34, 34)
            thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
            thumb_path = session.get("thumb")
            if thumb_path:
                pixmap = QPixmap(thumb_path)
                if not pixmap.isNull():
                    thumb.setPixmap(
                        pixmap.scaled(
                            34,
                            34,
                            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                            Qt.TransformationMode.SmoothTransformation,
                        )
                    )
            else:
                thumb.setText("🖼")
            row_layout.addWidget(thumb)
            title = QLabel(str(session.get("title") or "会话"), row)
            title.setObjectName("sideTitle")
            title.setMinimumWidth(0)
            row_layout.addWidget(title, 1)
            rename = QPushButton("✎", row)
            rename.setObjectName("sideDel")
            rename.setToolTip("重命名会话")
            rename.clicked.connect(
                lambda _checked=False, sid=session["id"]: self._rename_session(sid)
            )
            row_layout.addWidget(rename)
            remove = QPushButton("🗑", row)
            remove.setObjectName("sideDel")
            remove.setToolTip("删除会话")
            remove.clicked.connect(
                lambda _checked=False, sid=session["id"]: self._delete_session(sid)
            )
            row_layout.addWidget(remove)
            row.clicked.connect(lambda sid=session["id"]: self._activate_session(sid))
            self._rows_layout.addWidget(row)
        if not self.store.sessions:
            hint = QLabel("尚无会话 · 直接提交即自动创建", self._rows_layout.parent())
            hint.setObjectName("sideTitle")
            self._rows_layout.addWidget(hint)
        self._rows_layout.addStretch(1)

    def _activate_session(self, session_id: str) -> None:
        self.store.activate(session_id)
        self._render_sessions()
        self.creation.refresh()

    def _rename_session(self, session_id: str) -> None:
        session = self.store.get(session_id)
        if session is None:
            return
        text, ok = QInputDialog.getText(
            self, "重命名会话", "会话名称", text=str(session.get("title") or "")
        )
        if ok and text.strip():
            self.store.rename(session_id, text)
            self._render_sessions()
            self.creation.refresh()

    def _delete_session(self, session_id: str) -> None:
        session = self.store.get(session_id)
        if session is None:
            return
        answer = QMessageBox.question(
            self,
            "删除会话",
            "仅删除本机会话分组，任务与产物不受影响。确认删除？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.store.delete(session_id)
        self._render_sessions()
        self.creation.refresh()

    # ------------------------------------------------------------ 主题
    def _build_agent_service(
        self, task_service: TaskService | None, engine_manager: EngineManager | None, logger
    ):
        """AI 智能体的后端服务；任务服务没起来时返回 None，界面会诚实说明。"""
        if task_service is None:
            return None
        try:
            from app.agent.service import AgentService

            return AgentService(
                task_service=task_service,
                engine_manager=engine_manager,
                config=self.config_store.load(),
                logger=logger,
            )
        except Exception as exc:  # pragma: no cover - 智能体不可用不应拖垮主窗口
            logger.warning("AI 智能体服务初始化失败：%s", exc)
            return None

    def sweep_orphan_llm(self) -> tuple[int, ...]:
        """启动时清掉上次没退干净的大模型服务进程（只认自家 llama-server.exe）。

        只按自家镜像名找、并且只杀「父进程已经不在」的那些：别的程序（包括用户
        自己跑的 Ollama、别的推理工具）一律不动，宁可显存暂时要不回来也不越界。
        """
        reap = getattr(self.agent_service, "sweep_orphan_llm", None)
        if not callable(reap):
            return ()
        try:
            return tuple(reap())
        except Exception as exc:  # noqa: BLE001 - 清扫失败不影响主窗口
            self.logger.warning("清扫孤儿大模型进程失败：%s", exc)
            return ()

    def shutdown_services(self) -> None:
        """退出时把它拉起的大模型服务进程一并结束，别留孤儿占着显存。

        生成引擎的 worker 由任务服务那条循环负责（aboutToQuit 里它先停）；这里只
        补智能体自己拉起的 llama-server。生成途中关窗也没关系：worker 有父进程
        看门狗，父进程一没它就自己退出，不会卡在等输入上变成没人收尾的孤儿。
        """
        stop = getattr(self.agent_service, "stop_llm_for_exit", None)
        if not callable(stop):
            return
        try:
            note = stop()
            if note:
                self.logger.info("退出清理：%s", note)
        except Exception as exc:  # noqa: BLE001 - 退出阶段任何失败都不能拦住关窗
            self.logger.warning("退出时停止大模型服务失败：%s", exc)

    def _engine_names(self) -> list[str]:
        names = []
        if self._video_ready:
            names.append("Wan2.1 视频")
        return names

    def _phase_footer_text(self) -> str:
        names = self._engine_names()
        if names:
            return "本地 · 离线 · 串行队列\n" + " / ".join(names) + " 已接入"
        return "本地 · 离线 · 串行队列\n生成引擎未安装"

    def _apply_current_theme(self) -> None:
        self._theme_dark = system_prefers_dark()
        self.setStyleSheet(build_stylesheet(self._theme_dark, getattr(self, "_creation_mode", None)))

    def _refresh_theme(self) -> None:
        dark = system_prefers_dark()
        if dark == self._theme_dark:
            return
        self._theme_dark = dark
        self.setStyleSheet(build_stylesheet(dark, getattr(self, "_creation_mode", None)))
        if hasattr(self, "creation"):
            self.creation.set_tokens(palette(dark))
        self.logger.info("界面主题已切换为%s", "深色" if dark else "浅色")
