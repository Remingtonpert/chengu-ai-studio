"""下载 ACE-Step 1.5 音乐权重（辰鹄AI创作中心 · AI音乐模块专用工具）。

用法（项目根目录，任意装有 huggingface_hub 的环境）：
    python scripts\\download_music_weights.py
默认走 hf-mirror.com 镜像、断点续传，目标目录为项目相对路径
models\\ace-step-1.5（便于打包迁移，不写死绝对路径）。
只下载官方 Python pipeline 的最小集：turbo DiT + 5Hz-LM-1.7B +
Qwen3-Embedding-0.6B 文本编码器 + VAE + 根 config，约 10.1 GB。
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

# Windows 默认控制台代码页是 GBK，打不出中文和 emoji：不兜底的话脚本会在 print 时
# 直接抛 UnicodeEncodeError 崩掉，用户只看到「跑不起来」。errors="replace" 保证永不因此崩。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # 被重定向、已关闭或过老的流：忽略，不影响主流程
        pass

from huggingface_hub import snapshot_download  # noqa: E402

REPO_ID = "ACE-Step/Ace-Step1.5"
ALLOW_PATTERNS = [
    "config.json",
    "acestep-v15-turbo/*",
    "acestep-5Hz-lm-1.7B/*",
    "Qwen3-Embedding-0.6B/*",
    "vae/*",
]


def main() -> int:
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    local_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(project_root, "models", "ace-step-1.5")
    print(f"repo={REPO_ID}\nendpoint={os.environ['HF_ENDPOINT']}\ntarget={local_dir}", flush=True)
    path = snapshot_download(
        repo_id=REPO_ID,
        local_dir=local_dir,
        allow_patterns=ALLOW_PATTERNS,
        resume_download=True,
        max_workers=2,
    )
    print(f"DONE {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
