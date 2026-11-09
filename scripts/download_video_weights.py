"""下载 Wan2.1 视频引擎权重（Diffusers 格式，两仓库均 Apache-2.0、gated:false 无需 token）。

- 强制走 hf-mirror 镜像端点：本机直连 huggingface.co 会超时（WinError 10060）。
- snapshot_download 天然支持断点续传：中断后重跑本脚本，已完成的分片跳过、
  .incomplete 分片继续写（与音乐权重下载脚本同一策略）。
- 目标 models/wan2.1 采用 huggingface_hub 标准缓存布局（与 models/sd-realvis
  一致：blobs + models--Wan-AI--<repo>/snapshots/<sha>），引擎侧按快照目录离线加载。
- 跳过 assets/、examples/ 纯展示文件；保留 README/LICENSE 许可文本（随包再分发）。

用法（用任意含 huggingface_hub 的解释器，推荐 .venv-image）：
    python scripts\\download_video_weights.py            # 两个仓库都下
    python scripts\\download_video_weights.py wan-t2v    # 只下指定仓库（wan-t2v / wan-i2v）
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

# Windows 默认控制台代码页是 GBK，打不出中文和 emoji：不兜底的话脚本会在 print 时
# 直接抛 UnicodeEncodeError 崩掉，用户只看到「跑不起来」。errors="replace" 保证永不因此崩。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # 被重定向、已关闭或过老的流：忽略，不影响主流程
        pass
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

from huggingface_hub import snapshot_download  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "models" / "wan2.1"

REPOS = {
    "wan-t2v": "Wan-AI/Wan2.1-T2V-1.3B-Diffusers",
    "wan-i2v": "Wan-AI/Wan2.1-VACE-1.3B-diffusers",
}
IGNORE_PATTERNS = ["assets/*", "examples/*", ".gitattributes"]
MAX_ATTEMPTS = 8


def _dir_size(path: Path) -> float:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / 1024**3


def download(repo_id: str) -> str:
    last_exc: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return snapshot_download(
                repo_id=repo_id,
                cache_dir=str(CACHE),
                ignore_patterns=IGNORE_PATTERNS,
                max_workers=3,
            )
        except KeyboardInterrupt:
            raise
        except Exception as exc:  # noqa: BLE001 - 网络抖动重试；断点续传会跳过已完成分片
            last_exc = exc
            print(f"[video-weights] {repo_id} 第 {attempt} 次失败：{exc}", flush=True)
            time.sleep(10)
    raise SystemExit(f"[video-weights] 下载失败（连续 {MAX_ATTEMPTS} 次）：{repo_id}：{last_exc}")


def main() -> None:
    keys = sys.argv[1:] or list(REPOS)
    unknown = [k for k in keys if k not in REPOS]
    if unknown:
        raise SystemExit(f"未知仓库键：{unknown}（可用：{list(REPOS)}）")
    print(f"[video-weights] endpoint={os.environ['HF_ENDPOINT']} cache={CACHE}", flush=True)
    for key in keys:
        repo = REPOS[key]
        print(f"[video-weights] start {key} -> {repo}", flush=True)
        path = download(repo)
        ok_index = (Path(path) / "model_index.json").is_file()
        size = _dir_size(CACHE / f"models--{repo.replace('/', '--')}")
        print(
            f"[video-weights] DONE {key} snapshot={path} model_index={ok_index} repo_cache={size:.2f} GB",
            flush=True,
        )
        if not ok_index:
            raise SystemExit(f"[video-weights] 快照缺少 model_index.json，视为不完整：{path}")
    total = _dir_size(CACHE)
    print(f"[video-weights] ALL DONE models/wan2.1 total={total:.2f} GB", flush=True)


if __name__ == "__main__":
    main()
