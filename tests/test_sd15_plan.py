"""SD1.5 出图档位与显存预算（app/engine/image/plan.py）的离线单元测试。

2026-11-10 文生图换成 Sana-1.6B 多语言（sana-multiling）、图生图保留 Wan VACE（wan-i2i），
SD1.5 引擎与 plan.py / hires.py 仍在盘上但不再注册（它是目前唯一实测过峰值显存的出图路径，
留作后备）。这份测试保留的价值是钉住这两个模块自身的几何与预算规则不变——哪天它们被复用
或被删，这里都会先红给看；Sana 那一档的几何与像素预算在 test_sana_plan.py 里钉。

旧版还有一整节「三端一致性」：引擎、桌面面板、网页面板各抄一份 PIXEL_SAFE 再互相对拍。
面板重写成 Wan 480p 档后那些符号（PIXEL_SAFE / ASPECT_CHIPS_IMAGE /
estimate_hires_tiles / default_mode_params）已随旧档退场，抄写对拍自然失效，那一节
整体删除；两端一致的现行核对在 test_agent_and_i18n.py 与 test_mode_theme.py。

注意这里不 import app.engine.image.sd15（那个模块 import torch/diffusers），
只走 plan/hires 的纯几何，所以任何 venv 都能跑。
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from app.engine.image import hires
from app.engine.image.plan import (
    BASE_MAX_PIXELS,
    HARD_SINGLE_PASS_PIXELS,
    HIRES_MAX_PIXELS,
    MAX_SIDE,
    MULTIPLE,
    SD15_PROFILE,
    SINGLE_PASS_MAX_PIXELS,
    _PROFILES,
    PlanBudgetError,
    ImagePlan,
    resolve_output_plan,
    round_to_multiple,
)


# ------------------------------------------------------------------ 档位规则
def test_512_square_is_single_pass() -> None:
    """512×512（0.26MP）远低于单遍上限：直接单遍，底图就等于目标。"""
    plan = resolve_output_plan(512, 512)
    assert (plan.target_width, plan.target_height) == (512, 512)
    assert plan.use_hires is False
    assert (plan.base_width, plan.base_height) == (512, 512)
    assert plan.base_pixels == plan.target_pixels


def test_1024x576_is_single_pass() -> None:
    """默认档位 1024×576（0.59MP）也在单遍预算内，不该多花一次重绘的钱。"""
    plan = resolve_output_plan(1024, 576)
    assert plan.use_hires is False
    assert plan.target_pixels < SINGLE_PASS_MAX_PIXELS
    assert (plan.base_width, plan.base_height) == (1024, 576)


def test_1080p_automatically_enables_hires_within_base_budget() -> None:
    """1920×1080（2.07MP）单遍会顶穿 6GB：自动开重绘，且底图必须落在 BASE_MAX_PIXELS 内。"""
    plan = resolve_output_plan(1920, 1080)
    assert plan.use_hires is True
    assert plan.target_pixels > SINGLE_PASS_MAX_PIXELS
    assert plan.base_pixels <= BASE_MAX_PIXELS
    # 底图边长取整到 64：分块时最后一块贴边对齐，64 的倍数能保证块边界仍是 8 的倍数。
    assert plan.base_width % 64 == 0 and plan.base_height % 64 == 0
    assert (plan.target_width, plan.target_height) == (1920, 1080)
    assert (plan.base_width, plan.base_height) == (896, 512)
    # 底图与目标同比例（等比缩放，不拉伸变形）。
    assert abs(plan.base_width / plan.base_height - 1920 / 1080) < 0.05


def test_hires_false_over_hard_budget_raises() -> None:
    """用户显式 hires=False 又超过硬上限：直接报错，绝不偷偷降画质。"""
    with pytest.raises(PlanBudgetError) as caught:
        resolve_output_plan(1920, 1080, hires=False)
    message = str(caught.value)
    assert "单遍直出会超显存预算" in message
    assert f"{SINGLE_PASS_MAX_PIXELS / 1e6:.2f}MP" in message
    assert f"{HARD_SINGLE_PASS_PIXELS / 1e6:.2f}MP" in message


def test_hires_false_between_soft_and_hard_budget_is_allowed() -> None:
    """0.85MP～1.30MP 之间且显式关重绘＝用户自己扛显存：放行，不拦。"""
    assert SINGLE_PASS_MAX_PIXELS < HARD_SINGLE_PASS_PIXELS
    width, height = 1152, 1024
    assert width * height > SINGLE_PASS_MAX_PIXELS
    assert width * height <= HARD_SINGLE_PASS_PIXELS
    plan = resolve_output_plan(width, height, hires=False)
    assert plan.use_hires is False
    assert (plan.base_width, plan.base_height) == (width, height)


def test_canvas_beyond_hires_limit_raises(monkeypatch) -> None:
    """超过 HIRES_MAX_PIXELS 的画幅属于误输入：块数与耗时都会失控，必须报错。

    注意：MAX_SIDE=4096 已经把宽高各自钳住（4096×4096＝16.7MP < 25MP），所以按默认
    常量这条分支走不到；这里把上限临时压低来钉住「越线必抛 PlanBudgetError」这个行为，
    顺带确认默认常量本身没有把可用档位一起否掉。

    2026-11-10 换型之后判断走的是 SD15_PROFILE 里的数字（Sana 那档另有自己的常量，
    所以预算不再是函数里直接读全局常量），要临时压低上限就得改这张 profile 表本身，
    只 monkeypatch HIRES_MAX_PIXELS 已经影响不到 resolve_output_plan 了。
    """
    assert MAX_SIDE * MAX_SIDE < HIRES_MAX_PIXELS
    plan = resolve_output_plan(4096, 4096)  # 钳到 MAX_SIDE 后仍能走重绘
    assert plan.use_hires is True
    assert (plan.target_width, plan.target_height) == (4096, 4096)

    capped = replace(SD15_PROFILE, max_target_pixels=10_000)
    monkeypatch.setitem(_PROFILES, "sd15", capped)
    with pytest.raises(PlanBudgetError) as caught:
        resolve_output_plan(1920, 1080, profile="sd15")
    assert "超过高清重绘上限" in str(caught.value)


def test_hires_true_small_canvas_still_redraws() -> None:
    """hires=True 时小图也走一遍重绘（等于自带锐化），底图就是原尺寸。"""
    plan = resolve_output_plan(512, 512, hires=True)
    assert plan.use_hires is True
    assert (plan.base_width, plan.base_height) == (512, 512)
    # 底图这一遍照样进得了 UNet：分块几何不会切出空块。
    assert hires.count_tiles(plan.target_width, plan.target_height) >= 1


def test_target_size_is_rounded_to_multiple_of_eight() -> None:
    """VAE 是 8 倍下采样：目标宽高先就近取整到 8 的倍数，再谈预算。"""
    assert MULTIPLE == 8
    assert round_to_multiple(512) == 512
    assert round_to_multiple(500) == 504  # 余 4 == multiple/2 → 向上
    assert round_to_multiple(7) == MULTIPLE  # 下限就是 multiple 本身
    assert round_to_multiple(99999) == MAX_SIDE  # 上限钳到 MAX_SIDE
    plan = resolve_output_plan(1000, 700)
    assert (plan.target_width, plan.target_height) == (1000, 704)
    assert plan.target_width % MULTIPLE == 0 and plan.target_height % MULTIPLE == 0


def test_image_plan_pixel_properties() -> None:
    """ImagePlan 是冻结 dataclass：两个像素属性是给引擎和面板共用的同一算法。"""
    plan = resolve_output_plan(1920, 1080)
    assert plan.target_pixels == 1920 * 1080
    assert plan.base_pixels == plan.base_width * plan.base_height
    with pytest.raises(FrozenInstanceError):
        plan.target_width = 640  # frozen=True：预算一旦定下就不该被就地改小


def test_hires_budget_constants_are_ordered() -> None:
    """四道线的顺序不能被写反：单遍 < 硬上限 < 重绘上限，底图线最低。"""
    assert BASE_MAX_PIXELS < SINGLE_PASS_MAX_PIXELS < HARD_SINGLE_PASS_PIXELS < HIRES_MAX_PIXELS
    assert ImagePlan(512, 512, 512, 512, False).use_hires is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
