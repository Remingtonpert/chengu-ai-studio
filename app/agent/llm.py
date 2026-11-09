"""本机大模型客户端（OpenAI 兼容接口），只用标准库 urllib。

这一层刻意做得很薄：AI 智能体的聊天与文案能力依赖本机的推理服务。默认端点是
项目自带的 llama.cpp 服务（tools\\llama_cpp\\llama-server.exe，http://127.0.0.1:11435/v1，
见 app/agent/local_server.py）；用户自己的 Ollama 等 OpenAI 兼容端点照样能连，
只要把 config.json 的 llm_endpoint 指过去。任何一步失败都抛出 LlmUnavailable，
带上人类可读的中文原因，界面据此诚实说明“当前只能做规则路由，不能聊天”，
绝不伪造回复内容。
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger("chenguai.agent.llm")

# 端点探活结果的缓存时长：避免每条命令都做一次 /models 请求。
_PROBE_TTL_SECONDS = 20.0
# 候选模型优先级：本机常见命名习惯，越靠前越新/越强。
_PREFERRED_ORDER = ("qwen3", "qwen2.5", "qwen", "deepseek", "gemma", "llama", "phi", "mistral")


class LlmUnavailable(RuntimeError):
    """本机大模型不可用（未启动 / 模型缺失 / 超时）。"""


class LocalLlmClient:
    """面向 OpenAI 兼容 ``/chat/completions`` 的最小客户端。"""

    def __init__(
        self,
        endpoint: str,
        model: str = "",
        *,
        timeout: float = 60.0,
        api_key: str = "",
    ) -> None:
        self.endpoint = (endpoint or "").rstrip("/")
        self.model = (model or "").strip()
        self.timeout = float(timeout)
        self.api_key = api_key.strip()
        self._models: list[str] = []
        self._probed_at = 0.0
        self._probe_error: str = ""
        self._last_model = ""  # 上一次真正用过的模型，交还显存时要点名它

    def invalidate(self) -> None:
        """作废端点探活缓存：刚拉起服务之后调用，下一句「可用了吗」就是现查的。"""
        self._models = []
        self._probed_at = 0.0
        self._probe_error = ""

    # ------------------------------------------------------------------ 基础
    def _request(self, suffix: str, payload: dict[str, Any] | None = None) -> Any:
        if not self.endpoint:
            raise LlmUnavailable("未配置大模型端点（config.json 的 llm_endpoint 为空）")
        url = self.endpoint + suffix
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Accept": "application/json"}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(url, data=data, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:200] if exc.fp else ""
            # llama-server 先绑端口后加载权重：加载期间整个端点回 503「Loading model」。
            # 这不是坏了，是还没好——话要说清楚，否则用户以为要重装。
            if exc.code == 503 and "loading" in detail.lower():
                raise LlmUnavailable(
                    "自带大模型正在加载权重（一般 30-90 秒），稍等几秒再发一次"
                ) from exc
            raise LlmUnavailable(
                f"本机大模型端点返回 {exc.code}：{detail or exc.reason}"
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise LlmUnavailable(
                f"连不上本机大模型端点 {self.endpoint}（{exc}）。"
                "这个端点由项目自带的推理服务提供：双击 scripts\\run_agent_llm.bat "
                "或跑 scripts\\agent_llm.ps1 -Start；第一次用先跑 scripts\\agent_llm.ps1 -Setup"
                "（会把 llama.cpp 与 GGUF 权重下到项目内）；"
                "也可以在 config.json 里把 llm_endpoint 改成你本机已有的端点"
                "（例如自己的 Ollama：http://127.0.0.1:11434/v1）"
            ) from exc
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LlmUnavailable(f"端点返回的不是 JSON：{raw[:120]}") from exc

    # ------------------------------------------------------------------ 模型
    def list_models(self, *, force: bool = False) -> list[str]:
        """列出端点上的可用模型（带 TTL 缓存）。失败时抛 LlmUnavailable。"""
        now = time.monotonic()
        if not force and self._models and now - self._probed_at < _PROBE_TTL_SECONDS:
            return list(self._models)
        body = self._request("/models")
        items = body.get("data") or body.get("models") or []
        names: list[str] = []
        for item in items:
            if isinstance(item, str):
                names.append(item)
            elif isinstance(item, dict):
                name = item.get("id") or item.get("name")
                if isinstance(name, str) and name:
                    names.append(name)
        self._models = names
        self._probed_at = now
        self._probe_error = ""
        return list(names)

    def resolve_model(self) -> str:
        """选定实际可用的模型：配置里的模型存在就用它，否则按优先级挑一个。

        选中的名字同时记进 ``self._last_model``：``unload()`` 不带参数时必须知道
        该点名卸载谁，否则智能体生成前排队的「交还显存」会空转。
        """
        names = self.list_models()
        if not names:
            raise LlmUnavailable(
                "端点上没有任何可用模型：自带服务请确认 models\\llm\\gguf 里有 GGUF 权重"
                "（跑一次 scripts\\agent_llm.ps1 -Setup）；用自己的端点则先拉取一个模型"
            )
        selected = ""
        if self.model:
            if self.model in names:
                selected = self.model
            else:
                for name in names:
                    if name.split(":")[0] == self.model.split(":")[0]:
                        logger.info("配置的模型 %s 不存在，改用同族模型 %s", self.model, name)
                        selected = name
                        break
        if not selected:
            for want in _PREFERRED_ORDER:
                for name in names:
                    if want in name.lower():
                        logger.info("配置的模型不可用，自动选用 %s", name)
                        selected = name
                        break
                if selected:
                    break
        if not selected:
            selected = names[0]
        self._last_model = selected
        return selected

    def status(self) -> dict[str, Any]:
        """给界面用的能力说明，不抛异常。"""
        try:
            names = self.list_models()
            model = self.resolve_model()
            return {
                "configured_endpoint": self.endpoint,
                "configured_model": self.model,
                "available": bool(names),
                "model": model,
                "models": names[:24],
                "note": "" if names else "端点上没有可用模型",
            }
        except LlmUnavailable as exc:
            return {
                "configured_endpoint": self.endpoint,
                "configured_model": self.model,
                "available": False,
                "model": "",
                "models": [],
                "note": str(exc),
            }

    # ------------------------------------------------------------------ 对话
    def chat(self, messages: list[dict[str, str]], *, temperature: float = 0.6,
             max_tokens: int = 640) -> str:
        payload = {
            "model": self.resolve_model(),
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        body = self._request("/chat/completions", payload)
        choices = body.get("choices") or []
        if not choices:
            raise LlmUnavailable("模型没有返回任何内容")
        message = choices[0].get("message") or {}
        text = message.get("content")
        if not isinstance(text, str) or not text.strip():
            raise LlmUnavailable("模型返回了空回复")
        return text.strip()

    # -------------------------------------------------------------- 交还显存
    def _native_url(self, suffix: str) -> str:
        """把 OpenAI 兼容端点换算成同一个服务根下的原生端点。

        llama.cpp 的 /unload 和 Ollama 的 /api/generate 都挂在 /v1 的上一层；
        keep_alive 这种「交还显存」语义只有 Ollama 原生接口支持。
        """
        base = self.endpoint.rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3]
        return base.rstrip("/") + suffix

    def _unload_llama_server(self) -> bool:
        """项目自带的 llama.cpp 服务：POST /unload 交还显存，不用点名模型。"""
        url = self._native_url("/unload")
        try:
            request = urllib.request.Request(
                url,
                data=b"{}",
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=15.0) as response:
                response.read()
            logger.info("已请求 %s 交还显存（llama.cpp /unload）", url)
            return True
        except urllib.error.HTTPError as exc:
            # 404/405 = 这个服务没有 /unload（比如连的是用户自己的 Ollama），走原生接口
            logger.info("%s 不支持 /unload（HTTP %s），改用原生接口", url, exc.code)
            return False
        except Exception as exc:  # noqa: BLE001 - 交还失败不能影响生成任务
            logger.info("llama.cpp /unload 未生效（%s）：%s", url, exc)
            return False

    def unload(self, *, model: str = "", keep_alive_seconds: int = 0) -> bool:
        """请推理服务把模型从显存里交还出去（尽力而为，失败只记日志）。

        12GB 单卡上大模型与图像/视频/音乐引擎抢同一块显存：聊完不交还，
        下一次生成就会因为「空闲显存不足」失败。这里只提请求，不假设一定生效。
        """
        if self.endpoint and self._unload_llama_server():
            return True
        name = model or self._last_model
        if not name or not self.endpoint:
            return False
        url = self._native_url("/api/generate")
        payload = {"model": name, "keep_alive": int(keep_alive_seconds)}
        try:
            request = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=15.0) as response:
                response.read()
            logger.info("已请求 %s 交还显存（keep_alive=%ds）", name, int(keep_alive_seconds))
            return True
        except Exception as exc:  # noqa: BLE001 - 交还失败不能影响生成任务
            logger.info("显存交还请求未生效（%s）：%s", url, exc)
            return False
