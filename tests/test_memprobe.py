"""空闲物理内存探针与加载门槛的回归测试（纯标准库，任何 venv 都能跑）。

钉住的不是「能不能读到内存数字」这种小事，而是 2026-11-08 那次 wan-i2v 的教训：
加载权重前不量空闲物理内存 ⇒ 进程在 checkpoint shards 中途被系统杀掉，连 traceback 都没有，
排查时被我误判成「显存被占满」。所以现在必须有：够就放行、不够就给出可操作的中文错误，
而且读不到数值时**不能假拦截**。
"""

from __future__ import annotations

import pytest

from app.engine.memprobe import (
    RamSnapshot,
    floor_mb_or,
    ram_gate_message,
    ram_gate_ok,
    read_physical_ram,
)


def test_read_physical_ram_returns_usable_numbers():
    snap = read_physical_ram()
    # Windows 上必须读到真数值；其它平台允许 None（探针不能把调用方带崩）
    if snap.free_mb is not None:
        assert snap.total_mb is not None and snap.total_mb > 0
        assert 0 < snap.free_mb <= snap.total_mb
    else:
        assert snap.total_mb is None


def test_as_dict_keys_are_stable_for_task_metadata():
    assert RamSnapshot(total_mb=65536, free_mb=2560).as_dict() == {
        "ram_total_mb": 65536,
        "ram_free_mb": 2560,
    }


def test_gate_passes_when_enough_ram_is_free():
    assert ram_gate_ok(RamSnapshot(65536, 16_384), 16_384) is True
    assert ram_gate_ok(RamSnapshot(65536, 16_383), 16_384) is False


def test_gate_does_not_block_when_probe_has_no_reading():
    # 读不到数值（非 Windows / API 失败）时放行：不能造出一个假拦截
    assert ram_gate_ok(RamSnapshot(None, None), 16_384) is True


@pytest.mark.parametrize("raw,expected", [
    ("", 16_384),
    (None, 16_384),
    ("   ", 16_384),
    ("8192", 8192),
    ("8192.0", 8192),
    ("abc", 16_384),
    ("0", 16_384),
    ("-5", 16_384),
])
def test_floor_from_env_is_safe(raw, expected):
    assert floor_mb_or(raw, 16_384) == expected


def test_gate_message_says_measured_floor_and_next_step():
    message = ram_gate_message(
        "Wan2.1 视频引擎",
        16_384,
        RamSnapshot(65_536, 2_560),
        why="UMT5-XXL 文本编码单件就要约 11GB 物理内存",
    )
    assert "空闲物理内存只有 2.5 GB" in message
    assert "门槛 16.0 GB" in message
    assert "机器总内存 64 GB" in message
    assert "UMT5-XXL" in message
    # 必须给出可操作的下一步，而不是只说「内存不足」
    assert "关闭占内存的程序" in message
    assert "release_local_engine.bat" in message
    # 这条错误以前根本不存在，所以进程是静默消失的：文案要点名那次事故
    assert "静默" in message


def test_gate_message_survives_unknown_reading():
    message = ram_gate_message("ACE-Step 1.5 音乐引擎", 14_336, RamSnapshot(None, None))
    assert "未知" in message
    assert "门槛 14.0 GB" in message
    assert "（机器总内存" not in message
