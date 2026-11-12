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

# ---- Sana（DC-AE 32 倍下采样）这一档的预算 ----
# 为什么不能沿用 SD1.5 那组数字：DC-AE 把空间压 32 倍，宽高必须是 32 的倍数（8 的倍数会
# 让 latent 尺寸不整）；而 1.6B 的 Linear-DiT 在 1024 级画幅上激活很小，单遍能直出 1024×1024，
# 比 SD1.5 的 0.85MP 宽一大截。反过来，Sana 在 diffusers 0.41 里没有 img2img，做不了分块
# 重绘，所以超过单遍预算只能「预算内出底图 + Lanczos 放大」，上限 2048 级（再高就是纯插值，没意义）。
SANA_MULTIPLE = 32
SANA_MAX_SIDE = 2_048
SANA_SINGLE_PASS_MAX_PIXELS = 1_100_000   # 1024×1024 与 1152×896 / 1280×832 这类档位都放得下
SANA_BASE_MAX_PIXELS = 1_100_000          # 需要放大时，底图按这个像素预算就近取整
SANA_MAX_TARGET_PIXELS = 4_200_000        # 2048×2048：再高就是纯插值放大，属于误输入


@dataclass(frozen=True)
class PlanProfile:
    """一套分辨率预算。SD1.5 与 Sana 共用同一个判断函数，只是数字与「放大方式」不同。"""

    name: str
    multiple: int
    max_side: int
    single_pass_max_pixels: int
    base_max_pixels: int
    max_target_pixels: int
    # True = 超过单遍预算走「底图 + 分块重绘」（SD1.5 有 img2img 才能做）；
    # False = 只能「底图 +  Lanczos 放大」（Sana 没有 img2img，如实标注，不谎称重绘）。
    can_refine_tiles: bool
    # 底图的对齐倍数：SD1.5 分块重绘要求 64 对齐，Sana 只要 DC-AE 的 32 对齐。
    base_multiple: int = 64


SD15_PROFILE = PlanProfile(
    name="sd15",
    multiple=MULTIPLE,
    max_side=MAX_SIDE,
    single_pass_max_pixels=SINGLE_PASS_MAX_PIXELS,
    base_max_pixels=BASE_MAX_PIXELS,
    max_target_pixels=HIRES_MAX_PIXELS,
    can_refine_tiles=True,
)

SANA_PROFILE = PlanProfile(
    name="sana",
    multiple=SANA_MULTIPLE,
    max_side=SANA_MAX_SIDE,
    single_pass_max_pixels=SANA_SINGLE_PASS_MAX_PIXELS,
    base_max_pixels=SANA_BASE_MAX_PIXELS,
    max_target_pixels=SANA_MAX_TARGET_PIXELS,
    can_refine_tiles=False,
    base_multiple=SANA_MULTIPLE,
)

_PROFILES = {"sd15": SD15_PROFILE, "sana": SANA_PROFILE}


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
    # True = 这次是「预算内出底图 + 纯放大」（Sana 走这条，没有重绘可言）。
    upscale_only: bool = False

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


def _floor_to_multiple(value: float, multiple: int) -> int:
    """向下取整到 multiple 的倍数（Sana 底图专用）。

    和 round_to_multiple 的区别只在方向：底图面积必须 ≤ 预算，所以只能往下取，
    往上取整就会把峰值显存的估算顶出预算（1408x800 = 1.126MP > 1.10MP 就是这么来的）。
    """
    return max(multiple, int(value // multiple) * multiple)


def resolve_output_plan(
    width: int,
    height: int,
    hires: Optional[bool] = None,
    profile: "PlanProfile | str" = SD15_PROFILE,
) -> ImagePlan:
    """按目标宽高决定「单遍直出」还是「底图 + 放大/重绘」，数字按 profile 取。

    hires=None（默认）＝按像素自动判断；True＝强制重绘（小图也走一遍，等于自带锐化）；
    False＝强制单遍，此时超过 HARD_SINGLE_PASS_PIXELS 直接报错，不偷偷降画质。
    profile 传 "sd15"/"sana" 或 PlanProfile 对象：Sana 档没有 img2img，超过单遍预算时
    只能纯放大（ImagePlan.upscale_only=True），界面与元数据必须照实写，不能写成「高清重绘」。
    """
    prof = _PROFILES[profile] if isinstance(profile, str) else profile
    target_w = round_to_multiple(width, prof.multiple, prof.max_side)
    target_h = round_to_multiple(height, prof.multiple, prof.max_side)
    pixels = target_w * target_h

    if not prof.can_refine_tiles:
        if pixels > prof.max_target_pixels:
            raise PlanBudgetError(
                f"目标 {target_w}x{target_h}（{pixels / 1e6:.2f}MP）超过 {prof.name} 档上限 "
                f"{prof.max_target_pixels / 1e6:.2f}MP：再往上就是纯插值放大，没有意义，请降一档。"
            )
        if pixels <= prof.single_pass_max_pixels:
            return ImagePlan(target_w, target_h, target_w, target_h, False)
        # 底图按「等比缩到预算内」算，而不是把两条边各自塞进预算。
        # 这条链路最后是 Lanczos 把底图 resize 到目标（app/engine/image/sana.py 里的
        # image.resize((target_w, target_h), LANCZOS)），两条边一旦按不同比例取整，
        # 放大出来就会拉伸变形：实测「各自塞进预算」会把 1920x1080 给成 1344x800
        # （比例 1.68，目标 1.76，偏差 5.5%，人脸明显被拉宽）。
        # 等比缩放后向下取整到 base_multiple，面积只会更小，峰值显存按底图估算因此是安全的；
        # 1920x1080 现在得到 1376x768（1.06MP，偏差 1.5%），2048x2048 得到 1024x1024（偏差 0）。
        scale = (prof.base_max_pixels / pixels) ** 0.5
        base_w = _floor_to_multiple(target_w * scale, prof.base_multiple)
        base_h = _floor_to_multiple(target_h * scale, prof.base_multiple)
        # 兜底：浮点误差刚好压线时再退一档，宁可少几个像素也不能超预算。
        while base_w * base_h > prof.base_max_pixels:
            if base_w >= base_h:
                base_w = _floor_to_multiple(base_w - prof.base_multiple, prof.base_multiple)
            else:
                base_h = _floor_to_multiple(base_h - prof.base_multiple, prof.base_multiple)
        return ImagePlan(target_w, target_h, base_w, base_h, False, upscale_only=True)

    use_hires = (pixels > prof.single_pass_max_pixels) if hires is None else bool(hires)
    if not use_hires:
        if pixels > HARD_SINGLE_PASS_PIXELS:
            raise PlanBudgetError(
                f"目标 {target_w}x{target_h}（{pixels / 1e6:.2f}MP）单遍直出会超显存预算："
                f"超过约 {prof.single_pass_max_pixels / 1e6:.2f}MP 请走高清重绘（默认即开），"
                f"显式关闭时上限 {HARD_SINGLE_PASS_PIXELS / 1e6:.2f}MP。"
            )
        return ImagePlan(target_w, target_h, target_w, target_h, False)
    if pixels > prof.max_target_pixels:
        raise PlanBudgetError(
            f"目标 {target_w}x{target_h}（{pixels / 1e6:.1f}MP）超过高清重绘上限 "
            f"{prof.max_target_pixels / 1e6:.1f}MP：分块数量与耗时都会失控，请降一档分辨率。"
        )
    base_w, base_h = fit_within(target_w, target_h, prof.base_max_pixels, prof.base_multiple)
    return ImagePlan(target_w, target_h, base_w, base_h, True)
