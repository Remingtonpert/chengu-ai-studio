"""Creation-centre middle column — the desktop twin of the web console.

Top bar (session title + engine/health pills), three tabs (AI 智能体 /
文生视频 / 图生视频), the session feed and the composer bar. Every submit
path mirrors ``app/web/static/app.js`` 1:1: imagine (one video task, VRAM
gated) / retry / cancel / lightbox. Image and music boards are retired: the
backend only accepts ``task_type == "video"``, so legacy image / music records
stay viewable (open, save-as) but never spawn new work. The shared TaskService
and EngineManager are the same objects the web console drives; sessions live in
the local ``desktop_sessions.json`` via :class:`SessionStore`.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.core import i18n
from app.core.hardware import (
    cached_gpu_info,
    effective_free_mib,
    forget_vram_cache,
    get_gpu_info,
    holders_summary,
    nvidia_smi_available,
    vram_holders_cached,
)
from app.tasks.engine_manager import EngineManager
from app.tasks.models import TaskRecord, TaskRequest
from app.tasks.service import TaskService
from app.ui.session_store import SessionStore, new_id
from app.ui.widgets.common import (
    ToastHub,
    fmt_time,
    now_iso,
    open_local_file,
    rand_seed,
)
from app.ui.widgets.composer import Composer
from app.ui.widgets.feed import ACTIVE_STATUSES, FeedArea
from app.ui.widgets.lightbox import LightboxDialog
from app.ui.widgets.param_panel import (
    ENGINE_TASK_TYPES,
    MODALITY,
    MODE_ENGINE_ALLOW,
    ParamPanel,
    VIDEO_MAX_PIXELS,
    snap_frames,
)
from app.ui.widgets.tiles import (
    AUDIO_SUFFIXES,
    IMAGE_SUFFIXES,
    VIDEO_SUFFIXES,
    output_paths,
)

ABS_PATH_RE = re.compile(r"^[A-Za-z]:[\\/]")
REF_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".bmp"})
NEEDS_REF = {"i2i", "i2v"}
VIDEO_FPS = 16  # Wan2.1 原生帧率（Anima 时代的历史 24fps 已随引擎替换）
# Wan2.1 提交前的显存预检：按 6GB 显存档位设计（bf16 + 逐层 CPU 卸载，
# 视频档真机实测峰值 2021MB；图片档单帧、预期更低但未实测）。整机显存低于
# 6GB 直接诚实拦截；起跑还需要约 1500MB 空闲显存（引擎侧 _SEQUENTIAL_MIN_FREE_MB）。
VIDEO_MIN_VRAM_MB = 6_144
VIDEO_FREE_FLOOR_MB = 2_000
# ACE-Step 1.5 同样按 6GB 档设计（LM/DiT 走 CPU 卸载，峰值为预期值未实测）；
# 引擎 health_check 要求空闲显存也在 6GB 档水位，桌面端预检与它同一把尺。
MUSIC_MIN_VRAM_MB = 6_144
MUSIC_FREE_FLOOR_MB = 6_144
ADVANCED_WHITELIST_IMAGE = {
    "width",
    "height",
    "steps",
    "seed",
    "negative_prompt",
    "init_image_path",
}
ADVANCED_WHITELIST_VIDEO = {
    "width",
    "height",
    "num_frames",
    "steps",
    "seed",
    "negative_prompt",
    "init_image_path",
}
MODE_TABS = (
    ("agent", "AI 智能体"),
    ("t2i", "文生图"),
    ("i2i", "图生图"),
    ("t2v", "文生视频"),
    ("i2v", "图生视频"),
    ("music", "AI音乐"),
)
# 智能体回报的 task_type 跳回具体创作 Tab（image/video 只有一个默认入口）。
_TASK_TYPE_TO_MODE = {"image": "t2i", "video": "t2v", "music": "music"}


class TabButton(QPushButton):
    """Mode tab with the blue active-task badge (mirrors ``.tab-badge``)."""

    def __init__(self, mode: str, label: str, parent: QWidget | None = None) -> None:
        super().__init__(label, parent)
        self.mode = mode
        self.source_label = label
        self.setObjectName("tab")
        self.setCheckable(True)
        self.badge = QLabel("", self)
        self.badge.setObjectName("tabBadge")
        self.badge.hide()

    def set_count(self, count: int) -> None:
        if count > 0:
            self.badge.setText(str(count))
            self.badge.adjustSize()
            self.badge.move(self.width() - self.badge.width() - 8, 4)
            self.badge.show()
        else:
            self.badge.hide()


class CreationPage(QWidget):
    systemRequested = Signal(str)   # open a system page in MainWindow
    sessionsChanged = Signal()      # sidebar re-render hint
    modeChanged = Signal(str)
    _guiToast = Signal(str, str)    # (message, kind) — cross-thread safe

    def __init__(
        self,
        store: SessionStore,
        panel: ParamPanel,
        task_service: TaskService | None,
        engine_manager: EngineManager | None,
        service_loop: asyncio.AbstractEventLoop | None,
        refs_dir: Path,
        logger,
        tokens: dict[str, str],
        agent_service: object | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("creationPage")
        self.store = store
        self.panel = panel
        self.task_service = task_service
        self.engine_manager = engine_manager
        self.service_loop = service_loop
        self.refs_dir = Path(refs_dir)
        self.logger = logger
        self.agent_service = agent_service
        self.agent_page: QWidget | None = None
        self.tokens = tokens
        self._submitting = False
        self._engines: list = []
        self._tasks: dict[str, TaskRecord] = {}
        self._retried: set[str] = set()
        self._queue: dict[str, int] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        top = QFrame(self)
        top.setObjectName("topBar")
        top_row = QHBoxLayout(top)
        top_row.setContentsMargins(18, 9, 18, 9)
        top_row.setSpacing(10)
        self.title_label = QLabel("想象", top)
        self.title_label.setObjectName("topTitle")
        self._title_source = self.title_label.text()
        top_row.addWidget(self.title_label)
        top_row.addStretch(1)
        self.engine_pill = QLabel("无引擎", top)
        self.engine_pill.setObjectName("pillBad")
        top_row.addWidget(self.engine_pill)
        self.health_pill = QLabel("任务服务未启动", top)
        self.health_pill.setObjectName("pillBad")
        top_row.addWidget(self.health_pill)
        sys_button = QPushButton("⚙", top)
        sys_button.setObjectName("iconBtn")
        sys_button.setToolTip("系统功能（任务队列 / 历史 / 模型 / 引擎 / 设置 / 日志）")
        sys_button.clicked.connect(lambda: self.systemRequested.emit("home"))
        top_row.addWidget(sys_button)
        root.addWidget(top)

        tabs = QFrame(self)
        tabs.setObjectName("tabBar")
        tab_row = QHBoxLayout(tabs)
        tab_row.setContentsMargins(14, 6, 14, 0)
        tab_row.setSpacing(4)
        self.tab_buttons: list[TabButton] = []
        for mode, label in MODE_TABS:
            button = TabButton(mode, label, tabs)
            button.clicked.connect(lambda _checked=False, m=mode: self.switch_mode(m))
            tab_row.addWidget(button)
            self.tab_buttons.append(button)
        root.addWidget(tabs)

        self.feed = FeedArea(self.tokens, self)
        root.addWidget(self.feed, 1)

        self.composer = Composer(self.refs_dir, self)
        root.addWidget(self.composer)

        self.toast = ToastHub(self)
        self._guiToast.connect(lambda message, kind: self.toast.show(message, kind))

        self.feed.tileClicked.connect(self._on_tile_click)
        self.feed.variationRequested.connect(self.variation)
        self.feed.upscaleRequested.connect(lambda tid, _batch: self.upscale(tid))
        self.feed.cancelRequested.connect(self.cancel_task)
        self.feed.retryRequested.connect(self.retry_task)
        self.feed.saveRequested.connect(lambda tid, _batch: self.save_artifact_as(tid))
        self.composer.submitRequested.connect(self.imagine)
        self.composer.refChosen.connect(self._on_ref_chosen)
        self.composer.refPathEdited.connect(lambda _path: self.update_composer_meta())
        self.composer.refCleared.connect(self.update_composer_meta)
        self.composer.promptChanged.connect(self.panel.sync_style_chips)
        self.panel.style_toggled.connect(self._on_style_toggle)
        self.panel.params_changed.connect(self.update_composer_meta)
        self.panel.engine_changed.connect(self._on_engine_choice)
        self.panel.restart_requested.connect(self.restart_engine)

        mode = str(self.store.settings.get("mode") or "t2i")
        for button in self.tab_buttons:
            button.setChecked(button.mode == mode)
        self.panel.set_mode(mode)
        self.composer.set_mode(mode, MODALITY.get(mode, "image"))
        self.update_composer_meta()
        if self.task_service is None:
            self.composer.set_busy(True)

        self._timer = QTimer(self)
        self._timer.setInterval(2500)
        self._timer.timeout.connect(self.poll)
        self._timer.start()
        # 与网页端一致：加载即拉取一次引擎/任务状态，不等首个轮询周期。
        self.poll()

    # ------------------------------------------------------------------ 主题
    def set_tokens(self, tokens: dict[str, str]) -> None:
        self.tokens = tokens
        self.feed.tokens = tokens
        self.feed.render(
            self.store.active(), self._tasks, self._retried, self._queue, force=True
        )

    # ------------------------------------------------------------------ 轮询
    def poll(self) -> None:
        if self.engine_manager is not None:
            try:
                self._engines = list(self.engine_manager.list_statuses())
            except Exception as exc:  # pragma: no cover - defensive
                self.logger.warning("引擎状态读取失败：%s", exc)
        if self.task_service is not None:
            try:
                records = self.task_service.list_tasks(limit=400)
            except Exception as exc:  # pragma: no cover - defensive
                self.logger.warning("任务列表读取失败：%s", exc)
                records = ()
            self._tasks = {rec.task_id: rec for rec in records}
            self._retried = {rec.retry_of for rec in records if rec.retry_of}
            queued = [rec for rec in records if rec.status.value == "queued"]
            self._queue = {}
            for rec in queued:
                pos = 1
                for other in queued:
                    if (
                        other.task_id != rec.task_id
                        and str(other.created_at) < str(rec.created_at)
                    ):
                        pos += 1
                self._queue[rec.task_id] = pos
        self.panel.refresh_engines(self._engines)
        self._refresh_badges()
        self._refresh_pills()
        self._update_thumbs()
        self.feed.render(self.store.active(), self._tasks, self._retried, self._queue)

    def _refresh_badges(self) -> None:
        counts = {"image": 0, "video": 0, "music": 0}
        for rec in self._tasks.values():
            if rec.status.value in ACTIVE_STATUSES:
                counts[rec.request.task_type] = counts.get(rec.request.task_type, 0) + 1
        for button in self.tab_buttons:
            button.set_count(counts.get(MODALITY.get(button.mode, ""), 0))

    def retranslate_tabs(self) -> None:
        """语言切换后按登记过的中文源串重设模式 Tab 与顶栏标题。"""
        for button in self.tab_buttons:
            button.setText(i18n.tr(button.source_label))
        self.title_label.setText(i18n.tr(self._title_source))

    def _refresh_pills(self) -> None:
        text, kind = self.panel.engine_pill()
        self._set_pill(self.engine_pill, text, kind)
        if self.task_service is None:
            self._set_pill(self.health_pill, "任务服务未启动", "bad")
        else:
            self._set_pill(self.health_pill, "本地任务服务 · 串行队列", "ok")

    @staticmethod
    def _set_pill(label: QLabel, text: str, kind: str) -> None:
        name = {"ok": "pillOk", "bad": "pillBad"}.get(kind, "pill")
        if label.text() != text or label.objectName() != name:
            label.setObjectName(name)
            style = label.style()
            style.unpolish(label)
            style.polish(label)
            label.setText(text)

    def _update_thumbs(self) -> None:
        changed = False
        for session in self.store.sessions:
            if session.get("thumb"):
                continue
            for batch in session.get("batches") or []:
                for task_id in batch.get("taskIds") or []:
                    rec = self._tasks.get(task_id)
                    if rec is None:
                        continue
                    paths = output_paths(rec, IMAGE_SUFFIXES)
                    if paths:
                        session["thumb"] = paths[0]
                        changed = True
                        break
                if session.get("thumb"):
                    break
        if changed:
            self.store.persist()
            self.sessionsChanged.emit()

    # ------------------------------------------------------------------ 会话
    def refresh(self, force: bool = True) -> None:
        session = self.store.active()
        self.title_label.setText((session or {}).get("title") or "想象")
        self.feed.render(
            session, self._tasks, self._retried, self._queue, force=force
        )

    def new_session(self) -> None:
        seed = self.composer.prompt_text().strip()
        self.store.create(seed or "新会话")
        self.refresh()
        self.sessionsChanged.emit()
        self.composer.focus_prompt()

    def switch_mode(self, mode: str) -> None:
        if mode == "agent":
            # 智能体不是一种生成模式，而是五个板块的统一入口：切到它时收起
            # feed / composer / 右侧参数栏（由主窗口按 modeChanged 处理），
            # 也不把 "agent" 写进 store.settings["mode"]，避免参数面板读到陌生模式。
            if self._show_agent_panel():
                self.modeChanged.emit(mode)
            return
        if mode not in MODALITY:
            return
        self._hide_agent_panel()
        for button in self.tab_buttons:
            button.setChecked(button.mode == mode)
        self.panel.set_mode(mode)
        self.composer.set_mode(mode, MODALITY[mode])
        # 切板块后按当前提示词重新点亮对应的风格 / 曲风 chip。
        self.panel.sync_style_chips(self.composer.prompt_text())
        self.store.persist()
        self.update_composer_meta()
        self.modeChanged.emit(mode)

    # ---------------------------------------------------------------- agent
    def _show_agent_panel(self) -> bool:
        if self.agent_service is None:
            self.toast.show("智能体不可用：任务服务未启动", "error")
            return False
        if self.agent_page is None:
            from app.ui.pages.agent_page import AgentPage

            self.agent_page = AgentPage(self.agent_service, self.logger, self.tokens, self)
            self.agent_page.taskQueued.connect(lambda _payload: self.poll())
            self.agent_page.focusTaskRequested.connect(self._focus_agent_task)
            self.layout().insertWidget(2, self.agent_page, 1)
        self.agent_page.setVisible(True)
        self.feed.setVisible(False)
        self.composer.setVisible(False)
        for button in self.tab_buttons:
            button.setChecked(button.mode == "agent")
        self.agent_page.refresh_capabilities()
        return True

    def _hide_agent_panel(self) -> None:
        if self.agent_page is not None and self.agent_page.isVisible():
            self.agent_page.setVisible(False)
        self.feed.setVisible(True)
        self.composer.setVisible(True)

    def _focus_agent_task(self, task_type: str) -> None:
        self.switch_mode(_TASK_TYPE_TO_MODE.get(str(task_type), "t2i"))

    def retranslate_agent(self) -> None:
        """语言切换后刷新智能体页的外壳文案（未创建过就无需处理）。"""
        if self.agent_page is not None:
            self.agent_page.retranslate()

    # ------------------------------------------------------------- composer
    def update_composer_meta(self) -> None:
        mode = self.panel.mode
        if mode == "agent":
            # 智能体板块下创作 composer 整块被面板遮罩，没有出图参数行可刷新；
            # 直接返回，避免 MODALITY["agent"] 这种查表 KeyError。
            return
        modality = MODALITY[mode]
        p = self.panel.current_params()
        settings = self.store.settings
        seed_text = (
            f"seed {p.get('seed')}"
            if p.get("seedLock") and isinstance(p.get("seed"), int)
            else "seed 随机"
        )
        if modality == "music":
            bits = [
                self.panel.engine_id() or settings.get("mengine") or "ACE-Step 1.5（未注册）",
                f"BPM {settings.get('bpm', 120)}",
                f"目标 {settings.get('musicDuration', 180)}s（上限 480s）",
            ]
        elif modality == "video":
            frames = snap_frames(p.get("frames", 33))
            bits = [
                f"视频 {p.get('width')}×{p.get('height')}",
                f"{frames / VIDEO_FPS:.2f}s · {frames} 帧",
                f"steps {p.get('steps')}",
                seed_text,
            ]
        else:
            bits = [f"{p.get('width')}×{p.get('height')}", f"steps {p.get('steps')}", seed_text]
        if mode in NEEDS_REF:
            if self.composer.has_ref() and self.composer.ref_path():
                bits.append("垫图：已挂")
            elif self.composer.has_ref():
                bits.append("垫图：待确认路径")
            else:
                bits.append("垫图：未挂")
        self.composer.set_meta(" · ".join(str(bit) for bit in bits))

    def _on_ref_chosen(self, path: str) -> None:
        self.composer.set_ref(path)
        cut = max(str(path).rstrip("\\/").rfind("\\"), str(path).rstrip("\\/").rfind("/"))
        if cut > 1:
            self.store.settings["refDir"] = str(path)[:cut]
            self.store.persist()
        self.update_composer_meta()

    def _on_style_toggle(self, suffix: str, on: bool) -> None:
        text = self.composer.prompt_text()
        if on:
            if suffix not in text:
                self.composer.set_prompt(f"{text}, {suffix}" if text else suffix)
        else:
            cleaned = (
                text.replace(", " + suffix, "")
                .replace(suffix + ", ", "")
                .replace(suffix, "")
            )
            self.composer.set_prompt(cleaned)
        self.panel.sync_style_chips(self.composer.prompt_text())

    def _on_engine_choice(self, _engine_id: str) -> None:
        self._refresh_pills()
        self.update_composer_meta()

    def _engine_for(self, modality: str) -> str:
        key = {"video": "vengine", "music": "mengine"}.get(modality, "vengine")
        # 图片/视频各是两个独立引擎（wan-t2i / wan-i2i / wan-t2v / wan-i2v），
        # 各自独立进程、互斥加载。默认值按当前板块给；设置里存着别的板块的
        # 引擎时不跨板块串用（MODE_ENGINE_ALLOW 按板块一一对应）。
        allowed = MODE_ENGINE_ALLOW.get(self.mode) or ()
        stored = str(self.store.settings.get(key) or "")
        if allowed and stored and stored not in allowed:
            stored = ""
        if modality == "video":
            fallback = "wan-i2v" if self.mode == "i2v" else "wan-t2v"
        elif modality == "music":
            fallback = "ace-step-15"
        else:
            fallback = "wan-i2i" if self.mode == "i2i" else "wan-t2i"
        return self.panel.engine_id() or stored or fallback

    def _submit(
        self, prompt: str, parameters: dict[str, Any], task_type: str, engine_id: str
    ) -> TaskRecord:
        assert self.task_service is not None
        return self.task_service.submit_task(
            TaskRequest(
                task_type=task_type,
                engine_id=engine_id,
                prompt=prompt,
                model_id=None,
                parameters=parameters,
            )
        )

    # ------------------------------------------------------- 高级参数解析
    def _ref_path_error(self, raw: str) -> str | None:
        """垫图必须是本机已存在的具体图片文件；目录/不存在/不支持格式返回明确提示。"""
        try:
            p = Path(str(raw))
        except (OSError, ValueError) as exc:
            return f"垫图路径无法解析：{exc}"
        if p.is_dir():
            return f"垫图请选择具体图片文件，不能是文件夹：{raw}"
        if not p.is_file():
            return f"垫图文件不存在（请选择具体图片文件，不是文件夹）：{raw}"
        if p.suffix.lower() not in REF_IMAGE_SUFFIXES:
            return (
                "垫图格式需为 png / jpg / jpeg / webp / bmp；收到的是："
                + (p.suffix or "无扩展名")
            )
        return None

    def _parse_advanced(self, modality: str) -> dict[str, Any]:
        text = self.panel.advanced_text()
        if not text:
            return {}
        whitelist = (
            ADVANCED_WHITELIST_VIDEO if modality == "video" else ADVANCED_WHITELIST_IMAGE
        )
        try:
            data = json.loads(text)
        except ValueError as exc:
            self.toast.show(f"高级参数 JSON 无效：{exc}", "error")
            return {}
        if not isinstance(data, dict):
            self.toast.show("高级参数 JSON 无效：需为对象", "error")
            return {}
        clean: dict[str, Any] = {}
        rejected: list[str] = []
        for key, value in data.items():
            if key in whitelist:
                if key == "init_image_path":
                    bad = self._ref_path_error(str(value))
                    if bad:
                        self.toast.show("高级参数 " + bad, "error")
                        continue
                clean[key] = value
            else:
                rejected.append(str(key))
        if rejected:
            self.toast.show(
                "高级参数被忽略（不在白名单）：" + ", ".join(rejected), "error"
            )
        return clean

    def _collect_base_params(self) -> dict[str, Any]:
        p = self.panel.current_params()
        base: dict[str, Any] = {
            "width": int(p.get("width", 832)),
            "height": int(p.get("height", 480)),
            "steps": int(p.get("steps", 28)),
        }
        negative = str(p.get("negative", "")).strip()
        if negative:
            base["negative_prompt"] = negative
        if p.get("seedLock") and isinstance(p.get("seed"), int):
            base["seed"] = int(p["seed"])
        base.update(self._parse_advanced("image"))
        return base

    def _collect_video_params(self, ref_path: str) -> dict[str, Any]:
        p = self.panel.current_params()
        params: dict[str, Any] = {
            "width": int(p.get("width", 512)),
            "height": int(p.get("height", 384)),
            "steps": int(p.get("steps", 28)),
            "num_frames": snap_frames(p.get("frames", 33)),
        }
        negative = str(p.get("negative", "")).strip()
        if negative:
            params["negative_prompt"] = negative
        params["seed"] = (
            int(p["seed"])
            if p.get("seedLock") and isinstance(p.get("seed"), int)
            else rand_seed()
        )
        params.update(self._parse_advanced("video"))
        if ref_path:
            params["init_image_path"] = ref_path
        return params

    # ------------------------------------------------------------------ 提交
    def imagine(self) -> None:
        if self._submitting:
            return
        text = self.composer.prompt_text().strip()
        if not text:
            self.toast.show("先写提示词", "error")
            return
        if self.task_service is None:
            self.toast.show("任务服务未启动：请通过启动器运行完整应用", "error")
            return
        if not self._engines:
            self.toast.show(
                "没有已注册的引擎，请先检查 engines_enabled 与引擎环境", "error"
            )
            return
        mode = self.panel.mode
        modality = MODALITY[mode]
        if modality == "music":
            self._imagine_music(text)
            return
        if modality in ("video", "image"):
            want = "video" if modality == "video" else "image"
            label = "视频" if modality == "video" else "图片"
            if not any(
                want in (ENGINE_TASK_TYPES.get(e.engine_id) or ())
                for e in self._engines
            ):
                self.toast.show(
                    f"Wan2.1 {label}引擎未注册：需 .venv-video（运行 scripts\\setup_video_venv.ps1）"
                    "与 models\\wan2.1 权重（运行 scripts\\download_video_weights.py，Apache-2.0）；"
                    "未就绪前不会提交任务，也不会伪造输出。",
                    "error",
                )
                return
            if not nvidia_smi_available():
                self.toast.show(
                    f"显存不足，无法启动 Wan2.1 {label}模型：这台机器上没有 nvidia-smi"
                    "（检测不到 NVIDIA 显卡，最低要求 6GB 显存档位）。",
                    "error",
                )
                return
            gpu = cached_gpu_info(5.0)
            if gpu is None:
                self.toast.show(
                    "显存还没探到（nvidia-smi 正在后台跑）：本次先放行，"
                    "排队后引擎会按真实显存判定，不够会明确报错、不会伪造输出。",
                    "info",
                )
            elif gpu.memory_total_mib < VIDEO_MIN_VRAM_MB:
                self.toast.show(
                    f"显存不足，无法启动 Wan2.1 {label}模型：显卡 "
                    f"{gpu.name or '?'} 仅 {gpu.memory_total_mib} MB，"
                    f"最低要求 {VIDEO_MIN_VRAM_MB} MB（6GB 档）。",
                    "error",
                )
                return
            else:
                floor_ok, floor_note = self._vram_floor_check(
                    VIDEO_FREE_FLOOR_MB, f"Wan2.1 {label}模型"
                )
                if not floor_ok:
                    self.toast.show(floor_note, "error")
                    return
        if mode not in NEEDS_REF and self.composer.has_ref():
            self.composer.clear_ref()  # 文生图/文生视频不挂垫图
        extra: dict[str, Any] = {}
        if self.composer.has_ref():
            ref = self.composer.ref_path().strip()
            if not ref:
                self.toast.show(
                    "参考图需要本机完整路径。填路径后再提交，或点 ✕ 取消引用。",
                    "error",
                )
                return
            if not ABS_PATH_RE.match(ref):
                self.toast.show(
                    "参考图路径必须是盘符开头的绝对路径，例如 "
                    "D:\\ChenguAI\\uploads\\photo.png",
                    "error",
                )
                return
            err = self._ref_path_error(ref)
            if err:
                self.toast.show(err, "error")
                return
            extra["init_image_path"] = ref
            cut = max(ref.rstrip("\\/").rfind("\\"), ref.rstrip("\\/").rfind("/"))
            if cut > 1:
                self.store.settings["refDir"] = ref[:cut]
                self.store.persist()
        ref_path = str(extra.get("init_image_path") or "")
        if mode in NEEDS_REF and not ref_path:
            self.toast.show(
                "图生图需要先放垫图：点“选择图片”、拖入或粘贴，确认本机路径后再提交。"
                if mode == "i2i"
                else "图生视频需要先放首帧图片（也可用图片瓦片下的入口把已生成图推成视频）。",
                "error",
            )
            return
        self._submitting = True
        self.composer.set_busy(True)
        try:
            session = self.store.ensure_active(text[:48])
            if modality == "video":
                self._submit_video(session, text, mode, ref_path)
                return
            base = self._collect_base_params()
            base.update(extra)
            batch = self.store.add_batch(
                session["id"],
                {
                    "id": new_id(),
                    "kind": "imagine",
                    "mode": mode,
                    "prompt": text,
                    "time": now_iso(),
                    "refPath": ref_path,
                    "params": base,
                },
            )
            engine_id = self._engine_for("image")
            # Wan 单帧出图一次 1 张（旧 SD1.5「说一句出 4 张」已随引擎退场：
            # 1.3B + CPU 卸载下 4 连发要排队 4 轮，不如让用户看清一次真实耗时）。
            queued = 0
            for index in range(1):
                params = dict(base)
                panel_params = self.panel.current_params()
                if panel_params.get("seedLock") and isinstance(
                    panel_params.get("seed"), int
                ):
                    params["seed"] = int(panel_params["seed"]) + index
                else:
                    params["seed"] = rand_seed()
                try:
                    rec = self._submit(text, params, "image", engine_id)
                    batch["taskIds"].append(rec.task_id)
                    queued += 1
                except Exception as exc:
                    self.toast.show(f"提交失败：{exc}", "error")
            if queued:
                self.toast.show(
                    "图生图已排队：Wan2.1-VACE 按垫图参考重绘（单帧 PNG，480p 档），"
                    "首次生成需加载权重，通常数分钟"
                    if mode == "i2i"
                    else "文生图已排队：Wan2.1-T2V-1.3B 单帧渲染（PNG，480p 档），"
                    "首次生成需加载权重，通常数分钟",
                    "success",
                )
            self._after_submit()
        finally:
            self._submitting = False
            self.composer.set_busy(False)

    def _submit_video(
        self, session: dict[str, Any], text: str, mode: str, ref_path: str
    ) -> None:
        video_params = self._collect_video_params(ref_path)
        batch = self.store.add_batch(
            session["id"],
            {
                "id": new_id(),
                "kind": "video",
                "mode": mode,
                "prompt": text,
                "time": now_iso(),
                "refPath": ref_path,
                "params": video_params,
            },
        )
        try:
            rec = self._submit(
                text, video_params, "video", self._engine_for("video")
            )
            batch["taskIds"].append(rec.task_id)
            self.toast.show(
                "图生视频已排队：Wan2.1-VACE 从首帧生成（16fps mp4），"
                "首次生成需加载权重，通常 5-15 分钟"
                if ref_path
                else "文生视频已排队：Wan2.1-T2V-1.3B 直接渲染（16fps mp4），"
                "首次生成需加载权重，通常 5-15 分钟",
                "success",
            )
        except Exception as exc:
            self.toast.show(f"视频提交失败：{exc}", "error")
        self.composer.clear_ref()
        self._after_submit()

    def _imagine_music(self, text: str) -> None:
        if not any(
            "music" in (ENGINE_TASK_TYPES.get(e.engine_id) or ())
            for e in self._engines
        ):
            self.toast.show(
                "音乐引擎未注册：需 .venv-music 与 vendor\\ACE-Step-1.5"
                "（运行 scripts\\setup_music_venv.ps1）及权重下载；"
                "引擎未就绪前不会提交，也不会伪造输出。",
                "error",
            )
            return
        if not nvidia_smi_available():
            self.toast.show(
                "显存不足，无法启动ACE-Step1.5音乐模型：这台机器上没有 nvidia-smi"
                "（检测不到 NVIDIA 显卡，最低要求 6GB 显存档位）。",
                "error",
            )
            return
        gpu = cached_gpu_info(5.0)
        if gpu is None:
            self.toast.show(
                "显存还没探到（nvidia-smi 正在后台跑）：本次先放行，"
                "排队后引擎会按真实显存判定，不够会明确报错、不会伪造输出。",
                "info",
            )
        elif gpu.memory_total_mib < MUSIC_MIN_VRAM_MB:
            self.toast.show(
                "显存不足，无法启动ACE-Step1.5音乐模型：显卡 "
                f"{gpu.name or '?'} 仅 {gpu.memory_total_mib} MB，"
                f"最低要求 {MUSIC_MIN_VRAM_MB} MB（6GB 档）。",
                "error",
            )
            return
        else:
            floor_ok, floor_note = self._vram_floor_check(
                MUSIC_FREE_FLOOR_MB, "ACE-Step1.5 音乐模型"
            )
            if not floor_ok:
                self.toast.show(floor_note, "error")
                return
        settings = self.store.settings
        instrumental = bool(settings.get("instrumental"))
        vocal_language = str(settings.get("vocalLanguage") or "zh")
        answer = QMessageBox.question(
            self,
            "ACE-Step 1.5 能力边界确认",
            "ACE-Step 1.5 能力边界确认：\n"
            "· 支持带人声（中英文）完整乐曲或纯器乐，时长 10-480 秒，输出 48kHz wav；\n"
            "· 按 6GB 显存档位设计（LM/DiT 走 CPU 卸载），峰值显存为预期值、尚未实测，"
            "验收以任务元数据 peak_vram_mb 为准；\n"
            "· 不支持超长多轨道复杂编曲（无分轨导出/多轨工程）。\n\n"
            f"本次任务：BPM {settings.get('bpm', 120)} · 目标 "
            f"{settings.get('musicDuration', 180)}s · "
            f"{'纯器乐' if instrumental else f'人声（{vocal_language}）'} · "
            "与图片/视频共用串行队列（不并发抢占显存）。\n继续提交？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._submitting = True
        self.composer.set_busy(True)
        try:
            session = self.store.ensure_active(text[:48])
            params = {
                "bpm": int(settings.get("bpm", 120)),
                "duration": int(settings.get("musicDuration", 180)),
                "vocal_language": vocal_language,
                "instrumental": instrumental,
                "seed": rand_seed(),
            }
            batch = self.store.add_batch(
                session["id"],
                {
                    "id": new_id(),
                    "kind": "music",
                    "mode": self.panel.mode,
                    "prompt": text,
                    "time": now_iso(),
                    "refPath": "",
                    "params": params,
                },
            )
            try:
                rec = self._submit(
                    text, params, "music", self._engine_for("music")
                )
                batch["taskIds"].append(rec.task_id)
                self.toast.show(
                    "音乐任务已排队（串行队列）：首次生成需加载本地权重（约 9.4GB，"
                    "LM/DiT 走 CPU 卸载，约 1-3 分钟），产出 48kHz wav。",
                    "success",
                )
            except Exception as exc:
                self.toast.show(f"音乐提交失败：{exc}", "error")
            self._after_submit()
        finally:
            self._submitting = False
            self.composer.set_busy(False)

    def _after_submit(self) -> None:
        self.composer.clear_prompt()
        self.store.persist()
        self.feed.render(
            self.store.active(), self._tasks, self._retried, self._queue, force=True
        )
        self.feed.scroll_to_bottom()
        self.sessionsChanged.emit()

    # -------------------------------------------------------- 另存（自选位置）
    def save_artifact_as(self, task_id: str) -> None:
        """把产物复制一份到用户挑的位置——项目内 output\\ 里那份始终保留、工具显示它。

        这是网页端结果卡片上「另存」按钮（浏览器保存框）的 Qt 对应物。想每单都自动多存
        一份，去「设置」页填「自动另存目录」：两端共用同一份 config.json，哪一端填都生效。
        """
        rec = self._tasks.get(task_id)
        if rec is None or rec.status.value != "completed":
            self.toast.show("该任务还没有可另存的产物", "error")
            return
        task_type = rec.request.task_type
        suffixes = (
            VIDEO_SUFFIXES
            if task_type == "video"
            else AUDIO_SUFFIXES
            if task_type == "music"
            else IMAGE_SUFFIXES
        )
        files = output_paths(rec, suffixes)
        if not files:
            self.toast.show("该任务没有可另存的本地产物文件", "error")
            return
        source = Path(files[0])
        if not source.is_file():
            self.toast.show(f"产物文件不存在：{source}", "error")
            return
        selected, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "另存产物（可选任意盘符、移动硬盘或网盘目录）",
            str(source),
            f"生成产物 (*{source.suffix});;所有文件 (*)",
        )
        if not selected:
            return  # 用户取消，什么都不改
        target = Path(selected)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        except OSError as exc:
            self.toast.show(f"另存失败：{exc}", "error")
            return
        copies = (getattr(rec, "result_metadata", None) or {}).get("saved_copies") or []
        extra = f"（另有自动另存 {len(copies)} 份）" if copies else ""
        self.toast.show(f"已另存到 {target}{extra}", "success")

    # -------------------------------------------------------------- V / U
    def _source_record(self, task_id: str):
        rec = self._tasks.get(task_id)
        if rec is None or rec.status.value != "completed":
            self.toast.show("该任务没有可用的本地产物文件", "error")
            return None, None
        images = output_paths(rec, IMAGE_SUFFIXES)
        if not images:
            self.toast.show("该任务没有可用的本地产物文件", "error")
            return None, None
        return rec, images[0]

    def upscale(self, task_id: str) -> None:
        rec, path = self._source_record(task_id)
        if rec is None:
            return
        if self.task_service is None:
            return
        p = dict(rec.request.parameters or {})
        width0 = int(p.get("width") or 832)
        height0 = int(p.get("height") or 480)
        # Wan 档放大 = 按 480p 像素预算等比重绘一档（16 的倍数对齐 VAE 压缩）；
        # 源图已在预算内时放不了，直接如实说明，不提交伪放大任务。
        width = clamp_int(width0 * 1.34 / 16, 16, 1024) * 16
        height = clamp_int(height0 * 1.34 / 16, 16, 1024) * 16
        if width * height > VIDEO_MAX_PIXELS:
            factor = math.sqrt(VIDEO_MAX_PIXELS / (width * height))
            width = clamp_int(width * factor / 16, 16, 1024) * 16
            height = clamp_int(height * factor / 16, 16, 1024) * 16
        if width <= width0 and height <= height0:
            self.toast.show(
                f"源图 {width0}×{height0} 已在 480p 档像素预算（约 "
                f"{VIDEO_MAX_PIXELS // 1000}K 像素）上限附近，Wan 档无法再放大；"
                "要更大尺寸得另配超分或更大模型。",
                "error",
            )
            return
        params: dict[str, Any] = {
            "width": width,
            "height": height,
            "steps": int(p.get("steps") or 28),
            "init_image_path": path,
            "seed": int(p["seed"]) if isinstance(p.get("seed"), int) else rand_seed(),
        }
        if p.get("negative_prompt"):
            params["negative_prompt"] = p["negative_prompt"]
        session = self.store.ensure_active((rec.request.prompt or "U 放大")[:48])
        batch = self.store.add_batch(
            session["id"],
            {
                "id": new_id(),
                "kind": "upscale",
                "mode": "i2i",
                "prompt": rec.request.prompt,
                "time": now_iso(),
                "refPath": "",
                "params": params,
            },
        )
        try:
            new_rec = self._submit(rec.request.prompt, params, "image", self._engine_for("image"))
            batch["taskIds"].append(new_rec.task_id)
            self.poll()
            self.feed.render(
                self.store.active(),
                self._tasks,
                self._retried,
                self._queue,
                force=True,
            )
            self.feed.scroll_to_bottom()
            self.sessionsChanged.emit()
            self.toast.show(
                f"放大任务已排队：{width}×{height}（Wan VACE 参考重绘补细节，"
                "480p 档像素预算内）",
                "success",
            )
        except Exception as exc:
            self.toast.show(f"放大提交失败：{exc}", "error")

    def variation(self, task_id: str, _batch_id: str = "") -> None:
        rec, path = self._source_record(task_id)
        if rec is None:
            return
        if self.task_service is None:
            return
        p = dict(rec.request.parameters or {})
        session = self.store.ensure_active((rec.request.prompt or "V 变化")[:48])
        batch = self.store.add_batch(
            session["id"],
            {
                "id": new_id(),
                "kind": "variation",
                "mode": "i2i",
                "prompt": rec.request.prompt,
                "time": now_iso(),
                "refPath": "",
                "params": p,
            },
        )
        # 种子基准取「源任务自己」的参数，不取面板当前模式的参数桶：
        # current_params() 返回的是当前 Tab（可能是文生视频/音乐）的桶，
        # 那张图的种子锁和种子值与这张卡片的来源无关，会用错值或退化成随机。
        panel_params = self.panel.current_params()
        source_seed = p.get("seed")
        base_seed: int | None
        if isinstance(source_seed, int):
            base_seed = int(source_seed)
        elif panel_params.get("seedLock") and isinstance(panel_params.get("seed"), int):
            base_seed = int(panel_params["seed"])
        else:
            base_seed = None
        ok = 0
        # 变体 = 同一张垫图（源任务产物）换 seed 再走一遍 wan-i2i 参考重绘；
        # Wan 档一次 1 张（旧 SD 一次 4 张已随引擎退场），无 strength 参数。
        for index in range(1):
            params = {
                "width": int(p.get("width") or 832),
                "height": int(p.get("height") or 480),
                "steps": int(p.get("steps") or 28),
                "init_image_path": path,
                "seed": base_seed + index if base_seed is not None else rand_seed(),
            }
            if p.get("negative_prompt"):
                params["negative_prompt"] = p["negative_prompt"]
            try:
                new_rec = self._submit(
                    rec.request.prompt, params, "image", self._engine_for("image")
                )
                batch["taskIds"].append(new_rec.task_id)
                ok += 1
            except Exception as exc:
                self.toast.show(f"变化提交失败（第 {index + 1} 张）：{exc}", "error")
        if ok:
            self.poll()
            self.feed.render(
                self.store.active(),
                self._tasks,
                self._retried,
                self._queue,
                force=True,
            )
            self.feed.scroll_to_bottom()
            self.sessionsChanged.emit()
            self.toast.show(f"变体任务已排队（{ok} 张）", "success")

    # ------------------------------------------------------- 重试 / 取消
    def retry_task(self, task_id: str, _batch_id: str = "") -> None:
        if self.task_service is None:
            return
        try:
            new_rec = self.task_service.retry_task(task_id)
        except Exception as exc:
            self.toast.show(f"重试失败：{exc}", "error")
            return
        target = self._find_batch(task_id)
        if target is not None:
            target.setdefault("taskIds", []).append(new_rec.task_id)
            self.store.persist()
        else:
            source = self._tasks.get(task_id) or new_rec
            params = dict(source.request.parameters or {})
            if source.request.task_type == "video":
                fallback_mode = "i2v" if params.get("init_image_path") else "t2v"
                kind = "video"
            else:
                fallback_mode = "i2i" if params.get("init_image_path") else "t2i"
                kind = "imagine"
            session = self.store.ensure_active((source.request.prompt or "重试")[:48])
            self.store.add_batch(
                session["id"],
                {
                    "id": new_id(),
                    "kind": kind,
                    "mode": fallback_mode,
                    "prompt": source.request.prompt or "重试",
                    "time": now_iso(),
                    "refPath": "",
                    "params": params,
                    "taskIds": [new_rec.task_id],
                },
            )
        self.poll()
        self.feed.render(
            self.store.active(), self._tasks, self._retried, self._queue, force=True
        )
        self.sessionsChanged.emit()

    def _find_batch(self, task_id: str) -> dict[str, Any] | None:
        for session in self.store.sessions:
            for batch in session.get("batches") or []:
                if task_id in (batch.get("taskIds") or []):
                    return batch
        return None

    # -------------------------------------------------- 显存门槛（先自己让路）
    def _release_idle_engines_blocking(self, timeout_seconds: float = 12.0) -> tuple[str, ...]:
        """把本端自己拉起的空闲引擎 worker 停掉（显存当场交还），在这条线程上等它做完。

        只在「显存门槛差一点就要拦下提交」这条路上调：一次几百毫秒到一两秒，
        为的是先自己让路，而不是一句话把用户支去双击脚本。测试替身没有这个
        方法，直接跳过，不影响任何既有断言。
        """
        if self.task_service is None or self.service_loop is None:
            return ()
        release = getattr(self.task_service, "release_all_idle_engines", None)
        if release is None:
            return ()
        try:
            future = asyncio.run_coroutine_threadsafe(release(), self.service_loop)
            return tuple(future.result(timeout=timeout_seconds))
        except Exception as exc:  # noqa: BLE001 - 让路失败不能把提交本身搞崩
            self.logger.warning("释放空闲引擎失败（显存判定）：%s", exc)
            return ()

    def _vram_floor_check(self, need_free_mib: int, label: str) -> tuple[bool, str]:
        """显存到底够不够：先实测一次，不够就先释放自家空闲引擎，再实测，还不够才拦。

        为什么不能直接拿 nvidia-smi 的 used/free 当准：Windows 上它把驱动与显示子系统
        保留的、以及进程已经结束但驱动还没回收的都算进 used，明显虚高（本机比按进程
        加和多出 1GB 上下），拿它判定就会冤枉用户。所以这里用 effective_free_mib＝
        「nvidia-smi 的 free」与「总量 − 按进程实测加和」里较大的那个；真要拦下来，
        点名是哪个程序占着显存，让用户知道该关什么。
        """
        gpu = cached_gpu_info(5.0)
        if gpu is None:
            # 还没探到显存：放行，交给后端 health_check 兜底，不自己编判定。
            return True, ""
        if effective_free_mib(gpu, None) >= need_free_mib:
            return True, ""
        released = self._release_idle_engines_blocking()
        forget_vram_cache()
        fresh = get_gpu_info() or gpu
        holders = vram_holders_cached(0.0)
        free_now = effective_free_mib(fresh, holders)
        if free_now >= need_free_mib:
            gave = "、".join(released) if released else "上一个任务留下的显存"
            self.toast.show(
                f"先释放了本端空闲引擎（{gave}），现在显存够用了，继续提交。",
                "info",
            )
            return True, ""
        if released:
            gave = f"本端已经先把自己拉起的空闲引擎释放掉了（{'、'.join(released)}）"
        else:
            gave = "本端没有可释放的空闲引擎"
        note = (
            f"显存不足，无法启动{label}：按进程实测还能用的空闲显存只有 {free_now} MB"
            f"（这一步需要约 {need_free_mib} MB）。{gave}；显存目前主要被 "
            f"{holders_summary(holders)} 占着——退出占显存的程序再提交。"
            "（这里不是按 nvidia-smi 的「已用」算的：Windows 上那个数把驱动保留的、"
            "以及已经结束还没回收的进程也算进去，明显偏大。）"
        )
        return False, note

    def cancel_task(self, task_id: str) -> None:
        if self.task_service is None:
            return
        if self.service_loop is None:
            self.toast.show("取消失败：任务服务事件循环不可用", "error")
            return
        future = asyncio.run_coroutine_threadsafe(
            self.task_service.cancel_task(task_id), self.service_loop
        )

        def _done(fut: Any) -> None:
            exc = fut.exception()
            if exc is not None:
                self.logger.error("取消任务失败：%s", exc)
                self._guiToast.emit(f"取消失败：{exc}", "error")

        future.add_done_callback(_done)
        self.toast.show("已请求取消", "success")

    def restart_engine(self, engine_id: str) -> None:
        if self.engine_manager is None or self.service_loop is None:
            self.toast.show("重启失败：引擎管理器或事件循环不可用", "error")
            return
        future = asyncio.run_coroutine_threadsafe(
            self.engine_manager.restart_engine(engine_id), self.service_loop
        )

        def _done(fut: Any) -> None:
            exc = fut.exception()
            if exc is not None:
                self.logger.error("引擎重启失败：%s", exc)
                self._guiToast.emit(f"引擎重启失败：{exc}", "error")

        future.add_done_callback(_done)
        self.toast.show(f"已请求重启引擎 {engine_id}", "success")

    # ------------------------------------------------------------ lightbox
    def _on_tile_click(self, task_id: str, batch_id: str) -> None:
        rec = self._tasks.get(task_id)
        if rec is None:
            return
        if rec.request.task_type == "video":
            videos = output_paths(rec, VIDEO_SUFFIXES)
            if videos:
                open_local_file(videos[0])
            return
        if rec.request.task_type == "music":
            return
        images = output_paths(rec, IMAGE_SUFFIXES)
        if not images:
            return
        items: list[tuple[str, TaskRecord, str]] = []
        batch = None
        for session in self.store.sessions:
            batch = next(
                (b for b in session.get("batches") or [] if b.get("id") == batch_id),
                None,
            )
            if batch is not None:
                break
        seen: set[str] = set()
        if batch is not None:
            for member_id in batch.get("taskIds") or []:
                member = self._tasks.get(member_id)
                if member is None:
                    continue
                paths = output_paths(member, IMAGE_SUFFIXES)
                if paths and member_id not in seen:
                    seen.add(member_id)
                    items.append((member_id, member, paths[0]))
        if task_id not in seen:
            items.insert(0, (task_id, rec, images[0]))
        index = next(
            (i for i, item in enumerate(items) if item[0] == task_id), 0
        )
        dialog = LightboxDialog(self, items, index)
        dialog.variationRequested.connect(lambda tid: self.variation(tid, batch_id))
        dialog.upscaleRequested.connect(self.upscale)
        dialog.exec()
