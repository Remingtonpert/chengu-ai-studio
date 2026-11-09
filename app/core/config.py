"""Validated JSON configuration persistence."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from app.core.paths import AppPaths


class ConfigError(ValueError):
    """Raised when stored configuration is invalid or unreadable."""


class ConfigStore:
    def __init__(self, paths: AppPaths) -> None:
        self.path = paths.config_file
        self.defaults: dict[str, Any] = {
            "models_dir": str(paths.models_dir),
            "output_dir": str(paths.output_dir),
            # 自动另存目录：留空＝只存项目内 output\；填了则每次生成完成后再复制一份
            # 到这个目录（用户可以选本地硬盘任意位置，包括移动盘）。手动「另存为」不受它影响。
            "auto_save_dir": "",
            # 真实生成引擎（Wan2.1 视频）需用户确认模型下载/许可后才启用；默认关闭。
            # 文生图 / 图生图 / AI 音乐的引擎已按「≤6GB 峰值显存 + 中英文 Prompt + 权重可商用」
            # 三条要求下架，对应的 venv 与权重配置键一并移除（旧 config.json 里残留的键会被忽略）。
            "engines_enabled": False,
            # 视频引擎（Wan2.1 wan-t2v / wan-i2v）的环境与权重目录。
            # 之前 defaults 里没有这两个键：_validate 只遍历 defaults，用户手工
            # 写进 config.json 的 video_venv_python / video_model_dir 会在下一次
            # save() 时被静默删掉，设置页也永远看不见它们（换机器/换盘符时无解）。
            "video_venv_python": "",
            "video_model_dir": str(Path(__file__).resolve().parents[2] / "models" / "wan2.1"),
            # Web 前端服务固定监听 127.0.0.1:8765（仅本机可访问）。
            "web_host": "127.0.0.1",
            "web_port": 8765,
            # 提示词助手 / 智能体聊天：只连本机 OpenAI 兼容大模型端点，
            # 置空或连接失败时自动退回规则路由 + 诚实降级文案，绝不编造回复。
            # 「AI 智能体」的大模型不再依赖 Ollama：权重（GGUF）放在项目内
            # models\llm\gguf，推理用项目自带的 tools\llama_cpp\llama-server.exe，
            # 由 scripts\agent_llm.ps1（或程序自己按需拉起）启动，只绑 127.0.0.1 : 11435。
            # 用户自己装的 Ollama（11434）完全不受影响：想用它就把这里改回 11434/v1。
            "llm_endpoint": "http://127.0.0.1:11435/v1",
            # 智能体专用模型名：与 llama-server 的 --alias 一致（app/agent/local_server.py）。
            # 这个名字在端点上不存在时，app/agent/llm.py 会先试同族、再按优先级挑一个，
            # 所以换机器不会「聊天完全不能用」，只是日志里会多一条自动换模型的提示。
            "llm_model": "qwen3:8b",
            # 自带的 llama.cpp 服务没在跑时，是否由程序按需把它拉起来（只绑回环）。
            # 关掉就完全手动：双击 scripts\run_agent_llm.bat 或跑 scripts\agent_llm.ps1 -Start。
            "llm_autostart": True,
            # 自带服务要加载的 GGUF 权重；留空 = 自动挑 models\llm\gguf 里最大的那个。
            "llm_model_path": "",
            # 大模型冷启动要把 6-8GB 权重搬进显存，第一次对话就可能几十秒：
            # 90 秒的旧上限会在第一次对话时报「连不上本机大模型端点」。
            "llm_timeout_seconds": 240,
            # 「AI 智能体」等场景上传的参考图落盘目录（桌面端与 Web 控制台共用）。
            "upload_dir": str(paths.data_root / "uploads"),
            # 界面语言（桌面端持久化）：词典见 app/web/static/i18n/，zh = 简体中文源语言。
            # 网页端把选择存在浏览器 localStorage，两端互不覆盖。
            "ui_language": "zh",
        }

    # 整型配置项及其允许范围。
    _INT_RANGES: dict[str, tuple[int, int]] = {
        "web_port": (1, 65535),
        "llm_timeout_seconds": (15, 900),
    }
    # 非路径的普通字符串键（不做 Path 展开，允许留空表示"未配置"）。
    _FREE_TEXT_KEYS = frozenset(
        {
            "web_host",
            "llm_endpoint",
            "llm_model",
            "llm_model_path",
            "ui_language",
        }
    )
    # 允许留空的字符串键。
    _OPTIONAL_KEYS = frozenset(
        {
            "video_venv_python",
            "llm_model",
            "llm_endpoint",
            "llm_model_path",
            "auto_save_dir",
        }
    )

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return self.defaults.copy()
        try:
            # utf-8-sig：Windows 记事本「UTF-8」保存会写 BOM，普通 utf-8 读出来
            # 第一个字符是 \ufeff，json.loads 直接报「Unexpected UTF-8 BOM」，
            # 整个程序起不来（ConfigError），用户完全看不出是编码问题。
            raw: Any = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigError(f"无法读取配置文件：{self.path}") from exc
        if not isinstance(raw, dict):
            raise ConfigError("配置文件必须包含 JSON 对象")

        # 兼容早期版本：配置文件中允许存在已废弃的多余键，加载时忽略；
        # 保存时只写当前已知键，从而在下次保存时自动清理。
        config = self._validate(raw, context="配置文件")
        return config

    def save(self, config: dict[str, Any]) -> None:
        normalized = self._validate(config, context="配置")

        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.path.with_suffix(".json.tmp")
        try:
            temporary_path.write_text(
                json.dumps(normalized, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary_path, self.path)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()

    def _validate(self, raw: dict[str, Any], context: str) -> dict[str, Any]:
        """按 defaults 逐项校验并返回完整配置（缺失键取默认值）。"""
        validated: dict[str, Any] = {}
        for key, default in self.defaults.items():
            value = raw.get(key, default)
            if isinstance(default, bool):
                if not isinstance(value, bool):
                    raise ConfigError(f"{context}项 {key} 必须是布尔值")
                validated[key] = value
            elif isinstance(default, int):
                if isinstance(value, bool) or not isinstance(value, int):
                    raise ConfigError(f"{context}项 {key} 必须是整数")
                low, high = self._INT_RANGES.get(key, (-(2**31), 2**31 - 1))
                if not low <= value <= high:
                    raise ConfigError(f"{context}项 {key} 必须在 {low}~{high} 范围内")
                validated[key] = value
            elif key in self._FREE_TEXT_KEYS:
                if not isinstance(value, str):
                    raise ConfigError(f"{context}项 {key} 必须是字符串")
                if not value.strip() and key not in self._OPTIONAL_KEYS:
                    raise ConfigError(f"{context}项 {key} 必须是非空字符串")
                validated[key] = value.strip()
            else:
                if not isinstance(value, str):
                    raise ConfigError(f"{context}项 {key} 必须是路径字符串")
                # models_dir/output_dir 必须非空；可选路径（如 venv python）允许为空。
                if not value.strip() and key not in self._OPTIONAL_KEYS:
                    raise ConfigError(f"{context}项 {key} 必须是非空路径字符串")
                # Path("") 会变成 "."，所以先判空再展开。
                validated[key] = str(Path(value).expanduser()) if value.strip() else ""
        return validated