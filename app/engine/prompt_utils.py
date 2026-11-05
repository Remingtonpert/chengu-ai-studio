"""提示词规整工具：把任意来源的提示词转成 tokenizers 认可的纯文本。

背景：HuggingFace tokenizers 的 Rust 后端要求输入必须是 str 或 List[str]；
一旦上游（前端 JSON、脚本、第三方调用方）传进嵌套 list、bytes、数字等，
就会抛出 "TextEncodeInput must be Union[TextInputSequence, Tuple[...]]"。
本模块在引擎入口处统一收口：纯中文、纯英文、中英混合都能安全通过。

注意：本模块不导入 torch/diffusers，可被桌面 .venv 与 worker .venv-image 共用。
"""

from __future__ import annotations

from typing import Any, Optional


def _walk(value: Any, parts: list[str]) -> None:
    """深度优先展开任意嵌套结构，把其中的 str/bytes 片段收集到 parts。"""
    if value is None:
        return
    if isinstance(value, str):
        parts.append(value)
        return
    if isinstance(value, (bytes, bytearray)):
        parts.append(value.decode("utf-8", errors="replace"))
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _walk(item, parts)
        return
    parts.append(str(value))


def coerce_prompt_text(value: Any, *, joiner: str = ", ") -> Optional[str]:
    """把任意形态的提示词规整为干净的 str（None 原样返回 None）。

    - str：strip 后返回（中文/英文/混合一律透传，不做任何转义）。
    - bytes/bytearray：按 UTF-8 解码。
    - list/tuple（含任意层嵌套）：深度展平，片段用 joiner（默认 ", "）连接。
    - 其它标量：str() 字符串化。
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip()
    parts: list[str] = []
    _walk(value, parts)
    return joiner.join(piece.strip() for piece in parts if piece and piece.strip())
