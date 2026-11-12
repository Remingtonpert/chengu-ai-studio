"""图片引擎子包 - 封装各类图片生成模型。

现役文生图：**Sana-1.6B 多语言版**（app/engine/image/sana.py，引擎 id sana-multiling，
Apache-2.0，原生支持中文/英文/中英混合 Prompt，fp16 权重 9.06GB 在 models/sana-multiling）。
现役图生图：不在这里，走 Wan2.1-VACE（app/engine/video/wan.py，引擎 id wan-i2i）。
后备（保留代码、不注册、不上架）：SD1.5 + RealVis V5.1（app/engine/image/sd15.py），
它是历史上唯一实测过显存峰值的出图路径（4359MB），想自行验证需手工注册。

这里用 PEP 562 的模块级 __getattr__ 做惰性导出，原因很实际：torch/diffusers 只装在
.venv-image（图像）与 .venv-video（视频），桌面端 .venv 里根本没有。写成顶层
`from .sana import ...` 的话，连 `import app.engine.image.plan`（纯标准库 + PIL，
三端共用的显存/像素预算规则）都会被 torch 拖下水，桌面端与 tests 全都导不进来。
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "SanaImageEngine",
    "SANA_SUPPORTED_MODELS",
    "SanaEngineError",
    "SD15ImageEngine",
    "SD15_SUPPORTED_MODELS",
    "SD15EngineError",
]

# 名字 -> 所在模块。两个引擎模块都 import torch/diffusers，所以一律惰性取。
_LAZY_NAMES: dict[str, str] = {
    "SanaImageEngine": "sana",
    "SANA_SUPPORTED_MODELS": "sana",
    "SanaEngineError": "sana",
    "SD15ImageEngine": "sd15",
    "SD15_SUPPORTED_MODELS": "sd15",
    "SD15EngineError": "sd15",
}


def __getattr__(name: str) -> Any:
    module = _LAZY_NAMES.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} 没有属性 {name!r}")
    from importlib import import_module

    return getattr(import_module(f"app.engine.image.{module}"), name)


def __dir__() -> list[str]:
    return sorted(list(__all__) + [name for name in globals() if not name.startswith("_")])
