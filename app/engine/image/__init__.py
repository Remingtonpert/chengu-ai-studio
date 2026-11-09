"""图片引擎子包 - 封装各类图片生成模型（当前：SD1.5 + RealVis V5.1）。

这里用 PEP 562 的模块级 __getattr__ 做惰性导出，原因很实际：torch/diffusers 只装在
.venv-image，桌面端 .venv 里根本没有。写成顶层 `from .sd15 import ...` 的话，
连 `import app.engine.image.plan`（纯标准库 + PIL，三端共用的显存预算规则）都会被
torch 拖下水，桌面端与 tests 全都导不进来。
"""

from __future__ import annotations

from typing import Any

__all__ = ["SD15ImageEngine", "SD15_SUPPORTED_MODELS", "SD15EngineError"]

_LAZY_NAMES = frozenset({"SD15ImageEngine", "SD15_SUPPORTED_MODELS", "SD15EngineError"})


def __getattr__(name: str) -> Any:
    if name in _LAZY_NAMES:
        from app.engine.image import sd15 as _sd15

        return getattr(_sd15, name)
    raise AttributeError(f"module {__name__!r} 没有属性 {name!r}")


def __dir__() -> list[str]:
    return sorted(list(__all__) + [name for name in globals() if not name.startswith("_")])
