"""工具内播放窗：视频和音频都在本工具窗口里放，不再甩给系统播放器。

对应网页端灯箱里的 ``<video controls>``：桌面端以前点视频卡片是
``os.startfile``（弹出 Windows 媒体播放器），音乐卡片里虽然有播放条，
但播放失败只是静默停着。这里把两件事做实：窗口内播放 + 放不出来时说明原因。

没有 QtMultimediaWidgets（极端安装）时不硬撑：给一句说明，留一个交给系统
播放器的按钮，用户不会被卡住。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from app.ui.widgets.common import open_local_file, reveal_in_explorer

try:  # QtMultimedia 随 PySide6 一起装；QVideoWidget 在 Addons 里，照样守住。
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
    from PySide6.QtMultimediaWidgets import QVideoWidget

    HAS_MEDIA = True
except ImportError:  # pragma: no cover - 缺组件时的回退路径
    QAudioOutput = QMediaPlayer = QVideoWidget = None
    HAS_MEDIA = False

VIDEO_SUFFIXES = {".mp4", ".webm", ".mkv", ".mov"}
AUDIO_SUFFIXES = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".aac"}

PLAY_ERROR_TEXT = "播放失败：本机放不出来这个文件（多半是缺少对应的解码器）。"
NO_MEDIA_TEXT = "本机 PySide6 缺少播放组件，已准备好交给系统播放器。"


def _fmt_ms(milliseconds: int) -> str:
    total = max(0, int(round(int(milliseconds) / 1000)))
    seconds = total % 60
    minutes = total // 60 % 60
    hours = total // 3600
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


class MediaPlayerDialog(QDialog):
    """一个媒体一个窗：视频带画面，音频只有播放条（与网页端一致）。"""

    def __init__(self, parent: QWidget | None, path: str | Path, title: str = "") -> None:
        super().__init__(parent)
        self.source = Path(str(path))
        self.is_video = self.source.suffix.lower() in VIDEO_SUFFIXES
        self.setObjectName("mediaPlayer")
        label = (title or "").strip() or self.source.stem
        self.setWindowTitle(f"工具内播放 · {label}")
        self.setModal(False)  # 不挡界面：一边放一边还能改提示词、提交下一条
        self.setMinimumWidth(560)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        self._player: QMediaPlayer | None = None
        self._audio_output: QAudioOutput | None = None
        self._video_widget: QVideoWidget | None = None
        self._fallback_button: QPushButton | None = None

        if self.is_video and HAS_MEDIA:
            self._video_widget = QVideoWidget(self)
            self._video_widget.setObjectName("mediaVideo")
            self._video_widget.setMinimumSize(420, 236)
            layout.addWidget(self._video_widget, 1)
        elif not self.is_video and HAS_MEDIA:
            banner = QFrame(self)
            banner.setObjectName("mediaCard")
            banner_layout = QHBoxLayout(banner)
            banner_layout.setContentsMargins(16, 18, 16, 18)
            banner_layout.setSpacing(12)
            icon = QLabel("🎵", banner)
            icon.setObjectName("mediaIcon")
            name = QLabel(label, banner)
            name.setObjectName("mediaName")
            name.setTextFormat(Qt.TextFormat.PlainText)
            name.setWordWrap(True)
            banner_layout.addWidget(icon)
            banner_layout.addWidget(name, 1)
            layout.addWidget(banner, 1)
        else:
            hint = QLabel(
                NO_MEDIA_TEXT if self.is_video else "本机 PySide6 缺少播放组件。",
                self,
            )
            hint.setObjectName("mutedMsg")
            hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
            hint.setMinimumHeight(160)
            hint.setWordWrap(True)
            layout.addWidget(hint, 1)
            self._fallback_button = QPushButton("▶ 用系统播放器打开", self)
            self._fallback_button.setObjectName("btnMini")
            self._fallback_button.clicked.connect(
                lambda: open_local_file(self.source)
            )
            layout.addWidget(self._fallback_button)

        self.status = QLabel("", self)
        self.status.setObjectName("mutedMsg")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.status.setVisible(False)
        layout.addWidget(self.status)

        controls = QHBoxLayout()
        controls.setSpacing(8)
        self.play_button = QPushButton("▶", self)
        self.play_button.setObjectName("playBtn")
        self.play_button.setEnabled(HAS_MEDIA)
        self.play_button.clicked.connect(self._toggle_play)
        controls.addWidget(self.play_button)

        self.seek = QSlider(Qt.Orientation.Horizontal, self)
        self.seek.setRange(0, 0)
        self.seek.setEnabled(HAS_MEDIA)
        self.seek.sliderReleased.connect(self._seek_released)
        self.seek.sliderMoved.connect(
            lambda value: self.time_label.setText(
                f"{_fmt_ms(value)} / {_fmt_ms(self.seek.maximum())}"
            )
        )
        controls.addWidget(self.seek, 1)

        self.time_label = QLabel("0:00 / 0:00", self)
        self.time_label.setObjectName("playTime")
        controls.addWidget(self.time_label)

        volume = QSlider(Qt.Orientation.Horizontal, self)
        volume.setObjectName("volumeSlider")
        volume.setRange(0, 100)
        volume.setValue(80)
        volume.setFixedWidth(96)
        volume.setEnabled(HAS_MEDIA)
        volume.setToolTip("音量")
        controls.addWidget(volume)

        folder = QPushButton("📁", self)
        folder.setObjectName("btnMini")
        folder.setToolTip("在文件夹里显示这份产物")
        folder.clicked.connect(lambda: reveal_in_explorer(self.source))
        controls.addWidget(folder)

        save = QPushButton("⬇ 另存", self)
        save.setObjectName("btnMini")
        save.setToolTip("把这份产物复制到你选的目录")
        save.clicked.connect(self._save_as)
        controls.addWidget(save)

        close = QPushButton("关闭", self)
        close.setObjectName("btnMini")
        close.clicked.connect(self.close)
        controls.addWidget(close)
        layout.addLayout(controls)

        if HAS_MEDIA:
            self._audio_output = QAudioOutput(self)
            self._audio_output.setVolume(0.8)
            volume.valueChanged.connect(
                lambda value: self._audio_output.setVolume(value / 100.0)
            )
            self._player = QMediaPlayer(self)
            self._player.setAudioOutput(self._audio_output)
            if self._video_widget is not None:
                self._player.setVideoOutput(self._video_widget)
            self._player.positionChanged.connect(self._on_position)
            self._player.durationChanged.connect(self._on_duration)
            self._player.playbackStateChanged.connect(self._on_state)
            self._player.errorOccurred.connect(self._on_error)
            self._player.mediaStatusChanged.connect(self._on_media_status)
            self._audio_output.setVolume(volume.value() / 100.0)
            volume.valueChanged.connect(
                lambda value: self._audio_output.setVolume(value / 100.0)
            )
            if not self.source.is_file():
                self._show_status(f"产物文件不存在：{self.source}")
            else:
                self._player.setSource(QUrl.fromLocalFile(str(self.source.resolve())))

    # ------------------------------------------------------------------ 播放控制
    def _toggle_play(self) -> None:
        if self._player is None:
            if self.source.is_file():
                open_local_file(self.source)
            return
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    def _seek_released(self) -> None:
        if self._player is not None:
            self._player.setPosition(self.seek.value())

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() is Qt.Key.Key.Key_Space:
            self._toggle_play()
            return
        super().keyPressEvent(event)

    # ------------------------------------------------------------------ 播放器回调
    def _on_position(self, position_ms: int) -> None:
        if not self.seek.isSliderDown():
            self.seek.setValue(int(position_ms))
            self.time_label.setText(
                f"{_fmt_ms(position_ms)} / {_fmt_ms(self.seek.maximum())}"
            )

    def _on_duration(self, duration_ms: int) -> None:
        self.seek.setRange(0, max(0, int(duration_ms)))
        if int(duration_ms) <= 0:
            self._show_status("这段媒体读不到时长，可能是文件不完整。")

    def _on_state(self, state) -> None:
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.play_button.setText("⏸")
            self.status.setVisible(False)
        elif state == QMediaPlayer.PlaybackState.StoppedState:
            self.play_button.setText("▶")

    def _on_media_status(self, status) -> None:
        if status == QMediaPlayer.MediaStatus.InvalidMedia:
            self._show_status(PLAY_ERROR_TEXT)
        elif status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.play_button.setText("▶")

    def _on_error(self, error, error_string: str = "") -> None:
        if not error or int(error) == 0:
            return
        detail = str(error_string or "").strip()
        self._show_status(PLAY_ERROR_TEXT + (f"（{detail}）" if detail else ""))
        self.play_button.setEnabled(False)

    def _show_status(self, text: str) -> None:
        self.status.setText(text)
        self.status.setVisible(bool(text))

    # ------------------------------------------------------------------ 另存 / 关闭
    def _save_as(self) -> None:
        if not self.source.is_file():
            self._show_status(f"产物文件不存在：{self.source}")
            return
        selected, _filter = QFileDialog.getSaveFileName(
            self,
            "另存产物（可选任意盘符、移动硬盘或网盘目录）",
            str(self.source),
            f"生成产物 (*{self.source.suffix});;所有文件 (*)",
        )
        if not selected:
            return
        target = Path(selected)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.source, target)
        except OSError as exc:
            self._show_status(f"另存失败：{exc}")
            return
        self._show_status(f"已另存到 {target}")

    def closeEvent(self, event) -> None:  # noqa: N802
        # 解码器留给窗关掉为止：视频解码要吃显存/解码通道，留着会一直占着。
        if self._player is not None:
            self._player.stop()
            self._player.setSource(QUrl())
        super().closeEvent(event)
