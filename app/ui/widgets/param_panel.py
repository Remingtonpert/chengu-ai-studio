"""Right-hand parameter panel, the Qt counterpart of the web console's
``aside.panel``: engine select, size/aspect chips, frames, style/genre chips,
music options, steps, seed, negative, advanced JSON. Five generation boards
(``t2i`` / ``i2i`` / ``t2v`` / ``i2v`` / ``music``) plus the ``agent`` view
entry (2026-11-09 重新选型：图片走 Wan2.1 单帧、音乐走 ACE-Step 1.5，全部按
6GB 显存档位设计；旧的 FLUX/SD1.5 档位与 12GB 门槛已随引擎替换而退役)。
All parameter buckets live in the shared settings dict
(``settings["modes"][mode]``) exactly like ``state.settings.modes`` in
app.js, so nothing about quality semantics changes.
"""

from __future__ import annotations

from math import sqrt

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
    "wan-t2i": ("image",),
    "wan-i2i": ("image",),
    "wan-t2v": ("video",),
    "wan-i2v": ("video",),
    "ace-step-15": ("music",),
}
MODE_ENGINE_ALLOW = {
    # 引擎与标签页一一对应：文生图 wan-t2i（T2V-1.3B 单帧）、图生图 wan-i2i（VACE
    # 参考重绘）、文生视频 wan-t2v、图生视频 wan-i2v（VACE）——图片与视频共用
    # .venv-video 与 models\wan2.1 权重，但四个引擎各自独立进程、互斥加载，
    # 切换板块不会把多份 Wan 权重同时搬进显存。音乐 ace-step-15（ACE-Step 1.5）。
    "t2i": ("wan-t2i",),
    "i2i": ("wan-i2i",),
    "t2v": ("wan-t2v",),
    "i2v": ("wan-i2v",),
    "music": ("ace-step-15",),
}
ENGINE_STATE_LABELS = {"stopped": "未启动", "ready": "就绪", "failed": "失败"}

# 左侧栏（视图）合法取值：AI智能体 + 五个生成板块。「agent」只切视图，不产生
# 任何生成参数桶，所以参数栏在 agent 视图下整块收起。
VIEW_MODES = ("agent",) + MODES

# 像素预算上限：与 app/engine/video/wan.py 的 _MAX_PIXELS（832×480×1.15 ≈ 0.46MP）
# 同一档位。超过这个线引擎会自己缩到当前档位上限，这里提前缩好，界面上不给假档位。
VIDEO_MAX_PIXELS = int(832 * 480 * 1.15)
VIDEO_WARN_PIXELS = 459_000

# 出图 / 出视频分辨率只按 Wan2.1 1.3B 真正跑得动的 480p 档给：宽高都是 16 的倍数
# （VAE 空间 8× + 时间 4× 压缩），且不超过 VIDEO_MAX_PIXELS。要 1080p 得另配超分或更大模型。
# 图片档与视频档同一张权重同一预算，所以共用同一组档位。
ASPECT_CHIPS_VIDEO = [
    (832, 480, "16:9 480p"),
    (480, 832, "9:16 480p"),
    (624, 624, "1:1 480p"),
    (640, 480, "4:3 480p"),
    (480, 640, "3:4 480p"),
]
ASPECT_CHIPS_IMAGE = ASPECT_CHIPS_VIDEO

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

# 诚实文案：图片/音乐档的峰值显存是按 6GB 档位设计后的「预期值」，尚未在这台机器
# 上实测（验收要看任务元数据里的 peak_vram_mb）；视频档的 2021MB 是真机实测值。
ENGINE_MODE_HINTS = {
    "image": (
        "Wan2.1-T2V-1.3B 单帧出图（Apache-2.0，本地离线）：wan-t2i 直接渲染 / wan-i2i 用 "
        "VACE 参考重绘，输出 PNG。与视频共用 .venv-video 与 models\\wan2.1 权重：bf16 + "
        "逐层 CPU 卸载，按 6GB 显存档位设计（单帧 token 约为 81 帧视频的 1/13，预期峰值"
        "低于视频档的实测 2021MB，图片档本身尚未实测，验收看任务元数据 peak_vram_mb）；"
        "逐层卸载需约 16GB 空闲物理内存。分辨率停在 480p 档（宽高 16 的倍数，超预算引擎"
        "会自动缩到档位上限），一次 1 张。出图前先释放本机 LLM 引擎显存"
        "（scripts\\release_local_engine.bat）。"
    ),
    "video": (
        "Wan2.1 1.3B（Apache-2.0，本地离线）：文生视频 wan-t2v / 图生视频 wan-i2v（VACE），"
        "导出 16fps mp4。按 6GB 显存档位设计：bf16 + 逐层 CPU 卸载，真机实测峰值 2021MB，"
        "起跑只需约 1500MB 空闲显存；但逐层卸载需要约 16GB 空闲物理内存。分辨率停在 480p 档"
        "（约 0.46MP 像素预算，超预算引擎会自动缩到当前档位上限），帧数 5-161（自动取 4n+1），"
        "1.3B 档最高约 480p，要 1080p 得另配超分或更大模型。提交前本端会先把自己拉起的空闲"
        "引擎与大模型释放掉再判显存；真不够会点名是哪个程序占着显存，要手动清可以跑 "
        "scripts\\release_local_engine.bat。"
    ),
    "music": (
        "ACE-Step 1.5（MIT，本地离线，无云端 API）：48kHz wav，带人声（中英文）或纯器乐，"
        "10-480 秒。按 6GB 显存档位设计：5Hz 语言模型与 DiT 走 CPU 卸载，峰值显存为预期值"
        "（未实测，验收看任务元数据 peak_vram_mb）；turbo 默认 8 步。权重在 models\\ace-step-1.5"
        "（约 9.4GB，占物理内存），首次加载 1-3 分钟。与图片/视频共用 FIFO 串行队列。"
    ),
}

MUSIC_PANEL_WARNING = (
    "已接入 ACE-Step 1.5（权重 MIT，本地离线推理，无云端 API；按 6GB 显存档位设计，"
    "LM/DiT 走 CPU 卸载，峰值为预期值未实测）。支持带人声（中英文）或纯器乐乐曲，"
    "10-480 秒，输出 48kHz wav；不支持超长多轨道复杂编曲。显存不足会被引擎明确拦截，"
    "不会伪造输出。"
)


def default_mode_params(mode: str) -> dict:
    p = {
        "width": 832,
        "height": 480,
        "frames": 33,
        "steps": 28 if mode != "music" else 8,
        "seedLock": False,
        "seed": None,
        "negative": "",
        "advanced": "",
    }
    return p


def default_settings() -> dict:
    return {
        "mode": "agent",
        "refDir": "",
        "vengine": "",
        "mengine": "ace-step-15",
        "bpm": 120,
        "musicDuration": 180,
        "instrumental": False,
        "vocalLanguage": "zh",
        "modes": {mode: default_mode_params(mode) for mode in MODES},
    }


def mode_label(mode: object) -> str:
    """板块的显示名；陌生取值原样返回，历史记录不会渲染成空白或 KeyError。"""

    key = str(mode or "")
    return MODE_LABELS.get(key) or key


def coerce_view_mode(value: object) -> str:
    """把 settings["mode"]（视图板块）收敛到合法值。

    2026-11-09 重新选型后 t2i / i2i / music 三个板块重新在岗，旧存档里存的
    这三个值直接有效；只有真正的陌生值（含空）才回落到「AI智能体」。
    """

    mode = str(value or "")
    if mode in VIEW_MODES:
        return mode
    return "agent"


def _snap_multiple(value, default: int, low: int, high: int, multiple: int) -> int:
    try:
        n = int(round(float(value)))
    except (TypeError, ValueError):
        n = int(default)
    n = max(low, min(high, n))
    n -= n % multiple
    return max(low, n)


def _clamp_video(p: dict, mode: str = "t2v") -> dict:
    """把所有板块的参数夹进引擎真能吃的范围（与 app/engine/video/wan.py、
    app/tasks/music_worker.py 对齐）。"""

    width = _snap_multiple(p.get("width"), 832, 256, 1024, 16)
    height = _snap_multiple(p.get("height"), 480, 256, 1024, 16)
    pixels = width * height
    if pixels > VIDEO_MAX_PIXELS:
        # 超预算：等比缩到预算内再回到 16 的倍数，引擎也会做同样的事。
        factor = sqrt(VIDEO_MAX_PIXELS / pixels)
        width = _snap_multiple(width * factor, width, 256, 1024, 16)
        height = _snap_multiple(height * factor, height, 256, 1024, 16)
    p["width"], p["height"] = width, height
    p["frames"] = snap_frames(p.get("frames"))
    try:
        steps = int(p.get("steps"))
    except (TypeError, ValueError):
        steps = 28
    p["steps"] = max(1, min(100 if mode != "music" else 50, steps))
    seed = p.get("seed")
    if seed is not None:
        try:
            p["seed"] = int(seed)
        except (TypeError, ValueError):
            p["seed"] = None
    p["seedLock"] = bool(p.get("seedLock"))
    p["negative"] = str(p.get("negative") or "")
    p["advanced"] = str(p.get("advanced") or "")
    return p


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
            stored = modes_raw.get(mode)
            merged = dict(base["modes"][mode])
            if isinstance(stored, dict):
                merged.update({k: v for k, v in stored.items() if k in merged})
            base["modes"][mode] = _clamp_video(merged, mode)
    base["mode"] = coerce_view_mode(base.get("mode"))
    # 音乐档的顶层参数（BPM / 时长 / 纯器乐 / 人声语言）：夹进引擎白名单范围。
    try:
        base["bpm"] = max(30, min(300, int(base.get("bpm") or 120)))
    except (TypeError, ValueError):
        base["bpm"] = 120
    try:
        base["musicDuration"] = max(10, min(480, int(base.get("musicDuration") or 180)))
    except (TypeError, ValueError):
        base["musicDuration"] = 180
    base["instrumental"] = bool(base.get("instrumental"))
    if base.get("vocalLanguage") not in ("zh", "en"):
        base["vocalLanguage"] = "zh"
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
        hint.setWordWrap(True)
        hint.setTextFormat(Qt.TextFormat.PlainText)
        if warn:
            hint.setObjectName("hintWarn")
        else:
            hint.setObjectName("hint")
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
        self.mode = coerce_view_mode(settings.get("mode"))

        body = QWidget(self)
        body.setObjectName("panelBody")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(14, 12, 14, 18)
        layout.setSpacing(0)

        head = QLabel("参数设置", body)
        head.setObjectName("panelTitle")
        layout.addWidget(head)

        # ---------------------------------------------------------------- 引擎
        sec_engine = _Section(set(MODES), body)
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

        # -------------------------------------------------- 尺寸（图片/视频共用）
        # 图片与视频跑的是同一张 Wan2.1 1.3B 权重、同一个 480p 像素预算，所以
        # 四个档共用这一组档位与滑杆；只有帧数（时间维）是视频档独有的。
        self.size_modes = {"t2i", "i2i", "t2v", "i2v"}
        sec_video_size = _Section(self.size_modes, body)
        self.size_title = sec_video_size.add_title("生成尺寸（480p 档）")
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
        self.frames_modes = {"t2v", "i2v"}
        sec_frames = _Section(self.frames_modes, body)
        _, self.frames_label = sec_frames.add_value_row("时长")
        self.frames_range = self._slider(sec_frames, 5, 161, 4, 33)
        self.frames_range.valueChanged.connect(self._on_frames)
        sec_frames.add_hint(
            "导出固定 16fps（Wan 原生）；帧数自动取到 4n+1（时间压缩约束），越长越吃显存与时间。"
        )
        layout.addWidget(sec_frames)

        # ------------------------------------------------------ 风格 / 曲风 chip
        sec_style = _Section(set(MODES), body)
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

        # ---------------------------------------------------------------- 音乐
        self.music_modes = {"music"}
        sec_music = _Section(self.music_modes, body)
        sec_music.add_title("音乐选项")
        sec_music.add_hint(MUSIC_PANEL_WARNING, warn=True)
        _, self.bpm_label = sec_music.add_value_row("BPM")
        self.bpm_range = self._slider(sec_music, 60, 180, 1, 120)
        self.bpm_range.valueChanged.connect(self._on_bpm)
        _, self.music_dur_label = sec_music.add_value_row("目标时长")
        self.music_dur_range = self._slider(sec_music, 30, 480, 30, 180)
        self.music_dur_range.valueChanged.connect(self._on_music_duration)
        self.instrumental_toggle = QPushButton("纯器乐（不要人声）", sec_music)
        self.instrumental_toggle.setObjectName("chip")
        self.instrumental_toggle.setCheckable(True)
        self.instrumental_toggle.toggled.connect(self._on_instrumental)
        sec_music._layout.addWidget(self.instrumental_toggle)
        lang_row = QHBoxLayout()
        lang_row.setSpacing(8)
        lang_caption = QLabel("人声语言", sec_music)
        lang_caption.setObjectName("sliderLabel")
        self.vocal_lang_select = QComboBox(sec_music)
        self.vocal_lang_select.setObjectName("engineSelect")
        self.vocal_lang_select.addItem("中文（zh）", "zh")
        self.vocal_lang_select.addItem("English (en)", "en")
        self.vocal_lang_select.currentIndexChanged.connect(self._on_vocal_language)
        lang_row.addWidget(lang_caption)
        lang_row.addWidget(self.vocal_lang_select, 1)
        sec_music._layout.addLayout(lang_row)
        sec_music.add_hint(
            "时长 30-480 秒（引擎白名单 10-480，超出会被截到上限，产物按真实时长为准）；"
            "BPM 60-180（引擎可到 30-300，要更极端的速度写进高级参数）。"
        )
        layout.addWidget(sec_music)

        # --------------------------------------------------------------- steps
        sec_steps = _Section(set(MODES), body)
        _, self.steps_label = sec_steps.add_value_row("Steps")
        self.steps_range = self._slider(sec_steps, 1, 50, 1, 28)
        self.steps_range.valueChanged.connect(self._on_steps)
        self.steps_hint = sec_steps.add_hint(
            "图片 / 视频推荐 28 步（默认）；音乐 turbo 推荐 8 步。步数直接决定等待时长。"
        )
        layout.addWidget(sec_steps)

        # ---------------------------------------------------------------- seed
        sec_seed = _Section(set(MODES), body)
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
            "不固定 = 每次随机 seed；固定 = 可复现同一张图 / 同一段视频 / 同一段音乐。"
        )
        layout.addWidget(sec_seed)

        # -------------------------------------------------------- 负面提示词
        self.negative_modes = {"t2i", "i2i", "t2v", "i2v"}
        sec_negative = _Section(self.negative_modes, body)
        sec_negative.add_title("负面提示词")
        self.negative_input = QPlainTextEdit(sec_negative)
        self.negative_input.setObjectName("textareaSm")
        self.negative_input.setFixedHeight(56)
        self.negative_input.setPlaceholderText("模糊、低质量、水印…")
        self.negative_input.textChanged.connect(self._on_negative)
        sec_negative._layout.addWidget(self.negative_input)
        layout.addWidget(sec_negative)

        # ---------------------------------------------------------- 高级参数
        self.advanced_modes = {"t2i", "i2i", "t2v", "i2v"}
        sec_advanced = _Section(self.advanced_modes, body)
        sec_advanced.add_title("高级参数（JSON，可选）")
        self.advanced_input = QPlainTextEdit(sec_advanced)
        self.advanced_input.setObjectName("textareaSm")
        self.advanced_input.setFixedHeight(56)
        self.advanced_input.setPlaceholderText('{"num_frames": 49, "steps": 30}')
        self.advanced_input.textChanged.connect(self._on_advanced)
        sec_advanced._layout.addWidget(self.advanced_input)
        self.advanced_hint = sec_advanced.add_hint(
            "白名单：width height num_frames steps seed negative_prompt init_image_path"
        )
        layout.addWidget(sec_advanced)

        layout.addStretch(1)
        self.setWidget(body)
        self._sections = [
            sec_engine,
            sec_video_size,
            sec_frames,
            sec_style,
            sec_music,
            sec_steps,
            sec_seed,
            sec_negative,
            sec_advanced,
        ]
        self.set_mode(self.mode)
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
        if self.mode not in MODES:
            return {}
        return self.settings["modes"][self.mode]

    def advanced_text(self) -> str:
        return self.advanced_input.toPlainText().strip()

    def set_mode(self, mode: str) -> None:
        mode = coerce_view_mode(mode)
        self.mode = mode
        self.settings["mode"] = mode
        # agent 视图下没有可编辑的生成参数：整栏自己收起（主窗口同样按
        # mode != "agent" 处理，这里保证启动落在「AI智能体」时右栏不会空着）。
        generating = mode in MODES
        self.setVisible(generating)
        for section in self._sections:
            section.setVisible(generating and mode in section.modes)
        if generating:
            modality = MODALITY[mode]
            self.size_title.setText(
                "出图尺寸（480p 档）" if modality == "image" else "视频尺寸（480p 档）"
            )
            self.style_chips_container.setVisible(modality != "music")
            self.genre_chips_container.setVisible(modality == "music")
            self.engine_mode_hint.setText(ENGINE_MODE_HINTS[modality])
        self.sync_all()
        self.refresh_engines(self._engines)
        self.persist()

    # ------------------------------------------------------------------ 参数同步
    def _bucket(self) -> dict:
        if self.mode not in MODES:
            return {}
        return self.settings["modes"][self.mode]

    def _engine_mode(self) -> str:
        """引擎上下文用哪个板块：agent 视图下按默认生成板块给状态。"""

        return self.mode if self.mode in MODES else MODES[0]

    def sync_all(self) -> None:
        self._syncing = True
        try:
            if self.mode not in MODES:
                return
            p = self._bucket()
            modality = MODALITY[self.mode]
            if modality != "music":
                self._sync_size(
                    p,
                    self.vwidth_range,
                    self.vwidth_num,
                    self.vheight_range,
                    self.vheight_num,
                    self.aspect_chips_video,
                    self.video_hint,
                )
                if modality == "video":
                    self._sync_frames()
                self.negative_input.setPlainText(p.get("negative", ""))
                self.advanced_input.setPlainText(p.get("advanced", ""))
                self.advanced_hint.setText(
                    "白名单：width height num_frames steps seed negative_prompt init_image_path"
                )
            else:
                self._sync_music()
            self._sync_steps()
            self._sync_seed()
        finally:
            self._syncing = False

    def _sync_music(self) -> None:
        bpm = int(self.settings.get("bpm") or 120)
        self.bpm_range.setValue(max(self.bpm_range.minimum(), min(self.bpm_range.maximum(), bpm)))
        self.bpm_label.setText(f"{bpm} BPM")
        duration = int(self.settings.get("musicDuration") or 180)
        self.music_dur_range.setValue(
            max(self.music_dur_range.minimum(), min(self.music_dur_range.maximum(), duration))
        )
        self.music_dur_label.setText(f"{duration} 秒 · {duration / 60:.1f} 分钟")
        self.instrumental_toggle.setChecked(bool(self.settings.get("instrumental")))
        lang = str(self.settings.get("vocalLanguage") or "zh")
        index = self.vocal_lang_select.findData(lang)
        self.vocal_lang_select.setCurrentIndex(max(0, index))

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
        w_spin.setValue(max(w_spin.minimum(), min(w_spin.maximum(), width)))
        h_spin.setValue(max(w_spin.minimum(), min(w_spin.maximum(), height)))
        for chip in chips:
            chip_w, chip_h = chip._payload
            chip.setChecked(chip_w == width and chip_h == height)
        pixels = width * height
        is_video = MODALITY.get(self.mode) == "video"
        if pixels > VIDEO_WARN_PIXELS:
            hint.setObjectName("hintWarn")
            if is_video:
                frames = snap_frames(p.get("frames") or 33)
                hint.setText(
                    f"视频 {width}×{height} 已超出 480p 档约 {VIDEO_WARN_PIXELS // 1000}K 像素预算，"
                    f"引擎会自动缩到当前档位上限（× {frames} 帧，16fps）。"
                )
            else:
                hint.setText(
                    f"出图 {width}×{height} 已超出 480p 档约 {VIDEO_WARN_PIXELS // 1000}K 像素预算，"
                    "引擎会自动缩到当前档位上限（单帧 PNG）。"
                )
        else:
            hint.setObjectName("hint")
            if is_video:
                hint.setText(
                    f"视频像素 {pixels / 1e6:.2f}MP · 按 6GB 显存档位设计（bf16 + 逐层 CPU 卸载，"
                    "真机实测峰值 2021MB），另需约 16GB 空闲物理内存。"
                )
            else:
                hint.setText(
                    f"出图像素 {pixels / 1e6:.2f}MP · 与视频同一张 Wan2.1 权重、同一像素预算，"
                    "单帧 token 约为 81 帧视频的 1/13（预期峰值更低，未实测），"
                    "另需约 16GB 空闲物理内存。"
                )
        hint.style().unpolish(hint)
        hint.style().polish(hint)

    def _sync_steps(self) -> None:
        p = self._bucket()
        steps = int(p.get("steps") or 1)
        self.steps_range.setValue(max(1, min(self.steps_range.maximum(), steps)))
        self.steps_label.setText(f"{steps} 步")
        if MODALITY.get(self.mode) == "music":
            self.steps_hint.setText(
                "音乐 turbo 推荐 8 步（默认）；加到 16-30 步质量更好、耗时成倍增加。"
            )
        else:
            self.steps_hint.setText(
                "图片 / 视频推荐 28 步（默认）；6GB 档上步数直接决定等待时长。"
            )

    def _sync_seed(self) -> None:
        p = self._bucket()
        self.seed_lock.setChecked(bool(p.get("seedLock")))
        self.seed_num.setEnabled(bool(p.get("seedLock")))
        seed = p.get("seed")
        self.seed_num.setValue(int(seed) if isinstance(seed, (int, float)) else -1)
        self.seed_hint.setText(
            "不固定 = 每次随机 seed；固定 = 可复现同一张图 / 同一段视频 / 同一段音乐。"
        )

    def _sync_frames(self) -> None:
        p = self._bucket()
        frames = snap_frames(p.get("frames") or 33)
        self.frames_range.setValue(frames)
        self.frames_label.setText(f"{frames / 16:.2f} 秒 · {frames} 帧 @16fps")

    # ------------------------------------------------------------------ 交互
    def _commit(self) -> None:
        if not self._syncing:
            self.params_changed.emit()

    def _set_wh(self, key: str, value: int) -> None:
        p = self._bucket()
        if not p:
            return
        p[key] = int(value)
        self._clamp_into_budget(p)
        self._syncing = True
        try:
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

    def _clamp_into_budget(self, p: dict) -> None:
        """宽高改完后自动回到 16 的倍数与 480p 像素预算内（引擎同样会做这步）。"""

        width = _snap_multiple(p.get("width"), 832, 256, 1024, 16)
        height = _snap_multiple(p.get("height"), 480, 256, 1024, 16)
        pixels = width * height
        if pixels > VIDEO_MAX_PIXELS:
            factor = sqrt(VIDEO_MAX_PIXELS / pixels)
            width = _snap_multiple(width * factor, width, 256, 1024, 16)
            height = _snap_multiple(height * factor, height, 256, 1024, 16)
        p["width"], p["height"] = width, height

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
        if self._syncing or not self._bucket():
            return
        self._set_wh(key, int(value))

    def _on_chip(self, chip: QPushButton) -> None:
        payload = chip._payload
        p = self._bucket()
        if not p:
            return
        if chip.objectName() == "chip" and isinstance(payload, tuple) and len(payload) == 2:
            width, height = payload
            p["width"], p["height"] = int(width), int(height)
            self._clamp_into_budget(p)
            self._syncing = True
            try:
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

    def _on_frames(self, value: int) -> None:
        if self._syncing:
            return
        p = self._bucket()
        if not p:
            return
        p["frames"] = snap_frames(value)
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
        p = self._bucket()
        if not p:
            return
        p["steps"] = int(value)
        self.steps_label.setText(f"{value} 步")
        self._commit()
        self.persist()

    def _on_seed_lock(self, checked: bool) -> None:
        if self._syncing:
            return
        p = self._bucket()
        if not p:
            return
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
        if not p:
            return
        p["seed"] = None if value < 0 else int(value)
        self._commit()
        self.persist()

    def _on_seed_dice(self) -> None:
        p = self._bucket()
        if not p:
            return
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
        p = self._bucket()
        if not p:
            return
        p["negative"] = self.negative_input.toPlainText()
        self._commit()
        self.persist()

    def _on_advanced(self) -> None:
        if self._syncing:
            return
        p = self._bucket()
        if not p:
            return
        p["advanced"] = self.advanced_input.toPlainText()
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
        self.music_dur_label.setText(f"{value} 秒 · {value / 60:.1f} 分钟")
        self._commit()
        self.persist()

    def _on_instrumental(self, checked: bool) -> None:
        if self._syncing:
            return
        self.settings["instrumental"] = bool(checked)
        self._commit()
        self.persist()

    def _on_vocal_language(self, _index: int) -> None:
        if self._syncing:
            return
        self.settings["vocalLanguage"] = str(self.vocal_lang_select.currentData() or "zh")
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
    def _engine_setting_key(self) -> str:
        """当前板块的引擎选择存在哪个 settings 键：音乐一档独立、其余共用 vengine。"""

        return "mengine" if MODALITY.get(self._engine_mode()) == "music" else "vengine"

    def _modality_engines(self, engines: list, mode: str | None = None) -> list:
        modality = MODALITY.get(mode if mode in MODES else self._engine_mode(), "image")
        options = [
            e
            for e in engines
            if modality in (ENGINE_TASK_TYPES.get(e.engine_id) or ())
        ]
        allow = MODE_ENGINE_ALLOW.get(mode if mode in MODES else self._engine_mode())
        if allow:
            options = [e for e in options if e.engine_id in allow]
        return options

    def refresh_engines(self, engines: list) -> None:
        """``engines``: objects with engine_id/state/message (EngineStatus)."""

        self._engines = list(engines)
        mode = self._engine_mode()
        modality = MODALITY[mode]
        options = self._modality_engines(self._engines, mode)
        key = "mengine" if modality == "music" else "vengine"
        self._syncing = True
        try:
            self.engine_select.blockSignals(True)
            self.engine_select.clear()
            if not options:
                missing = {
                    "music": "音乐引擎未注册 · 需 .venv-music 与 models\\ace-step-1.5",
                    "video": "视频引擎未注册 · 需 .venv-video 与 models\\wan2.1",
                    "image": "图片引擎未注册 · 需 .venv-video 与 models\\wan2.1",
                }[modality]
                self.engine_select.addItem(missing)
                self.engine_state.setText(
                    {
                        "music": (
                            "音乐引擎未注册：需 .venv-music（scripts\\setup_music_venv.ps1）与 "
                            "models\\ace-step-1.5 权重（scripts\\download_music_weights.py），"
                            "未就绪前不会提交、不伪造输出"
                        ),
                        "video": (
                            "视频引擎未注册：需 .venv-video（scripts\\setup_video_venv.ps1）与 "
                            "models\\wan2.1 权重（scripts\\download_video_weights.py），"
                            "未就绪前不会提交、不伪造输出"
                        ),
                        "image": (
                            "图片引擎未注册：与视频共用 .venv-video（scripts\\setup_video_venv.ps1）"
                            "与 models\\wan2.1 权重（scripts\\download_video_weights.py），"
                            "未就绪前不会提交、不伪造输出"
                        ),
                    }[modality]
                )
                self.engine_select.setEnabled(False)
            else:
                ids = [e.engine_id for e in options]
                if self.settings.get(key) not in ids:
                    self.settings[key] = ids[0]
                for engine in options:
                    self.engine_select.addItem(engine.engine_id)
                self.engine_select.setCurrentIndex(ids.index(self.settings.get(key)))
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
                self.engine_state.setText((current.message or label) if current else label)
        finally:
            self.engine_select.blockSignals(False)
            self._syncing = False

    def engine_id(self) -> str:
        if not self._engines:
            return ""
        options = [e.engine_id for e in self._modality_engines(self._engines)]
        chosen = self.settings.get(self._engine_setting_key())
        return chosen if chosen in options else (options[0] if options else "")

    def engine_pill(self) -> tuple[str, str]:
        """(text, kind) for the topbar engine pill."""

        options = self._modality_engines(self._engines)
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
        if any(e.engine_id == text for e in self._engines):
            self.settings[self._engine_setting_key()] = text
            self.refresh_engines(self._engines)
            self.engine_changed.emit(text)
            self.persist()
