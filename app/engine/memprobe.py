"""空闲物理内存探针：所有需要把大权重读进内存的引擎共用（纯标准库，零依赖）。

为什么必须有这个东西：Wan2.1 的 UMT5-XXL 文本编码器单件就约 11GB，ACE-Step 1.5 的
DiT + 1.7B LM 合计 9.40GB，这些权重都要**先从磁盘进物理内存**，再分片换入显存。
2026-11-08 那次 ``wan-i2v``（VACE）真机尝试，日志停在
``checkpoint shards: 50%|…|1/2`` 之后进程直接消失，**没有任何 Python traceback**——
当时空闲物理内存只剩 2.5GB，原生层被系统直接杀掉，比「显存不够」安静得多，
排查时很容易误判成显存问题。所以加载前先量一次空闲内存，不够就明确报错，
把「静默消失」变成「可操作的中文错误」。

放在 ``app.engine`` 而不是各引擎里，是因为桌面端 ``.venv``（没有 torch）也要能 import
它做提交前预检；三个引擎 venv 与验收脚本同样可用。
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
from typing import Optional

MB = 1024 * 1024


@dataclass(frozen=True)
class RamSnapshot:
    """物理内存快照；读不到时两个字段都是 ``None``（非 Windows / API 失败）。"""

    total_mb: Optional[int]
    free_mb: Optional[int]

    def as_dict(self) -> dict:
        return {"ram_total_mb": self.total_mb, "ram_free_mb": self.free_mb}


def read_physical_ram() -> RamSnapshot:
    """用 ``GlobalMemoryStatusEx`` 读总量/空闲物理内存（MB）。非 Windows 返回全 None。"""
    try:
        import platform

        if platform.system() != "Windows":
            return RamSnapshot(None, None)

        class _MemoryStatusEx(ctypes.Structure):
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

        stat = _MemoryStatusEx()
        stat.dwLength = ctypes.sizeof(_MemoryStatusEx)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            return RamSnapshot(None, None)
        return RamSnapshot(
            total_mb=int(stat.ullTotalPhys // MB),
            free_mb=int(stat.ullAvailPhys // MB),
        )
    except Exception:  # noqa: BLE001 - 探针本身绝不能把调用方带崩
        return RamSnapshot(None, None)


def floor_mb_or(raw: Optional[str], default: int) -> int:
    """把环境变量里的门槛值安全转成整数；乱填、非正数一律回落到默认值。"""
    text = (raw or "").strip()
    if not text:
        return default
    try:
        value = int(float(text))
    except ValueError:
        return default
    return value if value > 0 else default


def ram_gate_ok(snapshot: RamSnapshot, floor: int) -> bool:
    """空闲内存是否够加载。读不到数值时**放行**（让原生加载自己报错，不做假拦截）。"""
    if snapshot.free_mb is None:
        return True
    return int(snapshot.free_mb) >= int(floor)


def ram_gate_message(
    label: str,
    floor: int,
    snapshot: RamSnapshot,
    why: str = "",
) -> str:
    """统一的中文拦截文案：说清楚测到多少、要多少、为什么、下一步做什么。"""
    measured = (
        f"{snapshot.free_mb / 1024:.1f} GB" if snapshot.free_mb is not None else "未知"
    )
    total = f"（机器总内存 {snapshot.total_mb / 1024:.0f} GB）" if snapshot.total_mb else ""
    reason = f"：{why}" if why else ""
    return (
        f"{label} 暂不能加载：系统空闲物理内存只有 {measured}{total}，"
        f"低于本引擎的加载门槛 {floor / 1024:.1f} GB{reason}。"
        "权重要先读进内存再分片换入显存，内存不够时进程会被系统直接杀掉、连报错都没有"
        "（2026-11-08 的 wan-i2v 就是这么静默消失的）。"
        "请先关闭占内存的程序（浏览器多标签页、其它 AI 工具、IDE）后重试；"
        "显存被本机推理引擎占着时可双击 scripts\\release_local_engine.bat 释放。"
    )
