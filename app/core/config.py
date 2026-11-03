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
            # 真实生成引擎（FLUX）需用户确认模型下载/许可后才启用；默认关闭。
            "engines_enabled": False,
            "image_venv_python": "",
            # Web 前端服务固定监听 127.0.0.1:8765（仅本机可访问）。
            "web_host": "127.0.0.1",
            "web_port": 8765,
            # 提示词助手：优先调用本地 OpenAI 兼容大模型（默认 Ollama）；
            # 置空或连接失败时自动退回内置模板增强。
            "llm_endpoint": "http://127.0.0.1:11434/v1",
            "llm_model": "qwen2.5:7b-instruct",
        }

    # 整型配置项及其允许范围。
    _INT_RANGES: dict[str, tuple[int, int]] = {"web_port": (1, 65535)}
    # 非路径的普通字符串键（不做 Path 展开，允许留空表示"未配置"）。
    _FREE_TEXT_KEYS = frozenset({"web_host", "llm_endpoint", "llm_model"})
    # 允许留空的字符串键。
    _OPTIONAL_KEYS = frozenset({"image_venv_python", "llm_model", "llm_endpoint"})

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return self.defaults.copy()
        try:
            raw: Any = json.loads(self.path.read_text(encoding="utf-8"))
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