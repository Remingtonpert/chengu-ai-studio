"""离屏 UI 回归测试：🌏 语言切换与「AI 智能体」Tab 的真实接线。

这里不启动任何 GPU 引擎（task_service / engine_manager 传 None），智能体用桩服务，
所以只验证界面接线：不产生生成任务，也不碰显卡。构造主窗口比较慢，整个模块共用
一个窗口实例，测试按文件内顺序执行。
"""

from __future__ import annotations

import json
import logging
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QPushButton  # noqa: E402

from app.core import i18n  # noqa: E402
from app.core.config import ConfigStore  # noqa: E402
from app.core.paths import AppPaths  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402
from app.ui.pages import agent_page as agent_ui  # noqa: E402


class StubAgentService:
    """替掉真智能体：不联网、不起大模型，只按话里的关键词回报。"""

    config: dict = {}

    def capabilities(self) -> dict:
        return {
            # 与真 service 出口同一份口径：文生图槽键在出口改名 "image"（前端两端契约），
            # 五个生成板块在岗（图片走 wan-t2i / wan-i2i，音乐走 ace-step-15）。
            "engines": {
                "image": "wan-t2i",
                "i2i": "wan-i2i",
                "t2v": "wan-t2v",
                "i2v": "wan-i2v",
                "music": "ace-step-15",
            },
            "engine_states": {
                "wan-t2i": "stopped",
                "wan-i2i": "stopped",
                "wan-t2v": "stopped",
                "wan-i2v": "stopped",
                "ace-step-15": "stopped",
            },
            "llm": {"available": True, "model": "qwen2.5:14b-instruct"},
            "tts": {"available": True, "voices": [{"name": "Huihui"}]},
            "notes": ["能力说明一条"],
            "diagnostics": ["显卡整卡 12288 MiB，已用 1200 MiB，空闲 11088 MiB（要求至少留 9000 MiB）"],
            "help": "帮助文本：说出你要生成的东西。",
        }

    def handle(self, message, *, history=None, ref_image="", action_hint=""):
        if "画" in message:
            return {
                "action": "generate",
                "mode": "t2i",
                "modality": "image",
                "prompt": message,
                "parameters": {"width": 832, "height": 480, "steps": 28},
                "notes": ["Wan2.1 单帧，480p 像素预算"],
                "tasks": [
                    {
                        "task_id": "abcdef123456",
                        "task_type": "image",
                        "engine_id": "wan-t2i",
                        "status": "queued",
                        "prompt": message,
                        "parameters": {},
                    }
                ],
                "reply": "已排入队列：图片 · 引擎 wan-t2i（任务 abcdef12）。",
                "model": "stub",
            }
        if "视频" in message:
            return {
                "action": "generate",
                "mode": "t2v",
                "modality": "video",
                "prompt": message,
                "parameters": {"width": 832, "height": 480, "num_frames": 161},
                "notes": ["默认 832×480"],
                "tasks": [
                    {
                        "task_id": "abcdef123456",
                        "task_type": "video",
                        "engine_id": "wan-t2v",
                        "status": "queued",
                        "prompt": message,
                        "parameters": {},
                    }
                ],
                "reply": "已排入队列：视频（任务 abcdef12）。",
                "model": "stub",
            }
        return {
            "action": "chat",
            "reply": "这是对白的回复",
            "model": "stub",
            "notes": [],
        }


def _texts(widget) -> list[str]:
    return [label.text() for label in widget.findChildren(QLabel) if label.text()]


def _pump(app: QApplication, seconds: float) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)


@pytest.fixture(scope="module")
def window(tmp_path_factory):
    home = tmp_path_factory.mktemp("chenguai-ui")
    paths = AppPaths(app_home=home, data_root=home)
    paths.prepare_app_home()
    app = QApplication.instance() or QApplication([])
    handle = MainWindow(
        ConfigStore(paths),
        logging.getLogger("ui-test"),
        str(paths.database_file),
        None,
        None,
        None,
    )
    handle.agent_service = StubAgentService()
    handle.creation.agent_service = handle.agent_service
    handle.show()
    _pump(app, 0.3)
    try:
        yield handle
    finally:
        handle.close()
        i18n.set_language("zh")


def test_mode_switch_recolors_desktop_accent(window) -> None:
    """规则 5（桌面端）：切模式必须真的换强调色，色值与网页端 MODE_ACCENTS 同一份。

    这条测试钉的是「换模式后样式表真的重算了」——只看 theme.py 里有几张色卡不够，
    因为不重算 stylesheet 的话桌面端永远是一套 Midjourney 中性色，跟网页端对不上。
    """
    from app.ui.theme import MODE_ACCENTS

    window.creation.switch_mode("agent")
    assert MODE_ACCENTS["agent"][0] in window.styleSheet()
    window.creation.switch_mode("t2v")
    sheet = window.styleSheet()
    assert MODE_ACCENTS["t2v"][0] in sheet and "#composerBar" in sheet
    assert MODE_ACCENTS["agent"][0] not in sheet, "#0f9d8f 残留在文生视频模式的样式表里"
    window.creation.switch_mode("i2v")
    sheet = window.styleSheet()
    assert MODE_ACCENTS["i2v"][0] in sheet


def test_all_five_generation_modes_are_live_on_the_desktop(window) -> None:
    """2026-11-09 重新选型后图片与音乐全部在岗：五个生成模式都必须能切换、能选中。

    旧版这条是「文生图 / 图生图 / AI 音乐已下架，切过去要被挡在门外」的看门测试，
    SD 下架期的口径，随 Wan/ACE-Step 回归整体替换为反向断言。
    """
    for mode in ("t2i", "i2i", "t2v", "i2v", "music"):
        window.creation.switch_mode(mode)
        checked = [b.mode for b in window.creation.tab_buttons if b.isChecked()]
        assert checked == [mode], f"{mode} 还能被选中：{checked}"


def test_agent_tab_comes_first_and_tabs_are_ordered(window) -> None:
    modes = [button.mode for button in window.creation.tab_buttons]
    assert modes == ["agent", "t2i", "i2i", "t2v", "i2v", "music"]
    assert window.lang_button.text().startswith("🌏")
    assert len(window._lang_menu.actions()) == len(i18n.languages())


def test_language_switch_retranslates_chrome_and_persists(window) -> None:
    assert window.windowTitle() == "辰鹄 AI 创作中心"
    window._set_ui_language("en")
    assert window.windowTitle() == "Chengu AI Creation Center"
    tabs = [button.text() for button in window.creation.tab_buttons]
    assert tabs[0] == "AI Agent" and "Text to Image" in tabs
    assert window._nav_entries["queue"].text() == "Task queue"
    assert window._sessions_caption.text() == "Sessions"
    shell = window._system_shells["queue"]
    assert shell.chrome_widgets[0].text() == "← Back to creation"
    assert window.lang_button.text() == "🌏 English"

    window._set_ui_language("ar")
    assert i18n.is_rtl()
    assert window._nav_entries["queue"].text() != "任务队列"

    window._set_ui_language("zh")
    assert window._nav_entries["queue"].text() == "任务队列"
    saved = json.loads(
        (window.data_dir / "config.json").read_text(encoding="utf-8-sig")
    )
    assert saved.get("ui_language") == "zh"


def test_agent_panel_replaces_creation_view(window) -> None:
    window._show_creation()
    assert window.creation.feed.isVisible()
    assert window.param_panel.isVisible()
    window.creation.switch_mode("agent")
    checked = [b.mode for b in window.creation.tab_buttons if b.isChecked()]
    assert checked == ["agent"]
    assert not window.creation.feed.isVisible()
    assert not window.creation.composer.isVisible()
    assert not window.param_panel.isVisible()
    page = window.creation.agent_page
    assert page is not None and page.isVisible()

    window.creation.switch_mode("t2i")
    assert window.creation.feed.isVisible()
    assert window.param_panel.isVisible()
    assert not page.isVisible()


def test_agent_page_shows_capabilities_and_submits_on_enter(window) -> None:
    window.creation.switch_mode("agent")
    page = window.creation.agent_page
    assert page is not None
    _pump(QApplication.instance(), 1.2)
    assert len(_texts(page.capability_bar)) >= 4
    before = len(page._history)

    page._send("画一张赛博朋克雨夜街头，16:9")
    _pump(QApplication.instance(), 1.5)
    assert page._busy is False
    # 一轮问答进两条：用户原话 + 智能体回报
    assert len(page._history) == before + 2
    assert page._last_user_text == "画一张赛博朋克雨夜街头，16:9"
    assert any("abcdef12" in text for text in _texts(page))

    page.input.setPlainText("FLUX 的步数一般设多少？")
    key = QKeyEvent(
        QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier
    )
    QApplication.sendEvent(page.input, key)
    _pump(QApplication.instance(), 1.5)
    assert page.input.toPlainText() == ""
    assert page._last_user_text == "FLUX 的步数一般设多少？"
    assert len(page._history) == before + 4

    window.creation.switch_mode("t2i")


class _RecordingTimer:
    """替掉 QTimer.singleShot：不真等 8 秒，只把安排出来的自动重试记下来。"""

    def __init__(self) -> None:
        self.scheduled: list[tuple[int, object]] = []

    def singleShot(self, msec: int, callback) -> None:  # noqa: N802
        self.scheduled.append((msec, callback))


def test_agent_panel_reprobes_without_restarting_the_app(window, monkeypatch) -> None:
    """开机探测降级不是终局：自动再探一次，界面上的「重新探测」也能手动再探。

    引擎在后台一个个注册、专用大模型实例可能晚几秒才就位，用户看到「无引擎」时
    不该被要求重启整个程序。这里把 QTimer 换掉，所以不真的等待重试间隔。
    """
    window.creation.switch_mode("agent")
    page = window.creation.agent_page
    assert page is not None

    healthy = StubAgentService().capabilities()
    degraded = dict(
        healthy,
        engines={"image": "wan-t2i"},
        llm={"available": False, "model": "", "note": "专用大模型实例还没起来"},
        notes=["当前没有可用的生成引擎…"],
    )
    outcomes = [degraded, healthy]
    calls = {"count": 0}

    def flaky_capabilities() -> dict:
        result = outcomes[min(calls["count"], len(outcomes) - 1)]
        calls["count"] += 1
        return result

    monkeypatch.setattr(page.service, "capabilities", flaky_capabilities)
    timer = _RecordingTimer()
    monkeypatch.setattr(agent_ui, "QTimer", timer)

    buttons = [b for b in page.capability_bar.findChildren(QPushButton) if b.objectName() == "btnMini"]
    assert len(buttons) == 1 and buttons[0] is page.probe_button
    assert page.probe_button.text() == "重新探测"

    page._capabilities = None
    page._probe_retries = 0
    page.refresh_capabilities(force=True)
    _pump(QApplication.instance(), 0.8)

    # 降级 → 安排一次稍后的自动重试，同时把按钮交还给用户手动触发
    assert calls["count"] == 1
    assert len(timer.scheduled) == 1
    assert timer.scheduled[0][0] == agent_ui._PROBE_RETRY_MS
    assert page._probe_retries == 1
    assert page.probe_button.isEnabled()
    assert page.probe_button.text() == "重新探测"
    assert any("无引擎" in text for text in _texts(page.capability_bar))

    # 点一下就好：不用重启程序，也不用等那 8 秒
    page.probe_button.click()
    _pump(QApplication.instance(), 0.8)

    assert calls["count"] == 2
    # 这次四个引擎和大模型都在位 → 不再安排新的重试
    assert len(timer.scheduled) == 1
    assert page._probe_retries == 0
    pills = _texts(page.capability_bar)
    assert not any("无引擎" in text for text in pills)
    assert any("qwen" in text.lower() for text in pills)
    # 胶囊重建过一轮，按钮还在原位
    assert page.probe_button.isVisible()


def test_agent_page_keeps_help_and_vram_readout_collapsed(window) -> None:
    """首屏干净：长说明收在「帮助」里，显存读数收在「调试信息」里，两个开关默认都是关的。

    用户要的是 ChatGPT 那样的界面：能力说明不铺开在对话里，技术读数不常驻输入框下面。
    桌面端与网页端行为必须一致，所以这里把「默认不显示、点了才展开」钉成契约。
    """
    window.creation.switch_mode("agent")
    page = window.creation.agent_page
    assert page is not None
    assert page.help_button.isChecked() is False
    assert page.debug_button.isChecked() is False

    page._capabilities = None
    page.refresh_capabilities(force=True)
    _pump(QApplication.instance(), 1.2)

    # 长说明不再作为气泡铺进对话区（首屏只有用户说话才起气泡）
    assert not any("帮助文本" in text for text in _texts(page._scroll.widget()))
    assert page.help_panel.isVisible() is False
    assert "帮助文本" in page.help_text.text()  # 文案还在，只是收着

    page.help_button.click()
    _pump(QApplication.instance(), 0.2)
    assert page.help_panel.isVisible() is True
    assert any("帮助文本" in text for text in _texts(page.help_panel))
    page.help_button.click()
    assert page.help_panel.isVisible() is False

    # 显存读数：默认看不见，打开「调试信息」才拼进底部那行
    assert "能力说明一条" in page.hint.text()
    assert "显卡整卡" not in page.hint.text()
    page.debug_button.click()
    assert "显卡整卡" in page.hint.text()
    page.debug_button.click()
    assert "显卡整卡" not in page.hint.text()

    # 换语言：两个开关的标签跟着换，开关状态不靠文字表达所以不丢
    window._set_ui_language("en")
    _pump(QApplication.instance(), 0.3)
    assert page.help_button.text() == "Help" and page.debug_button.text() == "Debug info"
    assert page.debug_button.isChecked() is False
    window._set_ui_language("zh")
    assert page.help_button.text() == "帮助" and page.debug_button.text() == "调试信息"
    window.creation.switch_mode("t2i")

    window.creation.switch_mode("t2i")
