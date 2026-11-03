"""FLUX 引擎的任务系统适配器（在 .venv-image 的 worker 子进程内运行）

用法（由 bootstrap 通过 ProcessEngineAdapter 启动）:
    python -m app.tasks.process_worker --adapter-factory app.tasks.flux_worker:create_adapter

环境变量（ProcessEngineAdapter 会把桌面进程的环境透传给子进程）:
    CHENGUAI_MODEL_CACHE_DIR  FLUX 权重缓存目录（默认 <data_root>/Models）
    CHENGUAI_OUTPUT_DIR       生成结果输出目录（默认 <data_root>/Output）
    CHENGUAI_FLUX_MODEL_ID    flux-schnell / flux-dev（默认 flux-schnell）
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Dict, FrozenSet, Optional

from app.tasks.models import TaskRequest, TaskResult

# FLUX 引擎依赖 torch/diffusers，只存在于 .venv-image；为了让桌面 .venv
# 也能导入本模块做离线校验/测试，重依赖全部延迟到 _get_engine 内部导入。
_FLUX_ENGINE_CLASS = None
_FLUX_SUPPORTED_MODELS: Dict[str, Any] = {}


def _load_flux_engine_symbols() -> tuple[Any, Dict[str, Any]]:
    global _FLUX_ENGINE_CLASS, _FLUX_SUPPORTED_MODELS
    if _FLUX_ENGINE_CLASS is None:
        from app.engine.image.flux import FLUX_SUPPORTED_MODELS, FluxImageEngine

        _FLUX_ENGINE_CLASS = FluxImageEngine
        _FLUX_SUPPORTED_MODELS = FLUX_SUPPORTED_MODELS
    return _FLUX_ENGINE_CLASS, _FLUX_SUPPORTED_MODELS

_ALLOWED_PARAMS = {
    "width",
    "height",
    "steps",
    "seed",
    "negative_prompt",
    "init_image_path",
    "strength",
}


class FluxTaskAdapter:
    """把 FluxImageEngine 包装成任务系统的 EngineAdapter 协议实现。"""

    def __init__(
        self,
        engine_id: str = "flux-schnell",
        cache_dir: Optional[str] = None,
        output_dir: Optional[str] = None,
    ):
        self.engine_id = engine_id
        self.task_types: FrozenSet[str] = frozenset({"image"})
        self.test_only = False
        self._cache_dir = cache_dir or os.environ.get("CHENGUAI_MODEL_CACHE_DIR", "./models")
        self._output_dir = output_dir or os.environ.get("CHENGUAI_OUTPUT_DIR", "./output")
        self._engine: Optional[Any] = None

    def _get_engine(self) -> Any:
        if self._engine is None:
            engine_cls, supported = _load_flux_engine_symbols()
            model_id = os.environ.get("CHENGUAI_FLUX_MODEL_ID", self.engine_id)
            self._engine = engine_cls(
                model_id=model_id,
                cache_dir=str(Path(self._cache_dir) / model_id),
                output_dir=self._output_dir,
            )
        return self._engine

    async def health_check(self) -> tuple[bool, str]:
        try:
            import torch  # noqa: F401 - 仅探测依赖是否可用

            if not torch.cuda.is_available():
                return False, "worker 环境没有可用的 CUDA 设备（RTX 3060 12GB 起）"
            name = torch.cuda.get_device_name(0)
            _, supported = _load_flux_engine_symbols()
            repo = supported.get(self.engine_id, {}).get("hf_repo", "?")
            return True, f"CUDA 可用：{name}；模型仓库 {repo}（权重将在首次生成时下载/加载）"
        except Exception as exc:  # noqa: BLE001 - 健康检查必须返回而非抛出
            return False, f"FLUX worker 环境异常：{exc}"

    def validate_request(self, request: TaskRequest) -> None:
        if not request.prompt or not request.prompt.strip():
            raise ValueError("提示词不能为空")
        params = request.parameters or {}
        unknown = set(params) - _ALLOWED_PARAMS
        if unknown:
            raise ValueError(f"不支持的参数：{', '.join(sorted(unknown))}")

        for key in ("width", "height", "steps", "seed"):
            if key in params and not isinstance(params[key], int):
                raise ValueError(f"参数 {key} 必须是整数")
        if "strength" in params and not isinstance(params["strength"], (int, float)):
            raise ValueError("参数 strength 必须是数字")

        init = params.get("init_image_path")
        if init is not None:
            if not isinstance(init, str) or not init.strip():
                raise ValueError("参数 init_image_path 必须是非空字符串")
            if not Path(init).is_file():
                raise ValueError(f"图生图输入文件不存在：{init}")

    async def generate(self, request: TaskRequest) -> TaskResult:
        engine = self._get_engine()
        params: Dict[str, Any] = dict(request.parameters or {})
        result = await engine.generate_image(
            prompt=request.prompt,
            width=params.get("width", 512),
            height=params.get("height", 512),
            steps=params.get("steps"),
            seed=params.get("seed"),
            negative_prompt=params.get("negative_prompt"),
            init_image_path=params.get("init_image_path"),
            strength=params.get("strength", 0.6),
        )
        return TaskResult(
            output_files=(result["path"],),
            metadata={
                "status": result["status"],
                "seed": result.get("seed"),
                "model_id": engine.model_id,
                "mode": "img2img" if params.get("init_image_path") else "txt2img",
            },
        )

    async def shutdown(self) -> None:
        if self._engine is not None:
            await self._engine.shutdown()
            self._engine = None


def create_adapter() -> FluxTaskAdapter:
    """process_worker --adapter-factory 的入口工厂。"""
    return FluxTaskAdapter(
        engine_id=os.environ.get("CHENGUAI_FLUX_ENGINE_ID", "flux-schnell")
    )
