"""文生图能力验收（真跑，不是空转）：Sana-1.6B 多语言版（引擎 sana-multiling）。

选型时「真机峰值显存 ≤6GB」是按权重体积推算的预期值，只有真机实测才算数，所以这个
脚本把每一张的实测峰值打印出来并直接判定：任何一张的峰值超过 --peak-limit（默认
6000MB）就**立刻停止**，后面的档位不再跑。开跑前以及每一档开跑前都要重新量一次空闲
显存与空闲物理内存，不够就退出 2 并说清缺什么，绝不把「没跑成」粉饰成「跑过了」。

跑两档：1024×1024（单遍上限附近，显存最坏情况）与 1920×1088（超单遍预算 → 引擎按
「预算内出底图 + Lanczos 放大」执行，元数据 upscaled=true、tiles=0）。第二档用中英混
合提示词，验证「中英文原生支持」这条不是口号。

运行：
    .venv-image\Scripts\python.exe scripts\acceptance_image.py
可选参数：--min-free 6144 --min-free-ram 16384 --steps 20 --peak-limit 6000
          --stages 1024x1024,1920x1088
退出码：0 = 两档都真落盘且峰值在门槛内；2 = 显存/内存不够或峰值超限（已立即停止）；
        1 = 其它失败。
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
os.environ.setdefault(
    "CHENGUAI_SANA_MODEL_CACHE_DIR", str(ROOT / "models" / "sana-multiling")
)

# 两档提示词：第一档纯中文，第二档中英混合（模型卡声明 Emoji/Chinese/English/mixed 原生支持）。
_PROMPTS = (
    "一杯拿铁拉花在木桌上，晨光，照片质感，浅景深，细节丰富",
    "a 橘猫 sitting on a 屋顶 watching the sunset，霓虹灯 reflection，赛博朋克 street，照片质感",
)


def _free_vram_mb() -> int | None:
    """问 CUDA 要空闲显存；没有 CUDA 返回 None（此时由引擎自己判断并报错）。"""
    import torch

    if not torch.cuda.is_available():
        return None
    free, _total = torch.cuda.mem_get_info()
    return int(free / (1024 * 1024))


def _parse_stages(raw: str) -> list[tuple[int, int]]:
    stages: list[tuple[int, int]] = []
    for item in raw.split(","):
        item = item.strip().lower().replace("×", "x")
        if not item:
            continue
        w, _, h = item.partition("x")
        stages.append((int(w), int(h)))
    if not stages:
        raise ValueError(f"--stages 解析不出任何档位：{raw!r}")
    return stages


def _check_gates(args, label: str) -> int | None:
    """每一档开跑前重新量一次门槛；不达标返回退出码 2，达标返回 None。

    这一条是硬要求：本机常驻的本地大模型引擎会占满显存，显存被占满时继续测就是拿
    OOM 当测试结论，所以宁可退出也不硬扛。
    """
    free = _free_vram_mb()
    from app.engine.memprobe import ram_gate_ok, ram_gate_message, read_physical_ram

    ram = read_physical_ram()
    print(
        f"[accept-image] {label} 前复查：空闲显存 "
        f"{free if free is not None else '无 CUDA'}MB（门槛 {args.min_free}MB）、"
        f"空闲物理内存 {ram.free_mb}MB（门槛 {args.min_free_ram}MB）",
        flush=True,
    )
    if free is not None and free < args.min_free:
        print(
            "[accept-image] 显卡占用过高，立即停止测试。先释放显存：关掉占显存的第三方程序，"
            "或运行 scripts\\release_local_engine.bat（只结束本项目自己的引擎进程）后重跑。",
            flush=True,
        )
        return 2
    if not ram_gate_ok(ram, args.min_free_ram):
        print(
            "[accept-image] "
            + ram_gate_message(
                "Sana-1.6B 多语言文生图验收",
                args.min_free_ram,
                ram,
                "fp16 三件权重 9.06GB 要先读进物理内存",
            ),
            flush=True,
        )
        return 2
    return None


def _prompt_for(index: int) -> str:
    """按档位取提示词：档位多于两条就循环用，保证纯中文与中英混合都被覆盖到。"""
    return _PROMPTS[index % len(_PROMPTS)]


async def main() -> int:
    ap = argparse.ArgumentParser(description="Sana-1.6B 多语言文生图能力验收")
    ap.add_argument("--min-free", type=int, default=6_144, help="开跑前要求的空闲显存 MB")
    ap.add_argument("--min-free-ram", type=int, default=16_384, help="开跑前要求的空闲物理内存 MB")
    ap.add_argument("--peak-limit", type=int, default=6_000, help="实测峰值显存上限 MB（超过立即停止）")
    ap.add_argument("--steps", type=int, default=20, help="采样步数（引擎按 10-40 夹取）")
    ap.add_argument("--stages", default="1024x1024,1920x1088", help="逗号分隔的 WxH 档位")
    args = ap.parse_args()

    stages = _parse_stages(args.stages)
    blocked = _check_gates(args, "加载权重")
    if blocked is not None:
        return blocked

    from app.engine.image.sana import SanaEngineError, SanaImageEngine

    out_dir = ROOT / "output" / "acceptance_image"
    out_dir.mkdir(parents=True, exist_ok=True)
    engine = SanaImageEngine(
        "sana-multiling",
        cache_dir=str(ROOT / "models" / "sana-multiling"),
        output_dir=str(out_dir),
    )

    results: list[dict] = []
    verdict = 0
    try:
        await engine.load()
        print(
            f"[accept-image] 权重已加载，卸载方式={engine.offload_mode or '常驻'}；"
            "开始按档位出图",
            flush=True,
        )
        for index, (width, height) in enumerate(stages):
            blocked = _check_gates(args, f"{width}x{height}")
            if blocked is not None:
                verdict = blocked
                break
            started = time.time()
            item = await engine.generate_image(
                _prompt_for(index),
                width=width,
                height=height,
                steps=args.steps,
                seed=20261110 + index,
            )
            peak = float(item.get("peak_vram_mb") or 0.0)
            print(
                f"[accept-image] {width}x{height} 出图 OK 底图={item.get('base_size')} "
                f"目标={item.get('target_size')} 放大={item.get('upscaled')} 分块={item.get('tiles')} "
                f"峰值={peak:.0f}MB 卸载={item.get('offload')} 耗时={time.time() - started:.1f}s "
                f"→ {item.get('path')}",
                flush=True,
            )
            results.append(item)
            if peak > args.peak_limit:
                print(
                    f"[accept-image] 实测峰值 {peak:.0f}MB 已超门槛 {args.peak_limit}MB，"
                    "立即停止后续档位（不得继续测试）。",
                    flush=True,
                )
                verdict = 2
                break
    except SanaEngineError as exc:
        print(f"[accept-image] 引擎拒绝/失败：{exc}", flush=True)
        return 2
    finally:
        await engine.shutdown()

    if verdict:
        return verdict

    problems = []
    for index, item in enumerate(results):
        path = Path(str(item.get("path") or ""))
        if not path.is_file() or path.stat().st_size < 50_000:
            problems.append(f"第 {index + 1} 档产物缺失或过小：{path}")
    if problems:
        print("[accept-image] 失败：" + "；".join(problems), flush=True)
        return 1
    if len(results) != len(stages):
        print(
            f"[accept-image] 失败：{len(stages)} 档只出图 {len(results)} 档",
            flush=True,
        )
        return 1
    worst = max(float(item.get("peak_vram_mb") or 0.0) for item in results)
    print(
        f"[accept-image] 通过：{len(results)} 档真落盘，实测峰值 {worst:.0f}MB"
        f"（门槛 {args.peak_limit}MB，6GB 档判定按 {args.min_free}MB 空闲显存）",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
