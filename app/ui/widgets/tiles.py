"""Feed tiles: image grid tiles (hover V/U), skeletons, error tiles,
video cards and inline music play bars -- the Qt counterparts of the
``.tile`` family in the web console's style.css.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import (
    QObject,
    Qt,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from app.tasks.models import TaskRecord
from app.ui.widgets.common import (
    open_local_file,
    reveal_in_explorer,
)
from app.ui.widgets.skeleton import SkeletonBlock

try:  # QtMultimedia ships with PySide6; guard anyway for exotic installs.
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

    HAS_MULTIMEDIA = True
except ImportError:  # pragma: no cover - fallback path
    QAudioOutput = QMediaPlayer = None
    HAS_MULTIMEDIA = False

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
VIDEO_SUFFIXES = {".mp4", ".webm", ".mkv", ".mov"}
AUDIO_SUFFIXES = {".wav", ".mp3", ".m4a", ".flac", ".ogg"}

KIND_LABELS = {
    "imagine": "想象",
    "upscale": "U 放大",
    "variation": "V 变化",
    "video": "视频",
    "music": "音乐",
}

STATUS_LABELS = {
    "queued": "排队中",
    "preparing": "加载模型…",
    "running": "生成中",
    "completed": "已完成",
    "failed": "失败",
    "cancelled": "已取消",
}


def output_paths(rec: TaskRecord, suffixes: set[str]) -> list[str]:
    return [p for p in rec.output_files if Path(p).suffix.lower() in suffixes]


class _Fader(QObject):
    """Fade child overlays in/out on hover (Qt version of ``.tile:hover .tile-acts``)."""

    def __init__(self, target: QWidget, overlays: list[QWidget]) -> None:
        super().__init__(target)
        self._effects = []
        for overlay in overlays:
            effect = QGraphicsOpacityEffect(overlay)
            overlay.setGraphicsEffect(effect)
            self._effects.append(effect)
        self._value = 0.0
        for effect in self._effects:
            effect.setOpacity(0.0)
        self._anim = QVariantAnimation(target)
        self._anim.setDuration(150)
        self._anim.valueChanged.connect(self._apply)
        target.installEventFilter(self)
        self._target = target

    def _apply(self, value) -> None:
        opacity = float(value)
        self._value = opacity
        for effect in self._effects:
            effect.setOpacity(opacity)

    def fade_to(self, opacity: float) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._value)
        self._anim.setEndValue(opacity)
        self._anim.start()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        from PySide6.QtCore import QEvent

        if watched is self._target:
            if event.type() is QEvent.Type.Enter:
                self.fade_to(1.0)
            elif event.type() is QEvent.Type.Leave:
                self.fade_to(0.0)
        return False


class TileBase(QFrame):
    tileClicked = Signal(str)
    variationRequested = Signal(str)
    upscaleRequested = Signal(str)
    cancelRequested = Signal(str)
    retryRequested = Signal(str)
    # 手动「另存」：产物本来就在项目内 output\（本工具即时显示的那份），这个信号让
    # 创作页弹目录/文件名选择框，再复制一份到用户挑的位置（对应网页端的「另存」）。
    saveRequested = Signal(str)
    # 「▶ 播放」：产物在工具内的播放窗里放，不交给系统播放器（对齐网页端灯箱）。
    playRequested = Signal(str)

    def __init__(self, rec: TaskRecord, tile_w: int, tokens: dict[str, str]) -> None:
        super().__init__()
        self.rec = rec
        self.task_id = rec.task_id
        self.tile_w = tile_w
        self.tokens = tokens
        self.setObjectName("tile")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def add_hover_overlay(self, acts: QWidget, foot: QLabel) -> None:
        self._fader = _Fader(self, [acts, foot])

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() is Qt.MouseButton.LeftButton:
            self.tileClicked.emit(self.task_id)
        super().mousePressEvent(event)

    def make_foot(self, text: str) -> QLabel:
        foot = QLabel(text, self)
        foot.setObjectName("tileFoot")
        foot.move(10, 8)
        foot.adjustSize()
        return foot

    def make_acts(self, buttons: list[QPushButton], height: int = 40) -> QWidget:
        acts = QFrame(self)
        acts.setObjectName("tileActs")
        layout = QHBoxLayout(acts)
        layout.setContentsMargins(8, 6, 8, 8)
        layout.setSpacing(6)
        layout.addStretch(1)
        for button in buttons:
            layout.addWidget(button)
        acts.setGeometry(1, self.height() - height - 1, self.width() - 2, height)
        acts.raise_()
        return acts

    def save_button(self, text: str = "⬇ 另存", object_name: str = "vuBtn") -> QPushButton:
        """「另存」按钮：点了由创作页弹保存对话框，把产物复制一份到自选位置。"""
        button = QPushButton(text, self)
        button.setObjectName(object_name)
        button.setToolTip(
            "把这份产物复制到你选的目录（可任意盘符/移动硬盘）；"
            "项目内 output 里那份始终保留，本工具显示的就是它"
        )
        button.clicked.connect(lambda: self.saveRequested.emit(self.task_id))
        return button

    def vu_buttons(self) -> list[QPushButton]:
        button_v = QPushButton("V 变化", self)
        button_u = QPushButton("U 放大", self)
        for button in (button_v, button_u):
            button.setObjectName("vuBtn")
        button_v.clicked.connect(lambda: self.variationRequested.emit(self.task_id))
        button_u.clicked.connect(lambda: self.upscaleRequested.emit(self.task_id))
        return [button_v, button_u, self.save_button()]


class ImageTile(TileBase):
    def __init__(
        self, rec: TaskRecord, tile_w: int, tokens: dict[str, str]
    ) -> None:
        super().__init__(rec, tile_w, tokens)
        files = output_paths(rec, IMAGE_SUFFIXES)
        pixmap = QPixmap(files[0]) if files else QPixmap()
        if not files or pixmap.isNull():
            label = QLabel(
                self,
                )
            label.setObjectName("mutedMsg")
            label.setWordWrap(True)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setText(
                "产物不在输出目录或无法读取"
                + (f"：{files[0]}" if files else "")
            )
            label.setGeometry(12, 12, tile_w - 24, 130)
            self.setFixedSize(tile_w, 140)
            return
        inner_w = tile_w - 2
        height = max(40, round(inner_w * pixmap.height() / max(1, pixmap.width())))
        self.setFixedSize(tile_w, height)
        image = QLabel(self)
        image.setObjectName("tileImage")
        image.setGeometry(1, 1, inner_w, height - 2)
        image.setPixmap(
            pixmap.scaled(
                inner_w,
                height - 2,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        params = rec.request.parameters or {}
        foot = self.make_foot(
            f"seed {params.get('seed', '—')} · {params.get('width', '?')}×{params.get('height', '?')}"
        )
        acts = self.make_acts(self.vu_buttons())
        self.add_hover_overlay(acts, foot)


class SkeletonTile(TileBase):
    """queued / preparing / running: shimmer + progress + honest cancel button."""

    def __init__(
        self,
        rec: TaskRecord,
        batch_params: dict,
        tile_w: int,
        height: int,
        tokens: dict[str, str],
        queue_position: int | None = None,
    ) -> None:
        super().__init__(rec, tile_w, tokens)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setFixedSize(tile_w, height)
        block = SkeletonBlock(tokens, self)
        block.setGeometry(1, 1, tile_w - 2, height - 2)

        status = rec.status.value
        label = QLabel(self)
        label.setObjectName("skelLabel")
        if rec.request.task_type == "music":
            label.setText(
                "音乐管线：加载权重（首次约 1-2 分钟）→ LM 谱曲填词 → DiT 去噪 → VAE 解码渲染 48kHz wav"
            )
            label.setObjectName("skelHint")
        elif rec.request.task_type == "video":
            label.setText("视频管线：首帧 → 去噪 → 解码 → 编码，渲染需数分钟")
            label.setObjectName("skelHint")
        self.set_wordwrap(label, height >= 120)

        bar = QProgressBar(self)
        bar.setObjectName("skelBar")
        bar.setFixedWidth(70)
        bar.setTextVisible(False)
        if status == "queued":
            label.setText(
                f"排队中" + (f" · 第 {queue_position} 位" if queue_position else "")
            )
            bar.setRange(0, 0)  # indeterminate
        elif status == "preparing":
            label.setText("加载模型…")
            bar.setRange(0, 0)
        else:
            progress = rec.progress
            if isinstance(progress, (int, float)):
                percent = int(progress * 100 if progress <= 1 else progress)
                percent = max(0, min(100, percent))
                label.setText(f"生成中 {percent}%")
                bar.setRange(0, 100)
                bar.setValue(percent)
            else:
                label.setText("生成中")
                bar.setRange(0, 0)

        label.adjustSize()
        max_w = tile_w - 96
        if label.width() > max_w and height >= 120:
            label.setWordWrap(True)
            label.setFixedSize(max_w, 34)
        else:
            label.setFixedSize(min(label.width(), max_w), 16)
        bar.move(tile_w - 80, height - 18)
        label.move(10, height - 22)
        label.raise_()
        bar.raise_()
        block.lower()

        if status in {"queued", "preparing", "running"}:
            cancel = QPushButton("取消", self)
            cancel.setObjectName("btnCancelTile")
            cancel.setCursor(Qt.CursorShape.PointingHandCursor)
            cancel.clicked.connect(lambda: self.cancelRequested.emit(self.task_id))
            cancel.adjustSize()
            cancel.move(tile_w - cancel.width() - 8, 8)
            cancel.raise_()
        if rec.request.task_type in {"video", "music"}:
            foot = self.make_foot(rec.request.prompt[:48])
            acts = QFrame(self)
            acts.setObjectName("tileActs")
            acts.setGeometry(1, height - 1, tile_w - 2, 1)
            self.add_hover_overlay(acts, foot)

    @staticmethod
    def set_wordwrap(label: QLabel, wrap: bool) -> None:
        label.setWordWrap(wrap)


class ErrorTile(TileBase):
    def __init__(
        self,
        rec: TaskRecord,
        tile_w: int,
        tokens: dict[str, str],
        already_retried: bool,
    ) -> None:
        super().__init__(rec, tile_w, tokens)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setFixedWidth(tile_w)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        if rec.status.value == "failed":
            message = QLabel(rec.error_message or "任务失败", self)
            message.setObjectName("errMsg")
        else:
            message = QLabel("已取消", self)
            message.setObjectName("mutedMsg")
        message.setWordWrap(True)
        layout.addWidget(message)
        row = QHBoxLayout()
        row.setSpacing(8)
        if already_retried:
            hint = QLabel("已重试 →", self)
            hint.setObjectName("mutedMsg")
            row.addWidget(hint)
        else:
            retry = QPushButton("重试", self)
            retry.setObjectName("btnMini")
            retry.clicked.connect(lambda: self.retryRequested.emit(self.task_id))
            row.addWidget(retry)
        row.addStretch(1)
        layout.addLayout(row)
        self.setMinimumHeight(120)


class VideoTile(TileBase):
    def __init__(
        self, rec: TaskRecord, tile_w: int, tokens: dict[str, str]
    ) -> None:
        super().__init__(rec, tile_w, tokens)
        pngs = output_paths(rec, IMAGE_SUFFIXES)
        videos = output_paths(rec, VIDEO_SUFFIXES)
        video_path = videos[0] if videos else None
        pixmap = QPixmap(pngs[0]) if pngs else QPixmap()
        params = rec.request.parameters or {}
        inner_w = tile_w - 2
        aspect_w = int(params.get("width") or 512)
        aspect_h = int(params.get("height") or 384)
        img_h = max(
            60,
            round(
                (pixmap.height() / max(1, pixmap.width()) * inner_w)
                if not pixmap.isNull()
                else aspect_h / max(1, aspect_w) * inner_w
            ),
        )
        strip_h = 36
        self.setFixedSize(tile_w, img_h + strip_h)
        image = QLabel(self)
        image.setObjectName("tileImage")
        image.setGeometry(1, 1, inner_w, img_h - 1)
        if not pixmap.isNull():
            image.setPixmap(
                pixmap.scaled(
                    inner_w,
                    img_h - 1,
                    Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        else:
            image.setText("没有首帧预览，点开仍可在工具内播放")
            image.setAlignment(Qt.AlignmentFlag.AlignCenter)
            image.setObjectName("mutedMsg")
        strip = QFrame(self)
        strip.setObjectName("tileActs")
        strip.setGeometry(1, img_h, inner_w, strip_h)
        strip_layout = QHBoxLayout(strip)
        strip_layout.setContentsMargins(8, 5, 8, 7)
        strip_layout.setSpacing(6)
        open_button = QPushButton("▶ 播放", self)
        folder_button = QPushButton("📁 文件夹", self)
        save_button = self.save_button("⬇ 另存")
        for button in (open_button, folder_button):
            button.setObjectName("vuBtn")
        open_button.setToolTip("在工具里的播放窗观看，不调用系统播放器")
        if video_path:
            open_button.clicked.connect(
                lambda: self.playRequested.emit(self.task_id)
            )
            folder_button.clicked.connect(
                lambda: reveal_in_explorer(video_path)
            )
        else:
            open_button.setEnabled(False)
            folder_button.setEnabled(False)
            save_button.setEnabled(False)
        strip_layout.addWidget(open_button)
        strip_layout.addWidget(folder_button)
        strip_layout.addWidget(save_button)
        strip_layout.addStretch(1)
        strip.raise_()
        frames = int(params.get("num_frames") or 0)
        seconds = f"{frames / 16:.2f}s @16fps" if frames else "时长?"
        foot = self.make_foot(
            f"seed {params.get('seed', '—')} · {params.get('width', '?')}×{params.get('height', '?')} · {seconds}"
        )
        hidden_acts = QFrame(self)
        hidden_acts.setGeometry(1, img_h + strip_h - 1, inner_w, 1)
        self.add_hover_overlay(hidden_acts, foot)


class MusicTile(TileBase):
    """Full-width play bar (QtMultimedia), web ``<audio controls>`` equivalent."""

    def __init__(
        self, rec: TaskRecord, width: int, tokens: dict[str, str]
    ) -> None:
        super().__init__(rec, width, tokens)
        self.setObjectName("mediaCard")
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setFixedHeight(76)
        self.setFixedWidth(width)
        audios = output_paths(rec, AUDIO_SUFFIXES)
        audio_path = audios[0] if audios else None
        params = rec.request.parameters or {}

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(10)

        self._player = None
        self._audio_output = None
        if audio_path and HAS_MULTIMEDIA:
            self._audio_output = QAudioOutput(self)
            self._player = QMediaPlayer(self)
            self._player.setAudioOutput(self._audio_output)
            self._player.setSource(QUrl.fromLocalFile(str(Path(audio_path).resolve())))

        self.play_button = QPushButton("▶", self)
        self.play_button.setObjectName("playBtn")
        if not audio_path:
            self.play_button.setEnabled(False)
        else:
            self.play_button.clicked.connect(self._toggle_play)
        layout.addWidget(self.play_button)

        text_column = QVBoxLayout()
        text_column.setSpacing(3)
        name = QLabel(rec.request.prompt or "音乐", self)
        name.setObjectName("mediaName")
        name.setTextFormat(Qt.TextFormat.PlainText)
        name.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        name.setWordWrap(False)
        name.setMinimumWidth(60)
        target = params.get("duration")
        meta_text = (
            f"BPM {params.get('bpm', '—')} · 目标 "
            f"{f'{round(float(target))}s' if target else '?'} · seed {params.get('seed', '—')}"
            + ("" if audio_path else " · 无音频产物")
        )
        meta = QLabel(meta_text, self)
        meta.setObjectName("mediaMeta")
        # 播放失败的原因写在这里：卡片上不能只出现「按钮按下去没反应」。
        self._meta = meta
        self._meta_base = meta_text
        text_column.addWidget(name)
        text_column.addWidget(meta)
        layout.addLayout(text_column, 1)

        self.seek = QSlider(Qt.Orientation.Horizontal, self)
        self.seek.setFixedWidth(180)
        self.seek.setRange(0, 0)
        self.seek.sliderPressed.connect(lambda: self.seek.setProperty("dragging", True))
        self.seek.sliderReleased.connect(self._seek_released)
        layout.addWidget(self.seek)

        self.time_label = QLabel("0:00", self)
        self.time_label.setObjectName("playTime")
        self.time_label.setFixedWidth(74)
        layout.addWidget(self.time_label)

        folder = QPushButton("📁", self)
        folder.setObjectName("btnMini")
        folder.setEnabled(audio_path is not None)
        if audio_path:
            folder.clicked.connect(lambda: reveal_in_explorer(audio_path))
        layout.addWidget(folder)

        save = self.save_button("⬇", object_name="btnMini")
        save.setEnabled(audio_path is not None)
        layout.addWidget(save)

        if self._player is not None:
            self._player.positionChanged.connect(self._on_position)
            self._player.durationChanged.connect(self._on_duration)
            self._player.playbackStateChanged.connect(self._on_state)
            self._player.errorOccurred.connect(self._on_error)
        elif audio_path:
            self._note("本机缺少 Qt 播放组件，点 ▶ 会改用系统播放器")

    def _note(self, text: str) -> None:
        """把播放为什么放不出来写在卡片上，不靠用户猜。"""
        if getattr(self, "_meta", None) is None:
            return
        self._meta.setText(f"{self._meta_base} · {text}")
        self._meta.setToolTip(text)

    # ------------------------------------------------------------------ 播放
    def _toggle_play(self) -> None:
        if self._player is None:
            audios = output_paths(self.rec, AUDIO_SUFFIXES)
            if not audios:
                self._note("音频文件已不存在")
                return
            if not Path(audios[0]).is_file():
                self._note(f"音频文件已不存在：{Path(audios[0]).name}")
                return
            self._note("已改用系统播放器播放")
            open_local_file(audios[0])
            return
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    def _on_state(self, state) -> None:
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.play_button.setText("⏸")
        elif state == QMediaPlayer.PlaybackState.StoppedState:
            self.play_button.setText("▶")

    def _on_duration(self, duration_ms: int) -> None:
        player = self._player
        if player is None:
            return
        if not self.seek.isSliderDown():
            self.seek.setRange(0, max(0, int(duration_ms)))
        self._set_time(player.position(), duration_ms)
        if int(duration_ms) <= 0:
            self._note("读不到时长，音频文件可能不完整")

    def _on_position(self, position_ms: int) -> None:
        player = self._player
        if player is None:
            return
        if not self.seek.isSliderDown():
            self.seek.setValue(int(position_ms))
        self._set_time(position_ms, player.duration())

    def _set_time(self, position_ms: int, duration_ms: int) -> None:
        def fmt(ms: float) -> str:
            total = max(0, int(round(float(ms) / 1000)))
            return f"{total // 60}:{total % 60:02d}"

        self.time_label.setText(f"{fmt(position_ms)} / {fmt(duration_ms)}")

    def _seek_released(self) -> None:
        if self._player is not None:
            self._player.setPosition(int(self.seek.value()))

    def _on_error(self, _error, message: str = "") -> None:
        # 不静默失效：先写清楚为什么放不出来，再保留系统播放器这条退路。
        detail = str(message or "").strip()
        self._note(
            "内嵌播放放不出来"
            + (f"（{detail}）" if detail else "（多半是缺解码器）")
            + "，点 ▶ 改用系统播放器"
        )
        self.time_label.setText("0:00")
        self._player = None  # 退路：点 ▶ 走系统播放器
        self.play_button.setEnabled(True)

    def stop(self) -> None:
        if self._player is not None:
            try:
                self._player.stop()
            except Exception:
                pass


def make_tile(
    rec: TaskRecord,
    batch: dict,
    tile_w: int,
    tokens: dict[str, str],
    retried_ids: set[str],
    queue_position: int | None,
    full_width: int | None = None,
) -> TileBase:
    """Factory mirroring the web ``tileNode`` dispatch."""
    status = rec.status.value
    if status == "completed":
        if rec.request.task_type == "music":
            return MusicTile(rec, full_width or tile_w, tokens)
        if rec.request.task_type == "video":
            return VideoTile(rec, full_width or tile_w, tokens)
        return ImageTile(rec, full_width or tile_w, tokens)
    if status in {"failed", "cancelled"}:
        tile = ErrorTile(rec, full_width or tile_w, tokens, rec.task_id in retried_ids)
        tile.setFixedHeight(120)
        return tile
    params = rec.request.parameters or {}
    if rec.request.task_type == "music":
        height = max(90, round((full_width or tile_w) * 0.38))
        return SkeletonTile(rec, params, full_width or tile_w, height, tokens, queue_position)
    width = int(params.get("width") or batch.get("params", {}).get("width") or 1024)
    height_px = int(params.get("height") or batch.get("params", {}).get("height") or 1024)
    skeleton_h = max(120, round((tile_w - 2) * height_px / max(1, width)))
    return SkeletonTile(rec, params, tile_w, skeleton_h, tokens, queue_position)
