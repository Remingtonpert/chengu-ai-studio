"""Right-hand parameter panel, the Qt counterpart of the web console's
``aside.panel``: engine select, size/aspect chips, steps, seed, negative,
strength/frames (mode-aware), style/genre chips, music section, advanced
JSON. All parameter buckets live in the shared settings dict
(``settings["modes"][mode]``) exactly like ``state.settings.modes`` in
app.js, so nothing about quality semantics changes.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.ui.widgets.common import rand_seed

MODES = ("t2i", "i2i", "t2v", "i2v", "music")
MODALITY = {
    "t2i": "image",
    "i2i": "image",
    "t2v": "video",
    "i2v": "video",
    "music": "music",
}
MODE_LABELS = {
    "t2i": "文生图",
    "i2i": "图生图",
    "t2v": "文生视频",
    "i2v": "图生视频",
    "music": "AI音乐",
}
ENGINE_TASK_TYPES = {
    "flux-schnell": ("image",),
    "anima-image2video": ("video",),
    "ace-step-15": ("music",),
}
ENGINE_STATE_LABELS = {"stopped": "未启动", "ready": "就绪", "failed": "失败"}

PIXEL_SAFE = 1_350_000
VIDEO_WARN_PIXELS = 480_000

ASPECT_CHIPS_IMAGE = [
    (1024, 1024, "1:1"),
    (896, 1152, "2:3"),
    (1152, 896, "3:2"),
    (1216, 704, "16:9"),
    (704, 1216, "9:16"),
    (512, 512, "512"),
]
ASPECT_CHIPS_VIDEO = [
    (512, 384, "4:3"),
    (384, 512, "3:4"),
    (448, 448, "1:1"),
    (640, 368, "16:9"),
]
STYLE_CHIPS = [
    ("摄影", "photorealistic, professional photography, natural lighting"),
    ("动漫", "anime key visual, clean line art, cel shading"),
    ("赛博朋克", "cyberpunk, neon lights, cinematic, high detail"),
    ("油画", "oil painting, impasto brushwork, classical composition"),
    ("水彩", "watercolor illustration, soft washes, paper texture"),
    ("3D", "3d render, octane, studio lighting, ultra detailed"),
    ("极简", "minimalist, flat design, generous negative space"),
]
GENRE_CHIPS = [
    ("电子", "synthwave, retro analog synths, driving beat"),
    ("Lo-Fi", "lo-fi hip hop, dusty drums, mellow, chillhop"),
    ("管弦", "epic orchestral, cinematic strings, powerful brass"),
    ("民谣", "acoustic folk, warm guitar, intimate"),
    ("爵士", "jazz piano trio, swing, smoky lounge"),
    ("氛围", "ambient, drone, meditative, slow evolving"),
    ("金属", "metal, distorted guitars, double kick, aggressive"),
]

ENGINE_MODE_HINTS = {
    "image": "出图前先释放本机 LLM 引擎显存（scripts\\release_local_engine.bat），否则易 OOM。",
    "video": (
        "Anima 权重未预下载：首次生成会联网下载（十几分钟量级）。"
        "出视频前先释放本机 LLM 引擎显存（scripts\\release_local_engine.bat）。"
    ),
    "music": (
        "ACE-Step 1.5（MIT 权重，本地离线）：48kHz wav，最低 12GB 显存，"
        "与图片/视频共用 FIFO 串行队列不并发抢占。出音乐前先释放本机 LLM 引擎显存"
        "（scripts\\release_local_engine.bat）。"
    ),
}

MUSIC_PANEL_WARNING = (
    "已接入 ACE-Step 1.5（权重 MIT，本地离线推理，无云端 API；最低 12GB 显存）。"
    "支持 4-10 分钟带人声完整乐曲或纯器乐，输出 48kHz wav；不支持超长多轨道复杂编曲。"
    "提交前会检查显存并弹出能力边界确认；显存不足会直接拦截。"
)


def default_mode_params(mode: str) -> dict:
    p = {
        "width": 1024,
        "height": 1024,
        "steps": 4,
        "seedLock": False,
        "seed": None,
        "negative": "",
        "advanced": "",
    }
    if mode in {"t2v", "i2v"}:
        p.update({"width": 512, "height": 384, "steps": 28, "frames": 33})
    if mode == "i2i":
        p["strength"] = 0.6
    return p


def default_settings() -> dict:
    return {
        "mode": "t2i",
        "refDir": "",
        "engine": "flux-schnell",
        "vengine": "anima-image2video",
        "mengine": "ace-step-15",
        "bpm": 120,
        "musicDuration": 180,
        "modes": {mode: default_mode_params(mode) for mode in MODES},
    }


def normalize_settings(raw: dict | None) -> dict:
    base = default_settings()
    if isinstance(raw, dict):
        for key, value in base.items():
            if key == "modes":
                continue
            if key in raw and isinstance(raw[key], type(value) if value is not None else object):
                base[key] = raw[key]
        modes_raw = raw.get("modes") or {}
        for mode in MODES:
            merged = base["modes"][mode]
            stored = modes_raw.get(mode)
            if isinstance(stored, dict):
                merged.update(
                    {k: v for k, v in stored.items() if k in merged or k == "frames"}
                )
                base["modes"][mode] = merged
    if base["mode"] not in MODES:
        base["mode"] = "t2i"
    return base


def snap_frames(value) -> int:
    try:
        n = max(5, min(161, int(round(float(value)))))
    except (TypeError, ValueError):
        n = 33
    return n - ((n - 1) % 4)


class _Section(QFrame):
    def __init__(self, modes: set[str], parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("panelSection")
        self.modes = modes
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 8, 0, 12)
        self._layout.setSpacing(8)

    def add_title(self, text: str) -> QLabel:
        label = QLabel(text, self)
        label.setObjectName("sectionTitle")
        self._layout.addWidget(label)
        return label

    def add_value_row(self, text: str) -> tuple[QLabel, QLabel]:
        row = QHBoxLayout()
        row.setSpacing(6)
        title = QLabel(text, self)
        title.setObjectName("sectionTitle")
        value = QLabel("", self)
        value.setObjectName("metaDim")
        row.addWidget(title)
        row.addWidget(value)
        row.addStretch(1)
        self._layout.addLayout(row)
        return title, value

    def add_hint(self, text: str, warn: bool = False) -> QLabel:
        hint = QLabel(text, self)
        hint.setObjectName("hintWarn" if warn else "hint")
        hint.setWordWrap(True)
        hint.setTextFormat(Qt.TextFormat.PlainText)
        self._layout.addWidget(hint)
        return hint


class ParamPanel(QScrollArea):
    engine_changed = Signal(str)          # engine_id chosen in the select
    restart_requested = Signal(str)       # restart this engine id
    params_changed = Signal()             # any bucket/settings value edited
    style_toggled = Signal(str, bool)     # (suffix, checked)

    def __init__(self, settings: dict, persist, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("panelColumn")
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.settings = settings
        self.persist = persist
        self._syncing = False
        self._engines: list = []
        self.mode = settings.get("mode", "t2i")

        body = QWidget(self)
        body.setObjectName("panelBody")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(14, 12, 14, 18)
        layout.setSpacing(0)

        head = QLabel("参数设置", body)
        head.setObjectName("panelTitle")
        layout.addWidget(head)

        # ---------------------------------------------------------------- 引擎
        sec_engine = _Section({"t2i", "i2i", "t2v", "i2v"}, body)
        sec_engine.add_title("引擎")
        self.engine_select = QComboBox(sec_engine)
        self.engine_select.setObjectName("engineSelect")
        self.engine_select.currentIndexChanged.connect(self._on_engine_selected)
        sec_engine._layout.addWidget(self.engine_select)
        state_row = QHBoxLayout()
        state_row.setSpacing(8)
        self.engine_state = QLabel("", sec_engine)
        self.engine_state.setObjectName("metaDim")
        self.engine_state.setWordWrap(True)
        restart = QPushButton("重启引擎", sec_engine)
        restart.setObjectName("btnMini")
        restart.clicked.connect(
            lambda: self.restart_requested.emit(self.engine_id())
        )
        state_row.addWidget(self.engine_state, 1)
        state_row.addWidget(restart)
        sec_engine._layout.addLayout(state_row)
        self.engine_mode_hint = sec_engine.add_hint(ENGINE_MODE_HINTS["image"])
        layout.addWidget(sec_engine)

        # ------------------------------------------------------------ 图像尺寸
        sec_image = _Section({"t2i", "i2i"}, body)
        sec_image.add_title("宽高比")
        self.aspect_chips_image = self._chip_row(
            sec_image,
            [(label, (w, h)) for w, h, label in ASPECT_CHIPS_IMAGE],
            kind="aspect",
        )
        self.width_range, self.width_num = self._size_row(
            sec_image, "宽", 512, 1408, 8, 256, 1536
        )
        self.height_range, self.height_num = self._size_row(
            sec_image, "高", 512, 1408, 8, 256, 1536
        )
        self.vram_hint = sec_image.add_hint("")
        layout.addWidget(sec_image)

        # ---------------------------------------------------------------- 强度
        sec_strength = _Section({"i2i"}, body)
        _, self.strength_label = sec_strength.add_value_row("重绘强度 strength")
        self.strength_range = self._slider(sec_strength, 10, 100, 5, 60)
        self.strength_range.valueChanged.connect(self._on_strength)
        sec_strength.add_hint(
            "低（0.3–0.5）贴近垫图构图；高（0.7–1.0）大改画面。"
            "V/U 用固定强度，不受此滑块影响。"
        )
        layout.addWidget(sec_strength)

        # ------------------------------------------------------------ 视频尺寸
        sec_video_size = _Section({"t2v", "i2v"}, body)
        sec_video_size.add_title("视频尺寸")
        self.aspect_chips_video = self._chip_row(
            sec_video_size,
            [(label, (w, h)) for w, h, label in ASPECT_CHIPS_VIDEO],
            kind="aspect",
        )
        self.vwidth_range, self.vwidth_num = self._size_row(
            sec_video_size, "宽", 256, 1024, 16, 256, 1024
        )
        self.vheight_range, self.vheight_num = self._size_row(
            sec_video_size, "高", 256, 1024, 16, 256, 1024
        )
        self.video_hint = sec_video_size.add_hint("")
        layout.addWidget(sec_video_size)

        # ---------------------------------------------------------------- 时长
        sec_frames = _Section({"t2v", "i2v"}, body)
        _, self.frames_label = sec_frames.add_value_row("时长")
        self.frames_range = self._slider(sec_frames, 5, 161, 4, 33)
        self.frames_range.valueChanged.connect(self._on_frames)
        sec_frames.add_hint(
            "导出固定 24fps；帧数自动取到 4n+1（Anima 3D-VAE 时间压缩约束），"
            "越长越吃显存与时间。"
        )
        layout.addWidget(sec_frames)

        # ------------------------------------------------------------ 运动幅度
        sec_motion = _Section({"t2v", "i2v"}, body)
        sec_motion.add_title("运动幅度")
        motion = QSlider(Qt.Orientation.Horizontal, sec_motion)
        motion.setRange(0, 100)
        motion.setValue(50)
        motion.setEnabled(False)
        sec_motion._layout.addWidget(motion)
        sec_motion.add_hint(
            "Anima 引擎当前不暴露运动幅度/帧率参数（未开放参数会被后端白名单拒绝），"
            "此控件保留占位，待引擎支持后启用。"
        )
        layout.addWidget(sec_motion)

        # --------------------------------------------------------------- steps
        sec_steps = _Section({"t2i", "i2i", "t2v", "i2v"}, body)
        _, self.steps_label = sec_steps.add_value_row("Steps")
        self.steps_range = self._slider(sec_steps, 1, 12, 1, 4)
        self.steps_range.valueChanged.connect(self._on_steps)
        self.steps_hint = sec_steps.add_hint(
            "schnell 推荐 4 步；更多步数收益低且更慢。"
        )
        layout.addWidget(sec_steps)

        # ---------------------------------------------------------------- seed
        sec_seed = _Section({"t2i", "i2i", "t2v", "i2v"}, body)
        sec_seed.add_title("Seed")
        seed_row = QHBoxLayout()
        seed_row.setSpacing(8)
        self.seed_lock = QPushButton("固定", sec_seed)
        self.seed_lock.setObjectName("chip")
        self.seed_lock.setCheckable(True)
        self.seed_lock.toggled.connect(self._on_seed_lock)
        self.seed_num = QSpinBox(sec_seed)
        self.seed_num.setObjectName("numWide")
        self.seed_num.setRange(-1, 2_147_483_647)
        self.seed_num.setValue(-1)
        self.seed_num.setSpecialValueText("随机")
        self.seed_num.valueChanged.connect(self._on_seed_num)
        dice = QPushButton("🎲", sec_seed)
        dice.setObjectName("btnMini")
        dice.clicked.connect(self._on_seed_dice)
        seed_row.addWidget(self.seed_lock)
        seed_row.addWidget(self.seed_num, 1)
        seed_row.addWidget(dice)
        sec_seed._layout.addLayout(seed_row)
        self.seed_hint = sec_seed.add_hint(
            "不固定时，一次想象 = 4 个不同 seed；固定时用连续 4 个 seed。"
        )
        layout.addWidget(sec_seed)

        # -------------------------------------------------------- 负面提示词
        sec_negative = _Section({"t2i", "i2i", "t2v", "i2v"}, body)
        sec_negative.add_title("负面提示词")
        self.negative_input = QPlainTextEdit(sec_negative)
        self.negative_input.setObjectName("textareaSm")
        self.negative_input.setFixedHeight(56)
        self.negative_input.setPlaceholderText("模糊、低质量、水印…")
        self.negative_input.textChanged.connect(self._on_negative)
        sec_negative._layout.addWidget(self.negative_input)
        layout.addWidget(sec_negative)

        # ---------------------------------------------------------- 风格/曲风
        sec_style = _Section({"t2i", "i2i", "t2v", "i2v", "music"}, body)
        sec_style.add_title("风格 / 曲风（点击追加到提示词）")
        self.style_chips = self._chip_row(
            sec_style,
            [(label, suffix) for label, suffix in STYLE_CHIPS],
            kind="style",
        )
        self.genre_chips = self._chip_row(
            sec_style,
            [(label, suffix) for label, suffix in GENRE_CHIPS],
            kind="genre",
        )
        self.genre_chips_container.setVisible(False)
        layout.addWidget(sec_style)

        # -------------------------------------------------------------- 音乐
        sec_music = _Section({"music"}, body)
        sec_music.add_title("音乐引擎状态")
        sec_music.add_hint(MUSIC_PANEL_WARNING, warn=True)
        _, self.bpm_label = sec_music.add_value_row("BPM")
        self.bpm_range = self._slider(sec_music, 60, 180, 1, 120)
        self.bpm_range.valueChanged.connect(self._on_bpm)
        _, self.music_dur_label = sec_music.add_value_row("目标时长")
        self.music_dur_range = self._slider(sec_music, 30, 480, 30, 180)
        self.music_dur_range.valueChanged.connect(self._on_music_duration)
        sec_music.add_hint(
            "12GB 显存档位实际最长约 8 分钟（480s），超出会被引擎自动截到上限，"
            "产物按真实时长为准。"
        )
        layout.addWidget(sec_music)

        # ---------------------------------------------------------- 高级参数
        sec_advanced = _Section({"t2i", "i2i", "t2v", "i2v"}, body)
        sec_advanced.add_title("高级参数（JSON，可选）")
        self.advanced_input = QPlainTextEdit(sec_advanced)
        self.advanced_input.setObjectName("textareaSm")
        self.advanced_input.setFixedHeight(56)
        self.advanced_input.setPlaceholderText('{"strength": 0.6}')
        self.advanced_input.textChanged.connect(self._on_advanced)
        sec_advanced._layout.addWidget(self.advanced_input)
        self.advanced_hint = sec_advanced.add_hint(
            "白名单：width height steps seed negative_prompt init_image_path strength"
        )
        layout.addWidget(sec_advanced)

        layout.addStretch(1)
        self.setWidget(body)
        self._sections = [
            sec_engine,
            sec_image,
            sec_strength,
            sec_video_size,
            sec_frames,
            sec_motion,
            sec_steps,
            sec_seed,
            sec_negative,
            sec_style,
            sec_music,
            sec_advanced,
        ]
        self.set_mode(self.mode)
        self.sync_music()
        self._size_handlers(self.width_range, self.width_num, "width")
        self._size_handlers(self.height_range, self.height_num, "height")
        self._size_handlers(self.vwidth_range, self.vwidth_num, "width")
        self._size_handlers(self.vheight_range, self.vheight_num, "height")

    # ------------------------------------------------------------------ 构造辅助
    def _chip_row(
        self, section: _Section, items, kind: str = ""
    ) -> list[QPushButton]:
        container = QWidget(section)
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        buttons: list[QPushButton] = []
        for label, payload in items:
            chip = QPushButton(label, container)
            chip.setObjectName("chip")
            chip.setCheckable(True)
            chip._payload = payload  # type: ignore[attr-defined]
            chip.clicked.connect(lambda _checked=False, c=chip: self._on_chip(c))
            row.addWidget(chip)
            buttons.append(chip)
        row.addStretch(1)
        section._layout.addWidget(container)
        if kind == "style":
            self.style_chips_container = container
        elif kind == "genre":
            self.genre_chips_container = container
        return buttons

    def _slider(
        self, section: _Section, low: int, high: int, step: int, value: int
    ) -> QSlider:
        slider = QSlider(Qt.Orientation.Horizontal, section)
        slider.setRange(low, high)
        if step > 1:
            slider.setSingleStep(step)
            slider.setPageStep(step)
        slider.setValue(value)
        section._layout.addWidget(slider)
        return slider

    def _size_row(
        self,
        section: _Section,
        label: str,
        slider_low: int,
        slider_high: int,
        slider_step: int,
        spin_low: int,
        spin_high: int,
    ) -> tuple[QSlider, QSpinBox]:
        row = QHBoxLayout()
        row.setSpacing(8)
        caption = QLabel(label, section)
        caption.setObjectName("sliderLabel")
        slider = QSlider(Qt.Orientation.Horizontal, section)
        slider.setRange(slider_low, slider_high)
        slider.setSingleStep(slider_step)
        spin = QSpinBox(section)
        spin.setObjectName("num")
        spin.setRange(spin_low, spin_high)
        spin.setSingleStep(slider_step)
        row.addWidget(caption)
        row.addWidget(slider, 1)
        row.addWidget(spin)
        section._layout.addLayout(row)
        return slider, spin

    # ------------------------------------------------------------------ 模式切换
    def current_params(self) -> dict:
        return self.settings["modes"][self.mode]

    def advanced_text(self) -> str:
        return self.advanced_input.toPlainText().strip()

    def set_mode(self, mode: str) -> None:
        if mode not in MODES:
            mode = "t2i"
        self.mode = mode
        self.settings["mode"] = mode
        for section in self._sections:
            section.setVisible(mode in section.modes)
        modality = MODALITY[mode]
        self.steps_range.setRange(1, 50 if modality == "video" else 12)
        self.style_chips_container.setVisible(modality != "music")
        self.genre_chips_container.setVisible(modality == "music")
        self.engine_mode_hint.setText(ENGINE_MODE_HINTS[modality])
        self.sync_all()
        self.refresh_engines(self._engines)
        self.persist()

    # ------------------------------------------------------------------ 参数同步
    def _bucket(self) -> dict:
        return self.settings["modes"][self.mode]

    def sync_all(self) -> None:
        self._syncing = True
        try:
            modality = MODALITY[self.mode]
            if modality == "image":
                self._sync_size(
                    self._bucket(),
                    self.width_range,
                    self.width_num,
                    self.height_range,
                    self.height_num,
                    self.aspect_chips_image,
                    self.vram_hint,
                )
            elif modality == "video":
                self._sync_size(
                    self._bucket(),
                    self.vwidth_range,
                    self.vwidth_num,
                    self.vheight_range,
                    self.vheight_num,
                    self.aspect_chips_video,
                    self.video_hint,
                )
                self._sync_frames()
            if modality in {"image", "video"}:
                self._sync_steps()
                self._sync_seed()
                if self.mode == "i2i":
                    self._sync_strength()
                self.negative_input.setPlainText(self._bucket().get("negative", ""))
                self.advanced_input.setPlainText(self._bucket().get("advanced", ""))
                self.advanced_hint.setText(
                    "白名单：width height num_frames steps seed negative_prompt init_image_path"
                    if modality == "video"
                    else "白名单：width height steps seed negative_prompt init_image_path strength"
                )
            self.sync_music()
        finally:
            self._syncing = False

    def _sync_size(
        self,
        p: dict,
        w_slider: QSlider,
        w_spin: QSpinBox,
        h_slider: QSlider,
        h_spin: QSpinBox,
        chips: list[QPushButton],
        hint: QLabel,
    ) -> None:
        width = int(p.get("width") or 0)
        height = int(p.get("height") or 0)
        w_slider.setValue(max(w_slider.minimum(), min(w_slider.maximum(), width)))
        h_slider.setValue(max(h_slider.minimum(), min(h_slider.maximum(), height)))
        w_spin.setValue(width)
        h_spin.setValue(height)
        for chip in chips:
            chip_w, chip_h = chip._payload
            chip.setChecked(chip_w == width and chip_h == height)
        pixels = width * height
        if MODALITY[self.mode] == "video":
            if pixels > VIDEO_WARN_PIXELS:
                hint.setObjectName("hintWarn")
                frames = snap_frames(p.get("frames") or 33)
                hint.setText(
                    f"视频 {width}×{height} × {frames} 帧在 12GB 上偏紧"
                    "（CPU offload 也吃力），建议 ≤512×384。"
                )
            else:
                hint.setObjectName("hint")
                hint.setText(
                    f"视频像素 {pixels / 1e6:.2f}MP · Anima 走 bf16 + CPU offload，12GB 可行。"
                )
        elif pixels > PIXEL_SAFE:
            hint.setObjectName("hintWarn")
            hint.setText(
                f"12GB 显存下 {width}×{height}（{pixels / 1e6:.2f}MP）有 OOM 风险，"
                "建议 ≤1024×1024。"
            )
        else:
            hint.setObjectName("hint")
            hint.setText(f"像素 {pixels / 1e6:.2f}MP · 12GB 显存安全范围内。")
        hint.style().unpolish(hint)
        hint.style().polish(hint)

    def _sync_steps(self) -> None:
        p = self._bucket()
        steps = int(p.get("steps") or 1)
        self.steps_range.setValue(max(1, min(self.steps_range.maximum(), steps)))
        self.steps_label.setText(f"{steps} 步")
        modality = MODALITY[self.mode]
        self.steps_hint.setText(
            "Anima 推荐 28 步（默认）；12GB 上步数直接决定等待时长。"
            if modality == "video"
            else "schnell 推荐 4 步；更多步数收益低且更慢。"
        )

    def _sync_seed(self) -> None:
        p = self._bucket()
        self.seed_lock.setChecked(bool(p.get("seedLock")))
        self.seed_num.setEnabled(bool(p.get("seedLock")))
        seed = p.get("seed")
        self.seed_num.setValue(int(seed) if isinstance(seed, (int, float)) else -1)
        self.seed_hint.setText(
            "不固定 = 每次随机 seed；固定 = 可复现同一段视频。"
            if MODALITY[self.mode] == "video"
            else "不固定时，一次想象 = 4 个不同 seed；固定时用连续 4 个 seed。"
        )

    def _sync_strength(self) -> None:
        p = self._bucket()
        strength = float(p.get("strength") or 0.6)
        self.strength_range.setValue(int(round(strength * 100)))
        self.strength_label.setText(f"{strength:.2f}")

    def _sync_frames(self) -> None:
        p = self._bucket()
        frames = snap_frames(p.get("frames") or 33)
        self.frames_range.setValue(frames)
        self.frames_label.setText(f"{frames / 24:.2f} 秒 · {frames} 帧 @24fps")

    def sync_music(self) -> None:
        self._syncing = True
        try:
            bpm = int(self.settings.get("bpm") or 120)
            duration = int(self.settings.get("musicDuration") or 180)
            self.bpm_range.setValue(max(60, min(180, bpm)))
            self.bpm_label.setText(f"{bpm} BPM")
            self.music_dur_range.setValue(max(30, min(480, duration)))
            self.music_dur_label.setText(f"{duration} 秒")
        finally:
            self._syncing = False

    # ------------------------------------------------------------------ 交互
    def _commit(self) -> None:
        if not self._syncing:
            self.params_changed.emit()

    def _set_wh(self, key: str, value: int) -> None:
        p = self._bucket()
        p[key] = int(value)
        modality = MODALITY[self.mode]
        if modality == "image":
            self._sync_size(
                p,
                self.width_range,
                self.width_num,
                self.height_range,
                self.height_num,
                self.aspect_chips_image,
                self.vram_hint,
            )
        else:
            self._sync_size(
                p,
                self.vwidth_range,
                self.vwidth_num,
                self.vheight_range,
                self.vheight_num,
                self.aspect_chips_video,
                self.video_hint,
            )
        self._commit()
        self.persist()

    def _size_handlers(self, slider: QSlider, spin: QSpinBox, key: str) -> None:
        slider.valueChanged.connect(
            lambda value: self._size_edge(slider, spin, key, value, from_slider=True)
        )
        spin.valueChanged.connect(
            lambda value: self._size_edge(slider, spin, key, value, from_slider=False)
        )

    def _size_edge(
        self,
        slider: QSlider,
        spin: QSpinBox,
        key: str,
        value: int,
        from_slider: bool,
    ) -> None:
        if self._syncing:
            return
        self._syncing = True
        try:
            if from_slider:
                spin.setValue(int(value))
            else:
                slider.setValue(max(slider.minimum(), min(slider.maximum(), int(value))))
        finally:
            self._syncing = False
        self._set_wh(key, int(value))

    def _on_chip(self, chip: QPushButton) -> None:
        payload = chip._payload
        if chip.objectName() == "chip" and isinstance(payload, tuple) and len(payload) == 2:
            width, height = payload
            p = self._bucket()
            p["width"], p["height"] = int(width), int(height)
            self._syncing = True
            try:
                if MODALITY[self.mode] == "image":
                    self._sync_size(
                        p,
                        self.width_range,
                        self.width_num,
                        self.height_range,
                        self.height_num,
                        self.aspect_chips_image,
                        self.vram_hint,
                    )
                else:
                    self._sync_size(
                        p,
                        self.vwidth_range,
                        self.vwidth_num,
                        self.vheight_range,
                        self.vheight_num,
                        self.aspect_chips_video,
                        self.video_hint,
                    )
            finally:
                self._syncing = False
            self._commit()
            self.persist()
            return
        # 风格 / 曲风 chip：追加/移除 suffix（由创作页改写提示词）
        suffix = str(payload)
        on = chip.isChecked()
        self.style_toggled.emit(suffix, on)

    def _on_strength(self, value: int) -> None:
        if self._syncing:
            return
        self._bucket()["strength"] = round(value / 100, 2)
        self.strength_label.setText(f"{value / 100:.2f}")
        self._commit()
        self.persist()

    def _on_frames(self, value: int) -> None:
        if self._syncing:
            return
        self._bucket()["frames"] = snap_frames(value)
        self._syncing = True
        try:
            self._sync_frames()
        finally:
            self._syncing = False
        self._commit()
        self.persist()

    def _on_steps(self, value: int) -> None:
        if self._syncing:
            return
        self._bucket()["steps"] = int(value)
        self.steps_label.setText(f"{value} 步")
        self._commit()
        self.persist()

    def _on_seed_lock(self, checked: bool) -> None:
        if self._syncing:
            return
        p = self._bucket()
        p["seedLock"] = bool(checked)
        if not checked:
            p["seed"] = None
        self._syncing = True
        try:
            self._sync_seed()
        finally:
            self._syncing = False
        self._commit()
        self.persist()

    def _on_seed_num(self, value: int) -> None:
        if self._syncing:
            return
        p = self._bucket()
        p["seed"] = None if value < 0 else int(value)
        self._commit()
        self.persist()

    def _on_seed_dice(self) -> None:
        p = self._bucket()
        p["seedLock"] = True
        p["seed"] = rand_seed()
        self._syncing = True
        try:
            self._sync_seed()
        finally:
            self._syncing = False
        self._commit()
        self.persist()

    def _on_negative(self) -> None:
        if self._syncing:
            return
        self._bucket()["negative"] = self.negative_input.toPlainText()
        self._commit()
        self.persist()

    def _on_advanced(self) -> None:
        if self._syncing:
            return
        self._bucket()["advanced"] = self.advanced_input.toPlainText()
        self._commit()
        self.persist()

    def _on_bpm(self, value: int) -> None:
        if self._syncing:
            return
        self.settings["bpm"] = int(value)
        self.bpm_label.setText(f"{value} BPM")
        self._commit()
        self.persist()

    def _on_music_duration(self, value: int) -> None:
        if self._syncing:
            return
        self.settings["musicDuration"] = int(value)
        self.music_dur_label.setText(f"{value} 秒")
        self._commit()
        self.persist()

    def sync_style_chips(self, prompt_text: str) -> None:
        self._syncing = True
        try:
            for chip in self.style_chips + self.genre_chips:
                chip.setChecked(str(chip._payload) in (prompt_text or ""))
        finally:
            self._syncing = False

    # ------------------------------------------------------------------ 引擎
    def refresh_engines(self, engines: list) -> None:
        """``engines``: objects with engine_id/state/message (EngineStatus)."""
        self._engines = list(engines)
        modality = MODALITY[self.mode]
        options = [
            e
            for e in self._engines
            if modality in (ENGINE_TASK_TYPES.get(e.engine_id) or ())
        ]
        self._syncing = True
        try:
            self.engine_select.blockSignals(True)
            self.engine_select.clear()
            if not options:
                self.engine_select.addItem(
                    "未注册视频引擎"
                    if modality == "video"
                    else "未注册音乐引擎（检查 .venv-music 与权重）"
                    if modality == "music"
                    else "未注册图像引擎（检查 engines_enabled）"
                )
                self.engine_state.setText(
                    "音乐引擎未注册：需 .venv-music 环境与 vendor\\ACE-Step-1.5"
                    "（运行 scripts\\setup_music_venv.ps1），未就绪前不会提交、不伪造输出"
                    if modality == "music"
                    else "引擎未注册"
                )
                self.engine_select.setEnabled(False)
            else:
                key = (
                    "vengine"
                    if modality == "video"
                    else "mengine"
                    if modality == "music"
                    else "engine"
                )
                ids = [e.engine_id for e in options]
                if self.settings.get(key) not in ids:
                    self.settings[key] = ids[0]
                for engine in options:
                    self.engine_select.addItem(engine.engine_id)
                self.engine_select.setCurrentIndex(
                    ids.index(self.settings.get(key))
                )
                self.engine_select.setEnabled(True)
                current = next(
                    (e for e in options if e.engine_id == self.settings.get(key)),
                    None,
                )
                label = (
                    ENGINE_STATE_LABELS.get(current.state, current.state)
                    if current
                    else "?"
                )
                state_text = (current.message or label) if current else label
                if modality == "music" and current and current.state == "stopped":
                    # 音乐引擎懒启动：注册成功但 worker 尚未拉起时给明确的 ready 文案，
                    # 避免看起来像「不可用」。
                    state_text = (
                        "ACE-Step 1.5（音乐）已注册 · 提交后懒启动"
                        "（首次加载权重约 10.03 GB，最低 12GB 显存）"
                    )
                self.engine_state.setText(state_text)
        finally:
            self.engine_select.blockSignals(False)
            self._syncing = False

    def engine_id(self) -> str:
        if not self._engines:
            return ""
        modality = MODALITY[self.mode]
        key = (
            "vengine"
            if modality == "video"
            else "mengine"
            if modality == "music"
            else "engine"
        )
        options = [
            e.engine_id
            for e in self._engines
            if modality in (ENGINE_TASK_TYPES.get(e.engine_id) or ())
        ]
        chosen = self.settings.get(key)
        return chosen if chosen in options else (options[0] if options else "")

    def engine_pill(self) -> tuple[str, str]:
        """(text, kind) for the topbar engine pill."""
        modality = MODALITY[self.mode]
        options = [
            e
            for e in self._engines
            if modality in (ENGINE_TASK_TYPES.get(e.engine_id) or ())
        ]
        if not options:
            return "无引擎", "bad"
        engine_id = self.engine_id()
        current = next((e for e in options if e.engine_id == engine_id), None)
        label = (
            ENGINE_STATE_LABELS.get(current.state, current.state) if current else "?"
        )
        kind = (
            "ok"
            if current and current.state == "ready"
            else "bad"
            if current and current.state == "failed"
            else ""
        )
        return f"{engine_id} · {label}", kind

    def _on_engine_selected(self, _index: int) -> None:
        if self._syncing:
            return
        text = self.engine_select.currentText()
        modality = MODALITY[self.mode]
        key = (
            "vengine"
            if modality == "video"
            else "mengine"
            if modality == "music"
            else "engine"
        )
        if any(e.engine_id == text for e in self._engines):
            self.settings[key] = text
            self.refresh_engines(self._engines)
            self.engine_changed.emit(text)
            self.persist()


# 尺寸滑杆 <-> 数字框 绑定（构造函数末尾统一接线，避免前向引用）
def _wire(panel: ParamPanel) -> None:
    panel._size_handlers(panel.width_range, panel.width_num, "width")
    panel._size_handlers(panel.height_range, panel.height_num, "height")
    panel._size_handlers(panel.vwidth_range, panel.vwidth_num, "width")
    panel._size_handlers(panel.vheight_range, panel.vheight_num, "height")
