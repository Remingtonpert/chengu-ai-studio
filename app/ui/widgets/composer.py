"""Bottom composer bar: reference-image zone/chip, prompt box (Enter to
submit, Shift+Enter for newline, drag/paste images), imagine button and
the live parameter summary line -- mirrors ``footer.composer`` in the web
console.
"""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}

PLACEHOLDERS = {
    # 与网页端 app.js:PLACEHOLDERS 逐字一致（改一处必须同步另一处与 i18n 词典）。
    "t2i": "描述你想要的画面…（Sana-1.6B 多语言版 · 中文英文混着写都行 · 1024 级直出，可到 1080p）",
    "i2i": "先放参考图，再描述想改成什么样…（Wan2.1-VACE-1.3B 按垫图参考重绘）",
    "t2v": "描述动态画面…（Wan2.1-T2V-1.3B · 16fps mp4 · 最高约 480p）",
    "i2v": "先放首帧图片，再描述画面如何运动…（Wan2.1-I2V-1.3B · 16fps mp4）",
    "music": "描述音乐：风格、情绪、乐器、节奏…（ACE-Step 1.5 · 10-480 秒 · 可带中文/英文人声）",
}
REF_NOTE = {
    "video": "该文件将作为 init_image_path 首帧，视频从它开始运动",
    "image": "垫图只作参考重绘的参照，改动幅度由提示词决定（图生图引擎没有「重绘强度」这个参数）",
}
REF_ZONE_NOTE = "也可以把本机图片拖到这里 / Ctrl+V 粘贴 · 路径自动预填"
PATH_PLACEHOLDER = "参考图的本机完整路径（绝对路径、具体图片文件），如 D:\\ChenguAI\\uploads\\photo.png"


class PromptBox(QPlainTextEdit):
    enter_pressed = Signal()
    image_pasted = Signal(str)  # saved temp file path

    def __init__(self, refs_dir: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("prompt")
        self.refs_dir = refs_dir
        self.setAcceptDrops(True)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter}:
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self.insertPlainText("\n")
                return
            self.enter_pressed.emit()
            return
        if event.matches(QKeySequence.StandardKey.Paste):
            handled = self._try_paste_image()
            if handled:
                return
        super().keyPressEvent(event)

    def _try_paste_image(self) -> bool:
        from PySide6.QtGui import QGuiApplication

        clipboard = QGuiApplication.clipboard()
        image = clipboard.image()
        if image.isNull():
            return False
        self.refs_dir.mkdir(parents=True, exist_ok=True)
        target = self.refs_dir / (
            "pasted_" + time.strftime("%Y%m%d_%H%M%S") + ".png"
        )
        image.save(str(target), "PNG")
        self.image_pasted.emit(str(target))
        return True

    # ------------------------------------------------------------------ 拖拽
    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if self._image_paths(event):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        if self._image_paths(event):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802
        paths = self._image_paths(event)
        if paths:
            event.acceptProposedAction()
            self.image_pasted.emit(paths[0])
            return
        super().dropEvent(event)

    @staticmethod
    def _image_paths(event) -> list[str]:
        mime = event.mimeData()
        if not mime.hasUrls():
            return []
        result = []
        for url in mime.urls():
            if url.isLocalFile():
                local = url.toLocalFile()
                if Path(local).suffix.lower() in IMAGE_EXTENSIONS:
                    result.append(local)
        return result


class Composer(QFrame):
    submitRequested = Signal()
    refChosen = Signal(str)        # an image file picked/dropped/pasted
    refPathEdited = Signal(str)    # path input edited (absolute path expected)
    refCleared = Signal()
    promptChanged = Signal(str)

    def __init__(self, refs_dir: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("composerBar")
        self.setAcceptDrops(True)
        self.refs_dir = refs_dir
        self._modality = "image"
        self._ref_path = ""
        self._last_dir = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 10, 16, 12)
        layout.setSpacing(8)

        # ---------------------------------------------------------------- 垫图区
        self.ref_zone = QFrame(self)
        self.ref_zone.setObjectName("refZone")
        zone_row = QHBoxLayout(self.ref_zone)
        zone_row.setContentsMargins(10, 8, 10, 8)
        zone_row.setSpacing(10)
        pick = QPushButton("🖼 选择垫图", self.ref_zone)
        pick.setObjectName("btnFile")
        pick.clicked.connect(self._pick_file)
        note = QLabel(REF_ZONE_NOTE, self.ref_zone)
        note.setObjectName("mutedMsg")
        zone_row.addWidget(pick)
        zone_row.addWidget(note)
        zone_row.addStretch(1)
        layout.addWidget(self.ref_zone)

        self.ref_chip = QFrame(self)
        self.ref_chip.setObjectName("refChip")
        chip_row = QHBoxLayout(self.ref_chip)
        chip_row.setContentsMargins(10, 8, 10, 8)
        chip_row.setSpacing(10)
        self.ref_thumb = QLabel(self.ref_chip)
        self.ref_thumb.setObjectName("sideThumb")
        self.ref_thumb.setFixedSize(44, 44)
        self.ref_name = QLabel("", self.ref_chip)
        self.ref_name.setObjectName("mutedMsg")
        self.ref_path_edit = QLineEdit(self.ref_chip)
        self.ref_path_edit.setPlaceholderText(PATH_PLACEHOLDER)
        self.ref_path_edit.textEdited.connect(self._on_path_edited)
        self.ref_note = QLabel(REF_NOTE["image"], self.ref_chip)
        self.ref_note.setObjectName("mutedMsg")
        close = QPushButton("✕", self.ref_chip)
        close.setObjectName("btnMini")
        close.clicked.connect(self._clear_ref)
        chip_row.addWidget(self.ref_thumb)
        chip_row.addWidget(self.ref_name)
        chip_row.addWidget(self.ref_path_edit, 1)
        chip_row.addWidget(self.ref_note)
        chip_row.addWidget(close)
        self.ref_chip.setVisible(False)
        layout.addWidget(self.ref_chip)

        # ---------------------------------------------------------------- 输入行
        row = QHBoxLayout()
        row.setSpacing(10)
        self.prompt = PromptBox(refs_dir, self)
        self.prompt.enter_pressed.connect(self.submitRequested)
        self.prompt.image_pasted.connect(self.refChosen)
        self.prompt.textChanged.connect(
            lambda: self.promptChanged.emit(self.prompt.toPlainText())
        )
        self.imagine = QPushButton("想象 Enter", self)
        self.imagine.setObjectName("btnImagine")
        self.imagine.clicked.connect(self.submitRequested)
        row.addWidget(self.prompt, 1)
        row.addWidget(self.imagine)
        layout.addLayout(row)

        self.meta = QLabel("", self)
        self.meta.setObjectName("composerMeta")
        self.meta.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        layout.addWidget(self.meta)

    # ------------------------------------------------------------------ 拖拽到整条 composer
    def dragEnterEvent(self, event) -> None:  # noqa: N802
        paths = PromptBox._image_paths(event)
        if paths and self._modality in {"image", "video"}:
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802
        paths = PromptBox._image_paths(event)
        if paths:
            event.acceptProposedAction()
            self.refChosen.emit(paths[0])
        else:
            super().dropEvent(event)

    # ------------------------------------------------------------------ 对外 API
    def set_mode(self, mode: str, modality: str) -> None:
        self._modality = modality
        self.prompt.setPlaceholderText(PLACEHOLDERS.get(mode, PLACEHOLDERS["t2i"]))
        self.ref_zone.setVisible(modality in {"image", "video"} and mode in {"i2i", "i2v"})
        self.ref_note.setText(REF_NOTE["video" if modality == "video" else "image"])

    def prompt_text(self) -> str:
        return self.prompt.toPlainText()

    def set_prompt(self, text: str) -> None:
        self.prompt.setPlainText(text)

    def clear_prompt(self) -> None:
        self.prompt.clear()

    def set_meta(self, text: str) -> None:
        self.meta.setText(text)

    def focus_prompt(self) -> None:
        self.prompt.setFocus(Qt.FocusReason.OtherFocusReason)

    def ref_path(self) -> str:
        return self._ref_path

    def has_ref(self) -> bool:
        # 与网页端一致：只要挂过垫图（chip 显示）就算有 ref，
        # 路径为空时走「待确认路径」而不是「未挂」。
        # 用 isHidden 而非 isVisible：后者受整个窗口可见性影响，
        # 窗口未显示（含离屏测试）时会把已挂载的垫图误判为未挂。
        return not self.ref_chip.isHidden()

    def clear_ref(self) -> None:
        self._clear_ref()

    def set_busy(self, busy: bool) -> None:
        self.imagine.setEnabled(not busy)

    def set_ref(self, path: str) -> None:
        self._ref_path = str(path)
        self._last_dir = str(Path(path).parent)
        pixmap = QPixmap(path)
        if pixmap.isNull():
            self.ref_thumb.setText("🖼")
            self.ref_thumb.setPixmap(QPixmap())
        else:
            self.ref_thumb.setPixmap(
                pixmap.scaled(
                    44,
                    44,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        self.ref_name.setText(Path(path).name)
        self.ref_path_edit.blockSignals(True)
        self.ref_path_edit.setText(self._ref_path)
        self.ref_path_edit.blockSignals(False)
        self.ref_chip.setVisible(True)
        self.ref_note.setText(REF_NOTE["video" if self._modality == "video" else "image"])

    # ------------------------------------------------------------------ 内部
    def _pick_file(self) -> None:
        path, _selected = QFileDialog.getOpenFileName(
            self,
            "选择垫图",
            self._last_dir or str(self.refs_dir),
            "图片 (*.png *.jpg *.jpeg *.webp *.bmp)",
        )
        if path:
            self.refChosen.emit(path)

    def _on_path_edited(self, text: str) -> None:
        self._ref_path = text.strip()
        self.refPathEdited.emit(self._ref_path)

    def _clear_ref(self) -> None:
        self._ref_path = ""
        self.ref_chip.setVisible(False)
        self.ref_path_edit.clear()
        self.refCleared.emit()
