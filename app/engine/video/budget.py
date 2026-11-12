"""Wan2.1 视频/单帧档的「档位账」——纯标准库，三端共用。

为什么单独一个模块：6GB 显存档位下真正决定峰值的是 DiT 的 token 数
（压缩帧数 × 宽/16 × 高/16），这个数以前只在引擎里算，而且超预算时**自动把
画幅缩小**，界面上完全看不见——用户选了 832×480，产物却是 624×352，只有一行
stderr 日志。现在规则改成：档位钉死，超预算就在提交前挡下，并把这一档的真实
上限用具体数字讲清楚；引擎侧同样不再自动降档，越线直接报错。

这个模块不 import torch，所以桌面 UI、Web 服务端、引擎子进程都能共用同一套
数字，不会出现「界面说 49 帧、引擎按 93 帧算」这种两端不一致。

对应关系（改一处必须同步 app/web/static/app.js 顶部同名常量）：
    MULTIPLE / FPS / MAX_PIXELS / MAX_TOKENS / MIN_FRAMES / MAX_FRAMES
桌面端 app/ui/widgets/param_panel.py 不在本地重算这些数，直接 import 本模块的
MAX_PIXELS / MAX_TOKENS（别名 VIDEO_MAX_PIXELS / MAX_TOKENS），所以这条像素线
全仓只有一份真数 = 459264；tests/test_mode_theme.py 会同时读这三处钉住同值。
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Optional, Tuple

# 尺寸必须可被 vae_scale_factor_spatial(8) * patch(2) = 16 整除
MULTIPLE = 16
# Wan 原生帧率与 1.3B 档像素上限（480p 级：832x480，留 1.15 倍余量）
FPS = 16
# 像素上限：**必须用整数运算**。写成 int(832*480*1.15) 会得到 459263——1.15 在二进制
# 浮点里略小于 1.15，399360*1.15 = 459263.99999999994，int() 直接砍掉小数。以前注释写
# 459264、代码跑出来 459263，前端照抄 459263、桌面端又另设 459000 的「预警线」，三个数
# 就是这么分叉的。下面这个写法保证「注释里的数 = 运行时的数 = 前端那个数」= 459264。
MAX_PIXELS = 832 * 480 * 115 // 100  # 459264
MIN_FRAMES, MAX_FRAMES = 5, 161
# 6GB 显存铁律下真正的驱动量是 DiT 的 token 数：latent 帧数 × (宽/16) × (高/16)。
# 20000 tokens 这个上限是从「512×384 / 33 帧实测 2021MB」和「约 10k tokens ≈ 5GB」
# 两个点外推来的，20k 这一点本身没实测过；实测脚本的「越线探测」档（停止线临时抬到
# 10000MB，跑 832×480 / 93 帧 = 39780 tokens）就是用来补这个高端锚点的。
MAX_TOKENS = 20_000

BUDGET_LABEL = "6GB 显存档位"


def round_to_multiple(value: Any, multiple: int = MULTIPLE, cap: int = 2048) -> int:
    """把尺寸就近取整到 multiple 的倍数，并限制在 [multiple*8, cap]。"""
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = multiple * 8
    number = max(multiple * 8, min(number, cap))
    return round(number / multiple) * multiple


def snap_frames(value: Any, default: int = 33) -> int:
    """Wan 时序 VAE 要求帧数为 4n+1；就近取整并夹在 [MIN_FRAMES, MAX_FRAMES]。"""
    try:
        frames = int(value)
    except (TypeError, ValueError):
        frames = int(default)
    frames = max(MIN_FRAMES, min(frames, MAX_FRAMES))
    snapped = ((frames - 1) // 4) * 4 + 1
    if (
        frames % 4 != 1
        and snapped + 4 <= MAX_FRAMES
        and (snapped + 4 - frames) <= (frames - snapped)
    ):
        snapped += 4
    return snapped


def latent_frames(num_frames: int) -> int:
    """Wan 的时间压缩是 4x：81 帧 → 21 个 latent 帧。"""
    return (int(num_frames) - 1) // 4 + 1


def frame_seconds(num_frames: int) -> float:
    return round(int(num_frames) / FPS, 2)


def tokens_for(width: int, height: int, num_frames: int) -> int:
    """该档位在该帧数下的 DiT token 数（决定显存峰值的那个量）。"""
    latent = latent_frames(num_frames)
    return max(1, (int(width) // MULTIPLE) * (int(height) // MULTIPLE) * latent)


def max_frames_for(width: int, height: int) -> int:
    """这一档分辨率在 token 预算内最长能跑到第几帧（4n+1；跑不动则 0）。"""
    per_latent = max(1, (int(width) // MULTIPLE) * (int(height) // MULTIPLE))
    latent = MAX_TOKENS // per_latent
    if latent < 1:
        return 0
    frames = (latent - 1) * 4 + 1
    return min(frames, MAX_FRAMES)


def fit_pixels(width: int, height: int) -> Tuple[int, int]:
    """按请求比例缩到不超过 MAX_PIXELS（1.3B 是 480p 档模型，只降规模不改比例）。

    这个函数只用来**报数**（「这一比例最大能到多少」）与图生视频按首帧比例推
    算目标尺寸；不再被用来悄悄改用户选的档位——超预算一律挡下。
    """
    w, h = round_to_multiple(width), round_to_multiple(height)
    if w * h > MAX_PIXELS:
        scale = (MAX_PIXELS / (w * h)) ** 0.5
        w = max(MULTIPLE, round_to_multiple(w * scale))
        h = max(MULTIPLE, round_to_multiple(h * scale))
        while w * h > MAX_PIXELS:
            if w >= h:
                w = max(MULTIPLE, w - MULTIPLE)
            else:
                h = max(MULTIPLE, h - MULTIPLE)
    return w, h


def max_size_for(num_frames: int, width: int, height: int) -> Tuple[int, int]:
    """保持当前比例，把画幅缩到 token 预算内允许的最大格子数（格子 = 16px 一边）。

    先按面积公式解出长边格子数，再按原比例求短边，最后夹回预算——这样报出来的
    「最大可用档位」和用户选的档位是同一比例，不会出现 832×480 推荐 432×480 这种走形数字。
    """
    latent = latent_frames(num_frames)
    budget_cells = max(1, MAX_TOKENS // latent)
    w_cells = max(1, max(MULTIPLE, round_to_multiple(width)) // MULTIPLE)
    h_cells = max(1, max(MULTIPLE, round_to_multiple(height)) // MULTIPLE)
    if w_cells * h_cells <= budget_cells:
        return w_cells * MULTIPLE, h_cells * MULTIPLE
    ratio = h_cells / w_cells
    w_cells = max(1, int(math.floor(math.sqrt(budget_cells / ratio))))
    h_cells = max(1, int(round(w_cells * ratio)))
    while w_cells * h_cells > budget_cells and min(w_cells, h_cells) > 1:
        if w_cells >= h_cells:
            w_cells -= 1
        else:
            h_cells -= 1
    # 面积还有富余时按同一比例再放大一档，免得白亏分辨率。
    while (
        max(1, int(round((w_cells + 1) * 1.0))) * max(1, int(round((w_cells + 1) * ratio)))
        <= budget_cells
    ):
        w_cells += 1
        h_cells = max(1, int(round(w_cells * ratio)))
    return w_cells * MULTIPLE, h_cells * MULTIPLE


def evaluate(width: Any, height: Any, num_frames: Any) -> dict[str, Any]:
    """把「用户选的档位」折算成 token/像素，返回判定与可显示的具体数字。

    ok=True 表示这个组合就在 6GB 档位预算内，引擎会按所选档位原样出片。
    """
    w = int(width or 0)
    h = int(height or 0)
    frames = snap_frames(num_frames)
    pixels = max(0, w * h)
    tokens = tokens_for(w, h, frames) if w and h else 0
    size_cap_w, size_cap_h = max_size_for(frames, w, h) if w and h else (0, 0)
    frames_cap = max_frames_for(w, h) if w and h else 0
    over_pixels = pixels > MAX_PIXELS
    over_tokens = tokens > MAX_TOKENS
    return {
        "ok": not (over_pixels or over_tokens),
        "width": w,
        "height": h,
        "frames": frames,
        "seconds": frame_seconds(frames),
        "pixels": pixels,
        "tokens": tokens,
        "max_tokens": MAX_TOKENS,
        "max_pixels": MAX_PIXELS,
        "frames_cap": frames_cap,
        "frames_cap_seconds": frame_seconds(frames_cap) if frames_cap else 0.0,
        "size_cap": [size_cap_w, size_cap_h],
        "over_pixels": over_pixels,
        "over_tokens": over_tokens,
    }


def explain_over(report: dict[str, Any], mode_label: str = "视频") -> str:
    """超预算时给用户的中文说明：具体数字 + 两条可选出路，不承诺任何自动降档。"""
    w, h, frames = report["width"], report["height"], report["frames"]
    if report["over_pixels"]:
        cap_w, cap_h = fit_pixels(w, h)
        return (
            f"{mode_label} {w}×{h} 超出 Wan2.1 1.3B 的 480p 像素上限 {MAX_PIXELS}px"
            f"（约 0.46MP）：本工具不会自动降档。这一比例最大可用 {cap_w}×{cap_h}，"
            f"请改到该尺寸或更小再提交。"
        )
    cap_frames = report["frames_cap"]
    cap_w, cap_h = report["size_cap"]
    parts = [
        f"{mode_label} {w}×{h} @ {frames} 帧需要 {report['tokens']} tokens，"
        f"超过 {BUDGET_LABEL}的 {MAX_TOKENS} tokens 上限（这就是 6GB 峰值的驱动量）。",
        f"保持 {w}×{h}：最长 {cap_frames} 帧（{frame_seconds(cap_frames)} 秒 @{FPS}fps）。"
        if cap_frames >= MIN_FRAMES
        else f"保持 {w}×{h}：连最低 {MIN_FRAMES} 帧都放不下，只能降分辨率。",
        f"保持 {frames} 帧（{report['seconds']} 秒）：最大只能 {cap_w}×{cap_h}。",
        "不会自动降档，也不会偷改帧数：请二选一后重新提交。",
    ]
    return " ".join(part for part in parts if part)


def read_image_size(path: Any) -> Optional[Tuple[int, int]]:
    """只读文件头拿 PNG/JPEG/BMP/GIF 的宽高；解析不出来返回 None（不猜）。

    Web 服务端不能 import PIL（纯标准库进程），这里用它给图生视频算出
    「按首帧比例的真实目标尺寸」，好让提交前的提示报的是引擎真正会用的数。
    """
    try:
        file_path = Path(str(path))
        with file_path.open("rb") as handle:
            head = handle.read(32)
            if len(head) < 24:
                return None
            if head.startswith(b"\x89PNG\r\n\x1a\n") and head[12:16] == b"IHDR":
                return (int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big"))
            if head[:6] in (b"GIF87a", b"GIF89a"):
                return (int.from_bytes(head[6:8], "little"), int.from_bytes(head[8:10], "little"))
            if head[:2] == b"BM":
                return (
                    int.from_bytes(head[18:22], "little", signed=True),
                    abs(int.from_bytes(head[22:26], "little", signed=True)),
                )
            if head[:2] == b"\xff\xd8":
                handle.seek(2)
                while True:
                    marker = handle.read(1)
                    if not marker or marker != b"\xff":
                        return None
                    code = handle.read(1)
                    if not code:
                        return None
                    if code in (b"\xc0", b"\xc1", b"\xc2", b"\xc3", b"\xc5", b"\xc6", b"\xc7",
                                b"\xc9", b"\xca", b"\xcb", b"\xcd", b"\xce", b"\xcf"):
                        segment = handle.read(7)
                        if len(segment) < 7:
                            return None
                        return (int.from_bytes(segment[5:7], "big"), int.from_bytes(segment[3:5], "big"))
                    if code in (b"\xd8", b"\x01") or b"\xd0" <= code <= b"\xd7":
                        continue
                    size = handle.read(2)
                    if len(size) < 2:
                        return None
                    step = int.from_bytes(size, "big")
                    if step < 2:
                        return None
                    handle.seek(step - 2, 1)
    except (OSError, ValueError):
        return None
    return None


def derive_i2v_size(ref_path: Any, fallback_width: Any, fallback_height: Any) -> dict[str, Any]:
    """图生视频的目标尺寸按首帧比例推（与引擎里的算法同一口径）。

    拿不到首帧尺寸时退回面板上的档位，并在 note 里说清楚是推不出来的。
    """
    size = read_image_size(ref_path)
    if not size:
        return {
            "width": round_to_multiple(fallback_width),
            "height": round_to_multiple(fallback_height),
            "derived": False,
        }
    iw, ih = size
    budget = MAX_PIXELS / 1.15
    scale = (budget / max(1, iw * ih)) ** 0.5
    width, height = fit_pixels(int(iw * scale), int(ih * scale))
    return {"width": width, "height": height, "derived": True, "ref_size": [iw, ih]}
