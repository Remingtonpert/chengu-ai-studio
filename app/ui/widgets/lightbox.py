"""Large-image viewer (lightbox), the desktop counterpart of the web
``#lightbox``: ‹ › navigation, meta line, V / U / copy-prompt actions.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.tasks.models import TaskRecord
from app.ui.widgets.common import open_local_file


class LightboxDialog(QDialog):
    variationRequested = Signal(str)
    upscaleRequested = Signal(str)

    def __init__(
        self,
        parent: QWidget | None,
        items: list[tuple[str, TaskRecord, str]],
        index: int = 0,
    ) -> None:
        super().__init__(parent)
        self._items = items
        self._index = max(0, min(index, len(items) - 1)) if items else 0
        self._pixmap: QPixmap | None = None
        self.setWindowTitle("生成结果 · 大图")
        self.setObjectName("lightbox")
        self.resize(1080, 780)
        self.setMinimumSize(640, 480)

        self._image = QLabel(self)
        self._image.setObjectName("lbImage")
        self._image.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._prev = QPushButton("‹", self)
        self._prev.setObjectName("lbNav")
        self._prev.setCursor(Qt.CursorShape.PointingHandCursor)
        self._prev.clicked.connect(self._previous)
        self._next = QPushButton("›", self)
        self._next.setObjectName("lbNav")
        self._next.setCursor(Qt.CursorShape.PointingHandCursor)
        self._next.clicked.connect(self._next_item)

        self._meta = QLabel(self)
        self._meta.setObjectName("lbMeta")
        self._meta.setWordWrap(True)
        self._meta.setTextFormat(Qt.TextFormat.PlainText)
        self._meta.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )

        actions = QHBoxLayout()
        actions.setSpacing(8)
        for label, signal in (
            ("V 变化", self.variationRequested),
            ("U 放大", self.upscaleRequested),
        ):
            button = QPushButton(label, self)
            button.setObjectName("btnMini")
            button.clicked.connect(
                lambda _checked=False, sig=signal: sig.emit(self._current_id())
            )
            actions.addWidget(button)
        copy_button = QPushButton("复制提示词", self)
        copy_button.setObjectName("btnMini")
        copy_button.clicked.connect(self._copy_prompt)
        actions.addWidget(copy_button)
        folder_button = QPushButton("打开所在文件夹", self)
        folder_button.setObjectName("btnMini")
        folder_button.clicked.connect(self._open_folder)
        actions.addWidget(folder_button)
        actions.addStretch(1)
        self._counter = QLabel(self)
        self._counter.setObjectName("metaDim")
        actions.addWidget(self._counter)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(10)
        layout.addWidget(self._meta)
        layout.addWidget(self._image, 1)
        layout.addLayout(actions)
        self.show_item()

    # ------------------------------------------------------------------ 导航
    def _current_id(self) -> str:
        return self._items[self._index][0] if self._items else ""

    def _record(self) -> TaskRecord | None:
        item = self._items[self._index] if self._items else None
        return item[1] if item else None

    def _path(self) -> str:
        item = self._items[self._index] if self._items else None
        return item[2] if item else ""

    def _previous(self) -> None:
        if len(self._items) > 1:
            self._index = (self._index - 1) % len(self._items)
            self.show_item()

    def _next_item(self) -> None:
        if len(self._items) > 1:
            self._index = (self._index + 1) % len(self._items)
            self.show_item()

    def show_item(self) -> None:
        rec = self._record()
        path = self._path()
        self._pixmap = QPixmap(path) if path else QPixmap()
        if self._pixmap.isNull():
            self._image.setText("无法读取图片文件：" + path)
        else:
            self._image.setPixmap(self._pixmap)
        self._refit()
        if rec is not None:
            params = rec.request.parameters or {}
            size = f"{params.get('width', '?')}×{params.get('height', '?')}"
            steps = params.get("steps", "—")
            seed = params.get("seed", "—")
            mode_note = " · img2img" if params.get("init_image_path") else ""
            finished = rec.finished_at or ""
            if "T" in finished:
                finished = finished.replace("T", " ")[:19]
            self._meta.setText(
                f"{rec.request.prompt}\n"
                f"{size} · steps {steps} · seed {seed}{mode_note}"
                + (f" · {finished}" if finished else "")
            )
            self._counter.setText(
                f"{self._index + 1} / {len(self._items)}" if self._items else ""
            )

    def _copy_prompt(self) -> None:
        rec = self._record()
        if rec is not None:
            QApplication.clipboard().setText(rec.request.prompt or "")

    def _open_folder(self) -> None:
        path = self._path()
        if path:
            open_local_file(str(Path(path).parent))

    # ------------------------------------------------------------------ 布局
    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._refit()

    def _refit(self) -> None:
        if not hasattr(self, "_image") or self._image is None:
            return
        nav_y = self.height() // 2 - 22
        self._prev.move(16, max(56, nav_y))
        self._next.move(max(16, self.width() - 60), max(56, nav_y))
        self._prev.raise_()
        self._next.raise_()
        if self._pixmap is None or self._pixmap.isNull():
            return
        available_w = max(120, self._image.width() - 8)
        available_h = max(120, self._image.height() - 8)
        scaled = self._pixmap.scaled(
            available_w,
            available_h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._image.setPixmap(scaled)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
        if key is Qt.Key.Key_Left:
            self._previous()
            return
        if key is Qt.Key.Key_Right:
            self._next_item()
            return
        text = (event.text() or "").lower()
        task_id = self._current_id()
        if text == "v" and task_id:
            self.variationRequested.emit(task_id)
            return
        if text == "u" and task_id:
            self.upscaleRequested.emit(task_id)
            return
        super().keyPressEvent(event)
