"""下载 Sana-1.6B 多语言文生图权重（Diffusers 格式，Apache-2.0、gated:false 无需 token）。

选型依据（2026-11-10 复核，全部取自模型卡原文与 HF 元数据）：
- 商用：仓库 license=apache-2.0；文本编码器为 Gemma-2-2B-IT，附带 Gemma Terms of Use
  （允许商用，附禁止用途条款）。权重已内置在本仓库 text_encoder/ 下，不需要登录、
  不需要同意 Google 的 gated 协议。
- 中英文：模型卡原文「This model is fine-tuned from ... Sana_1600M_1024px_BF16 and it
  supports Emoji, Chinese and English and all mixed prompts.」——原生中英混合提示词。
- 显存：1.6B Linear-DiT + DC-AE（32 倍下采样 VAE），模型卡称「deployable on laptop GPU」；
  引擎侧默认逐模块 CPU 卸载，把常驻显存压到 6GB 以内（真机峰值待实测）。

- 强制走 hf-mirror 镜像端点：本机直连 huggingface.co 会超时（WinError 10060）。
  注意：这只用于下载权重；许可与「支持中英文」的取证一律以 huggingface.co 原始仓库为准。
- snapshot_download 天然支持断点续传：中断后重跑本脚本，已完成的分片跳过。
- 只取 fp16 变体（transformer/vae/text_encoder 的 fp16 分片），约 9.3 GB；
  仓库里的 fp32 权重（另外 12 GB）不下载。
- 目标 models/sana-multiling 采用 huggingface_hub 标准缓存布局（blobs +
  models--Efficient-Large-Model--<repo>/snapshots/<sha>），引擎侧按快照目录离线加载。

用法（用 .venv-image 的解释器，它已具备 diffusers/transformers/huggingface_hub）：
    .venv-image\Scripts\python.exe scripts\download_sana_weights.py
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
CACHE = ROOT / "models" / "sana-multiling"

REPO = "Efficient-Large-Model/Sana_1600M_1024px_MultiLing_diffusers"

# 反向黑名单：仓库里 fp32 与 fp16 两套权重并存，只排除 fp32 那三件（合计 12.3GB），
# 其余（配置、调度器、分词器、许可文本、fp16 权重与其 index json）全部下载，约 9.3GB。
# 为什么用黑名单而不是白名单：分片权重的 index 文件名很容易漏写
# （本仓库是 text_encoder/model.safetensors.index.fp16.json，不是常规命名），
# 漏了它 diffusers 加载时会报「缺 index」，宁可全收再去掉明显不需要的。
IGNORE_PATTERNS = [
    ".gitattributes",
    "assets/*",
    "examples/*",
    "*/model-0000*-of-*.safetensors",                    # text_encoder fp32 分片
    "*/diffusion_pytorch_model-0000*-of-*.safetensors",  # transformer fp32 分片
    "*/diffusion_pytorch_model.safetensors",             # vae fp32（fp16 文件名带 .fp16，不会被误伤）
]
REQUIRED_FILES = [
    "model_index.json",
    "LICENSE",
    "scheduler/scheduler_config.json",
    "text_encoder/config.json",
    "text_encoder/model.safetensors.index.fp16.json",
    "text_encoder/model.fp16-00001-of-00002.safetensors",
    "text_encoder/model.fp16-00002-of-00002.safetensors",
    "tokenizer/special_tokens_map.json",
    "tokenizer/tokenizer.json",
    "tokenizer/tokenizer.model",
    "tokenizer/tokenizer_config.json",
    "transformer/config.json",
    "transformer/diffusion_pytorch_model.fp16.safetensors",
    "vae/config.json",
    "vae/diffusion_pytorch_model.fp16.safetensors",
]
MAX_ATTEMPTS = 8


def _dir_size(path: Path) -> float:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / 1024**3


def download() -> str:
    last_exc: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return snapshot_download(
                repo_id=REPO,
                cache_dir=str(CACHE),
                ignore_patterns=IGNORE_PATTERNS,
                max_workers=3,
            )
        except KeyboardInterrupt:
            raise
        except Exception as exc:  # noqa: BLE001 - 网络抖动重试；断点续传会跳过已完成分片
            last_exc = exc
            print(f"[sana-weights] {REPO} 第 {attempt} 次失败：{exc}", flush=True)
            time.sleep(10)
    raise SystemExit(f"[sana-weights] 下载失败（连续 {MAX_ATTEMPTS} 次）：{REPO}：{last_exc}")


def main() -> None:
    print(f"[sana-weights] endpoint={os.environ['HF_ENDPOINT']} cache={CACHE}", flush=True)
    print(f"[sana-weights] start -> {REPO} (fp16 only, 约 9.3 GB)", flush=True)
    path = download()
    snapshot = Path(path)
    missing = [rel for rel in REQUIRED_FILES if not (snapshot / rel).is_file()]
    size = _dir_size(CACHE / f"models--{REPO.replace('/', '--')}")
    print(
        f"[sana-weights] DONE snapshot={snapshot} repo_cache={size:.2f} GB missing={missing or '无'}",
        flush=True,
    )
    if missing:
        raise SystemExit("[sana-weights] 快照不完整，缺上述文件；重跑本脚本即可续传。")
    print(f"[sana-weights] ALL DONE models/sana-multiling total={_dir_size(CACHE):.2f} GB", flush=True)


if __name__ == "__main__":
    main()
