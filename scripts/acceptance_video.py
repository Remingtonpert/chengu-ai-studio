"""视频能力验收（真跑）：Wan2.1-1.3B 的文生视频（wan-t2v）+ 图转视频（wan-i2v）各一条。

只有 mp4 真的落盘且体积合理才退出 0；显存不够退出 2 并说清缺什么。
验收用 12 步（约 6 分钟/条）；界面默认 28 步约 14 分钟/条。

运行：
    .venv-video\Scripts\python.exe scripts\acceptance_video.py
    .venv-video\Scripts\python.exe scripts\acceptance_video.py --skip-i2v
退出码：0 = 真出片通过；2 = 显存/环境不足；1 = 其它失败。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Windows 默认控制台代码页是 GBK，打不出中文和 emoji：不兜底的话脚本会在 print 时
# 直接抛 UnicodeEncodeError 崩掉，用户只看到「跑不起来」。errors="replace" 保证永不因此崩。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # 被重定向、已关闭或过老的流：忽略，不影响主流程
        pass

# 纯标准库探针：加载 44GB 级权重之前必须先量空闲物理内存，否则进程会被系统静默杀掉
from app.engine.memprobe import ram_gate_message, ram_gate_ok, read_physical_ram  # noqa: E402

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")


def _free_vram_mb() -> int | None:
    import torch

    if not torch.cuda.is_available():
        return None
    free, _total = torch.cuda.mem_get_info()
    return int(free / (1024 * 1024))


def _pick_still(explicit: str | None) -> Path | None:
    if explicit:
        path = Path(explicit)
        return path if path.is_file() else None
    # 新的 Sana 验收目录优先；旧的 SD 目录保留，历史上跑过的图还能当首帧用。
    for folder in ("acceptance_image", "acceptance_sd", "smoke_sd_i2i", "smoke_sd"):
        files = sorted((ROOT / "output" / folder).glob("*.png"), key=lambda p: p.stat().st_mtime)
        if files:
            return files[-1]
    return None


async def main() -> int:
    ap = argparse.ArgumentParser(description="Wan2.1 视频能力验收")
    ap.add_argument("--steps", type=int, default=12)
    ap.add_argument("--frames", type=int, default=33)
    ap.add_argument("--width", type=int, default=512)
    ap.add_argument("--height", type=int, default=384)
    ap.add_argument("--min-free", type=int, default=4000, help="提交前要求的空闲显存 MB")
    ap.add_argument("--min-ram", type=int, default=16000,
                    help="加载权重前要求的空闲物理内存 MB（不够会把进程静默杀掉）")
    ap.add_argument("--init-image", default=None, help="图转视频的首帧图（默认自动挑一张本项目真出过的图）")
    ap.add_argument("--skip-i2v", action="store_true", help="只验收文生视频")
    args = ap.parse_args()

    ram = read_physical_ram()
    if not ram_gate_ok(ram, args.min_ram):
        print(ram_gate_message(
            "Wan2.1 视频引擎", args.min_ram, ram, "UMT5-XXL 文本编码单件就要约 11GB 物理内存"
        ), flush=True)
        return 2
    print(f"[accept-video] 当前空闲物理内存：{ram.free_mb if ram.free_mb is not None else '未知'}MB（门槛 {args.min_ram}MB）", flush=True)
    free = _free_vram_mb()
    print(f"[accept-video] 当前空闲显存：{free if free is not None else '无 CUDA'}MB（门槛 {args.min_free}MB）", flush=True)
    if free is not None and free < args.min_free:
        print(
            "[accept-video] 显存不够：Wan2.1 逐层换入也需要这么多空闲显存（第三方程序占满时会硬崩）。"
            "请先释放显存（关闭占显存的程序，或运行 scripts\\release_local_engine.bat）后重跑。",
            flush=True,
        )
        return 2

    from app.engine.video.wan import WanVideoEngine

    out_dir = ROOT / "output" / "acceptance_video"
    out_dir.mkdir(parents=True, exist_ok=True)
    results: list[tuple[str, dict]] = []
    codes = []

    engine = WanVideoEngine("wan-t2v", cache_dir=str(ROOT / "models" / "wan2.1"), output_dir=str(out_dir))
    started = time.time()
    try:
        t2v = await engine.generate_video(
            "镜头缓慢推进，云海之上日出，金色光线，真实感电影镜头",
            num_frames=args.frames,
            width=args.width,
            height=args.height,
            steps=args.steps,
            seed=20261109,
        )
        results.append(("文生视频", t2v))
        print(f"[accept-video] 文生视频 OK {t2v['path']} 峰值={t2v.get('peak_vram_mb')}MB 耗时={time.time() - started:.1f}s", flush=True)
    except Exception as exc:  # noqa: BLE001 - 验收脚本要把任何真实失败原样说清
        print(f"[accept-video] 文生视频 失败：{exc}", flush=True)
        codes.append(2)
    finally:
        await engine.shutdown()

    still = None if args.skip_i2v else _pick_still(args.init_image)
    if still is not None:
        print(f"[accept-video] 图转视频首帧={still}", flush=True)
        engine2 = WanVideoEngine("wan-i2v", cache_dir=str(ROOT / "models" / "wan2.1"), output_dir=str(out_dir))
        started = time.time()
        try:
            i2v = await engine2.generate_video(
                "镜头缓慢推进，画面主体自然运动，光线自然变化，真实感",
                image_path=str(still),
                num_frames=args.frames,
                width=args.width,
                height=args.height,
                steps=args.steps,
                seed=20261109,
            )
            results.append(("图转视频", i2v))
            print(f"[accept-video] 图转视频 OK {i2v['path']} 峰值={i2v.get('peak_vram_mb')}MB 耗时={time.time() - started:.1f}s", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[accept-video] 图转视频 失败：{exc}", flush=True)
            codes.append(2)
        finally:
            await engine2.shutdown()
    elif not args.skip_i2v:
        print("[accept-video] 没找到可当首帧的图片（output/acceptance_image 等目录为空），先跑 scripts\\acceptance_image.bat", flush=True)
        codes.append(2)

    for label, item in results:
        path = Path(item["path"])
        if not path.is_file() or path.stat().st_size < 50_000:
            print(f"[accept-video] {label} 产物异常：{path}", flush=True)
            codes.append(1)

    if codes:
        return max(codes)
    print(f"[accept-video] 通过：{len(results)} 条视频真落盘（{args.width}x{args.height}、{args.frames} 帧、{args.steps} 步）", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
