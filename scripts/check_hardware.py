"""启动前置硬件检测（辰鹄AI创作中心 · 便携包分级门槛）。

纯本机检测：nvidia-smi 读显卡显存 + Windows API 读内存，不联网、不上报。
按需求分级：

  达标  full  内存 ≥64GB 档（阈值取 60GB 兼容标称 64GB 的实际可用容量）
              且 显存 ≥12288MB                → 图片/视频/音乐全部可用
  中等  mid   显存 <12288MB（≥6144MB，有 NVIDIA），或 显存够但内存不足 60GB 档
                                                → 自动禁用音乐与视频，仅保留图片
  低配  low   无 NVIDIA 显卡 / 显存 <6144MB / 内存 <16GB
                                                → 提示性能不足，所有生成引擎不注册
                                                （界面仍可浏览历史会话与下载产物）

用法：
    python scripts\\check_hardware.py             # 人类可读报告（打印到 stderr）
    python scripts\\check_hardware.py --emit-env  # stdout 只输出两行环境变量赋值，
                                                 # 供启动 bat 用 for /f 捕获；报告照旧打到 stderr
评级明细同时写入项目内 logs\\hardware_report.json（本地文件，不上传）。
"""

from __future__ import annotations

import ctypes
import json
import subprocess
import sys
import time
from pathlib import Path

VRAM_FULL_MIN_MB = 12288
VRAM_MIN_WORKABLE_MB = 6144
RAM_FULL_MIN_MB = 60 * 1024   # 64GB 档（实际可用略低，取 60GB 兼容）
RAM_MIN_WORKABLE_MB = 16 * 1024


def detect_gpu() -> dict | None:
    try:
        out = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=8,
        )
        if out.returncode != 0:
            return None
        lines = [l.strip() for l in out.stdout.splitlines() if l.strip()]
        if not lines:
            return None
        # 多卡取显存最大的一块（推理只用单卡）。
        best_name, best_mem = "", -1
        for line in lines:
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 2:
                continue
            try:
                mem = int(float(parts[-1]))
            except ValueError:
                continue
            if mem > best_mem:
                best_name, best_mem = " ".join(parts[:-1]), mem
        if best_mem < 0:
            return None
        return {"name": best_name, "memory_total_mb": best_mem}
    except (OSError, subprocess.TimeoutExpired):
        return None


def detect_ram_mb() -> int:
    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    status = MEMORYSTATUSEX()
    status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return 0
    return int(status.ullTotalPhys / (1024 * 1024))


def classify(gpu: dict | None, ram_mb: int) -> tuple[str, str, list[str]]:
    vram_mb = gpu["memory_total_mb"] if gpu else 0
    if vram_mb >= VRAM_FULL_MIN_MB and ram_mb >= RAM_FULL_MIN_MB:
        return "full", "达标", []
    if vram_mb >= VRAM_MIN_WORKABLE_MB or (vram_mb >= VRAM_FULL_MIN_MB and ram_mb >= RAM_MIN_WORKABLE_MB):
        # 显存不满 12GB（视频/音乐需要），或显存够但内存不到 64GB 档。
        return "mid", "中等", ["video", "music"]
    return "low", "低配", ["image", "video", "music"]


def main() -> int:
    emit_env = "--emit-env" in sys.argv[1:]
    gpu = detect_gpu()
    ram_mb = detect_ram_mb()
    tier, tier_label, disabled = classify(gpu, ram_mb)

    report = {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "tier": tier,
        "tier_label": tier_label,
        "gpu": gpu,
        "ram_total_mb": ram_mb,
        "disabled_modalities": disabled,
    }
    project_root = Path(__file__).resolve().parents[1]
    report_path = project_root / "logs" / "hardware_report.json"
    try:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass  # 报告写不进就跳过，不影响分级判定

    gpu_text = f"{gpu['name']}（{gpu['memory_total_mb']} MB）" if gpu else "未检测到 NVIDIA 显卡"
    ram_text = f"{ram_mb / 1024:.1f} GB"
    # 人类可读信息一律走 stderr：--emit-env 时 stdout 只留变量赋值，供 bat 捕获。
    print(f"硬件检测：显卡 {gpu_text} · 内存 {ram_text} · 评级【{tier_label}】", file=sys.stderr)
    if tier == "full":
        print("  全部模块可用：文生图/图生图/文生视频/图生视频/AI音乐。", file=sys.stderr)
    elif tier == "mid":
        print("  中等配置：已自动禁用音乐与视频模块（ACE-Step 1.5 与 Wan2.1 均最低 12GB 显存；64GB 档内存），仅保留图片生成。", file=sys.stderr)
    else:
        print("  设备性能不足：显卡或内存低于最低门槛，文生图/视频/音乐均不可用，仅可浏览历史会话与已生成产物。", file=sys.stderr)
    print(f"  评级明细：{report_path}", file=sys.stderr)

    if emit_env:
        print("CHENGUAI_DISABLE_MODALITIES=" + ",".join(disabled))
        print(f"CHENGUAI_HW_TIER={tier}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
