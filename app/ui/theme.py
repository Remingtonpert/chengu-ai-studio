"""Light/dark theme: MJ-style palette tokens plus a stylesheet built from them.

The palette values mirror the web console's ``app/web/static/style.css``
``:root`` design tokens (background/card/line/accent scale, radii, status
colours) so the desktop shell and the browser UI are visually identical.

The active mode follows the Windows system setting (AppsUseLightTheme).
The environment variable ``CHENGUAI_THEME`` (``light`` / ``dark`` / ``auto``)
overrides it, which is handy for testing both palettes without touching the
user's system settings.
"""

from __future__ import annotations

import os

THEME_ENV_VAR = "CHENGUAI_THEME"

# ---------------------------------------------------------------- 设计 token
# 深色：网页端 style.css :root 原值；浅色：同一 token 体系的浅色对应值。
DARK_TOKENS: dict[str, str] = {
    "bg": "#0a0b0d",
    "bg_soft": "#101114",
    "bg_card": "#16181c",
    "bg_hover": "#1e2126",
    "line": "#23262c",
    "line_soft": "#2c3038",
    "text": "#e8eaed",
    "text_mid": "#a7abb3",
    "text_dim": "#6b7078",
    "accent": "#d8dade",
    "accent_ink": "#101114",
    "blue": "#7d9bff",
    "red": "#f07171",
    "green": "#6fd08c",
    "radius": "12px",
    "radius_sm": "8px",
    "overlay": "rgba(12, 13, 15, 0.72)",
    "toast_shadow": "#05060777",
}

LIGHT_TOKENS: dict[str, str] = {
    "bg": "#f4f5f7",
    "bg_soft": "#fafbfc",
    "bg_card": "#ffffff",
    "bg_hover": "#eceef1",
    "line": "#dcdfe4",
    "line_soft": "#c9cdd4",
    "text": "#17181c",
    "text_mid": "#4b4f57",
    "text_dim": "#83878f",
    "accent": "#1b1c20",
    "accent_ink": "#f7f8fa",
    "blue": "#3d63d8",
    "red": "#c2403f",
    "green": "#2e7d4f",
    "radius": "12px",
    "radius_sm": "8px",
    "overlay": "rgba(24, 26, 30, 0.66)",
    "toast_shadow": "#10121a33",
}

# 兼容旧名字（外部若还引用 LIGHT_PALETTE/DARK_PALETTE 不必改动导入）。
LIGHT_PALETTE = LIGHT_TOKENS
DARK_PALETTE = DARK_TOKENS


def palette(dark: bool) -> dict[str, str]:
    """Token dict for the requested mode (used by custom-painted widgets)."""
    return DARK_TOKENS if dark else LIGHT_TOKENS


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
    """Stylesheet for the whole main window, built from MJ palette tokens."""
    p = palette(dark)
    rules = [
        # ---------------------------------------------------------------- 基底
        f"QMainWindow {{ background: {p['bg']}; color: {p['text']}; }}",
        'QWidget { font-family: "Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei",'
        f" sans-serif; font-size: 13px; color: {p['text']}; }}",
        "QToolTip { background:"
        f" {p['bg_card']}; color: {p['text_mid']}; border: 1px solid {p['line']};",
        " padding: 6px 8px; border-radius: 6px; }",
        f"QLabel {{ background: transparent; color: {p['text']}; }}",
        # ------------------------------------------------------- 三栏与顶栏
        f"QFrame#sideColumn {{ background: {p['bg_soft']}; border-right: 1px solid {p['line']}; }}",
        f"QFrame#panelColumn {{ background: {p['bg_soft']}; border-left: 1px solid {p['line']}; }}",
        f"QFrame#composerBar {{ background: {p['bg_soft']}; border-top: 1px solid {p['line']}; }}",
        f"QFrame#topBar {{ background: {p['bg']}; border-bottom: 1px solid {p['line']}; }}",
        f"QLabel#brand {{ font-size: 15px; font-weight: 700; color: {p['text']}; }}",
        f"QLabel#brandSub {{ font-size: 10px; color: {p['text_dim']}; }}",
        f"QLabel#topTitle {{ font-size: 13px; color: {p['text_mid']}; }}",
        f"QFrame#sysTopBar {{ background: {p['bg_soft']}; border-bottom: 1px solid {p['line']}; }}",
        f"QLabel#sysTitle {{ font-size: 15px; font-weight: 650; color: {p['text']}; }}",
        f"QLabel#sysDesc {{ font-size: 11px; color: {p['text_dim']}; }}",
        # ------------------------------------------------------------- 会话
        f"QPushButton#btnNew {{ background: {p['accent']}; color: {p['accent_ink']}; border: 0;",
        f" border-radius: {p['radius_sm']}; padding: 9px 12px; font-weight: 600; }}",
        f"QPushButton#btnNew:hover {{ background: {p['line_soft']}; }}",
        "QScrollArea#sessionScroll, QScrollArea#navScroll { border: 0; background: transparent; }",
        "QWidget#sessionRowsHost, QWidget#navHost { background: transparent; }",
        f"QLabel#sectionLabel {{ font-size: 11px; color: {p['text_dim']}; padding: 6px 4px 2px; }}",
        "QWidget#sideRow { border-radius: 8px; }",
        f"QWidget#sideRow:hover {{ background: {p['bg_hover']}; }}",
        f"QWidget#sideRow[active=\"true\"] {{ background: {p['bg_hover']}; }}",
        f"QLabel#sideTitle {{ font-size: 13px; color: {p['text_mid']}; background: transparent; }}",
        "QLabel#sideThumb { border-radius: 6px; background:"
        f" {p['bg_hover']}; border: 1px solid {p['line']}; }}",
        f"QPushButton#sideDel {{ border: 0; background: transparent; color: {p['text_dim']}; font-size: 14px;",
        " padding: 4px 6px; border-radius: 6px; }",
        f"QPushButton#sideDel:hover {{ color: {p['red']}; background: {p['bg_hover']}; }}",
        f"QPushButton#navEntry {{ text-align: left; border: 0; background: transparent; color: {p['text_mid']};",
        f" border-radius: 8px; padding: 8px 10px; }}",
        f"QPushButton#navEntry:hover {{ background: {p['bg_hover']}; color: {p['text']}; }}",
        # --------------------------------------------------------- pill 徽章
        f"QLabel#pill {{ font-size: 11px; color: {p['text_mid']}; border: 1px solid {p['line']};",
        f" border-radius: 999px; padding: 4px 10px; background: {p['bg_card']}; }}",
        f"QLabel#pillOk {{ font-size: 11px; color: {p['green']}; border: 1px solid {p['line']};",
        f" border-radius: 999px; padding: 4px 10px; background: {p['bg_card']}; }}",
        f"QLabel#pillBad {{ font-size: 11px; color: {p['red']}; border: 1px solid {p['line']};",
        f" border-radius: 999px; padding: 4px 10px; background: {p['bg_card']}; }}",
        # ---------------------------------------------------------- 模式 Tab
        f"QFrame#tabBar {{ background: {p['bg']}; border-bottom: 1px solid {p['line']}; }}",
        f"QPushButton#tab {{ background: transparent; border: 1px solid transparent; color: {p['text_mid']};",
        f" padding: 8px 15px 9px; font-size: 13px; border-radius: {p['radius_sm']} {p['radius_sm']} 0 0; }}",
        f"QPushButton#tab:hover {{ color: {p['text']}; background: {p['bg_hover']}; }}",
        f"QPushButton#tab:checked {{ color: {p['text']}; background: {p['bg_card']}; border: 1px solid {p['line']};",
        f" border-bottom: 2px solid {p['accent']}; }}",
        "QPushButton#tab:disabled { color:"
        f" {p['text_dim']}; }}",
        f"QLabel#tabBadge {{ background: {p['blue']}; color: {p['accent_ink']}; font-size: 10px;",
        f" font-weight: 700; border-radius: 8px; padding: 1px 6px; }}",
        f"QWidget#creationPage {{ background: {p['bg']}; }}",
        # ------------------------------------------------------------ 会话流
        f"QScrollArea#feedArea {{ border: 0; background: {p['bg']}; }}",
        f"QWidget#feedHost {{ background: {p['bg']}; }}",
        f"QLabel#feedLogo {{ font-size: 34px; font-weight: 700; color: {p['line_soft']}; }}",
        f"QLabel#feedHint {{ color: {p['text_mid']}; font-size: 13px; }}",
        "QWidget#batchBlock { background: transparent; }",
        f"QFrame#userBubble {{ background: {p['bg_card']}; border: 1px solid {p['line']};",
        f" border-radius: {p['radius']}; padding: 10px 14px; }}",
        f"QLabel#bubbleText {{ font-size: 13px; color: {p['text']}; }}",
        f"QLabel#tag {{ font-size: 10px; color: {p['text_dim']}; border: 1px solid {p['line']};",
        " border-radius: 4px; padding: 1px 6px; }",
        f"QLabel#tagRef {{ font-size: 10px; color: {p['blue']}; border: 1px solid {p['line']};",
        " border-radius: 4px; padding: 1px 6px; }",
        # -------------------------------------------------------- 图片瓦片
        f"QFrame#tile {{ background: {p['bg_card']}; border: 1px solid {p['line']};",
        f" border-radius: {p['radius_sm']}; }}",
        f"QLabel#tileImage {{ background: {p['bg_hover']}; border: 0; border-radius: {p['radius_sm']}; }}",
        "QFrame#tileActs { background: qlineargradient(x1:0, y1:0, x2:0, y2:1,",
        " stop:0 rgba(0, 0, 0, 0), stop:0.6 rgba(6, 7, 8, 217)); border: 0; }",
        f"QPushButton#vuBtn {{ border: 1px solid rgba(255, 255, 255, 0.22); background: {p['overlay']};",
        f" color: {p['text']}; border-radius: 6px; padding: 4px 10px; font-size: 12px; }}",
        f"QPushButton#vuBtn:hover {{ background: {p['accent']}; color: {p['accent_ink']}; border-color: {p['accent']}; }}",
        f"QLabel#tileFoot {{ font-size: 10px; color: {p['text_dim']}; }}",
        f"QLabel#errMsg {{ font-size: 12px; color: {p['red']}; }}",
        f"QLabel#mutedMsg {{ font-size: 12px; color: {p['text_dim']}; }}",
        f"QPushButton#btnCancelTile {{ border: 1px solid {p['line']}; background: {p['bg_soft']};",
        f" color: {p['text_mid']}; border-radius: 6px; font-size: 11px; padding: 3px 8px; }}",
        f"QPushButton#btnCancelTile:hover {{ color: {p['red']}; }}",
        "QProgressBar#skelBar { border: 0; border-radius: 2px; height: 4px;"
        f" background: {p['line']}; text-align: right; min: 0; max: 100; }}",
        f"QProgressBar#skelBar::chunk {{ background: {p['blue']}; border-radius: 2px; }}",
        f"QLabel#skelLabel {{ font-size: 11px; color: {p['text_mid']}; }}",
        f"QLabel#skelHint {{ font-size: 11px; color: {p['text_dim']}; }}",
        # ------------------------------------------------------- 小按钮族
        f"QPushButton#btnMini {{ border: 1px solid {p['line']}; background: {p['bg_card']}; color: {p['text_mid']};",
        f" border-radius: 6px; padding: 4px 10px; font-size: 12px; text-decoration: none; }}",
        f"QPushButton#btnMini:hover {{ background: {p['bg_hover']}; color: {p['text']}; }}",
        f"QPushButton#btnFile {{ background: {p['bg_card']}; border: 1px solid {p['line']}; color: {p['text']};",
        f" border-radius: 999px; padding: 6px 14px; font-size: 12px; }}",
        f"QPushButton#btnFile:hover {{ background: {p['bg_hover']}; border-color: {p['line_soft']}; }}",
        f"QPushButton#iconBtn {{ border: 0; background: transparent; color: {p['text_mid']}; font-size: 15px;",
        f" padding: 6px 8px; border-radius: 6px; }}",
        f"QPushButton#iconBtn:hover {{ background: {p['bg_hover']}; color: {p['text']}; }}",
        # ---------------------------------------------------------- composer
        "QFrame#refZone { border: 1px dashed"
        f" {p['line']}; border-radius: {p['radius_sm']}; background: {p['bg_soft']}; padding: 6px 12px; }}",
        "QLabel#refMuted { font-size: 12px;"
        f" color: {p['text_dim']}; }}",
        f"QFrame#refChip {{ background: {p['bg_card']}; border: 1px solid {p['line']}; border-radius: {p['radius_sm']};",
        " padding: 8px 10px; }",
        f"QLabel#refPath {{ font-size: 12px; color: {p['text']}; }}",
        f"QLabel#refNote {{ font-size: 11px; color: {p['text_dim']}; }}",
        f"QPlainTextEdit#prompt {{ background: {p['bg_card']}; color: {p['text']}; border: 1px solid {p['line']};",
        f" border-radius: {p['radius']}; padding: 12px 14px; font-size: 15px; }}",
        f"QPlainTextEdit#prompt:focus {{ border: 1px solid {p['blue']}; }}",
        f"QPushButton#btnImagine {{ background: {p['accent']}; color: {p['accent_ink']}; border: 0;",
        f" border-radius: {p['radius']}; padding: 0 22px; font-size: 15px; font-weight: 600; }}",
        f"QPushButton#btnImagine:disabled {{ background: {p['accent']}; color: {p['accent_ink']}; }}",
        f"QLabel#composerMeta {{ font-size: 11px; color: {p['text_dim']}; }}",
        # ---------------------------------------------------------- 参数面板
        f"QLabel#panelTitle {{ font-size: 13px; font-weight: 600; color: {p['text_mid']}; }}",
        f"QLabel#sectionTitle {{ font-size: 12px; font-weight: 600; color: {p['text_mid']}; }}",
        f"QFrame#panelSection {{ border: 0; border-bottom: 1px solid {p['line']}; }}",
        "QScrollArea#panelScroll { border: 0; background: transparent; }",
        "QWidget#panelBodyHost { background: transparent; }",
        f"QComboBox {{ background: {p['bg_card']}; color: {p['text']}; border: 1px solid {p['line']};",
        f" border-radius: {p['radius_sm']}; padding: 7px 10px; }}",
        f"QComboBox QAbstractItemView {{ background: {p['bg_card']}; color: {p['text']};",
        f" border: 1px solid {p['line']}; selection-background-color: {p['bg_hover']}; }}",
        f"QLineEdit {{ background: {p['bg_card']}; color: {p['text']}; border: 1px solid {p['line']};",
        f" border-radius: {p['radius_sm']}; padding: 7px 10px; }}",
        f"QLineEdit:focus {{ border: 1px solid {p['blue']}; }}",
        f"QPlainTextEdit {{ background: {p['bg_card']}; color: {p['text']}; border: 1px solid {p['line']};",
        f" border-radius: {p['radius_sm']}; padding: 8px 10px; }}",
        f"QPlainTextEdit:focus {{ border: 1px solid {p['blue']}; }}",
        f"QSpinBox {{ background: {p['bg_card']}; color: {p['text']}; border: 1px solid {p['line']};",
        f" border-radius: {p['radius_sm']}; padding: 6px 8px; }}",
        f"QSpinBox:disabled {{ color: {p['text_dim']}; background: {p['bg_soft']}; }}",
        f"QPushButton#chip {{ border: 1px solid {p['line']}; background: {p['bg_card']}; color: {p['text_mid']};",
        " border-radius: 999px; padding: 5px 11px; font-size: 12px; }",
        f"QPushButton#chip:hover {{ background: {p['bg_hover']}; color: {p['text']}; }}",
        f"QPushButton#chip:checked {{ background: {p['accent']}; color: {p['accent_ink']}; border-color: {p['accent']}; }}",
        f"QLabel#hint {{ font-size: 11px; color: {p['text_dim']}; }}",
        f"QLabel#hintWarn {{ font-size: 11px; color: {p['red']}; }}",
        f"QLabel#sliderLabel {{ font-size: 12px; color: {p['text_dim']}; }}",
        f"QCheckBox {{ font-size: 12px; color: {p['text_mid']}; }}",
        f"QCheckBox::indicator {{ width: 14px; height: 14px; border: 1px solid {p['line']}; border-radius: 4px;",
        f" background: {p['bg_card']}; }}",
        f"QCheckBox::indicator:checked {{ background: {p['accent']}; border-color: {p['accent']}; }}",
        # ------------------------------------------------------------ 滑杆
        f"QSlider::groove:horizontal {{ height: 4px; background: {p['line']}; border-radius: 2px; }}",
        f"QSlider::sub-page:horizontal {{ background: {p['accent']}; border-radius: 2px; }}",
        f"QSlider::handle:horizontal {{ background: {p['bg_card']}; border: 1px solid {p['line_soft']};",
        " width: 14px; height: 14px; margin: -6px 0; border-radius: 8px; }",
        "QSlider::handle:horizontal:hover { border-color:"
        f" {p['accent']}; }}",
        # ----------------------------------------------------- 音乐/视频卡
        f"QFrame#mediaCard {{ background: {p['bg_card']}; border: 1px solid {p['line']};",
        f" border-radius: {p['radius_sm']}; padding: 10px 12px; }}",
        f"QLabel#mediaName {{ font-size: 12px; color: {p['text']}; }}",
        f"QLabel#mediaMeta {{ font-size: 11px; color: {p['text_dim']}; }}",
        f"QPushButton#playBtn {{ background: {p['accent']}; color: {p['accent_ink']}; border: 0; border-radius: 14px;",
        " min-width: 28px; max-width: 28px; min-height: 28px; max-height: 28px; font-size: 12px; }",
        f"QLabel#playTime {{ font-size: 11px; color: {p['text_dim']}; }}",
        # ---------------------------------------------------------- lightbox
        f"QDialog#lightbox {{ background: #050607e0; }}",
        f"QLabel#lbImage {{ background: {p['bg_card']}; border: 1px solid {p['line']}; border-radius: {p['radius']}; }}",
        f"QLabel#lbMeta {{ font-size: 12px; color: {p['text_mid']}; }}",
        f"QPushButton#lbNav {{ border: 1px solid {p['line']}; background: #16181cb0; color: {p['text']};",
        " border-radius: 999px; min-width: 40px; min-height: 40px; font-size: 20px; }",
        f"QPushButton#lbNav:hover {{ background: {p['bg_hover']}; }}",
        # ------------------------------------------------------------- toast
        f"QLabel#toast {{ background: {p['bg_card']}; color: {p['text']}; border: 1px solid {p['line']};",
        f" border-radius: 999px; padding: 10px 18px; font-size: 13px; }}",
        f"QLabel#toastError {{ background: {p['bg_card']}; color: {p['red']}; border: 1px solid {p['red']};",
        f" border-radius: 999px; padding: 10px 18px; font-size: 13px; }}",
        f"QLabel#toastSuccess {{ background: {p['bg_card']}; color: {p['text']}; border: 1px solid {p['green']};",
        f" border-radius: 999px; padding: 10px 18px; font-size: 13px; }}",
        # ------------------------------------------------------ 通用列表/框
        f"QListWidget {{ border: 1px solid {p['line']}; border-radius: {p['radius_sm']}; background: {p['bg_card']};",
        f" color: {p['text']}; padding: 4px; }}",
        "QListWidget::item { padding: 6px 10px; border-radius: 4px; }",
        f"QListWidget::item:selected {{ background: {p['bg_hover']}; color: {p['text']}; }}",
        f"QTextEdit {{ background: {p['bg_card']}; color: {p['text']}; border: 1px solid {p['line']};",
        f" border-radius: {p['radius_sm']}; padding: 8px; }}",
        f"QPushButton {{ background: {p['bg_card']}; color: {p['text_mid']}; border: 1px solid {p['line']};",
        f" border-radius: {p['radius_sm']}; padding: 7px 14px; }}",
        f"QPushButton:hover {{ background: {p['bg_hover']}; color: {p['text']}; }}",
        f"QPushButton:disabled {{ color: {p['text_dim']}; background: {p['bg_soft']}; border-color: {p['line']}; }}",
        f"QFrame#statusCard {{ background: {p['bg_card']}; border: 1px solid {p['line']}; border-radius: {p['radius_sm']};",
        " padding: 12px 14px; }",
        f"QLabel#pageHeading {{ font-size: 24px; font-weight: 650; color: {p['text']}; }}",
        f"QLabel#metaDim {{ font-size: 12px; color: {p['text_dim']}; }}",
        f"QLabel#statusOk {{ color: {p['green']}; font-size: 12px; }}",
        f"QLabel#statusBad {{ color: {p['red']}; font-size: 12px; }}",
        # ------------------------------------------------------------ 滚动条
        "QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }",
        f"QScrollBar::handle:vertical {{ background: {p['line']}; min-height: 30px; border-radius: 5px; }}",
        f"QScrollBar::handle:vertical:hover {{ background: {p['line_soft']}; }}",
        "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }",
        "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }",
        "QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }",
        f"QScrollBar::handle:horizontal {{ background: {p['line']}; min-width: 30px; border-radius: 5px; }}",
        "QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }",
        "QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: transparent; }",
    ]
    return "\n".join(rules)
