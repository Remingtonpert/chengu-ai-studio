"""Light/dark theme: palette tokens plus a stylesheet built from them.

The active mode follows the Windows system setting (AppsUseLightTheme).
The environment variable ``CHENGUAI_THEME`` (``light`` / ``dark`` / ``auto``)
overrides it, which is handy for testing both palettes without touching the
user's system settings.
"""

from __future__ import annotations

import os

THEME_ENV_VAR = "CHENGUAI_THEME"

LIGHT_PALETTE: dict[str, str] = {
    "window_bg": "#f4f6f4",
    "window_text": "#1c2722",
    "sidebar_bg": "#183d32",
    "brand_text": "#f5f4e9",
    "nav_text": "#deebe4",
    "nav_selected_bg": "#2c5b4b",
    "nav_selected_text": "white",
    "heading": "#183d32",
    "warning_text": "#8a4c17",
    "warning_bg": "#fff2d8",
    "warning_border": "#d48b37",
    "info_text": "#1f5c40",
    "info_bg": "#e2f1e8",
    "info_border": "#38915f",
    "input_bg": "white",
    "input_text": "#1c2722",
    "input_border": "#cbd5ce",
    "button_bg": "#267253",
    "button_text": "white",
    "button_disabled_bg": "#aeb9b2",
    "button_disabled_text": "#f7f8f7",
}

DARK_PALETTE: dict[str, str] = {
    "window_bg": "#101614",
    "window_text": "#e8ede9",
    "sidebar_bg": "#122a23",
    "brand_text": "#f2f1e4",
    "nav_text": "#c8d8cf",
    "nav_selected_bg": "#2c5b4b",
    "nav_selected_text": "white",
    "heading": "#dfe9e2",
    "warning_text": "#e5c98d",
    "warning_bg": "#3a2f1c",
    "warning_border": "#b07c33",
    "info_text": "#9fd6b4",
    "info_bg": "#1e3b2c",
    "info_border": "#3fa078",
    "input_bg": "#1d2421",
    "input_text": "#e8ede9",
    "input_border": "#33413a",
    "button_bg": "#349468",
    "button_text": "#f4f8f5",
    "button_disabled_bg": "#39463f",
    "button_disabled_text": "#9fb0a4",
}


def system_prefers_dark() -> bool:
    """Whether the UI should use the dark palette right now."""
    override = os.environ.get(THEME_ENV_VAR, "auto").strip().lower()
    if override == "dark":
        return True
    if override == "light":
        return False
    try:
        import winreg

        key_path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return int(value) == 0
    except (OSError, ValueError):
        return False


def build_stylesheet(dark: bool) -> str:
    """Stylesheet for the whole main window, built from palette tokens."""
    p = DARK_PALETTE if dark else LIGHT_PALETTE
    return (
        f"QMainWindow {{ background: {p['window_bg']}; color: {p['window_text']}; }}"
        f"QWidget {{ color: {p['window_text']}; }}"
        f"QFrame#sidebar {{ background: {p['sidebar_bg']}; }}"
        f"QLabel#brand {{ color: {p['brand_text']}; font-size: 20px; font-weight: 700; }}"
        f"QLabel#phaseInfo {{ color: {p['nav_text']}; }}"
        f"QListWidget#sidebarNav {{ border: 0; background: transparent; color: {p['nav_text']}; }}"
        "QListWidget#sidebarNav::item { padding: 11px 14px; border-radius: 4px; }"
        f"QListWidget#sidebarNav::item:selected {{ background: {p['nav_selected_bg']};"
        f" color: {p['nav_selected_text']}; }}"
        f"QListWidget {{ border: 1px solid {p['input_border']}; border-radius: 4px;"
        f" background: {p['input_bg']}; color: {p['input_text']}; padding: 4px; }}"
        "QListWidget::item { padding: 6px 10px; border-radius: 3px; }"
        f"QListWidget::item:selected {{ background: {p['nav_selected_bg']}; color: white; }}"
        f"QLabel#pageHeading {{ font-size: 25px; font-weight: 650; color: {p['heading']}; }}"
        f"QLabel#statusWarning {{ color: {p['warning_text']}; padding: 10px;"
        f" background: {p['warning_bg']}; border-left: 3px solid {p['warning_border']}; }}"
        f"QLabel#statusInfo {{ color: {p['info_text']}; padding: 10px;"
        f" background: {p['info_bg']}; border-left: 3px solid {p['info_border']}; }}"
        f"QPlainTextEdit, QLineEdit {{ background: {p['input_bg']}; color: {p['input_text']};"
        f" border: 1px solid {p['input_border']}; border-radius: 4px; padding: 9px; }}"
        f"QPushButton {{ background: {p['button_bg']}; color: {p['button_text']}; border: 0;"
        " border-radius: 4px; padding: 9px 14px; }"
        f"QPushButton:disabled {{ background: {p['button_disabled_bg']};"
        f" color: {p['button_disabled_text']}; }}"
    )
