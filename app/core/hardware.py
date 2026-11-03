"""Small, non-invasive hardware probes for the dashboard."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GpuInfo:
    name: str
    memory_total_mib: int
    memory_free_mib: int


def get_gpu_info() -> GpuInfo | None:
    executable = shutil.which("nvidia-smi")
    if executable is None:
        return None
    try:
        result = subprocess.run(
            [
                executable,
                "--query-gpu=name,memory.total,memory.free",
                "--format=csv,noheader,nounits",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
        )
        first_gpu = result.stdout.splitlines()[0]
        name, total, free = (part.strip() for part in first_gpu.split(",", 2))
        return GpuInfo(name, int(total), int(free))
    except (OSError, subprocess.SubprocessError, IndexError, ValueError):
        return None


def get_disk_free_gib(path: Path) -> float | None:
    existing_path = path
    while not existing_path.exists() and existing_path != existing_path.parent:
        existing_path = existing_path.parent
    try:
        return round(shutil.disk_usage(existing_path).free / (1024**3), 1)
    except OSError:
        return None