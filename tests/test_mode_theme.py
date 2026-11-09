"""模式专属皮肤的回归测试：规则 5（一个模块一套外观）+ 规则 3（两端必须一致）。

钉死四件事：
1. 网页端 CSS 里确实有 agent/t2v/i2v/music 四套皮肤（含 PIKA 胶片条、SUNO 波形条、ChatGPT 气泡）；
2. 桌面端 ``theme.MODE_ACCENTS`` 与 CSS 的强调色**逐字相同**——两端不可能悄悄跑偏；
3. ``build_stylesheet(dark, mode)`` 真按模式上色；
4. 文生图 t2i / 图生图 i2i 刻意保持 Midjourney 中性近白：两端都没有它们的独立皮肤，
   这两个 mode 一律回落成 MJ 中性配色，不会冒野色。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSS = (ROOT / "app" / "web" / "static" / "style.css").read_text(encoding="utf-8")
APP_JS = (ROOT / "app" / "web" / "static" / "app.js").read_text(encoding="utf-8")
INDEX_HTML = (ROOT / "app" / "web" / "static" / "index.html").read_text(encoding="utf-8")

try:
    from app.ui import theme

    _needs_theme = False
except Exception:  # noqa: BLE001 - 没有 PySide6 的环境里跳过桌面端那半
    theme = None
    _needs_theme = True

import pytest

# 与 CSS 块、theme.MODE_ACCENTS 三方共用的期望表：在岗且有独立皮肤的四个板块。
EXPECTED = {
    "agent": "#0f9d8f",
    "t2v": "#e8615c",
    "i2v": "#e8615c",
    "music": "#b15cf6",
}
# 文生图 / 图生图：刻意不配独立皮肤，两端统一走 Midjourney 中性兜底。
NEUTRAL_MODES = ("t2i", "i2i")
# index.html 里静态资源的缓存版本号；改过 style.css/app.js/agent.js/i18n.js 必须一起抬。
ASSET_VERSION = "20261109c"


def test_web_css_has_a_skin_per_mode():
    for mode, accent in EXPECTED.items():
        pattern = rf'html\[data-mode="{mode}"\][^{{]*\{{[^}}]*--accent:\s*{re.escape(accent)}\s*;'
        assert re.search(pattern, CSS), f"缺少 {mode} 的强调色 {accent}"


def test_neutral_image_modes_have_no_skin_on_either_end():
    for mode in NEUTRAL_MODES:
        assert f'html[data-mode="{mode}"]' not in CSS, f"{mode} 应保持 MJ 中性，网页端 CSS 里不该有它的皮肤"
        assert f'html[data-mode="{mode}"] .composer::before' not in CSS
    assert theme is None or set(theme.MODE_ACCENTS) == set(EXPECTED)


def test_pika_and_agent_flourishes_exist():
    # PIKA 风：视频模式输入框上方一条胶片刻度
    assert 'html[data-mode="t2v"] .composer::before' in CSS
    assert 'html[data-mode="i2v"] .composer::before' in CSS
    # SUNO 风的波形条随 AI 音乐板块回归重新上岗
    assert 'html[data-mode="music"] .prompt' in CSS
    # ChatGPT 风：智能体气泡与发送键
    assert 'html[data-mode="agent"] .agent-bubble' in CSS
    assert 'html[data-mode="agent"] .agent-send' in CSS


def test_app_js_publishes_active_mode_on_html_element():
    assert "document.documentElement.dataset.mode = mode;" in APP_JS
    assert 'document.documentElement.dataset.mode = "agent";' in APP_JS


def test_app_js_mode_list_has_agent_plus_five_boards():
    """六板块回归后网页端 ``MODES``：智能体排第一，其后图片两块、视频两块、音乐。"""
    match = re.search(r"const MODES\s*=\s*\[([^\]]*)\]", APP_JS)
    assert match, "app.js 里找不到 MODES 清单"
    listed = [item.strip().strip('"\'') for item in match.group(1).split(",") if item.strip()]
    assert listed == ["agent", "t2i", "i2i", "t2v", "i2v", "music"], f"网页端板块清单不对：{listed}"


def test_static_assets_cache_bumped_after_skin_change():
    assert f"?v={ASSET_VERSION}" in INDEX_HTML
    for stale in ("?v=20261108c", "?v=20261108d", "?v=20261108e", "?v=20261109a", "?v=20261109b"):
        if stale == f"?v={ASSET_VERSION}":
            continue
        assert stale not in INDEX_HTML, f"{stale} 还留在 index.html 里，浏览器会拿到旧样式"


@pytest.mark.skipif(_needs_theme, reason="本解释器没有 PySide6，跳过桌面端对拍")
def test_desktop_accents_match_web_css_character_for_character():
    assert theme.MODE_ACCENTS.keys() == EXPECTED.keys()
    for mode, accent in EXPECTED.items():
        entry = theme.MODE_ACCENTS[mode]
        assert entry is not None and entry[0] == accent, f"{mode} 桌面/网页强调色不一致"
        # 桌面端这份色值必须真的出现在 CSS 里（反向对拍，防止只改一端）
        assert accent in CSS, f"{mode} 的 {accent} 在网页端 CSS 里找不到"


@pytest.mark.skipif(_needs_theme, reason="本解释器没有 PySide6，跳过桌面端皮肤")
def test_desktop_stylesheet_changes_accent_per_mode():
    dark = theme.build_stylesheet(True)
    assert "#b15cf6" not in dark and "#e8615c" not in dark and "#0f9d8f" not in dark
    assert "#b15cf6" in theme.build_stylesheet(True, "music")
    assert "#e8615c" in theme.build_stylesheet(True, "t2v")
    assert "#e8615c" in theme.build_stylesheet(True, "i2v")
    assert "#0f9d8f" in theme.build_stylesheet(True, "agent")
    # 保持中性的板块 / 未知模式 / 空值都必须回落成原有 MJ 配色，不能出野色
    for neutral in NEUTRAL_MODES + ("nope",):
        assert theme.build_stylesheet(True, neutral) == dark
    assert theme.build_stylesheet(True, None) == dark
    # 浅色主题下保持中性的板块也必须与不带 mode 时完全一致；音乐在浅色同样上紫（与深色分支对称）
    light = theme.build_stylesheet(False)
    for neutral in NEUTRAL_MODES + ("nope",):
        assert theme.build_stylesheet(False, neutral) == light
    assert "#b15cf6" in theme.build_stylesheet(False, "music")


@pytest.mark.skipif(_needs_theme, reason="本解释器没有 PySide6，跳过桌面端 composer 描边")
def test_desktop_composer_border_follows_mode_accent():
    sheet = theme.build_stylesheet(True, "t2v")
    assert "#composerBar" in sheet
    assert "#e8615c" in sheet.split("#composerBar", 1)[1][:200]
