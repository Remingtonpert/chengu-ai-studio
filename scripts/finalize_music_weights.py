"""监控 ACE-Step 1.5 权重下载进度；下载全部完成后自动执行两步收尾：

  1) 同步建模代码：把 vendor\\ACE-Step-1.5\\acestep\\models\\turbo\\ 下的
     configuration_acestep_v15.py / modeling_acestep_v15_turbo.py 复制进
     checkpoint 目录 models\\ace-step-1.5\\acestep-v15-turbo\\
     （等价官方 model_downloader._sync_model_code_files，权重包内自带的同名
     文件也会被 repo 版本覆盖，保证与已安装代码严格同版）。
  2) 校验 4 个 safetensors：逐个输出文件大小，用 safetensors 解析文件头并
     统计张量数量——打不开 / 头部非法 / 无张量 → 标记损坏；缺文件 → 标记缺失。

每 15 秒打印一行：已落盘字节数（成品+半成品）、区间平均速度、各半成品大小与
最后写入时刻。连续 15 分钟没有任何新字节写入则判定 STALLED 退出。
全部校验通过时打印 [权重就绪]；否则打印失败明细。
下载未完成前请勿调用音乐生成功能（引擎 health_check 也会因权重缺失拒绝启动）。
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CKPT = PROJECT_ROOT / "models" / "ace-step-1.5"
CACHE = CKPT / ".cache" / "huggingface" / "download"
REPO_TURBO = PROJECT_ROOT / "vendor" / "ACE-Step-1.5" / "acestep" / "models" / "turbo"

EXPECTED_SAFE_TENSORS = [
    ("turbo DiT", CKPT / "acestep-v15-turbo" / "model.safetensors", 1_500_000_000),
    ("5Hz-LM-1.7B", CKPT / "acestep-5Hz-lm-1.7B" / "model.safetensors", 1_500_000_000),
    ("Qwen3-Embedding-0.6B", CKPT / "Qwen3-Embedding-0.6B" / "model.safetensors", 100_000_000),
    ("VAE", CKPT / "vae" / "diffusion_pytorch_model.safetensors", 50_000_000),
]
SYNC_CODE_FILES = ["configuration_acestep_v15.py", "modeling_acestep_v15_turbo.py"]

INTERVAL = 15.0
STALL_SECONDS = 900.0


def scan_state() -> tuple[int, int, list[tuple[Path, int, float]]]:
    """返回 (成品字节, 半成品字节, [(文件, 大小, mtime)])，成品=checkpoint 正式目录内文件。"""
    done_bytes = 0
    if CKPT.is_dir():
        for path in CKPT.rglob("*"):
            if path.is_file() and ".cache" not in path.parts:
                done_bytes += path.stat().st_size
    partials: list[tuple[Path, int, float]] = []
    inc_bytes = 0
    if CACHE.is_dir():
        for path in CACHE.rglob("*"):
            if path.is_file() and path.name.endswith(".incomplete"):
                st = path.stat()
                inc_bytes += st.st_size
                partials.append((path, st.st_size, st.st_mtime))
    return done_bytes, inc_bytes, partials


def monitor() -> bool:
    prev_total: int | None = None
    stale_since: float | None = None
    print(f"watching {CKPT}", flush=True)
    while True:
        done_bytes, inc_bytes, partials = scan_state()
        now = time.time()
        total = done_bytes + inc_bytes
        speed = "-" if prev_total is None else f"{(total - prev_total) / INTERVAL / 1e6:6.2f} MB/s"
        print(
            f"[{time.strftime('%H:%M:%S')}] final {done_bytes / 1e9:6.2f} GB | partial {inc_bytes / 1e9:6.2f} GB | total {total / 1e9:6.2f} GB | {speed}",
            flush=True,
        )
        for path, size, mtime in partials:
            print(f"    incomplete {size / 1e6:8.1f} MB  idle {now - mtime:6.0f}s  {path.parent.name}/{path.name[:40]}", flush=True)
        if not partials and all(path.is_file() for _, path, _ in EXPECTED_SAFE_TENSORS):
            print("all expected safetensors finalized, no in-flight parts", flush=True)
            return True
        newest = max((mtime for _, _, mtime in partials), default=0.0)
        if now - newest > STALL_SECONDS:
            if stale_since is None:
                stale_since = now
                print("  !! no writes for >15 min, watching for 2 more minutes before declaring stalled", flush=True)
            elif now - stale_since > 120:
                print("STALLED: 下载停滞（17 分钟无任何写入），请检查镜像/网络后重跑 download_music_weights.py", flush=True)
                return False
        else:
            stale_since = None
        prev_total = total
        time.sleep(INTERVAL)


def sync_model_code() -> None:
    dst_dir = CKPT / "acestep-v15-turbo"
    for name in SYNC_CODE_FILES:
        src = REPO_TURBO / name
        if not src.is_file():
            raise SystemExit(f"MISSING repo code file: {src}")
        shutil.copy2(src, dst_dir / name)
        print(f"synced {src.relative_to(PROJECT_ROOT)} -> {dst_dir / name}", flush=True)


def verify() -> bool:
    from safetensors import safe_open  # .venv-music 提供

    ok = True
    for label, path, min_bytes in EXPECTED_SAFE_TENSORS:
        if not path.is_file():
            print(f"VERIFY {label:<22} MISSING          {path}", flush=True)
            ok = False
            continue
        size = path.stat().st_size
        try:
            with safe_open(str(path), framework="pt") as f:
                keys = list(f.keys())
        except Exception as exc:  # noqa: BLE001
            print(f"VERIFY {label:<22} CORRUPT ({exc.__class__.__name__}: {exc})  {size / 1e6:.1f} MB  {path}", flush=True)
            ok = False
            continue
        flag = "OK" if size >= min_bytes and keys else "SUSPECT"
        if flag != "OK":
            ok = False
        print(f"VERIFY {label:<22} {flag:<7} {size / 1e9:6.2f} GB ({size:,} bytes) tensors={len(keys)}", flush=True)
    return ok


def main() -> int:
    # --verify-only：跳过停滞监视（旧会话遗留的 .cache/*.incomplete 垃圾文件会
    # 让监视条件永远无法满足），直接执行同步建模代码 + 四文件校验。
    verify_only = "--verify-only" in sys.argv[1:]
    if not verify_only and not monitor():
        return 2
    sync_model_code()
    ok = verify()
    print("[权重就绪] 全部校验通过，可以开始音乐真机首测（先释放显存）" if ok else "[校验失败] 存在缺失/损坏文件，勿调用音乐生成", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
