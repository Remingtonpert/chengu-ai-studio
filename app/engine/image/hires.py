"""分块高清重绘（tiled hires fix）的纯几何与混合工具。

为什么需要它：1920×1080 单张直接送进 UNet，激活显存按像素数线性涨，实测会顶到
6-8GB，超出「峰值不超过 6GB」这条硬预算。所以大图最后一遍切成 512×512 的小块分别
重绘再拼回去——每次进 UNet 的只有 512×512，峰值与出 512 小图时一样（约 3-4GB），
不管最终是 1080p 还是 2K 都不会涨。

本模块只算「切几块、每块在哪、怎么无缝拼回」，不 import torch，因此桌面端 .venv
（没有 torch）也能直接单测；模型调用全部留在 sd15.py 里。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

import PIL.Image

try:  # 桌面端 .venv 里没有 numpy：几何工具（切几块、每块在哪）仍然可用，
    # 只有真正混合像素时才需要它，所以这里允许导入失败、用到的地方明确报错。
    import numpy as np
except ImportError:  # pragma: no cover - 取决于所在 venv
    np = None

# 分块尺寸与重叠带：块太小则每块都要重跑一遍文本编码与模块换入换出（慢），
# 太大则峰值回升；512 与 SD1.5 的训练分辨率一致，重叠 128 足够盖住接缝。
DEFAULT_TILE = 512
DEFAULT_OVERLAP = 128


def _require_numpy(function_name: str) -> None:
    if np is None:  # pragma: no cover - 取决于所在 venv
        raise RuntimeError(
            f"{function_name} 需要 numpy 才能混合像素；请使用图像引擎环境 "
            "（.venv-image）运行出图任务，桌面端只做几何估算。"
        )


@dataclass(frozen=True)
class TileSpec:
    """一块在最终大图坐标系里的位置（左上角 + 尺寸）。"""

    x: int
    y: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height


def fit_within(
    width: int,
    height: int,
    max_pixels: int,
    multiple: int = 64,
) -> Tuple[int, int]:
    """把尺寸等比缩到 max_pixels 以内，并取整到 multiple 的倍数。

    SD1.5 的 VAE 按 8 倍下采样，UNet 要求宽高可被 8 整除；用 64 的倍数是因为
    分块时最后一块会贴右/贴下边对齐，64 的倍数能保证块边界仍落在 8 的倍数上。
    """
    width = max(multiple, int(width))
    height = max(multiple, int(height))
    pixels = width * height
    if pixels <= max_pixels:
        return width - (width % multiple) or multiple, height - (height % multiple) or multiple
    scale = (max_pixels / pixels) ** 0.5
    new_w = max(multiple, int(round(width * scale / multiple)) * multiple)
    new_h = max(multiple, int(round(height * scale / multiple)) * multiple)
    return new_w, new_h


def _axis_positions(length: int, tile: int, overlap: int) -> List[int]:
    """一条轴上的块起点（最后一块贴边对齐，避免留下没有归属的窄边）。"""
    tile = max(8, min(int(tile), int(length)))
    overlap = max(0, min(int(overlap), tile - 8))
    if length <= tile:
        return [0]
    step = max(8, tile - overlap)
    positions = list(range(0, length - tile + 1, step))
    if positions[-1] + tile < length:
        positions.append(length - tile)
    deduped: List[int] = []
    for pos in positions:
        if not deduped or pos != deduped[-1]:
            deduped.append(pos)
    return deduped


def tile_grid(
    width: int,
    height: int,
    tile: int = DEFAULT_TILE,
    overlap: int = DEFAULT_OVERLAP,
) -> List[TileSpec]:
    """把 width×height 的画布切成带重叠的块（行优先）。"""
    xs = _axis_positions(width, tile, overlap)
    ys = _axis_positions(height, tile, overlap)
    tw = min(int(tile), width)
    th = min(int(tile), height)
    return [TileSpec(x, y, tw, th) for y in ys for x in xs]


def feather_mask(width: int, height: int, ramp: int = DEFAULT_OVERLAP) -> np.ndarray:
    """生成 float32 线性羽化权重掩膜（0..1）：边缘越靠外权重越低，接缝处两边相加约等于 1。"""
    _require_numpy("feather_mask")
    width = max(1, int(width))
    height = max(1, int(height))
    ramp = max(1, min(int(ramp), min(width, height)))
    xs = np.minimum(np.arange(width, dtype=np.float32), np.arange(width - 1, -1, -1, dtype=np.float32))
    ys = np.minimum(np.arange(height, dtype=np.float32), np.arange(height - 1, -1, -1, dtype=np.float32))
    wx = np.clip(xs / ramp, 0.0, 1.0)
    wy = np.clip(ys / ramp, 0.0, 1.0)
    # 中间平台段给 1，边缘线性过渡；再加一个下限，防止整块权重为 0。
    mask = np.outer(wy, wx)
    return np.maximum(mask, 1e-3).astype(np.float32)


def upscale_lanczos(image: PIL.Image.Image, width: int, height: int) -> PIL.Image.Image:
    """把底图放大到目标尺寸（确定性、无权重、零显存）。"""
    return image.convert("RGB").resize((int(width), int(height)), PIL.Image.LANCZOS)


class TileBlender:
    """把重绘好的块按羽化权重加权平均拼成大图。"""

    def __init__(self, width: int, height: int) -> None:
        _require_numpy("TileBlender")
        self.width = int(width)
        self.height = int(height)
        self._acc = np.zeros((self.height, self.width, 3), dtype=np.float32)
        self._weight = np.zeros((self.height, self.width), dtype=np.float32)
        self._pasted = 0

    def paste(self, tile: PIL.Image.Image, spec: TileSpec, mask: np.ndarray | None = None) -> None:
        arr = np.asarray(tile.convert("RGB"), dtype=np.float32)
        if arr.shape[0] != spec.height or arr.shape[1] != spec.width:
            # 模型侧有时会返回与请求差 8 像素的结果（VAE 取整），就近缩回格子尺寸，
            # 不至于把整张拼图崩掉。
            arr = np.asarray(
                PIL.Image.fromarray(arr.astype(np.uint8)).resize((spec.width, spec.height), PIL.Image.LANCZOS),
                dtype=np.float32,
            )
        if spec.right > self.width or spec.bottom > self.height:
            raise ValueError(
                f"块超出画布：块({spec.x},{spec.y},{spec.width}x{spec.height}) "
                f"画布({self.width}x{self.height})"
            )
        if mask is None:
            mask = feather_mask(spec.width, spec.height)
        mask = mask[: spec.height, : spec.width]
        region = self._acc[spec.y : spec.bottom, spec.x : spec.right]
        region += arr * mask[:, :, None]
        self._weight[spec.y : spec.bottom, spec.x : spec.right] += mask
        self._pasted += 1

    def result(self) -> PIL.Image.Image:
        if not self._pasted:
            raise ValueError("TileBlender 还没有拼进任何块")
        safe = np.where(self._weight > 0, self._weight, 1.0)[:, :, None]
        out = self._acc / safe
        out = np.clip(out, 0.0, 255.0).astype(np.uint8)
        return PIL.Image.fromarray(out, "RGB")

    @property
    def pasted_tiles(self) -> int:
        return self._pasted


def count_tiles(
    width: int,
    height: int,
    tile: int = DEFAULT_TILE,
    overlap: int = DEFAULT_OVERLAP,
) -> int:
    """估算分块数量（参数面板给出「大概要多久」时用得上）。"""
    return len(tile_grid(width, height, tile, overlap))
