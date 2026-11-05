"""Shared widget helpers for the MJ-style desktop creation UI."""

from __future__ import annotations

import random
import time
from pathlib import Path

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QLabel, QWidget


def fmt_time(iso_or_none: str | None) -> str:
    """``2026-11-04T17:28:35`` -> ``17:28:35`` (display only, local naive)."""
    if not iso_or_none:
        return time.strftime("%H:%M:%S")
    text = str(iso_or_none)
    if "T" in text:
        return text.split("T", 1)[1][:8]
    return text[:8]


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def rand_seed() -> int:
    return random.randint(0, 2**31 - 1)


def clamp_int(value: float, low: int, high: int) -> int:
    return max(low, min(high, int(value)))


def open_local_file(path: str | Path) -> None:
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


def reveal_in_explorer(path: str | Path) -> None:
    from PySide6.QtCore import QProcess

    QProcess.startDetached(
        "explorer.exe", ["/select,", str(Path(path).resolve())]
    )


def set_child_geometry(child: QWidget, x: int, y: int, width: int, height: int) -> None:
    child.setGeometry(x, y, max(0, width), max(0, height))


def toast_geometry(host: QWidget, toast: QLabel) -> None:
    """Bottom-centre placement mirroring ``.toast`` in style.css."""
    toast.adjustSize()
    x = (host.width() - toast.width()) // 2
    y = max(8, host.height() - toast.height() - 96)
    toast.move(max(8, x), y)


class ToastHub:
    """One reusable toast label per host widget (create once, reuse)."""

    def __init__(self, host: QWidget) -> None:
        self.host = host
        self.label = QLabel(host)
        self.label.setObjectName("toast")
        self.label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.label.setWordWrap(True)
        self.label.hide()

    def show(self, message: str, kind: str = "info") -> None:
        self.label.setObjectName(
            {"error": "toastError", "success": "toastSuccess"}.get(kind, "toast")
        )
        style = self.label.style()
        style.unpolish(self.label)
        style.polish(self.label)
        self.label.setText(message)
        self.label.setMaximumWidth(max(240, self.host.width() - 160))
        self.label.adjustSize()
        toast_geometry(self.host, self.label)
        self.label.raise_()
        self.label.show()
        from PySide6.QtCore import QTimer

        QTimer.singleShot(4500, self.label.hide)
