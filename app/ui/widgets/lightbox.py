"""Large-image viewer (lightbox), the desktop counterpart of the web
``#lightbox``: ‹ › navigation, meta line, V / U / copy-prompt actions.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
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
        # 关掉就销毁：每次打开都新建一个实例，不保留就会一直挂在父窗口下，
        # 连带全尺寸 QPixmap 一起常驻内存。
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
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
        save_button = QPushButton("另存为…", self)
        save_button.setObjectName("btnMini")
        save_button.setToolTip(
            "复制一份到你选的位置（任意盘符、移动硬盘或网盘目录）；"
            "项目内 output 里那份保留不动，工具里显示的就是它"
        )
        save_button.clicked.connect(self._save_as)
        actions.addWidget(save_button)
        self._save_hint = QLabel(self)
        self._save_hint.setObjectName("metaDim")
        self._save_hint.setTextFormat(Qt.TextFormat.PlainText)
        actions.addWidget(self._save_hint)
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
        self._save_hint.setText("")  # 换张图就把上一次的「已另存」提示清掉
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

    def _save_as(self) -> None:
        r"""另存当前这张产物：复制一份到用户选的位置，原文件留在输出目录里。

        网页端大图查看器的「另存」走浏览器保存框（showSaveFilePicker），这里是 Qt 版；
        想每单自动多存一份，去「设置」页填「自动另存目录」（两端共用 config.json）。
        """
        raw = self._path()
        source = Path(raw) if raw else None
        if source is None or not source.is_file():
            self._save_hint.setText("产物文件不在输出目录，无法另存")
            return
        selected, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "另存产物（可选任意盘符、移动硬盘或网盘目录）",
            str(source),
            f"生成产物 (*{source.suffix});;所有文件 (*)",
        )
        if not selected:
            return  # 取消：什么都不改
        target = Path(selected)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        except OSError as exc:
            self._save_hint.setText(f"另存失败：{exc}")
            return
        self._save_hint.setText(f"已另存 → {target.name}")

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
