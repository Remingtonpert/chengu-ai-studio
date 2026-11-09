"""分块高清重绘（app/engine/image/hires.py）的几何与拼接单元测试。

为什么值得单独测：1080p/2K 走「底图 → Lanczos 放大 → 分块重绘」是 6GB 显存预算的
唯一出路，块切错一格就会表现为「拼回去少一条边」或「接缝处错位」，而这类几何错在
真实出图里要跑十几分钟才看得见。所以这里只算几何，不碰模型。

桌面 .venv 没有 numpy：几何部分（切几块、每块在哪、放大）在这里真跑；
真正混合像素的部分（feather_mask / TileBlender）需要 numpy，按 hires.np 是否为 None
标记 skip——在 .venv 里是 skip，在 .venv-image 里会实跑并验证「重叠区取平均」这类不变量。
"""

from __future__ import annotations

import pytest
from PIL import Image

from app.engine.image import hires
from app.engine.image.hires import (
    DEFAULT_OVERLAP,
    DEFAULT_TILE,
    TileBlender,
    TileSpec,
    _axis_positions,
    count_tiles,
    fit_within,
    tile_grid,
    upscale_lanczos,
)

# 混合像素需要 numpy：桌面 .venv 导不到，图像引擎环境（.venv-image）里才实跑。
needs_numpy = pytest.mark.skipif(
    hires.np is None,
    reason="桌面 .venv 没有 numpy：像素混合需要在 .venv-image 里跑（hires.np is None）",
)


def _fully_covered(specs: list[TileSpec], width: int, height: int) -> tuple[bool, int, int]:
    """逐条扫描线把落在该行的块按 x 区间合并，判断每个像素是否至少被一块覆盖。

    返回 (是否铺满, 首个没盖住的扫描线, 该行覆盖到的最右像素)，失败时能直接说出是哪一行断的。
    """
    for y in range(height):
        spans = sorted((spec.x, spec.right) for spec in specs if spec.y <= y < spec.bottom)
        reach = 0
        for left, right in spans:
            if left > reach:
                return False, y, reach
            reach = max(reach, right)
            if reach >= width:
                break
        if reach < width:
            return False, y, reach
    return True, -1, -1


# ---------------------------------------------------------------------- fit_within
def test_fit_within_keeps_aspect_ratio_and_caps_pixels() -> None:
    """等比缩到预算内：1920×1080 → 896×512，比例不变、像素不越线。"""
    width, height = fit_within(1920, 1080, 460_000)
    assert width * height <= 460_000
    assert abs(width / height - 1920 / 1080) < 0.06
    assert width <= 1920 and height <= 1080
    assert (width, height) == (896, 512)


def test_fit_within_aligns_both_sides_to_64() -> None:
    """两边都取整到 64 的倍数：分块贴边后块边界仍是 VAE 要求的 8 的倍数。"""
    for source in ((500, 300), (1000, 700), (1919, 1079), (576, 1024)):
        width, height = fit_within(source[0], source[1], 10_000_000)
        assert width % 64 == 0 and height % 64 == 0, source
        assert width >= 64 and height >= 64, source
    # 已经小于预算也照样对齐：500×300 → 448×256（不是原样返回）。
    assert fit_within(500, 300, 10_000_000) == (448, 256)


def test_fit_within_leaves_cheap_canvas_alone() -> None:
    """本来就在预算内（512×512）就只对齐不缩放。"""
    assert fit_within(512, 512, 460_000) == (512, 512)


def test_fit_within_never_returns_side_below_multiple() -> None:
    """极端误输入（10×10 且预算只有 1000 像素）也不给出一边小于 64 的档位。"""
    width, height = fit_within(10, 10, 1000)
    assert width >= 64 and height >= 64
    assert width % 64 == 0 and height % 64 == 0


# ------------------------------------------------------------------ _axis_positions
def test_axis_positions_last_tile_touches_the_edge() -> None:
    """最后一块必须贴边：否则会留下一条没有块归属的窄边（1080p 右/下各少 64px）。"""
    for length in (1920, 1080, 1152, 576):
        positions = _axis_positions(length, DEFAULT_TILE, DEFAULT_OVERLAP)
        assert positions[0] == 0, length
        assert positions[-1] + DEFAULT_TILE == length, (length, positions)
        assert all(b > a for a, b in zip(positions, positions[1:])), (length, positions)
        # 相邻两块的重叠不得小于重叠带以外的程度，也不能留下超过一块宽的洞。
        assert all(b - a <= DEFAULT_TILE - DEFAULT_OVERLAP for a, b in zip(positions, positions[1:])), length


def test_axis_positions_single_tile_when_axis_is_short() -> None:
    """短边不足一块：只给一块起点 0，块尺寸后面由 tile_grid 钳到画布大小。"""
    assert _axis_positions(400, DEFAULT_TILE, DEFAULT_OVERLAP) == [0]
    assert _axis_positions(DEFAULT_TILE, DEFAULT_TILE, DEFAULT_OVERLAP) == [0]


def test_axis_positions_exact_multiple_does_not_add_extra_tile() -> None:
    """896 = 512 + 384 刚好落边：不该再补一块起点相同的重复块（重复块会让接缝处权重翻倍）。"""
    assert _axis_positions(896, 512, 128) == [0, 384]


# -------------------------------------------------------------------- tile_grid
def test_tile_grid_stays_inside_the_canvas() -> None:
    """每块都在画布内：越界块会被 TileBlender.paste 判成「块超出画布」。"""
    specs = tile_grid(1920, 1080)
    assert specs, "1080p 至少要切出块"
    for spec in specs:
        assert spec.x >= 0 and spec.y >= 0
        assert spec.right <= 1920 and spec.bottom <= 1080
        assert spec.width == DEFAULT_TILE and spec.height == DEFAULT_TILE
    # 1080 不是 512 的整数倍：纵向最后一块贴下边，正好落在 1080。
    assert max(spec.bottom for spec in specs) == 1080
    assert max(spec.right for spec in specs) == 1920


def test_tile_grid_covers_every_pixel() -> None:
    """铺满检查：1024×576 的每条扫描线上，块区间合并后必须从 0 覆盖到宽边。"""
    width, height = 1024, 576
    specs = tile_grid(width, height)
    covered, bad_row, reach = _fully_covered(specs, width, height)
    assert covered, f"第 {bad_row} 行只覆盖到 x={reach}，右侧留下没归属的像素"


def test_tile_grid_on_real_output_sizes_covers_everything() -> None:
    """把真实出图档位全过一遍：任何一枚都不得留洞、不得越界。

    前一组是现面板（Wan 480p 档）的尺寸，后一组走高清重绘路径。旧版本这里直接 import
    参数面板的六枚档位对拍；面板重写成 Wan 档位后符号已退场，就地把名单写死，
    对拍的是几何本身而不是面板源码。
    """
    real_sizes = (
        (832, 480), (480, 832), (624, 624), (640, 480), (480, 640),  # 现面板：Wan 480p 档
        (1024, 576), (1920, 1080), (2048, 1152),  # 高清重绘路径的常见画幅
    )
    for width, height in real_sizes:
        specs = tile_grid(width, height)
        covered, bad_row, reach = _fully_covered(specs, width, height)
        assert covered, (width, height, bad_row, reach)
        assert all(spec.right <= width and spec.bottom <= height for spec in specs), (width, height)


def test_tile_grid_clamps_tile_to_canvas_for_small_canvas() -> None:
    """画布比一块还小（256×128）：块尺寸钳到画布，不产生负起点。"""
    specs = tile_grid(256, 128)
    assert len(specs) == 1
    spec = specs[0]
    assert (spec.x, spec.y) == (0, 0)
    assert (spec.width, spec.height) == (256, 128)
    assert (spec.right, spec.bottom) == (256, 128)


def test_count_tiles_equals_len_of_tile_grid() -> None:
    """面板上「大概几块」的估算与实际网格必须同一个数（少报就是误导用户）。"""
    for width, height in ((512, 512), (1024, 576), (1920, 1080), (2048, 1152), (896, 512)):
        for tile, overlap in ((DEFAULT_TILE, DEFAULT_OVERLAP), (512, 256), (256, 64)):
            assert count_tiles(width, height, tile, overlap) == len(tile_grid(width, height, tile, overlap)), (
                width,
                height,
                tile,
                overlap,
            )


# ------------------------------------------------------------------ upscale_lanczos
def test_upscale_lanczos_returns_target_size_as_rgb() -> None:
    """底图放大这一遍没有权重、零显存：尺寸必须是目标尺寸，模式统一成 RGB。"""
    base = Image.new("RGBA", (64, 32), (10, 120, 240, 255))
    upscaled = upscale_lanczos(base, 896, 512)
    assert upscaled.size == (896, 512)
    assert upscaled.mode == "RGB"
    # 确定性：同一张底图两次放大结果完全一致（可复现出图的一部分）。
    assert upscaled.tobytes() == upscale_lanczos(base, 896, 512).tobytes()


# ------------------------------------------------- 需要 numpy 的羽化与混合（.venv-image 实跑）
@needs_numpy
def test_feather_mask_shape_and_weight_range() -> None:
    """羽化掩膜：形状 (h, w)、float32、中心 1、边缘低但非 0（防止整块权重为 0）。

    ramp 显式给 16（64×64 的小格子）：默认 128 会被钳到格子短边，中间升不到 1。
    引擎实际用 512×512 + ramp 128，中间平台段同样是 1。
    """
    np = hires.np
    mask = hires.feather_mask(64, 48, 16)
    assert mask.shape == (48, 64)
    assert mask.dtype == np.float32
    assert float(mask.min()) > 0.0
    assert float(mask.max()) <= 1.0
    assert float(mask.max()) == pytest.approx(1.0)
    assert float(mask[24, 32]) == pytest.approx(1.0)  # 平台段
    assert float(mask[0, 32]) < float(mask[24, 32])  # 靠边权重更低
    assert float(hires.feather_mask(512, 512)[256, 256]) == pytest.approx(1.0)  # 引擎实际档位
    # 左右/上下对称：接缝两边的权重相加才约等于 1，拼接才看不出台阶。
    assert np.array_equal(mask, mask[::-1, :])
    assert np.array_equal(mask, mask[:, ::-1])


@needs_numpy
def test_tile_blender_averages_the_overlap_band() -> None:
    """两块重叠区取加权平均：96×64 画布放两块 64×64（重叠 32 列），重叠区就是 20/40 的均值 30。"""
    np = hires.np
    blender = TileBlender(96, 64)
    flat_a = Image.new("RGB", (64, 64), (20, 20, 20))
    flat_b = Image.new("RGB", (64, 64), (40, 40, 40))
    ones = np.ones((64, 64), dtype=np.float32)
    blender.paste(flat_a, TileSpec(0, 0, 64, 64), mask=ones)
    blender.paste(flat_b, TileSpec(32, 0, 64, 64), mask=ones)
    assert blender.pasted_tiles == 2

    result = blender.result()
    assert result.size == (96, 64)
    assert result.mode == "RGB"
    assert result.getpixel((10, 32)) == (20, 20, 20)  # 只有 A 盖到的地方保持原值
    assert result.getpixel((85, 32)) == (40, 40, 40)  # 只有 B 盖到的地方
    assert result.getpixel((48, 32)) == (30, 30, 30)  # 重叠区：两块各占一半
    # 整行都连续过渡，没有台阶：重叠带内每个像素都落在 20..40 之间。
    row = [result.getpixel((x, 32))[0] for x in range(96)]
    assert all(20 <= value <= 40 for value in row)
    assert row == sorted(row)  # 从 20 单调升到 40，接缝处不出现来回跳变


@needs_numpy
def test_tile_blender_rejects_tile_outside_canvas() -> None:
    """块超出画布必须抛 ValueError：越界写 numpy 切片会静默截掉一条边。"""
    np = hires.np
    blender = TileBlender(64, 64)
    ones = np.ones((64, 64), dtype=np.float32)
    with pytest.raises(ValueError, match="块超出画布"):
        blender.paste(Image.new("RGB", (64, 64)), TileSpec(8, 0, 64, 64), mask=ones)
    assert blender.pasted_tiles == 0


@needs_numpy
def test_tile_blender_result_without_any_tile_raises() -> None:
    """一块都没拼进去时不给出一张全黑的图：直接报错说明没拼过。"""
    blender = TileBlender(64, 64)
    assert blender.pasted_tiles == 0
    with pytest.raises(ValueError, match="还没有拼进任何块"):
        blender.result()


@needs_numpy
def test_tile_blender_tolerates_tile_off_by_eight_pixels() -> None:
    """模型侧偶尔给出与请求差几像素的结果（VAE 取整）：就近缩回格子，不崩掉整张拼图。"""
    np = hires.np
    blender = TileBlender(64, 64)
    blender.paste(Image.new("RGB", (56, 64), (200, 200, 200)), TileSpec(0, 0, 64, 64), mask=np.ones((64, 64), np.float32))
    assert blender.pasted_tiles == 1
    result = blender.result()
    assert result.size == (64, 64)
    assert result.getpixel((32, 32)) == (200, 200, 200)


@needs_numpy
def test_tile_blender_without_mask_uses_feather_weighting() -> None:
    """不给掩膜时走默认羽化：中心取到两块均值，边缘仍偏向各自那一块。"""
    blender = TileBlender(96, 64)
    blender.paste(Image.new("RGB", (64, 64), (0, 0, 0)), TileSpec(0, 0, 64, 64))
    blender.paste(Image.new("RGB", (64, 64), (200, 200, 200)), TileSpec(32, 0, 64, 64))
    result = blender.result()
    assert result.size == (96, 64)
    # 输出是 RGB，getpixel 返回三元组：这里比的是同一个通道的亮度
    assert result.getpixel((0, 32))[0] < 10  # 左边几乎纯黑
    assert result.getpixel((95, 32))[0] > 190  # 右边几乎纯亮
    assert 0 < result.getpixel((48, 32))[0] < 200  # 中间是混合区


if __name__ == "__main__":
    pytest.main([__file__, "-v"])