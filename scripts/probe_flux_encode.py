"""验证（只加载文本编码器，CPU）：中文/英文/混合提示词通过 diffusers FluxPipeline.encode_prompt。

跳过 transformer / VAE（不占显存、省内存），只装 CLIP-L + T5-XXL 到 CPU fp16，
直接调用 FluxPipeline.encode_prompt —— 正是此前 TextEncodeInput 报错的那条代码路径。
"""
import os
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

import torch
from diffusers import FluxPipeline
from transformers import CLIPTextModel, CLIPTokenizer, T5EncoderModel, T5Tokenizer

REPO = "black-forest-labs/FLUX.1-schnell"
CACHE = r"models\flux-schnell"

print("loading CLIP ...", flush=True)
tok1 = CLIPTokenizer.from_pretrained(REPO, subfolder="tokenizer", cache_dir=CACHE, local_files_only=True)
enc1 = CLIPTextModel.from_pretrained(REPO, subfolder="text_encoder", cache_dir=CACHE, local_files_only=True, dtype=torch.float16)
print("loading T5-XXL (CPU fp16) ...", flush=True)
tok2 = T5Tokenizer.from_pretrained(REPO, subfolder="tokenizer_2", cache_dir=CACHE, local_files_only=True)
enc2 = T5EncoderModel.from_pretrained(REPO, subfolder="text_encoder_2", cache_dir=CACHE, local_files_only=True, dtype=torch.float16)

pipe = FluxPipeline(
    vae=None,
    vae_scale_factor=8,
    text_encoder=enc1,
    tokenizer=tok1,
    text_encoder_2=enc2,
    tokenizer_2=tok2,
    transformer=None,
    scheduler=None,
)

CASES = {
    "zh": "古风，青城山，山间云雾，一座小亭子，清晨，柔和天光，写实风景，8k，高清，干净画面",
    "en": "A pavilion on mountain, mist, morning light, photorealistic",
    "mix": "古风 pavilion 亭子 mist 清晨 morning light 写实 8k",
}
ok = True
for label, p in CASES.items():
    try:
        pe, pooled, tids = pipe.encode_prompt(prompt=p, device=torch.device("cpu"))
        print(f"OK   encode_prompt [{label}] -> prompt_embeds {tuple(pe.shape)} pooled {tuple(pooled.shape)}", flush=True)
    except Exception as exc:
        ok = False
        print(f"FAIL encode_prompt [{label}] -> {type(exc).__name__}: {str(exc)[:200]}", flush=True)
print("RESULT:", "PASS" if ok else "FAIL")
