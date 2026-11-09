"""桌面端界面翻译：与 Web 控制台共用同一份词典。

词典目录：``app/web/static/i18n/<code>.json``，key 就是界面上出现的中文源串
（源语言 = 简体中文，见 ``source-zh.txt``）。这样源码里照常写中文，只在需要跟随
语言的外壳文案（导航、Tab、按钮、输入框提示、状态标签）上套一层 :func:`tr`。

范围是诚实的：词典里没有的条目——引擎自己回报的长错误（显存不足、引擎未注册等）、
日志正文、模型名——原样保留中文，不做机翻式伪装。词典缺失或语言代码不认识时，
一律回退简体中文，并在 :func:`coverage` 里如实报告翻译覆盖率。

语言选择的持久化两端分开：桌面端写 config.json 的 ``ui_language``，网页端写浏览器
localStorage（``chenguai.lang``），互不覆盖。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

CATALOG_DIR = Path(__file__).resolve().parents[1] / "web" / "static" / "i18n"
SOURCE_LIST = CATALOG_DIR / "source-zh.txt"

# 动态消息里允许做片段替换的键（例如「排队中 · 第 N 位」），
# 只替换这些约定片段，避免把长句里的普通词硬凑在一起。
FRAGMENT_KEYS: tuple[str, ...] = ("排队中 · 第", "生成中", "位")

_CJK_RANGES = (
    (0x3400, 0x9FFF),
    (0x3040, 0x30FF),
    (0xAC00, 0xD7AF),
)


def _has_cjk(text: str) -> bool:
    for char in text:
        code = ord(char)
        for low, high in _CJK_RANGES:
            if low <= code <= high:
                return True
    return False


class Translator:
    """按语言代码缓存词典并提供 :meth:`tr`。不认识的语言代码回退 ``zh``。"""

    def __init__(self, language: str = "zh") -> None:
        self._cache: dict[str, dict[str, str]] = {}
        self._languages: list[dict[str, Any]] | None = None
        self._language = "zh"
        self._table: dict[str, str] = {}
        self._fragments: list[tuple[str, str]] = []
        self.set_language(language)

    # ------------------------------------------------------------- 目录

    def languages(self) -> list[dict[str, Any]]:
        """返回语言清单（code/native/english/dir）；读不到目录文件时只给简体中文。"""
        if self._languages is not None:
            return self._languages
        try:
            raw = json.loads((CATALOG_DIR / "index.json").read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            raw = []
        entries: list[dict[str, Any]] = []
        if isinstance(raw, list):
            for item in raw:
                if not isinstance(item, dict) or not item.get("code"):
                    continue
                entries.append(
                    {
                        "code": str(item["code"]),
                        "native": str(item.get("native") or item["code"]),
                        "english": str(item.get("english") or item["code"]),
                        "dir": str(item.get("dir") or "ltr"),
                    }
                )
        if not any(item["code"] == "zh" for item in entries):
            entries.insert(0, {"code": "zh", "native": "简体中文", "english": "Chinese", "dir": "ltr"})
        self._languages = entries
        return entries

    def _load_table(self, code: str) -> dict[str, str]:
        if code in self._cache:
            return self._cache[code]
        table: dict[str, str] = {}
        if code != "zh":
            path = CATALOG_DIR / f"{code}.json"
            try:
                raw = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError) as exc:
                # 词典缺失/损坏：回退中文，并在日志里留痕（不静默假装已翻译）。
                self._load_error = f"{code}: {exc}"
                raw = {}
            if isinstance(raw, dict):
                for key, value in raw.items():
                    if isinstance(value, str):
                        table[str(key)] = value
        self._cache[code] = table
        return table

    # --------------------------------------------------------------- 状态

    @property
    def language(self) -> str:
        return self._language

    @property
    def rtl(self) -> bool:
        return self._dir == "rtl"

    @property
    def load_error(self) -> str:
        return getattr(self, "_load_error", "")

    def language_label(self, code: str | None = None) -> str:
        target = code or self._language
        for entry in self.languages():
            if entry["code"] == target:
                return f"{entry['native']} / {entry['english']}"
        return target

    def set_language(self, code: str) -> str:
        code = (code or "zh").strip().lower()
        known = {entry["code"] for entry in self.languages()}
        if code not in known:
            code = "zh"
        self._language = code
        self._table = self._load_table(code)
        self._fragments = sorted(
            ((key, self._table[key]) for key in FRAGMENT_KEYS if key in self._table),
            key=lambda pair: len(pair[0]),
            reverse=True,
        )
        for entry in self.languages():
            if entry["code"] == code:
                self._dir = entry["dir"]
                break
        else:  # pragma: no cover - zh 一定在清单里
            self._dir = "ltr"
        return code

    # --------------------------------------------------------------- 翻译

    def tr(self, text: str) -> str:
        if not text or not self._table:
            return text
        exact = self._table.get(text)
        # 空串是合法翻译（某些语言不需要「位」这类后缀片段），但不能让整段文案消失，
        # 所以 exact 只认非空值，空串只在片段替换里起作用。
        if exact:
            return exact
        if not _has_cjk(text):
            return text
        out = text
        for key, value in self._fragments:
            if key in out:
                out = out.replace(key, value)
        return out

    def coverage(self) -> tuple[int, int]:
        """(已翻译条数, 源串总数)。诊断页用它如实报告，而不是宣称「全部语言完成」。"""
        try:
            total = len(
                [
                    line
                    for line in SOURCE_LIST.read_text(encoding="utf-8-sig").splitlines()
                    if line.strip()
                ]
            )
        except OSError:
            total = 0
        return (len(self._table), total)


_ACTIVE = Translator()


def set_language(code: str) -> str:
    return _ACTIVE.set_language(code)


def current_language() -> str:
    return _ACTIVE.language


def is_rtl() -> bool:
    return _ACTIVE.rtl


def language_label(code: str | None = None) -> str:
    return _ACTIVE.language_label(code)


def native_name(code: str | None = None) -> str:
    """当前语言的母语名（侧栏 🌏 按钮上显示的就是它）。"""
    target = code or _ACTIVE.language
    for entry in _ACTIVE.languages():
        if entry["code"] == target:
            return str(entry["native"])
    return target


def languages() -> list[dict[str, Any]]:
    return _ACTIVE.languages()


def coverage() -> tuple[int, int]:
    return _ACTIVE.coverage()


def load_error() -> str:
    return _ACTIVE.load_error


def _source_lines() -> frozenset[str]:
    global _SOURCE_SET
    if _SOURCE_SET is None:
        try:
            lines = SOURCE_LIST.read_text(encoding="utf-8-sig").splitlines()
        except OSError:
            lines = []
        _SOURCE_SET = frozenset(line for line in lines if line.strip())
    return _SOURCE_SET


_SOURCE_SET: frozenset[str] | None = None


def is_source(text: str) -> bool:
    """这段文字是不是已登记在 source-zh.txt 里的界面源串。

    界面各处（参数面板、系统页顶栏）用这个判定要不要翻译：只有登记过的静态
    文案才换语言，动态内容（会话标题、GPU 摘要、任务正文）永远不会命中，
    因此不会被语言切换过程改坏。
    """
    return bool(text) and text in _source_lines()


def tr(text: str) -> str:
    """把界面上的中文源串换成当前语言的文案；没有对应词条就原样返回中文。"""
    return _ACTIVE.tr(text)
