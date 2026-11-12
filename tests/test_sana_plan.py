"""Sana 多语言文生图的出图档位与像素预算（app/engine/image/plan.py 的 SANA_PROFILE）。

2026-11-10 文生图换成 Sana-1.6B 多语言版（引擎 sana-multiling）之后，图片档的几何规则
和 SD1.5 / Wan 那两套都不一样：宽高必须按 32 对齐（DC-AE 是 32 倍下采样）、单遍预算
1.10MP、超过预算走「按预算出底图 + Lanczos 放大」而不是高清重绘（SANA_PROFILE 的
can_refine_tiles=False，因为没有实测过的分块重绘路径，不能谎称重绘）。

下面这些期望值全部来自真跑一遍 plan 得出的读数（不是手算的理想值），改动常量就会先红。
这里只走纯几何，不 import torch/diffusers，任何 venv 都能跑。
"""

from __future__ import annotations

import pytest

from app.engine.image.plan import (
    SANA_BASE_MAX_PIXELS,
    SANA_MAX_SIDE,
    SANA_MAX_TARGET_PIXELS,
    SANA_MULTIPLE,
    SANA_PROFILE,
    SANA_SINGLE_PASS_MAX_PIXELS,
    ImagePlan,
    PlanProfile,
    resolve_output_plan,
    round_to_multiple,
)


def _sana(width: int, height: int) -> ImagePlan:
    return resolve_output_plan(width, height, profile=SANA_PROFILE)


# ------------------------------------------------------------------ 常量口径
def test_sana_constants_are_ordered_and_self_consistent() -> None:
    """四道线的顺序：底图预算 ≤ 单遍预算 ≤ 目标硬上限，且 MAX_SIDE 不会否掉自己的顶档。"""
    assert SANA_MULTIPLE == 32  # DC-AE f32c32：32 倍下采样，边长必须是 32 的倍数
    assert SANA_BASE_MAX_PIXELS <= SANA_SINGLE_PASS_MAX_PIXELS
    assert SANA_SINGLE_PASS_MAX_PIXELS < SANA_MAX_TARGET_PIXELS
    # 2048×2048 = 4.19MP 恰好落在 4.20MP 目标线内：MAX_SIDE 钳位不会把顶档一起否掉。
    assert SANA_MAX_SIDE * SANA_MAX_SIDE <= SANA_MAX_TARGET_PIXELS
    assert SANA_PROFILE.name == "sana"
    assert SANA_PROFILE.multiple == SANA_MULTIPLE
    assert SANA_PROFILE.base_multiple == SANA_MULTIPLE  # 没有分块，就不需要 64 对齐
    assert SANA_PROFILE.can_refine_tiles is False


def test_profile_can_be_selected_by_name() -> None:
    """引擎侧传的是字符串 "sana"，注册表与适配器都靠它，不能只对 dataclass 生效。"""
    by_name = resolve_output_plan(1024, 1024, profile="sana")
    by_object = _sana(1024, 1024)
    assert by_name == by_object
    assert isinstance(by_object, ImagePlan)
    assert isinstance(SANA_PROFILE, PlanProfile)


# ------------------------------------------------------------------ 目标尺寸
def test_odd_sizes_snap_to_multiple_of_32() -> None:
    """700×500 这类随手输入的宽高先就近取整到 32 的倍数，再谈预算。"""
    assert round_to_multiple(1080, SANA_MULTIPLE, SANA_MAX_SIDE) == 1088
    assert round_to_multiple(99999, SANA_MULTIPLE, SANA_MAX_SIDE) == SANA_MAX_SIDE
    plan = _sana(700, 500)
    assert (plan.target_width, plan.target_height) == (704, 512)
    assert plan.target_width % 32 == 0 and plan.target_height % 32 == 0
    # 小到不成比例的输入也照样给出一张合法图（下限就是 32 本身），而不是报错。
    tiny = _sana(13, 7)
    assert (tiny.target_width, tiny.target_height) == (32, 32)


def test_1024_square_is_single_pass() -> None:
    """默认档位 1024×1024（1.05MP）在单遍预算内：底图就等于目标，不做任何放大。"""
    plan = _sana(1024, 1024)
    assert (plan.target_width, plan.target_height) == (1024, 1024)
    assert (plan.base_width, plan.base_height) == (1024, 1024)
    assert plan.upscale_only is False
    assert plan.use_hires is False
    assert plan.target_pixels <= SANA_SINGLE_PASS_MAX_PIXELS


def test_720p_snaps_and_stays_single_pass() -> None:
    """1280×720 的高度取整到 736，取整后仍在单遍预算内，不该多花一次放大的钱。"""
    plan = _sana(1280, 720)
    assert (plan.target_width, plan.target_height) == (1280, 736)
    assert (plan.base_width, plan.base_height) == (1280, 736)
    assert plan.upscale_only is False
    assert plan.target_pixels <= SANA_SINGLE_PASS_MAX_PIXELS
    # 竖着写的同比例档位同样单遍直出。
    portrait = _sana(832, 1248)
    assert portrait.upscale_only is False
    assert (portrait.base_width, portrait.base_height) == (portrait.target_width, portrait.target_height)


# ------------------------------------------------------------------ 超预算走放大
def test_1080p_goes_through_upscale_within_base_budget() -> None:
    """1920×1080（2.07MP）单遍顶穿预算：按预算出底图，再放大到目标；底图必须落在预算内。"""
    plan = _sana(1920, 1080)
    assert (plan.target_width, plan.target_height) == (1920, 1088)
    assert plan.target_pixels > SANA_SINGLE_PASS_MAX_PIXELS
    assert plan.upscale_only is True
    # 关键：底图必须按「等比缩到预算内」算，而不是把两条边各自塞进预算。
    # 早先按 fit_within 就近取整会得到 1408×800（1.126MP，超预算）→ 沿长边退成 1344×800，
    # 比例 1.68 对目标 1.76 差 5.5%，Lanczos 放大时人脸被拉宽；现在等比取整是 1376×768。
    assert (plan.base_width, plan.base_height) == (1376, 768)
    assert plan.base_pixels <= SANA_BASE_MAX_PIXELS
    assert plan.base_width % 32 == 0 and plan.base_height % 32 == 0
    # 底图与目标同比例（偏差 <2%）：放大只做等比 + 居中裁边，不各向异性拉伸。
    assert abs(plan.base_width / plan.base_height - 1920 / 1088) / (1920 / 1088) < 0.02
    # Sana 没有分块重绘：use_hires 必须一直是 False，产物元数据才不会被写成「重绘」。
    assert plan.use_hires is False


def test_base_keeps_aspect_for_every_upscale_case() -> None:
    """所有走放大的档位，底图长宽比都必须贴住目标：偏差的来源只是 32 对齐，不是算法。

    放大这一步是 image.resize(...) 各向异性拉伸（除非等比 + 裁边），所以这里把
    「任何超预算档位的比例偏差 <2%」钉成硬规矩，改 plan 的取整方向就会先红。
    """
    for width, height in [(1920, 1080), (2048, 2048), (4096, 1024), (2560, 1440), (1080, 1920), (768, 2048)]:
        plan = _sana(width, height)
        assert plan.upscale_only is True, (width, height)
        assert plan.base_pixels <= SANA_BASE_MAX_PIXELS, (width, height)
        ratio_target = plan.target_width / plan.target_height
        assert abs(plan.base_width / plan.base_height - ratio_target) / ratio_target < 0.02, (width, height, plan)


def test_top_end_square_base_also_backs_off_to_budget() -> None:
    """2048×2048（4.19MP）刚好在目标硬上限内：等比缩到预算内是 1024×1024，比例不歪。"""
    plan = _sana(2048, 2048)
    assert (plan.target_width, plan.target_height) == (2048, 2048)
    assert plan.target_pixels <= SANA_MAX_TARGET_PIXELS
    assert plan.upscale_only is True
    # 等比缩放对正方形档位不会歪（旧算法给 1024×1056，放大回正方形时会有 3% 拉伸）。
    assert (plan.base_width, plan.base_height) == (1024, 1024)
    assert plan.base_pixels <= SANA_BASE_MAX_PIXELS


def test_sides_are_clamped_before_the_target_ceiling_rejects() -> None:
    """宽高先各自钳到 SANA_MAX_SIDE，所以 3000×3000 不会被目标硬上限判死。

    这是既有设计（SD1.5 也一样先钳 MAX_SIDE），钉住它是为了：目标硬上限只在钳位之后
    仍然失控的输入上生效，而不是把「用户手滑填了 3000」变成一次报错。
    """
    plan = _sana(3000, 3000)
    assert (plan.target_width, plan.target_height) == (2048, 2048)
    assert plan.upscale_only is True
    assert _sana(4096, 1024).target_width == 2048


# ------------------------------------------------------------------ 不回归旧档
def test_default_profile_is_still_sd15_rules() -> None:
    """不传 profile 时仍是 SD1.5 那套（8 对齐、三档高清重绘），换型没有改旧路径。"""
    plan = resolve_output_plan(1920, 1080)
    assert (plan.base_width, plan.base_height) == (896, 512)
    assert plan.use_hires is True
    assert plan.upscale_only is False
    small = resolve_output_plan(512, 512)
    assert (small.base_width, small.base_height) == (512, 512)
    assert small.use_hires is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
