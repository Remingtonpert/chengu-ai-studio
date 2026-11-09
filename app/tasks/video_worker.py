"""Wan2.1 引擎的任务系统适配器（在 .venv-video 的 worker 子进程内运行）

同一份 worker 承载四个引擎：视频 wan-t2v / wan-i2v（mp4）与图片 wan-t2i / wan-i2i
（PNG，单帧模式，权重与视频共用 models/wan2.1）。按注入的引擎 ID 决定任务类型。

用法（由 bootstrap 通过 ProcessEngineAdapter 启动）:
    python -m app.tasks.process_worker --adapter-factory app.tasks.video_worker:create_adapter

环境变量（ProcessEngineAdapter 会把桌面/Web 进程的环境透传给子进程）:
    CHENGUAI_VIDEO_ENGINE_ID       wan-t2v / wan-i2v / wan-t2i / wan-i2i（必需，注册时按引擎分别注入）
    CHENGUAI_VIDEO_MODEL_CACHE_DIR Wan 权重缓存根（HF 缓存布局，默认 ./models/wan2.1）
    CHENGUAI_OUTPUT_DIR            生成结果输出目录（默认 <data_root>/Output）
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, FrozenSet, Optional

from app.tasks.models import TaskRequest, TaskResult

# Wan 引擎依赖 torch/diffusers，只存在于 .venv-video；为了让桌面 .venv
# 也能导入本模块做离线校验/测试，重依赖全部延迟到 _get_engine 内部导入。
_WAN_ENGINE_CLASS = None
_WAN_SUPPORTED_MODELS: Dict[str, Any] = {}
_WAN_SNAPSHOT_PICKER = None


def _load_wan_engine_symbols() -> tuple[Any, Dict[str, Any], Any]:
    global _WAN_ENGINE_CLASS, _WAN_SUPPORTED_MODELS, _WAN_SNAPSHOT_PICKER
    if _WAN_ENGINE_CLASS is None:
        from app.engine.video.wan import (
            WAN_SUPPORTED_MODELS,
            WanVideoEngine,
            _pick_local_snapshot,
        )

        _WAN_ENGINE_CLASS = WanVideoEngine
        _WAN_SUPPORTED_MODELS = WAN_SUPPORTED_MODELS
        _WAN_SNAPSHOT_PICKER = _pick_local_snapshot
    return _WAN_ENGINE_CLASS, _WAN_SUPPORTED_MODELS, _WAN_SNAPSHOT_PICKER

# wan-t2v 不接受参考图；wan-i2v 必须带首帧（init_image_path）
_ALLOWED_PARAMS_T2V = {"width", "height", "num_frames", "steps", "seed", "negative_prompt"}
_ALLOWED_PARAMS_I2V = _ALLOWED_PARAMS_T2V | {"init_image_path"}
# 图片档（单帧）没有帧数概念；wan-i2i 必须带参考图
_ALLOWED_PARAMS_T2I = {"width", "height", "steps", "seed", "negative_prompt"}
_ALLOWED_PARAMS_I2I = _ALLOWED_PARAMS_T2I | {"init_image_path"}

_VIDEO_ENGINE_IDS = ("wan-t2v", "wan-i2v")
_IMAGE_ENGINE_IDS = ("wan-t2i", "wan-i2i")
_ALL_ENGINE_IDS = _VIDEO_ENGINE_IDS + _IMAGE_ENGINE_IDS


class WanTaskAdapter:
    """把 WanVideoEngine 包装成任务系统的 EngineAdapter 协议实现。"""

    def __init__(
        self,
        engine_id: str = "wan-t2v",
        cache_dir: Optional[str] = None,
        output_dir: Optional[str] = None,
    ):
        if engine_id not in _ALL_ENGINE_IDS:
            raise ValueError(f"不支持的 Wan 引擎 ID: {engine_id}")
        self.engine_id = engine_id
        self.is_image = engine_id in _IMAGE_ENGINE_IDS
        self.task_types: FrozenSet[str] = frozenset({"image" if self.is_image else "video"})
        self.test_only = False
        self._cache_dir = cache_dir or os.environ.get(
            "CHENGUAI_VIDEO_MODEL_CACHE_DIR", str(Path("./models") / "wan2.1")
        )
        self._output_dir = output_dir or os.environ.get("CHENGUAI_OUTPUT_DIR", "./output")
        self._engine: Optional[Any] = None

    @property
    def _is_ref_engine(self) -> bool:
        """需要垫图的引擎：wan-i2v（首帧）与 wan-i2i（参考图）。"""
        return self.engine_id in ("wan-i2v", "wan-i2i")

    @property
    def _is_i2v(self) -> bool:
        return self.engine_id == "wan-i2v"

    def _get_engine(self) -> Any:
        if self._engine is None:
            engine_cls, _, _ = _load_wan_engine_symbols()
            self._engine = engine_cls(
                model_id=self.engine_id,
                cache_dir=self._cache_dir,
                output_dir=self._output_dir,
            )
        return self._engine

    async def health_check(self) -> tuple[bool, str]:
        try:
            import torch  # noqa: F401 - 仅探测依赖是否可用

            if not torch.cuda.is_available():
                return False, "worker 环境没有可用的 CUDA 设备（RTX 3060 12GB 级）"
            name = torch.cuda.get_device_name(0)
            _, supported, pick_snapshot = _load_wan_engine_symbols()
            repo = supported.get(self.engine_id, {}).get("hf_repo", "?")
            cache = Path(self._cache_dir)
            if pick_snapshot(cache, repo) is None:
                return False, (
                    f"权重缺失：{repo}（期望 HF 缓存目录 {cache}）。"
                    "请运行 scripts\\download_video_weights.py 下载（Apache-2.0，无需 token）"
                )
            return True, f"CUDA 可用：{name}；模型 {repo} 权重已就位（首次生成时离线加载）"
        except Exception as exc:  # noqa: BLE001 - 健康检查必须返回而非抛出
            return False, f"Wan worker 环境异常：{exc}"

    def validate_request(self, request: TaskRequest) -> None:
        if not request.prompt or not request.prompt.strip():
            raise ValueError("提示词不能为空")
        params = request.parameters or {}
        if self.is_image:
            allowed = _ALLOWED_PARAMS_I2I if self._is_ref_engine else _ALLOWED_PARAMS_T2I
            ref_label = "图生图"
        else:
            allowed = _ALLOWED_PARAMS_I2V if self._is_ref_engine else _ALLOWED_PARAMS_T2V
            ref_label = "图生视频"
        unknown = set(params) - allowed
        if unknown:
            raise ValueError(f"不支持的参数：{', '.join(sorted(unknown))}")

        for key in ("width", "height", "num_frames", "steps", "seed"):
            if key in params and not isinstance(params[key], int):
                raise ValueError(f"参数 {key} 必须是整数")

        init = params.get("init_image_path")
        if self._is_ref_engine:
            if not init or not isinstance(init, str) or not init.strip():
                raise ValueError(f"{ref_label}缺少垫图：请先上传参考图（对应标签页）")
            if not Path(init).is_file():
                raise ValueError(f"{ref_label}垫图文件不存在：{init}")
        elif init is not None:
            raise ValueError(
                f"文生{'图' if self.is_image else '视频'}不接受参考图："
                f"请改用{'图生图' if self.is_image else '图生视频'}引擎（wan-i2i / wan-i2v）"
            )

    async def generate(self, request: TaskRequest) -> TaskResult:
        engine = self._get_engine()
        # 防御性收口：即便上游 JSON 里混进非字符串提示词，也在进入引擎前规整为合法 str。
        from app.engine.prompt_utils import coerce_prompt_text

        params: Dict[str, Any] = dict(request.parameters or {})
        if self.is_image:
            result = await engine.generate_image(
                prompt=coerce_prompt_text(request.prompt) or request.prompt,
                image_path=params.get("init_image_path") if self._is_ref_engine else None,
                width=params.get("width", 832),
                height=params.get("height", 480),
                steps=params.get("steps"),
                seed=params.get("seed"),
                negative_prompt=params.get("negative_prompt"),
            )
            return TaskResult(
                output_files=(result["path"],),
                metadata={
                    "status": result["status"],
                    "seed": result.get("seed"),
                    "model_id": engine.model_id,
                    "mode": "i2i" if self._is_ref_engine else "t2i",
                    "frames": 1,
                    "width": result.get("width"),
                    "height": result.get("height"),
                    "peak_vram_mb": result.get("peak_vram_mb"),
                },
            )
        result = await engine.generate_video(
            prompt=coerce_prompt_text(request.prompt) or request.prompt,
            image_path=params.get("init_image_path") if self._is_i2v else None,
            num_frames=params.get("num_frames", 81),
            width=params.get("width", 512),
            height=params.get("height", 384),
            steps=params.get("steps"),
            seed=params.get("seed"),
            negative_prompt=params.get("negative_prompt"),
        )
        return TaskResult(
            output_files=(result["path"],),
            metadata={
                "status": result["status"],
                "seed": result.get("seed"),
                "model_id": engine.model_id,
                "mode": "i2v" if self._is_i2v else "t2v",
                "frames": result.get("frames"),
                "fps": result.get("fps"),
                "seconds": result.get("seconds"),
                "width": result.get("width"),
                "height": result.get("height"),
            },
        )

    async def shutdown(self) -> None:
        if self._engine is not None:
            await self._engine.shutdown()
            self._engine = None


def create_adapter() -> WanTaskAdapter:
    """process_worker --adapter-factory 的入口工厂（按注入的引擎 ID 建 t2v 或 i2v 适配器）。"""
    return WanTaskAdapter(
        engine_id=os.environ.get("CHENGUAI_VIDEO_ENGINE_ID", "wan-t2v")
    )
