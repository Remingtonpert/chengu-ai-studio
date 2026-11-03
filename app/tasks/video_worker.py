"""视频引擎的任务系统适配器（在 .venv-image 的 worker 子进程内运行）

用法（由 web bootstrap 通过 ProcessEngineAdapter 启动）:
    python -m app.tasks.process_worker --adapter-factory app.tasks.video_worker:create_adapter

环境变量（ProcessEngineAdapter 会把主进程的环境透传给子进程）:
    CHENGUAI_MODEL_CACHE_DIR   模型权重缓存目录（默认 <data_root>/Models）
    CHENGUAI_OUTPUT_DIR        生成结果输出目录（默认 <data_root>/Output）
    CHENGUAI_VIDEO_ENGINE_ID   引擎 ID（默认 anima-image2video）

文生视频实现说明：Anima 是图生视频模型，本适配器把"文生视频"实现为两段流水线——
先用 FLUX（同 worker 进程内懒加载）按提示词生成首帧，再交给 Anima 生成视频。
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Dict, FrozenSet, Optional

from app.tasks.models import TaskRequest, TaskResult

# 重依赖（torch/diffusers）延迟导入，保证 .venv 也能导入本模块做离线校验。
_VIDEO_ENGINE_CLASS = None
_FLUX_ENGINE_CLASS = None


def _load_video_engine_symbols() -> Any:
    global _VIDEO_ENGINE_CLASS
    if _VIDEO_ENGINE_CLASS is None:
        from app.engine.video.anima import AnimaVideoEngine

        _VIDEO_ENGINE_CLASS = AnimaVideoEngine
    return _VIDEO_ENGINE_CLASS


def _load_flux_engine_class() -> Any:
    global _FLUX_ENGINE_CLASS
    if _FLUX_ENGINE_CLASS is None:
        from app.engine.image.flux import FluxImageEngine

        _FLUX_ENGINE_CLASS = FluxImageEngine
    return _FLUX_ENGINE_CLASS


_ALLOWED_PARAMS = {
    "width",
    "height",
    "num_frames",
    "steps",
    "seed",
    "negative_prompt",
    "init_image_path",
}


class VideoTaskAdapter:
    """把 AnimaVideoEngine（+可选 FLUX 首帧）包装成任务系统的 EngineAdapter 协议实现。"""

    def __init__(
        self,
        engine_id: str = "anima-image2video",
        cache_dir: Optional[str] = None,
        output_dir: Optional[str] = None,
    ):
        self.engine_id = engine_id
        # i2v 引擎同时承载 text-to-video（FLUX 首帧 + Anima），任务类型都是 video。
        self.task_types: FrozenSet[str] = frozenset({"video"})
        self.test_only = False
        self._cache_dir = cache_dir or os.environ.get("CHENGUAI_MODEL_CACHE_DIR", "./models")
        self._output_dir = output_dir or os.environ.get("CHENGUAI_OUTPUT_DIR", "./output")
        self._engine: Optional[Any] = None
        self._flux_engine: Optional[Any] = None

    def _get_engine(self) -> Any:
        if self._engine is None:
            engine_cls = _load_video_engine_symbols()
            self._engine = engine_cls(
                cache_dir=str(Path(self._cache_dir) / "anima-image2video"),
                output_dir=self._output_dir,
            )
        return self._engine

    def _get_flux_engine(self) -> Any:
        if self._flux_engine is None:
            flux_cls = _load_flux_engine_class()
            model_id = os.environ.get("CHENGUAI_FLUX_MODEL_ID", "flux-schnell")
            self._flux_engine = flux_cls(
                model_id=model_id,
                cache_dir=str(Path(self._cache_dir) / model_id),
                output_dir=str(Path(self._output_dir) / "_t2v_frames"),
            )
        return self._flux_engine

    async def health_check(self) -> tuple[bool, str]:
        try:
            import torch  # noqa: F401 - 仅探测依赖是否可用

            if not torch.cuda.is_available():
                return False, "worker 环境没有可用的 CUDA 设备（RTX 3060 12GB 起）"
            name = torch.cuda.get_device_name(0)
            return True, (
                f"CUDA 可用：{name}；模型仓库 moovenio/Anima_2B_model"
                "（权重将在首次生成时下载/加载，文生视频还会用到 FLUX 首帧）"
            )
        except Exception as exc:  # noqa: BLE001 - 健康检查必须返回而非抛出
            return False, f"video worker 环境异常：{exc}"

    def validate_request(self, request: TaskRequest) -> None:
        if not request.prompt or not request.prompt.strip():
            raise ValueError("提示词不能为空")
        params = request.parameters or {}
        unknown = set(params) - _ALLOWED_PARAMS
        if unknown:
            raise ValueError(f"不支持的参数：{', '.join(sorted(unknown))}")

        for key in ("width", "height", "num_frames", "steps", "seed"):
            if key in params and not isinstance(params[key], int):
                raise ValueError(f"参数 {key} 必须是整数")

        init = params.get("init_image_path")
        if init is not None:
            if not isinstance(init, str) or not init.strip():
                raise ValueError("参数 init_image_path 必须是非空字符串")
            if not Path(init).is_file():
                raise ValueError(f"图生视频输入文件不存在：{init}")

    async def generate(self, request: TaskRequest) -> TaskResult:
        params: Dict[str, Any] = dict(request.parameters or {})
        init_image = params.get("init_image_path")
        mode = "img2video"

        if not init_image:
            # 文生视频：先用 FLUX 生成首帧，再走 Anima 图生视频。
            mode = "txt2video"
            flux = self._get_flux_engine()
            frame = await flux.generate_image(
                prompt=request.prompt,
                width=params.get("width", 512),
                height=params.get("height", 384),
                steps=4,
                seed=params.get("seed"),
            )
            init_image = frame["path"]

        engine = self._get_engine()
        result = await engine.generate_video(
            prompt=request.prompt,
            image_path=init_image,
            height=params.get("height", 384),
            width=params.get("width", 512),
            num_frames=params.get("num_frames", 33),
            steps=params.get("steps"),
            seed=params.get("seed"),
            negative_prompt=params.get("negative_prompt"),
        )
        output_files = [result["path"]]
        if mode == "txt2video" and init_image:
            output_files.insert(0, init_image)
        return TaskResult(
            output_files=tuple(output_files),
            metadata={
                "status": result["status"],
                "seed": result.get("seed"),
                "mode": mode,
                "model_id": "anima-image2video",
                "num_frames": result.get("num_frames"),
            },
        )

    async def shutdown(self) -> None:
        if self._engine is not None:
            await self._engine.shutdown()
            self._engine = None
        if self._flux_engine is not None:
            await self._flux_engine.shutdown()
            self._flux_engine = None


def create_adapter() -> VideoTaskAdapter:
    """process_worker --adapter-factory 的入口工厂。"""
    return VideoTaskAdapter(
        engine_id=os.environ.get("CHENGUAI_VIDEO_ENGINE_ID", "anima-image2video")
    )
