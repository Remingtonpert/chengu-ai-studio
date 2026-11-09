"""「AI 智能体」页面（桌面端，排在创作页模式 Tab 第一位）。

一句话输入 → 后端 :class:`app.agent.service.AgentService` 判定动作与模态：
生成类命令排进与六个板块（AI智能体、文生图、图生图、文生视频、图生视频、AI音乐）
同一条任务队列（同一个 SQLite FIFO + 执行者锁），
聊天/文案/写文档走本机 OpenAI 兼容大模型（默认是项目自带的 llama.cpp 服务），
朗读走 Windows 系统语音；「做个表格 / 写份方案 / 做个 PPT」由本机写库落成
.docx/.xlsx/.pptx，气泡里给「打开」「打开所在文件夹」两个按钮（与网页端的下载链接等效）。

这一层只做界面：不复制任何调度逻辑，也不美化失败——服务回报 error / degraded
就按原话显示。所有可能耗时的调用（大模型、语音、能力探测）都丢进线程池，
UI 线程不阻塞。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QEvent, QObject, QRunnable, Qt, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.core import i18n

_EXAMPLES: tuple[str, ...] = (
    "画一张赛博朋克雨夜街头，16:9",
    "生成一段10秒的海浪拍打礁石延时摄影",
    "生成一首轻快的钢琴曲，纯音乐，90秒",
    "把这张图改成水彩风",
    "帮我写一条小红书文案，介绍这款保温杯",
    "做个新品上市的排期表",
    "写份活动执行方案，做成 Word 文档",
    "做个产品介绍 PPT",
    "出图步数一般设多少？",
)
_MODE_TO_TAB = {"t2i": "t2i", "i2i": "i2i", "t2v": "t2v", "i2v": "i2v", "music": "music"}
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
_MAX_REF_BYTES = 8 * 1024 * 1024
_HISTORY_LIMIT = 12
# 程序刚起来那阵，引擎还在后台一个个注册，专用大模型实例也可能还没就位；
# 所以「开机那一刻的探测快照」不能钉死在界面上：降级的结果自动再探几次，
# 界面上也留一个手动「重新探测」的按钮，不用重启整个程序。
_PROBE_RETRIES = 8  # 自带大模型首次加载权重要几十秒，自动重试的窗口得盖住这次冷启动
_PROBE_RETRY_MS = 8000


class _WorkerSignals(QObject):
    done = Signal(dict)


class _InvokeTask(QRunnable):
    """在线程池里跑一次可能耗时的服务调用，结果用信号送回 UI 线程。"""

    def __init__(self, call: Callable[[], Any], on_done: Callable[[dict], None]) -> None:
        super().__init__()
        self._call = call
        self.signals = _WorkerSignals()
        self.signals.done.connect(on_done)
        self.setAutoDelete(True)

    def run(self) -> None:  # noqa: D102
        try:
            payload = {"ok": True, "data": self._call()}
        except Exception as exc:  # 服务层已经尽量把失败变成可读文案，这里兜底
            payload = {"ok": False, "error": str(exc)}
        self.signals.done.emit(payload)


class AgentPage(QWidget):
    """智能体对话面板：能力条 + 对话气泡 + 输入区 + 垫图选择。"""

    taskQueued = Signal(dict)        # 有生成任务入队 → 创作页刷新徽标/切 Tab
    focusTaskRequested = Signal(str)  # 点任务 chip → 切到对应模式 Tab

    def __init__(
        self,
        agent_service: Any,
        logger: Any,
        tokens: dict[str, str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("agentPage")
        self.service = agent_service
        self.logger = logger
        self.tokens = tokens or {}
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(2)
        self._busy = False
        self._ref_path = ""
        self._history: list[dict[str, str]] = []
        self._capabilities: dict[str, Any] | None = None
        self._pending_label: QLabel | None = None
        self._last_user_text = ""
        self._probing = False
        self._probe_retries = 0

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 12, 18, 12)
        root.setSpacing(10)

        self.capability_bar = QFrame(self)
        self.capability_bar.setObjectName("agentCapBar")
        bar_row = QHBoxLayout(self.capability_bar)
        bar_row.setContentsMargins(2, 2, 2, 2)
        bar_row.setSpacing(6)
        # 能力小胶囊每次探测都要重建，所以放进一个单独的子容器里；
        # 「重新探测」按钮放在容器外面，不然会被一起清掉。
        self._cap_pills = QWidget(self.capability_bar)
        self._cap_pills.setObjectName("agentCapPills")
        pill_row = QHBoxLayout(self._cap_pills)
        pill_row.setContentsMargins(0, 0, 0, 0)
        pill_row.setSpacing(6)
        self.probe_button = QPushButton(i18n.tr("重新探测"), self.capability_bar)
        self.probe_button.setObjectName("btnMini")
        self.probe_button.clicked.connect(self._probe_clicked)
        bar_row.addWidget(self._cap_pills)
        bar_row.addWidget(self.probe_button)
        bar_row.addStretch(1)
        root.addWidget(self.capability_bar)

        # 那段长能力说明不再铺开在首屏：收在这里，点「帮助」才展开（与网页端同一个开关）。
        self.help_panel = QFrame(self)
        self.help_panel.setObjectName("agentHelpPanel")
        self.help_panel.setVisible(False)
        help_layout = QVBoxLayout(self.help_panel)
        help_layout.setContentsMargins(12, 10, 12, 10)
        help_layout.setSpacing(4)
        self.help_text = QLabel("", self.help_panel)
        self.help_text.setWordWrap(True)
        self.help_text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.help_text.setStyleSheet(
            f"color:{self.tokens.get('text_dim', '#6b7078')};"
            f"border:1px solid {self.tokens.get('line', '#23262c')};"
            f"background:{self.tokens.get('bg_card', '#16181c')};"
            "border-radius:10px;padding:10px 12px;"
        )
        help_layout.addWidget(self.help_text)
        root.addWidget(self.help_panel)

        scroll = QScrollArea(self)
        scroll.setObjectName("agentScroll")
        scroll.setWidgetResizable(True)
        host = QWidget()
        host.setObjectName("agentLogHost")
        self._log_layout = QVBoxLayout(host)
        self._log_layout.setContentsMargins(0, 0, 0, 0)
        self._log_layout.setSpacing(8)
        self._log_layout.addStretch(1)
        scroll.setWidget(host)
        self._scroll = scroll
        root.addWidget(scroll, 1)

        self.ref_row = QFrame(self)
        self.ref_row.setObjectName("agentRefRow")
        self.ref_row.setVisible(False)
        ref_layout = QHBoxLayout(self.ref_row)
        ref_layout.setContentsMargins(4, 4, 4, 4)
        ref_layout.setSpacing(8)
        self.ref_thumb = QLabel("", self.ref_row)
        self.ref_thumb.setFixedSize(48, 48)
        self.ref_name = QLabel("", self.ref_row)
        self.ref_name.setObjectName("agentRefName")
        clear_ref = QPushButton("✕", self.ref_row)
        clear_ref.setObjectName("btnMini")
        clear_ref.clicked.connect(self._clear_ref_image)
        ref_layout.addWidget(self.ref_thumb)
        ref_layout.addWidget(self.ref_name, 1)
        ref_layout.addWidget(clear_ref)
        root.addWidget(self.ref_row)

        chip_row = QFrame(self)
        chip_row.setObjectName("agentChipRow")
        self._chip_layout = QHBoxLayout(chip_row)
        self._chip_layout.setContentsMargins(0, 0, 0, 0)
        self._chip_layout.setSpacing(6)
        root.addWidget(chip_row)

        input_row = QHBoxLayout()
        input_row.setSpacing(8)
        self.input = QPlainTextEdit(self)
        self.input.setObjectName("agentInput")
        self.input.setFixedHeight(64)
        self.input.setPlaceholderText(i18n.tr("描述你想要的画面、视频或音乐，也可以让我写文案、聊天或朗读…"))
        send = QPushButton(i18n.tr("发送"), self)
        send.setObjectName("agentSend")
        send.clicked.connect(lambda: self._send())
        input_row.addWidget(self.input, 1)
        input_row.addWidget(send, 0, Qt.AlignBottom)
        self.send_button = send
        root.addLayout(input_row)

        self.hint = QLabel("", self)
        self.hint.setObjectName("agentHint")
        self.hint.setWordWrap(True)
        self.hint.setVisible(False)  # 没话可说时不占位（显存读数默认也不在这里）
        root.addWidget(self.hint)

        self._build_chips(chip_row)
        # Enter 提交、Shift+Enter 换行；输入法组字期间的 Enter 不提交。
        self.input.installEventFilter(self)

    # ------------------------------------------------------------ 事件过滤

    def eventFilter(self, watched: QObject, event: Any) -> bool:  # noqa: N802
        if watched is self.input and event.type() == QEvent.Type.KeyPress:
            if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not (
                event.modifiers() & Qt.ShiftModifier
            ):
                # Enter 提交、Shift+Enter 换行。中文输入法组字期间的 Enter 由 Qt 的
                # 输入方法吃掉（用来确认候选词），不会走到这里，所以不需要网页端那种
                # isComposing 判定。
                self._send()
                return True
        return super().eventFilter(watched, event)

    # -------------------------------------------------------------- chips

    def _build_chips(self, host: QWidget) -> None:
        for example in _EXAMPLES:
            chip = QPushButton(example, host)
            chip.setObjectName("chip")
            chip.clicked.connect(lambda _checked=False, value=example: self.input.setPlainText(value))
            self._chip_layout.addWidget(chip)
        clear = QPushButton(i18n.tr("清空对话"), host)
        clear.setObjectName("btnMini")
        clear.clicked.connect(self._clear_conversation)
        self._chip_layout.addWidget(clear)
        # 「帮助」与「调试信息」默认都是关的：按钮文字不随状态变（切语言不会掉回中文），
        # 开关状态靠描边颜色表达。
        self.help_button = QPushButton(i18n.tr("帮助"), host)
        self.help_button.setObjectName("btnMini")
        self.help_button.setCheckable(True)
        self.help_button.toggled.connect(self._toggle_help)
        self._chip_layout.addWidget(self.help_button)
        self.debug_button = QPushButton(i18n.tr("调试信息"), host)
        self.debug_button.setObjectName("btnMini")
        self.debug_button.setCheckable(True)
        self.debug_button.toggled.connect(
            lambda checked: (self._paint_toggle(self.debug_button, checked), self._render_hint())
        )
        self._chip_layout.addWidget(self.debug_button)
        pick = QPushButton(i18n.tr("🖼 选择垫图"), host)
        pick.setObjectName("btnMini")
        pick.clicked.connect(self._pick_ref_image)
        self._chip_layout.addWidget(pick)
        self._chip_layout.addStretch(1)

    # -------------------------------------------------- 帮助 / 调试信息（默认收起）

    def _paint_toggle(self, button: QPushButton, checked: bool) -> None:
        """开关状态只用描边说话：按钮文字不变，切语言时不会掉回中文。"""
        if checked:
            blue = self.tokens.get("blue", "#7d9bff")
            button.setStyleSheet(f"border:1px solid {blue}; color:{blue};")
        else:
            button.setStyleSheet("")

    def _toggle_help(self, checked: bool) -> None:
        self._paint_toggle(self.help_button, checked)
        if checked and not (self._capabilities or {}).get("help"):
            # 还没探到过能力说明：先探一次，回来时 _on_capabilities 会把面板填好再展开。
            self.refresh_capabilities(force=True)
            return
        self._render_help()

    def _render_help(self) -> None:
        text = str((self._capabilities or {}).get("help") or "")
        self.help_text.setText(text)
        self.help_panel.setVisible(bool(text) and self.help_button.isChecked())

    def _set_hint(self, message: str) -> None:
        """底部提示行统一走这里：有字才占位，空字符串不在输入框下面留一条白。"""
        self.hint.setText(str(message or ""))
        self.hint.setVisible(bool(message))

    def _render_hint(self) -> None:
        """底部那行：默认只有人话提示；打开「调试信息」才把显存读数那类技术细节拼上。"""
        data = self._capabilities or {}
        parts = [str(note) for note in (data.get("notes") or [])]
        if self.debug_button.isChecked():
            parts += [str(item) for item in (data.get("diagnostics") or [])]
        self._set_hint(" · ".join(parts))

    # ------------------------------------------------------------- 能力条

    def refresh_capabilities(self, force: bool = False) -> None:
        if self._probing:
            return
        if self._capabilities is not None and not force:
            self._render_capabilities(self._capabilities)
            return
        self._probing = True
        self._set_probe_busy(True)
        self._pool.start(
            _InvokeTask(self.service.capabilities, on_done=self._on_capabilities)
        )

    def _probe_clicked(self) -> None:
        """手动重新探测：清零自动重试次数，让它重新武装起来。"""
        self._probe_retries = 0
        self.refresh_capabilities(force=True)

    def _set_probe_busy(self, busy: bool) -> None:
        self.probe_button.setText(
            i18n.tr("正在重新探测…") if busy else i18n.tr("重新探测")
        )
        self.probe_button.setEnabled(not busy)

    @staticmethod
    def _capabilities_degraded(data: dict[str, Any]) -> bool:
        """引擎没注册全 / 大模型连不上 / 没有系统声音，都算「还没就位」。"""
        engines = data.get("engines") or {}
        llm = data.get("llm") or {}
        voices = (data.get("tts") or {}).get("voices") or []
        # 五个板块槽（与 Web 端同一组）：少一个就是「还没就位」，自动再探一次。
        missing = [key for key in ("image", "i2i", "t2v", "i2v", "music") if not engines.get(key)]
        return bool(missing) or not llm.get("available") or not voices

    def _arm_probe_retry(self) -> None:
        """探测结果降级就不是终局：过几秒自动再探一次，最多 _PROBE_RETRIES 次。"""
        if self._probe_retries >= _PROBE_RETRIES:
            return
        self._probe_retries += 1
        QTimer.singleShot(_PROBE_RETRY_MS, lambda: self.refresh_capabilities(force=True))

    def _on_capabilities(self, payload: dict) -> None:
        self._probing = False
        self._set_probe_busy(False)
        if not payload.get("ok"):
            self._set_hint(i18n.tr("没有可用的 AI 智能体引擎") + f"：{payload.get('error', '')}")
            self._arm_probe_retry()
            return
        data = payload.get("data") or {}
        self._capabilities = data
        self._render_capabilities(data)
        self._render_help()
        if self._capabilities_degraded(data):
            self._arm_probe_retry()
        else:
            self._probe_retries = 0

    def _render_capabilities(self, data: dict[str, Any]) -> None:
        row = self._cap_pills.layout()
        while row.count():
            item = row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        engines = data.get("engines") or {}
        states = data.get("engine_states") or {}
        # 槽位、标签与顺序必须与 Web 端一字不差：两端显示的是同一份能力清单，
        # 不能一端有「图生图」另一端没有、一端绿色一端灰色。
        labels = {
            "image": i18n.tr("文生图"),
            "i2i": i18n.tr("图生图"),
            "t2v": i18n.tr("文生视频"),
            "i2v": i18n.tr("图生视频"),
            "music": i18n.tr("音乐"),
        }
        for key, label in labels.items():
            engine = engines.get(key)
            if not engine:
                pill = QLabel(f"{label}：{i18n.tr('无引擎')}")
                pill.setObjectName("pillBad")
            elif str(states.get(engine) or "") in {"ready", "busy", "starting"}:
                pill = QLabel(f"{label}：{engine}")
                pill.setObjectName("pillOk")
            else:
                # 已注册、只是当前没把模型搬进显存：这是正常待命状态，不是「没有引擎」。
                pill = QLabel(f"{label}：{engine} · {i18n.tr('未启动')}")
                pill.setObjectName("pill")
            row.addWidget(pill)
        llm = data.get("llm") or {}
        llm_pill = QLabel(
            f"{i18n.tr('本机大模型')}：{llm.get('model') or ''}"
            if llm.get("available")
            else f"{i18n.tr('本机大模型')}：{i18n.tr('不可用')}"
        )
        llm_pill.setObjectName("pillOk" if llm.get("available") else "pillBad")
        row.addWidget(llm_pill)
        voices = (data.get("tts") or {}).get("voices") or []
        tts_pill = QLabel(
            f"{i18n.tr('系统语音')}：{len(voices)} {i18n.tr('个声音')}"
            if voices
            else f"{i18n.tr('系统语音')}：{i18n.tr('不可用')}"
        )
        tts_pill.setObjectName("pillOk" if voices else "pillBad")
        row.addWidget(tts_pill)
        # 底部那行按开关拼装：notes 常驻，显存读数（diagnostics）只在「调试信息」打开时出现。
        self._render_hint()

    # ---------------------------------------------------------- 气泡渲染

    def _add_bubble(
        self,
        role: str,
        text: str,
        *,
        notes: list[str] | None = None,
        tasks: list[dict[str, Any]] | None = None,
        documents: list[dict[str, Any]] | None = None,
        error: bool = False,
        dim: bool = False,
        speakable: bool = False,
    ) -> QLabel | None:
        if not text and not tasks and not documents:
            return None
        frame = QFrame(self)
        frame.setObjectName("agentBubbleUser" if role == "user" else "agentBubble")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)
        label = QLabel(text, frame)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        if dim or error:
            label.setStyleSheet(f"color:{self.tokens.get('text_dim', '#6b7078')}")
        layout.addWidget(label)
        for note in notes or []:
            note_label = QLabel(f"· {note}", frame)
            note_label.setWordWrap(True)
            note_label.setStyleSheet(f"color:{self.tokens.get('text_dim', '#6b7078')}")
            layout.addWidget(note_label)
        for task in tasks or []:
            chip_row = QHBoxLayout()
            chip_row.setSpacing(6)
            button = QPushButton(
                f"→ {str(task.get('task_id', ''))[:8]} · {task.get('engine_id', '')}", frame
            )
            button.setObjectName("btnMini")
            button.setToolTip(str(task.get("prompt") or ""))
            button.clicked.connect(
                lambda _checked=False, task_type=task.get("task_type"): self.focusTaskRequested.emit(
                    str(task_type or "")
                )
            )
            chip_row.addWidget(button)
            chip_row.addStretch(1)
            layout.addLayout(chip_row)
        for document in documents or []:
            # 生成好的 .docx/.xlsx/.pptx：桌面端不玩预览，直接交给系统关联程序打开
            # （装了 Word/WPS/Office 就能改），另外给一份所在文件夹的入口。
            path = str(document.get("path") or "")
            name = str(document.get("name") or Path(path).name)
            size = int(document.get("bytes") or 0)
            detail = " · ".join(
                part
                for part in (
                    str(document.get("label") or ""),
                    str(document.get("stats") or ""),
                    f"{max(1, size // 1024)} KB" if size else "",
                )
                if part
            )
            doc_row = QHBoxLayout()
            doc_row.setSpacing(6)
            caption = QLabel(f"📄 {name}\n{detail}" if detail else f"📄 {name}", frame)
            caption.setWordWrap(True)
            caption.setTextInteractionFlags(Qt.TextSelectableByMouse)
            caption.setToolTip(path)
            doc_row.addWidget(caption, 1)
            open_button = QPushButton(i18n.tr("打开"), frame)
            open_button.setObjectName("btnMini")
            open_button.setToolTip(path)
            open_button.clicked.connect(
                lambda _checked=False, value=path: self._open_local_file(value)
            )
            doc_row.addWidget(open_button)
            folder_button = QPushButton(i18n.tr("打开所在文件夹"), frame)
            folder_button.setObjectName("btnMini")
            folder_button.clicked.connect(
                lambda _checked=False, value=path: self._open_local_folder(value)
            )
            doc_row.addWidget(folder_button)
            layout.addLayout(doc_row)
            for mirror in document.get("mirrors") or []:
                mirror_label = QLabel(f"· {i18n.tr('同一份也在')}：{mirror}", frame)
                mirror_label.setWordWrap(True)
                mirror_label.setStyleSheet(f"color:{self.tokens.get('text_dim', '#6b7078')}")
                layout.addWidget(mirror_label)
        if speakable and text:
            action_row = QHBoxLayout()
            action_row.setSpacing(6)
            speak = QPushButton(i18n.tr("朗读"), frame)
            speak.setObjectName("btnMini")
            speak.clicked.connect(lambda _checked=False, value=text: self._speak(value))
            action_row.addWidget(speak)
            action_row.addStretch(1)
            layout.addLayout(action_row)
        self._log_layout.insertWidget(self._log_layout.count() - 1, frame)
        bar = self._scroll.verticalScrollBar()
        bar.setValue(bar.maximum())
        return label

    def _clear_conversation(self) -> None:
        self._history = []
        while self._log_layout.count() > 1:
            item = self._log_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._pending_label = None
        self.refresh_capabilities()

    # ------------------------------------------------------------ 生成好的文档

    def _open_local_file(self, path: str) -> None:
        """用系统关联的程序打开生成的文档（装了 Word / WPS / Office 就能直接改）。"""
        if not path or not Path(path).is_file():
            self._set_hint(i18n.tr("文件不在了，可能被移动或删除"))
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(path)):
            self._set_hint(i18n.tr("系统里没有能打开这种文件的程序"))

    def _open_local_folder(self, path: str) -> None:
        """打开文件所在文件夹（输出目录 / 项目内 output\\ / 自动另存目录都走这一条）。"""
        directory = Path(path).parent if path else Path(".")
        if not directory.is_dir():
            self._set_hint(i18n.tr("文件夹不在了"))
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory))):
            self._set_hint(i18n.tr("打不开这个文件夹"))

    # ------------------------------------------------------------ 垫图选择

    def _pick_ref_image(self) -> None:
        upload_dir = str(self.service.config.get("upload_dir") or "").strip()
        start = upload_dir if upload_dir and Path(upload_dir).is_dir() else str(Path.cwd())
        path, _selected = QFileDialog.getOpenFileName(
            self,
            i18n.tr("🖼 选择垫图"),
            start,
            "图片 (*.png *.jpg *.jpeg *.webp *.bmp)",
        )
        if not path:
            return
        candidate = Path(path)
        if candidate.suffix.lower() not in _IMAGE_SUFFIXES:
            self._set_hint(i18n.tr("只能上传图片文件"))
            return
        try:
            size = candidate.stat().st_size
        except OSError as exc:
            self._set_hint(f"{i18n.tr('读取图片失败')}：{exc}")
            return
        if size > _MAX_REF_BYTES:
            self._set_hint(i18n.tr("图片超过 8MB，请先缩小"))
            return
        self._ref_path = str(candidate.resolve())
        pixmap = QPixmap(self._ref_path)
        self.ref_thumb.setPixmap(
            pixmap.scaled(48, 48, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            if not pixmap.isNull()
            else QPixmap()
        )
        self.ref_name.setText(candidate.name)
        self.ref_row.setVisible(True)

    def _clear_ref_image(self) -> None:
        self._ref_path = ""
        self.ref_thumb.setPixmap(QPixmap())
        self.ref_name.setText("")
        self.ref_row.setVisible(False)

    # ---------------------------------------------------------------- 发送

    def _send(self, override_text: str | None = None) -> None:
        text = (override_text if override_text is not None else self.input.toPlainText()).strip()
        if not text or self._busy:
            return
        self._last_user_text = text
        self.input.setPlainText("")
        self._add_bubble("user", text)
        self._pending_label = self._add_bubble("agent", "…", dim=True)
        self._set_busy(True)
        history = list(self._history)
        ref_image = self._ref_path
        self._pool.start(
            _InvokeTask(
                lambda: self.service.handle(
                    text, history=history, ref_image=ref_image, action_hint=""
                ),
                on_done=self._on_command_result,
            )
        )

    def _on_command_result(self, payload: dict) -> None:
        if self._pending_label is not None:
            widget = self._pending_label.parent()
            if widget is not None:
                widget.deleteLater()
            self._pending_label = None
        self._set_busy(False)
        if not payload.get("ok"):
            self._add_bubble("agent", f"{i18n.tr('智能体请求失败')}：{payload.get('error', '')}", error=True)
            return
        result = payload.get("data") or {}
        reply = str(result.get("reply") or result.get("error") or "")
        tasks = list(result.get("tasks") or [])
        documents = list(result.get("documents") or [])
        self._add_bubble(
            "agent",
            reply,
            notes=[str(note) for note in (result.get("notes") or [])],
            tasks=tasks,
            documents=documents,
            error=bool(result.get("error")),
            speakable=bool(reply) and not tasks and not documents,
        )
        if reply:
            self._history.append({"role": "user", "content": self._last_user_text})
            self._history.append({"role": "assistant", "content": reply})
            while len(self._history) > _HISTORY_LIMIT:
                self._history.pop(0)
        if tasks:
            self.taskQueued.emit({"tasks": tasks, "mode": result.get("mode")})

    def _speak(self, text: str) -> None:
        if self._busy:
            return
        self._set_busy(True)
        self._pool.start(
            _InvokeTask(
                lambda: self.service.handle(text, action_hint="speak"),
                on_done=self._on_speak_result,
            )
        )

    def _on_speak_result(self, payload: dict) -> None:
        self._set_busy(False)
        if not payload.get("ok"):
            self._set_hint(f"{i18n.tr('朗读')}：{payload.get('error', '')}")
            return
        result = payload.get("data") or {}
        self._set_hint(str(result.get("reply") or result.get("error") or ""))

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.send_button.setEnabled(not busy)

    # -------------------------------------------------------------- 重翻译

    def retranslate(self) -> None:
        """语言切换后重刷外壳文案（对话内容保持用户原话，不做机翻）。"""
        self.input.setPlaceholderText(
            i18n.tr("描述你想要的画面、视频或音乐，也可以让我写文案、聊天或朗读…")
        )
        self.send_button.setText(i18n.tr("发送"))
        # 两个开关的标签同样要换语言；开关状态用描边表达，跟文字无关，所以换语言不丢状态。
        self.help_button.setText(i18n.tr("帮助"))
        self.debug_button.setText(i18n.tr("调试信息"))
        for button in self._chip_layout.findChildren(QPushButton):
            text = button.text()
            if text in (i18n.tr("清空对话"), "清空对话"):
                button.setText(i18n.tr("清空对话"))
            elif text in (i18n.tr("🖼 选择垫图"), "🖼 选择垫图"):
                button.setText(i18n.tr("🖼 选择垫图"))
        if self._capabilities is not None:
            self._render_capabilities(self._capabilities)
        if self.help_button.isChecked():
            # 帮助面板开着才重新探一次：让面板里那段说明换成新语言，关着时不打扰用户。
            self.refresh_capabilities(force=True)
