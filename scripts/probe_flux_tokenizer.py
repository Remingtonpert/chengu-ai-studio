"""最小复现：FLUX schnell 快照 tokenizer 在 transformers 5.19 + tokenizers 0.23 下的调用形状测试。

只加载 tokenizer（CPU、离线、秒级），逐一尝试 diffusers FluxPipeline 实际使用的调用形状，
定位 'TextEncodeInput must be Union[...]' 究竟由哪种输入形状触发。
"""
import os
os.environ.setdefault("HF_HUB_OFFLINE", "1")

import traceback
from transformers import AutoTokenizer

REPO = "black-forest-labs/FLUX.1-schnell"
CACHE = r"models\flux-schnell"

def _resolve_src():
    from pathlib import Path
    snaps = Path(CACHE, "models--black-forest-labs--FLUX.1-schnell", "snapshots")
    if snaps.is_dir():
        for d in snaps.iterdir():
            if (d / "tokenizer" / "tokenizer_config.json").is_file():
                return str(d)
    return REPO

SRC = _resolve_src()

prompts = {
    "zh": "古风，勇峻山，山间云雾，一座小亭子，清晨，柔和天光，写实风景，8k，高清，干净画面",
    "en": "A pavilion on mountain, mist, morning light, photorealistic",
    "mix": "山间 pavilion 亭子 morning mist 8k",
}

def try_call(label, fn):
    try:
        out = fn()
        shape = tuple(out["input_ids"].shape) if hasattr(out, "__getitem__") else "?"
        print(f"OK   {label} -> {shape}")
    except Exception as exc:
        print(f"FAIL {label} -> {type(exc).__name__}: {str(exc)[:120]}")

for sub in ("tokenizer", "tokenizer_2"):
    print(f"===== {sub} =====")
    try:
        tok = AutoTokenizer.from_pretrained(SRC, subfolder=sub, local_files_only=True)
    except Exception as exc:
        print(f"load failed: {type(exc).__name__}: {exc}")
        continue
    ml = getattr(tok, "model_max_length", 77)
    for plabel, p in prompts.items():
        try_call(f"{sub} str[{plabel}]", lambda tok=tok: tok(p, padding="max_length", max_length=min(ml, 512), truncation=True, return_tensors="pt"))
        try_call(f"{sub} [str][{plabel}]", lambda tok=tok: tok([p], padding="max_length", max_length=min(ml, 512), truncation=True, return_tensors="pt"))
    # diffusers _get_clip_prompt_embeds 前置 maybe_convert_prompt 的 tokenize 路径
    try:
        toks = tok.tokenize(prompts["zh"])
        print(f"OK   tokenize(zh) -> {len(toks)} tokens; added_tokens_encoder? {hasattr(tok, 'added_tokens_encoder')}")
    except Exception as exc:
        print(f"FAIL tokenize -> {type(exc).__name__}: {exc}")
    # 可疑形状：list[list[str]]（嵌套）与 bytes
    try_call(f"{sub} nested[[str]]", lambda tok=tok: tok([prompts['zh']], padding="max_length", truncation=True, return_tensors="pt") and tok([[prompts['en']]], return_tensors="pt"))
    try_call(f"{sub} bytes", lambda tok=tok: tok(prompts['en'].encode('utf-8'), return_tensors="pt"))
