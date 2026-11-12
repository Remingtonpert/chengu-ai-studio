"""控制台编码兜底的回归测试。

真机踩过两次：Windows 中文环境默认代码页是 GBK，脚本/入口一 print 中文或 emoji 就抛
UnicodeEncodeError，整个程序「一启动就崩」，用户拿不到任何有用信息。这里钉住三件事：
兜底函数本身不崩、两端入口都调用了它、用户能双击/手跑的入口都有编码保护。
"""

from __future__ import annotations

from pathlib import Path

from app.core.console import ensure_utf8_console

ROOT = Path(__file__).resolve().parent.parent


class _RecordingStream:
    """有 reconfigure 的流：记录参数，供断言。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def reconfigure(self, **kwargs: object) -> None:
        self.calls.append(kwargs)


class _PlainStream:
    """没有 reconfigure 的流（老 Python / 被换掉的流）：必须跳过而不是崩。"""


class _BrokenStream:
    """reconfigure 自己抛异常的流（已关闭、被重定向）：必须吞掉。"""

    def reconfigure(self, **kwargs: object) -> None:
        raise OSError("stream is closed")


def test_guard_reconfigures_both_streams_to_utf8_with_replace(monkeypatch) -> None:
    out, err = _RecordingStream(), _RecordingStream()
    monkeypatch.setattr("sys.stdout", out)
    monkeypatch.setattr("sys.stderr", err)
    assert ensure_utf8_console() is None
    for stream in (out, err):
        assert len(stream.calls) == 1
        assert stream.calls[0]["encoding"] == "utf-8"
        # errors="replace" 是关键：编码兜底本身绝不能再成为崩溃源
        assert stream.calls[0]["errors"] == "replace"


def test_guard_is_idempotent_and_survives_odd_streams(monkeypatch) -> None:
    out = _RecordingStream()
    monkeypatch.setattr("sys.stdout", out)
    monkeypatch.setattr("sys.stderr", _PlainStream())
    ensure_utf8_console()
    ensure_utf8_console()  # 幂等：调用两次不报错
    assert len(out.calls) == 2
    monkeypatch.setattr("sys.stdout", _BrokenStream())
    monkeypatch.setattr("sys.stderr", _BrokenStream())
    ensure_utf8_console()  # 流坏了也必须安静跳过


def test_both_entry_points_call_the_guard() -> None:
    """桌面端与网页端入口必须在 main 一开始就把流切到 UTF-8。"""
    for rel in ("app/bootstrap.py", "app/web/server.py"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "from app.core.console import ensure_utf8_console" in text, rel
        assert "ensure_utf8_console()" in text, rel


def test_user_facing_scripts_and_bat_wrappers_are_encoding_safe() -> None:
    """手跑的脚本自带内联兜底；双击的 .bat 先把代码页切到 UTF-8。"""
    scripts = [
        "verify_env.py",
        "check_hardware.py",
        "acceptance_image.py",
        "acceptance_video.py",
        "download_image_weights.py",
        "download_sana_weights.py",
        "download_video_weights.py",
        "download_music_weights.py",
    ]
    for name in scripts:
        text = (ROOT / "scripts" / name).read_text(encoding="utf-8")
        assert 'reconfigure(encoding="utf-8", errors="replace")' in text, name

    bats = [
        "acceptance_image.bat",
        "acceptance_video.bat",
        "acceptance_music.bat",
        "run_web.bat",
        "run_agent_llm.bat",
        "release_local_engine.bat",
    ]
    for name in bats:
        raw = (ROOT / "scripts" / name).read_bytes().decode("utf-8", errors="replace")
        assert "chcp 65001" in raw, name
        assert "PYTHONIOENCODING=utf-8" in raw, name
