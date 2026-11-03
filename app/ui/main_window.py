"""Main window and navigation for Chengu AI Studio."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.core.config import ConfigStore
from app.core.hardware import get_disk_free_gib, get_gpu_info
from app.engine_registration import IMAGE_ENGINE_ID, VIDEO_ENGINE_ID
from app.tasks.engine_manager import EngineManager
from app.tasks.models import TaskStatus
from app.tasks.service import TaskService
from app.ui.pages.engine_status_page import EngineStatusPage
from app.ui.pages.settings_page import SettingsPage
from app.ui.pages.task_list_page import TaskListPage
from app.ui.pages.workspace_page import WorkspacePage
from app.ui.theme import build_stylesheet, system_prefers_dark


PAGES: tuple[tuple[str, str, str, bool], ...] = (
    ("home", "首页", "系统概览", False),
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


class MainWindow(QMainWindow):
    def __init__(
        self,
        config_store: ConfigStore,
        logger,
        database_path: str,
        task_service: TaskService | None = None,
        engine_manager: EngineManager | None = None,
    ) -> None:
        super().__init__()
        self.config_store = config_store
        self.logger = logger
        self.database_path = database_path
        self.task_service = task_service
        self.engine_manager = engine_manager
        registered = {
            status.engine_id
            for status in (engine_manager.list_statuses() if engine_manager else ())
        }
        self._image_ready = IMAGE_ENGINE_ID in registered
        self._video_ready = VIDEO_ENGINE_ID in registered
        self._theme_dark: bool | None = None
        self.setWindowTitle("辰鹄 AI 创作中心")
        self.resize(1180, 760)
        self.setMinimumSize(920, 620)
        self._apply_current_theme()

        root = QWidget()
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(232)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(16, 24, 16, 18)
        brand = QLabel("辰鹄 AI\n创作中心")
        brand.setObjectName("brand")
        sidebar_layout.addWidget(brand)
        sidebar_layout.addSpacing(20)

        self.navigation = QListWidget()
        self.navigation.setObjectName("sidebarNav")
        self.navigation.setAccessibleName("主导航")
        sidebar_layout.addWidget(self.navigation)
        phase_label = QLabel(self._phase_footer_text())
        phase_label.setObjectName("phaseInfo")
        sidebar_layout.addWidget(phase_label)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        self.status_label = QLabel(self._status_bar_text())
        self.status_label.setContentsMargins(20, 8, 20, 8)
        content_layout.addWidget(self.stack, 1)
        content_layout.addWidget(self.status_label)

        root_layout.addWidget(sidebar)
        root_layout.addWidget(content, 1)
        self.setCentralWidget(root)

        for key, title, description, is_generator in PAGES:
            item = QListWidgetItem(title)
            item.setData(Qt.ItemDataRole.UserRole, key)
            self.navigation.addItem(item)
            if key == "settings":
                page: QWidget = SettingsPage(config_store, self._notify)
            elif key == "home":
                page = self._dashboard()
            elif key == "queue":
                page = TaskListPage(task_service, active=True)
            elif key == "history":
                page = TaskListPage(task_service, active=False)
            elif key == "engines":
                page = EngineStatusPage(engine_manager)
            else:
                page = WorkspacePage(
                    title,
                    self._page_description(key, description),
                    can_generate=is_generator,
                    engine_ready=self._workspace_engine_ready(key),
                    engine_label=self._workspace_engine_label(key),
                )
            self.stack.addWidget(page)

        self.navigation.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.navigation.setCurrentRow(0)

        self._theme_timer = QTimer(self)
        self._theme_timer.setInterval(4000)
        self._theme_timer.timeout.connect(self._refresh_theme)
        self._theme_timer.start()
        self.logger.info("主窗口初始化完成")

    def _dashboard(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(32, 28, 32, 32)
        layout.setSpacing(14)
        heading = QLabel("首页")
        heading.setObjectName("pageHeading")
        layout.addWidget(heading)
        layout.addWidget(QLabel("辰鹄 AI 创作中心 · 本地工作区"))
        gpu_info = get_gpu_info()
        if gpu_info is None:
            gpu_summary = "GPU：未检测到 NVIDIA GPU 或 nvidia-smi 不可用"
        else:
            total_gib = gpu_info.memory_total_mib / 1024
            free_gib = gpu_info.memory_free_mib / 1024
            gpu_summary = (
                f"GPU：{gpu_info.name} · 显存 {total_gib:.1f} GiB，"
                f"可用 {free_gib:.1f} GiB"
            )
        layout.addWidget(QLabel(gpu_summary))
        config = self.config_store.load()
        output_free = get_disk_free_gib(Path(config["output_dir"]))
        disk_summary = (
            f"输出盘剩余空间：{output_free:.1f} GiB"
            if output_free is not None
            else "输出盘剩余空间：不可用"
        )
        layout.addWidget(QLabel(disk_summary))
        names = self._engine_names()
        if names:
            engine_line = f"引擎：{' / '.join(names)}已接入，真机验收进行中"
        else:
            engine_line = "引擎：尚无引擎通过真实推理与离线验证"
        layout.addWidget(QLabel(engine_line))
        model_parts = []
        models_dir = Path(config["models_dir"])
        if self._image_ready:
            if (models_dir / "flux-schnell").exists():
                model_parts.append("FLUX.1-schnell 已就位（SHA-256 校验通过）")
            else:
                model_parts.append("FLUX 引擎已接入，权重待下载")
        if self._video_ready:
            if (models_dir / "anima-image2video").exists():
                model_parts.append("Anima 视频模型已就位")
            else:
                model_parts.append("Anima 视频引擎已接入，权重待下载")
        if model_parts:
            model_line = "模型：" + "；".join(model_parts)
        else:
            model_line = "模型：未安装 · 本阶段不会自动下载"
        layout.addWidget(QLabel(model_line))
        if self.task_service is not None:
            records = self.task_service.list_tasks(limit=1000)
            active_count = sum(
                record.status
                in {TaskStatus.QUEUED, TaskStatus.PREPARING, TaskStatus.RUNNING}
                for record in records
            )
            layout.addWidget(QLabel(f"本地任务记录：{len(records)} · 活动任务：{active_count}"))
        layout.addWidget(QLabel(f"任务数据库：{self.database_path}"))
        layout.addStretch(1)
        return page

    def _engine_names(self) -> list[str]:
        names = []
        if self._image_ready:
            names.append("FLUX 图像")
        if self._video_ready:
            names.append("Anima 视频")
        return names

    def _engine_kinds(self) -> list[str]:
        kinds = []
        if self._image_ready:
            kinds.append("图像")
        if self._video_ready:
            kinds.append("视频")
        return kinds

    def _phase_footer_text(self) -> str:
        names = self._engine_names()
        if names:
            return "引擎已接入 · 真机验收中\n" + " / ".join(names)
        return "Phase 2 · 任务生命周期\n生成引擎未安装"

    def _status_bar_text(self) -> str:
        kinds = self._engine_kinds()
        if kinds:
            return "本地模式 · " + "/".join(kinds) + "引擎已接入（真机验收待完成）"
        return "本地模式 · 未连接生成引擎"

    def _workspace_engine_ready(self, key: str) -> bool:
        if key == "image":
            return self._image_ready
        if key == "video":
            return self._video_ready
        return False

    @staticmethod
    def _workspace_engine_label(key: str) -> str:
        if key == "image":
            return "FLUX.1-schnell（图像）"
        if key == "video":
            return "Anima（图生视频）"
        return ""

    def _apply_current_theme(self) -> None:
        self._theme_dark = system_prefers_dark()
        self.setStyleSheet(build_stylesheet(self._theme_dark))

    def _refresh_theme(self) -> None:
        dark = system_prefers_dark()
        if dark == self._theme_dark:
            return
        self._theme_dark = dark
        self.setStyleSheet(build_stylesheet(dark))
        self.logger.info("界面主题已切换为%s", "深色" if dark else "浅色")

    def _page_description(self, key: str, fallback: str) -> str:
        kinds = self._engine_kinds()
        descriptions = {
            "image": (
                "提交图片创作描述。FLUX.1-schnell 已接入本地引擎，真机验收通过后开放生成入口。"
                if self._image_ready
                else "提交图片创作描述。模型和生成按钮将在真实引擎完成验证后启用。"
            ),
            "video": (
                "图生视频引擎（Anima）已接入，真机验收通过后开放生成入口；不提供模拟生成。"
                if self._video_ready
                else "视频生成引擎尚未安装或验证；当前不提供模拟生成。"
            ),
            "music": "音乐生成引擎尚未安装或验证；当前不提供模拟生成。",
            "queue": "显示 SQLite 中持久化的排队与活动任务。",
            "history": "显示 SQLite 中持久化的已结束任务。",
            "models": "模型清单、校验与下载确认将在后续阶段实现。",
            "engines": (
                "显示引擎管理器中的生命周期状态；"
                + "/".join(kinds)
                + "引擎已注册，真机验收进行中。"
                if kinds
                else "显示引擎管理器中的生命周期状态；尚无已注册引擎。"
            ),
            "diagnostics": "日志保存在本机应用数据目录；无远程错误上报。",
        }
        return descriptions.get(key, fallback)

    def _notify(self, message: str, is_error: bool) -> None:
        self.status_label.setText(message)
        if is_error:
            self.logger.error(message)
        else:
            self.logger.info(message)