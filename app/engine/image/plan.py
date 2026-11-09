"""出图分辨率档位与「峰值显存不超过 6GB」的预算规则（不 import torch）。

为什么单独一个模块：这些数字是三处共用的同一套预算——

* 引擎 app/engine/image/sd15.py（真正决定一次进 UNet 多少像素）；
* 桌面端参数面板 app/ui/widgets/param_panel.py（PIXEL_SAFE 与提示文案）；
* 网页端参数面板 app/web/static/app.js（PIXEL_SAFE 与同一句提示）。

sd15.py 依赖 torch/diffusers，桌面端 .venv 里导不进来，所以预算规则放这里，
tests/test_sd15_plan.py 才能同时核对「引擎规则 == 两端面板文案」不跑偏。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.engine.image.hires import fit_within

# SD1.5 的 VAE 是 8 倍下采样：宽高必须能被 8 整除（分块对齐后仍是 8 的倍数）。
MULTIPLE = 8
MAX_SIDE = 4096

# 单遍直出的像素上限：超过就必须走分块重绘（1152x704 这个量级实测已经贴着 6GB）。
SINGLE_PASS_MAX_PIXELS = 850_000
# 显式 hires=False（用户自己扛显存）时的硬上限：再高就不给跑，免得把预算顶穿。
HARD_SINGLE_PASS_PIXELS = 1_300_000
# 高清重绘能接的最大画布：再往上块数与耗时都失控，属于误输入。
HIRES_MAX_PIXELS = 25_000_000
# 分块重绘时底图的像素上限：底图太小放大后细节不够，0.46MP 是「1080p 够看」与
# 「底图这一遍也稳稳在预算内」之间的折中。
BASE_MAX_PIXELS = 460_000
# 分块重绘的去噪强度：太低等于纯放大（发糊），太高会每块各画各的（接缝、结构错位）。
HIRES_STRENGTH = 0.32


class PlanBudgetError(RuntimeError):
    """分辨率超出显存预算；由调用方换成引擎自己的异常类型。"""


@dataclass(frozen=True)
class ImagePlan:
    """一次出图的完整档位：最终尺寸、底图尺寸、是否走分块高清重绘。"""

    target_width: int
    target_height: int
    base_width: int
    base_height: int
    use_hires: bool

    @property
    def target_pixels(self) -> int:
        return self.target_width * self.target_height

    @property
    def base_pixels(self) -> int:
        return self.base_width * self.base_height


def round_to_multiple(value: int, multiple: int = MULTIPLE, max_side: int = MAX_SIDE) -> int:
    """把尺寸就近取整到 multiple 的倍数，并限制在 [multiple, max_side]。"""
    value = max(multiple, min(int(value), max_side))
    rounded = (value // multiple) * multiple
    if value % multiple >= multiple / 2:
        rounded += multiple
    return max(multiple, min(rounded, max_side))


def resolve_output_plan(width: int, height: int, hires: Optional[bool] = None) -> ImagePlan:
    """按目标宽高决定「单遍直出」还是「底图 + 分块高清重绘」。

    hires=None（默认）＝按像素自动判断；True＝强制重绘（小图也走一遍，等于自带锐化）；
    False＝强制单遍，此时超过 HARD_SINGLE_PASS_PIXELS 直接报错，不偷偷降画质。
    """
    target_w = round_to_multiple(width)
    target_h = round_to_multiple(height)
    pixels = target_w * target_h
    use_hires = (pixels > SINGLE_PASS_MAX_PIXELS) if hires is None else bool(hires)
    if not use_hires:
        if pixels > HARD_SINGLE_PASS_PIXELS:
            raise PlanBudgetError(
                f"目标 {target_w}x{target_h}（{pixels / 1e6:.2f}MP）单遍直出会超显存预算："
                f"超过约 {SINGLE_PASS_MAX_PIXELS / 1e6:.2f}MP 请走高清重绘（默认即开），"
                f"显式关闭时上限 {HARD_SINGLE_PASS_PIXELS / 1e6:.2f}MP。"
            )
        return ImagePlan(target_w, target_h, target_w, target_h, False)
    if pixels > HIRES_MAX_PIXELS:
        raise PlanBudgetError(
            f"目标 {target_w}x{target_h}（{pixels / 1e6:.1f}MP）超过高清重绘上限 "
            f"{HIRES_MAX_PIXELS / 1e6:.1f}MP：分块数量与耗时都会失控，请降一档分辨率。"
        )
    base_w, base_h = fit_within(target_w, target_h, BASE_MAX_PIXELS, 64)
    return ImagePlan(target_w, target_h, base_w, base_h, True)
