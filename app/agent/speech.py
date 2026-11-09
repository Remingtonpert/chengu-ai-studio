"""AI 智能体的语音能力：Windows 自带 SAPI 语音播报。

只调用系统组件（System.Speech），不需要下载任何权重，也不引入第三方依赖，
因此符合“只用免费可商用素材”的要求。文本通过临时文件传递，避免命令行引号与注入问题。

注意：本模块只做“念出来”（TTS）。系统级语音识别（听写）需要额外开启 Windows
语音识别服务，不属于本项目的免费可商用范围，界面不会假装支持。
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("chenguai.agent.speech")

# 语音列表缓存：装机后基本不变，5 分钟刷新一次足够。
_VOICE_TTL_SECONDS = 300.0
_MAX_SPEAK_CHARS = 1200

_SCRIPT_VOICES = """
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
foreach ($v in $synth.GetInstalledVoices()) {
  if ($v.Enabled) {
    $i = $v.VoiceInfo
    Write-Output ("{0}|{1}|{2}" -f $i.Name, $i.Culture, $i.Gender)
  }
}
"""

_SCRIPT_SPEAK = """
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$voice = $env:CHENGUAI_TTS_VOICE
if ($voice) { try { $synth.SelectVoice($voice) | Out-Null } catch { } }
$rate = 0
[void][int]::TryParse($env:CHENGUAI_TTS_RATE, [ref]$rate)
$synth.Rate = $rate
$text = (Get-Content -LiteralPath $env:CHENGUAI_TTS_TEXT_PATH -Raw -Encoding UTF8).Trim()
if ($text) { $synth.Wait($text) }
Write-Output "spoken"
"""


class SpeechUnavailable(RuntimeError):
    """系统语音组件不可用（非 Windows / 缺少语音 / 组件被策略禁用）。"""


def _shell() -> list[str]:
    exe = shutil.which("pwsh") or shutil.which("powershell")
    if not exe:
        raise SpeechUnavailable("本机没有 PowerShell，无法调用系统语音组件")
    return [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command"]


def _run(script: str, env: dict[str, str], timeout: float) -> str:
    command = _shell() + [script]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env={**os.environ, **env},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SpeechUnavailable(f"系统语音进程启动失败：{exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()[:300]
        raise SpeechUnavailable(f"系统语音组件返回错误：{detail or completed.returncode}")
    return completed.stdout or ""


_VOICE_CACHE: list[dict[str, str]] = []
_VOICE_CACHE_AT = 0.0


def available_voices(*, force: bool = False) -> list[dict[str, str]]:
    """列出系统已安装且已启用的语音（带缓存）。失败时抛 SpeechUnavailable。"""
    global _VOICE_CACHE, _VOICE_CACHE_AT
    now = time.monotonic()
    if not force and _VOICE_CACHE and now - _VOICE_CACHE_AT < _VOICE_TTL_SECONDS:
        return list(_VOICE_CACHE)
    output = _run(_SCRIPT_VOICES, {}, timeout=25.0)
    voices: list[dict[str, str]] = []
    for line in output.splitlines():
        parts = line.strip().split("|")
        if len(parts) != 3 or not parts[0]:
            continue
        voices.append({"name": parts[0], "culture": parts[1], "gender": parts[2]})
    if not voices:
        raise SpeechUnavailable("系统里没有已启用的语音（请在「设置 → 时间和语言 → 语音」里添加）")
    _VOICE_CACHE = voices
    _VOICE_CACHE_AT = now
    return list(voices)


def _is_cjk(text: str) -> bool:
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return cjk >= max(1, len(text.strip()) // 4)


def _pick_voice(text: str, requested: str, voices: list[dict[str, str]]) -> str:
    if requested:
        for voice in voices:
            if voice["name"].lower() == requested.lower():
                return voice["name"]
    want = "zh" if _is_cjk(text) else "en"
    preferred = ("huihui", "xiaoxiao", "kangkang", "zira", "david", "mark")
    for name in preferred:
        for voice in voices:
            if name in voice["name"].lower():
                return voice["name"]
    for voice in voices:
        if voice["culture"].lower().startswith(want):
            return voice["name"]
    return voices[0]["name"]


def status() -> dict[str, Any]:
    """给界面用的语音能力说明，不抛异常。"""
    try:
        voices = available_voices()
        return {
            "available": True,
            "voices": voices,
            "note": "",
            "platform": "Windows SAPI",
        }
    except SpeechUnavailable as exc:
        return {
            "available": False,
            "voices": [],
            "note": str(exc),
            "platform": "Windows SAPI",
        }


def speak(text: str, *, voice: str = "", rate: int = 0) -> dict[str, Any]:
    """朗读文本（阻塞直到念完，调用方放在线程池里跑）。"""
    body = (text or "").strip()
    if not body:
        raise SpeechUnavailable("没有要朗读的文本")
    if len(body) > _MAX_SPEAK_CHARS:
        body = body[:_MAX_SPEAK_CHARS]
        truncated = True
    else:
        truncated = False
    voices = available_voices()
    chosen = _pick_voice(body, voice, voices)
    handle = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".txt", delete=False
    )
    try:
        handle.write(body)
        handle.close()
        started = time.monotonic()
        _run(
            _SCRIPT_SPEAK,
            {
                "CHENGUAI_TTS_TEXT_PATH": str(Path(handle.name)),
                "CHENGUAI_TTS_VOICE": chosen,
                "CHENGUAI_TTS_RATE": str(max(-10, min(10, int(rate)))),
            },
            timeout=180.0,
        )
    finally:
        try:
            Path(handle.name).unlink(missing_ok=True)
        except OSError:  # pragma: no cover - 清理失败不影响结果
            logger.warning("临时朗读文本删除失败：%s", handle.name)
    return {
        "voice": chosen,
        "characters": len(body),
        "truncated": truncated,
        "seconds": round(time.monotonic() - started, 2),
    }
