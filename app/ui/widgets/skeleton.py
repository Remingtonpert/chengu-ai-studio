"""Shimmering loading skeleton, mirroring ``.skel::before`` in style.css."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, QRectF
from PySide6.QtGui import QColor, QLinearGradient, QPainter
from PySide6.QtWidgets import QWidget


class SkeletonBlock(QWidget):
    """Animated gradient sweep (bg-card -> bg-hover -> bg-card, 1.6s loop)."""

    def __init__(self, tokens: dict[str, str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._base = QColor(tokens["bg_card"])
        self._peak = QColor(tokens["bg_hover"])
        self._phase = 0.0
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._timer = QTimer(self)
        self._timer.setInterval(60)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def _tick(self) -> None:
        self._phase = (self._phase + 0.0375) % 1.0  # full sweep in 1.6 s
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        width = self.width()
        # Sweep window travels from +120% to -80% of the widget width.
        travel = width * 2.0
        start = width * 1.2 - self._phase * travel
        gradient = QLinearGradient(start, 0, start + width * 0.8, 0)
        gradient.setColorAt(0.0, self._base)
        gradient.setColorAt(0.5, self._peak)
        gradient.setColorAt(1.0, self._base)
        painter.fillRect(QRectF(self.rect()), gradient)

    def hideEvent(self, event) -> None:  # stop repaints for hidden skeletons
        self._timer.stop()
        super().hideEvent(event)

    def showEvent(self, event) -> None:
        if not self._timer.isActive():
            self._timer.start()
        super().showEvent(event)
