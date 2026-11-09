"""图像能力验收（真跑，不是空转）：SD1.5 RealVis V5.1 的文生图 + 图生图各一张。

只有产物文件真的落盘、体积合理，脚本才退出 0；显存/环境不够就退出 2 并说清缺什么，
绝不把「没跑成」粉饰成「跑过了」。

运行：
    .venv-image\Scripts\python.exe scripts\acceptance_sd.py
可选参数：--width 1024 --height 576 --min-free 2500
退出码：0 = 真出图通过；2 = 显存/环境不足；1 = 其它失败。
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

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")


def _free_vram_mb() -> int | None:
    """问 CUDA 要空闲显存；没有 CUDA 返回 None（此时由引擎自己判断）。"""
    import torch

    if not torch.cuda.is_available():
        return None
    free, _total = torch.cuda.mem_get_info()
    return int(free / (1024 * 1024))


async def main() -> int:
    ap = argparse.ArgumentParser(description="SD1.5 图像能力验收")
    ap.add_argument("--width", type=int, default=1024)
    ap.add_argument("--height", type=int, default=576)
    ap.add_argument("--min-free", type=int, default=1500, help="提交前要求的空闲显存 MB")
    args = ap.parse_args()

    free = _free_vram_mb()
    print(f"[accept-sd] 当前空闲显存：{free if free is not None else '无 CUDA'}MB（门槛 {args.min_free}MB）", flush=True)
    if free is not None and free < args.min_free:
        print(
            "[accept-sd] 显存不够，先释放：关掉占显存的第三方程序，或运行 scripts\\release_local_engine.bat"
            "（只结束本项目自己的引擎进程）后重跑。",
            flush=True,
        )
        return 2

    os.environ.setdefault("CHENGUAI_SD_MIN_FREE_MB", str(max(700, args.min_free // 2)))
    from app.engine.image.sd15 import SD15EngineError, SD15ImageEngine

    out_dir = ROOT / "output" / "acceptance_sd"
    out_dir.mkdir(parents=True, exist_ok=True)
    engine = SD15ImageEngine("sd-realvis", cache_dir=str(ROOT / "models" / "sd-realvis"), output_dir=str(out_dir))

    try:
        await engine.load()
        t2i = await engine.generate_image(
            "一杯拿铁拉花在木桌上，晨光，照片质感，细节丰富",
            width=args.width,
            height=args.height,
            steps=6,
            seed=20261109,
        )
        print(
            f"[accept-sd] 文生图 OK {t2i['target_size']} {t2i['path']} 峰值={t2i['peak_vram_mb']:.0f}MB "
            f"offload={t2i['offload']} lcm={t2i['lcm']}",
            flush=True,
        )
        started = time.time()
        i2i = await engine.generate_image(
            "同样的杯子换成夜景暖光，杯口冒热气，照片质感，细节丰富",
            width=args.width,
            height=args.height,
            steps=6,
            seed=20261109,
            strength=0.55,
            init_image_path=str(t2i["path"]),
        )
        print(
            f"[accept-sd] 图生图 OK {i2i['target_size']} {i2i['path']} 峰值={i2i['peak_vram_mb']:.0f}MB "
            f"耗时={time.time() - started:.1f}s",
            flush=True,
        )
    except SD15EngineError as exc:
        print(f"[accept-sd] 引擎拒绝/失败：{exc}", flush=True)
        return 2
    finally:
        await engine.shutdown()

    problems = []
    for label, item in (("文生图", t2i), ("图生图", i2i)):
        path = Path(item["path"])
        if not path.is_file() or path.stat().st_size < 50_000:
            problems.append(f"{label} 产物缺失或过小：{path}")
    if problems:
        print("[accept-sd] 失败：" + "；".join(problems), flush=True)
        return 1
    print(f"[accept-sd] 通过：文生图 + 图生图各一张真落盘（{args.width}x{args.height}）", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
