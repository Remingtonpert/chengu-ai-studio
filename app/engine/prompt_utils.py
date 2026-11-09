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
        # 严格解码：errors="replace" 会把坏字节悄悄变成 U+FFFD，引擎照样跑完，
        # 产物却是乱码提示词生成的「没人要的图」，白烧十几分钟 GPU。
        # 坏字节直接抛 ValueError，让调用方（worker / 引擎入口）报可读错误。
        try:
            parts.append(value.decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise ValueError(
                f"提示词字节序列不是合法 UTF-8（{exc}）；已拒绝生成，"
                "请确认前端/脚本按 UTF-8 传参，不要按 gbk/cp936 编码"
            ) from exc
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _walk(item, parts)
        return
    parts.append(str(value))


def contains_lone_surrogate(value: Any) -> bool:
    """检测字符串中是否混入**孤立代理码位**（U+D800–U+DFFF）。

    这是进程间传输编码错乱的典型痕迹：例如 worker 的 stdin 按 gbk/cp936 +
    `errors="surrogateescape"` 解码 UTF-8 字节时，中文会解出 `\\udcXX` 这样的坏字符。
    这类 str 无法转成 Rust `String`，tokenizers 会把它当成非法入参并报
    "TextEncodeInput must be Union[TextInputSequence, ...]"——报错信息完全看不出根因，
    所以在引擎入口显式检测并给出可读错误。
    """
    if not isinstance(value, str):
        return False
    return any(0xD800 <= ord(ch) <= 0xDFFF for ch in value)


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
