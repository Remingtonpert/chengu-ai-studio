"""把 docs\\使用说明书.md 转成 Word/WPS 能直接打开的 .doc。

为什么是 .doc 但内容是 RTF：用户要的只是「.doc 文档即可」，而本机没有任何文档库
（python-docx 之类只装在 .venv 里给智能体用，不该把文档生成绑到那个依赖上）。
RTF 是 Word/WPS 双击就能打开的纯文本格式，用标准库就能生成，中文靠 \\uN? 转义，
不依赖代码页，也不会出现乱码。

用法（任意解释器都行，只用标准库）：
    python scripts\\build_manual.py
    python scripts\\build_manual.py --src docs\\使用说明书.md --dst docs\\辰鹄AI创作中心-使用说明书.doc

支持的 Markdown 子集（够用且刻意不多做）：
    # / ## / ###  标题      - 无序列表      1. 有序列表（按字面保留）
    **粗体**     `等宽`     空行分段
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SRC = ROOT / "docs" / "使用说明书.md"
DEFAULT_DST = ROOT / "docs" / "辰鹄AI创作中心-使用说明书.doc"

FONT_TABLE = r"{\fonttbl{\f0\fnil\fcharset134 Microsoft YaHei;}{\f1\fmodern\fcharset0 Consolas;}}"
INLINE_TOKEN = re.compile(r"(\*\*.+?\*\*|`[^`]+`)")
BULLET = "\u2022"


def escape(text: str) -> str:
    """RTF 文本转义：反斜杠/花括号要转义，非 ASCII 一律走 \\uN? 转义。"""
    out: list[str] = []
    for char in text:
        code = ord(char)
        if char in "\\{}":
            out.append("\\" + char)
        elif code < 128:
            out.append(char)
        elif code > 0xFFFF:  # RTF \u 只吃 16 位，超出的按代理对拆
            surrogate = code - 0x10000
            high = 0xD800 + (surrogate >> 10)
            low = 0xDC00 + (surrogate & 0x3FF)
            out.append(f"\\u{high}?\\u{low}?")
        else:
            out.append(f"\\u{code}?")
    return "".join(out)


def runs(text: str, *, bold: bool = False) -> str:
    """把一行里的 **粗体** 与 `等宽` 翻成 RTF run；其余用主字体。"""
    pieces: list[str] = []
    for token in INLINE_TOKEN.split(text):
        if not token:
            continue
        if token.startswith("**") and token.endswith("**") and len(token) > 4:
            pieces.append(r"{\f0\b " + escape(token[2:-2]) + r"}")
        elif token.startswith("`") and token.endswith("`") and len(token) > 2:
            pieces.append(r"{\f1\fs20 " + escape(token[1:-1]) + r"}")
        else:
            pieces.append(escape(token))
    body = "".join(pieces)
    return rf"{{\f0\b {body}}}" if bold else body


def paragraph(text: str, *, size: int = 22, bold: bool = False, center: bool = False, indent: int = 0) -> str:
    align = r"\qc" if center else r"\ql"
    hang = r"\fi-360\li360" if indent else ""
    return rf"\pard{align}\pardir\partightenfactor0\sa120{hang}\f0\fs{size} {runs(text, bold=bold)}\par"


def convert(markdown: str) -> str:
    body: list[str] = [
        r"\pard\qc\sa240\f0\fs40\b " + runs("辰鹄 AI 创作中心 · 使用说明书", bold=True) + r"\par",
        r"\pard\qc\sa360\f0\fs20 "
        + runs("版本 2026-11-10 · 文生图引擎：Sana-1.6B 多语言版（sana-multiling） · 桌面端与网页端一致")
        + r"\par",
    ]
    for raw in markdown.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped or set(stripped) <= {"-", "="} and len(stripped) >= 3:
            continue
        if stripped.startswith("### "):
            body.append(paragraph(stripped[4:], size=26, bold=True))
        elif stripped.startswith("## "):
            body.append(paragraph(stripped[3:], size=30, bold=True))
            body[-1] = body[-1].replace(r"\sa120", r"\sb240\sa160")
        elif stripped.startswith("# "):
            body.append(paragraph(stripped[2:], size=34, bold=True, center=True))
        elif stripped.startswith("- "):
            body.append(paragraph(f"{BULLET}  {stripped[2:]}", indent=360))
        else:
            body.append(paragraph(stripped))
    header = r"\rtf1\ansi\ansicpg936\deff0\deflang1033\deflangfe2052" + FONT_TABLE
    return header + r"\viewkind4\uc1\pard\f0\fs22" + "\n" + "\n".join(body) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="生成使用说明书 .doc（内容即 RTF）")
    parser.add_argument("--src", type=Path, default=DEFAULT_SRC)
    parser.add_argument("--dst", type=Path, default=DEFAULT_DST)
    args = parser.parse_args()

    source = args.src.read_text(encoding="utf-8")
    rtf = convert(source)
    args.dst.parent.mkdir(parents=True, exist_ok=True)
    args.dst.write_text(rtf, encoding="ascii", errors="backslashreplace")
    print(f"已生成：{args.dst}（{args.dst.stat().st_size} 字节，源文件 {args.src}）")
    print("打开方式：Word / WPS / LibreOffice 双击即可（.doc 里面是 RTF，不是二进制 Word）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
