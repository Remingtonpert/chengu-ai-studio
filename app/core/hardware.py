"""Small, non-invasive hardware probes for the dashboard."""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GpuInfo:
    name: str
    memory_total_mib: int
    memory_free_mib: int
    # nvidia-smi 自己报的 used：只做仪表盘展示与「按进程实测」对不上时的参考。
    # Windows 上它把驱动/显示子系统保留的、以及进程已经结束但驱动还没回收的都算进去，
    # 比按进程实测加和明显虚高，所以拦任务时不能拿它当真实占用（见本文件下半部分）。
    memory_used_mib: int = 0


def get_gpu_info() -> GpuInfo | None:
    executable = shutil.which("nvidia-smi")
    if executable is None:
        return None
    try:
        result = subprocess.run(
            [
                executable,
                "--query-gpu=name,memory.total,memory.free,memory.used",
                "--format=csv,noheader,nounits",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
        )
        first_gpu = result.stdout.splitlines()[0]
        # 显卡名里可能带逗号（"NVIDIA GeForce RTX 3060, 12288 MiB" 这种），数字在末尾三项。
        parts = [part.strip() for part in first_gpu.split(",")]
        if len(parts) < 4:
            return None
        name = ",".join(parts[:-3])
        total, free, used = (int(part) for part in parts[-3:])
        return GpuInfo(name, total, free, used)
    except (OSError, subprocess.SubprocessError, IndexError, ValueError):
        return None


def nvidia_smi_available() -> bool:
    """只做一次 PATH 查找，不起子进程，可以安全地在 UI 线程上调用。"""
    return shutil.which("nvidia-smi") is not None


_gpu_cache: tuple[float, GpuInfo | None] | None = None
_gpu_lock = threading.Lock()
_gpu_refreshing = False


def _refresh_gpu_info() -> None:
    global _gpu_cache, _gpu_refreshing
    info = get_gpu_info()
    with _gpu_lock:
        _gpu_cache = (time.monotonic(), info)
        _gpu_refreshing = False


def _start_probe_locked() -> None:
    global _gpu_refreshing
    if _gpu_refreshing:
        return
    _gpu_refreshing = True
    threading.Thread(
        target=_refresh_gpu_info,
        name="chenguai-gpu-probe",
        daemon=True,
    ).start()


def prime_gpu_cache() -> None:
    """提前在后台线程探一次显卡，避免第一次打开界面时 UI 线程去等 nvidia-smi。"""
    with _gpu_lock:
        _start_probe_locked()


def cached_gpu_info(max_age_seconds: float = 10.0) -> GpuInfo | None:
    """返回最近一次探测到的显卡信息；数据过期时安排后台线程刷新。

    nvidia-smi 在显存被占满时可能耗时一秒以上，UI 线程不能等它：这里最多给出
    一份略旧（或尚无）的缓存，真正的探测在守护线程里做。返回 None 表示
    「还没有探测结果」，调用方应把它当未知，而不是「机器上没有显卡」。
    """
    now = time.monotonic()
    with _gpu_lock:
        cache = _gpu_cache
        if cache is not None and now - cache[0] < max_age_seconds:
            return cache[1]
        _start_probe_locked()
        return cache[1] if cache is not None else None


# ------------------------------------------------------------ 按进程实测显存占用
#
# 为什么要按进程实测：nvidia-smi 的 used 在 Windows 上会虚高（驱动/显示子系统保留的、
# 进程已结束但驱动没回收的都算进去），而 --query-compute-apps 的 used_memory 在 Windows
# 上干脆全是 [N/A]。所以「显存被谁占着」用 Windows 性能计数器
# ``\GPU Process Memory(*)\Local Usage`` 按进程实测，进程名顺手从 CIM 取。
#
# 这条查询要起一次 PowerShell（几百毫秒），绝不能挂每几秒刷新的仪表盘上：只在
# 「准备拦下一个任务」的判定和报错路径上调，结果缓存 20 秒；查不到就降级回
# nvidia-smi 的数，绝不因为探测失败而拦掉任务。
_PER_PROCESS_SCRIPT = r"""
$pairs = @{}
try {
    $samples = (Get-Counter '\GPU Process Memory(*)\Local Usage' -ErrorAction Stop).CounterSamples
    foreach ($s in $samples) {
        $instance = [string]$s.InstanceName
        if ($instance -match '^pid_(\d+)') { $pairs[$matches[1]] = [long]$s.CookedValue }
    }
} catch { exit 0 }
if ($pairs.Count -eq 0) { exit 0 }
$names = @{}
try {
    foreach ($p in Get-CimInstance Win32_Process -ErrorAction SilentlyContinue) {
        $names[[string]$p.ProcessId] = [string]$p.Name
    }
} catch { }
foreach ($k in $pairs.Keys) {
    $n = 'unknown'; if ($names.ContainsKey($k)) { $n = [string]$names[$k] }
    Write-Output ("{0}`t{1}`t{2}" -f $k, [long]$pairs[$k], $n)
}
"""


@dataclass(frozen=True)
class VramHolder:
    """某个进程当前占用的显存（MiB），以及它的镜像名。"""

    pid: int
    name: str
    memory_mib: int


def process_vram_holders(*, timeout_seconds: float = 8.0) -> tuple[VramHolder, ...] | None:
    """按进程实测显存占用，从大到小。返回 None 表示「这条路上拿不到数」。

    只读计数器与进程列表，不结束任何进程；查不到（非 Windows、没有 PowerShell、
    没有 GPU 计数器）一律 None，由调用方决定怎么降级。
    """
    if os.name != "nt":  # pragma: no cover - 非 Windows
        return None
    shell = shutil.which("powershell") or shutil.which("pwsh")
    if shell is None:  # pragma: no cover - Windows 一定带 powershell
        return None
    try:
        result = subprocess.run(
            [shell, "-NoProfile", "-NonInteractive", "-Command", _PER_PROCESS_SCRIPT],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    holders: list[VramHolder] = []
    for line in result.stdout.splitlines():
        fields = line.strip().split("\t")
        if len(fields) != 3:
            continue
        pid_text, bytes_text, name_text = fields
        if not pid_text.isdigit() or not bytes_text.isdigit():
            continue
        mib = int(int(bytes_text) / (1024 * 1024))
        if mib <= 0:
            continue
        holders.append(VramHolder(int(pid_text), name_text[:80] or "unknown", mib))
    holders.sort(key=lambda holder: holder.memory_mib, reverse=True)
    return tuple(holders)


_vram_cache: tuple[float, tuple[VramHolder, ...] | None] | None = None
_vram_lock = threading.Lock()


def vram_holders_cached(max_age_seconds: float = 20.0) -> tuple[VramHolder, ...] | None:
    """缓存版：``None`` 表示拿不到数（或这次没查），``()`` 表示确实没有按进程占用。

    会在这条线程上真查一次（几百毫秒），所以只能在「准备拦任务」这种用户能等的地方调，
    不能挂仪表盘轮询上。
    """
    global _vram_cache
    now = time.monotonic()
    with _vram_lock:
        cache = _vram_cache
        if cache is not None and now - cache[0] < max_age_seconds:
            return cache[1]
    found = process_vram_holders()
    with _vram_lock:
        _vram_cache = (time.monotonic(), found)
    return found


def forget_vram_cache() -> None:
    """让下一次判定重新实测（刚释放过引擎、显存变了就用这个）。"""
    global _vram_cache
    with _vram_lock:
        _vram_cache = None


def holders_summary(holders: tuple[VramHolder, ...] | None, limit: int = 3) -> str:
    """把占用最多的几个程序写成一句人话（报显存不够时要点名，不能让用户瞎猜）。"""
    if not holders:
        return "没查到是哪个程序占着显存"
    picked = [f"{holder.name} {holder.memory_mib}MB" for holder in holders[:limit]]
    return "、".join(picked)


def effective_free_mib(
    gpu: GpuInfo | None,
    holders: tuple[VramHolder, ...] | None,
    *,
    ours: tuple[int, ...] = (),
) -> int:
    """还能用多少显存（MiB）：nvidia-smi 的 free 与「总量 − 按进程实测加和」取较大的那个。

    nvidia-smi 的 used 虚高会让 free 偏低，按进程实测能把被冤枉的那部分还回来。
    ``ours`` 里的 PID 是本进程自己这边还没停干净的 worker：它们的显存是「可以马上要回来」的，
    不能算成别人占走的。拿不到按进程数据时退回 nvidia-smi 的 free，不编数字。
    """
    if gpu is None:
        return -1
    free = int(gpu.memory_free_mib)
    if not holders:
        return free
    ours_set = {int(pid) for pid in ours}
    theirs = sum(
        holder.memory_mib for holder in holders if holder.pid not in ours_set
    )
    if theirs <= 0:
        return max(free, int(gpu.memory_total_mib))
    return max(free, int(gpu.memory_total_mib) - theirs)


def get_disk_free_gib(path: Path) -> float | None:
    existing_path = path
    while not existing_path.exists() and existing_path != existing_path.parent:
        existing_path = existing_path.parent
    try:
        return round(shutil.disk_usage(existing_path).free / (1024**3), 1)
    except OSError:
        return None