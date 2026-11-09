"""SD1.5（RealVis V5.1）引擎的任务系统适配器（在 .venv-image 的 worker 子进程内运行）

用法（由 bootstrap 通过 ProcessEngineAdapter 启动）:
    python -m app.tasks.process_worker --adapter-factory app.tasks.sd_worker:create_adapter

环境变量（ProcessEngineAdapter 会把桌面进程的环境透传给子进程）:
    CHENGUAI_MODEL_CACHE_DIR  权重缓存根目录（默认 <data_root>/Models）
    CHENGUAI_OUTPUT_DIR       生成结果输出目录（默认 <data_root>/Output）
    CHENGUAI_SD_MODEL_ID      权重 id（默认取引擎 id；sd-realvis-i2i 会回落到 sd-realvis）
    CHENGUAI_SD_IMAGE_ROLE    板块角色：t2i=只做文生图 / i2i=只做图生图（默认不限制）
    CHENGUAI_SD_DEBUG_PROMPT  =1 时把 prompt/negative_prompt 的 type 与 repr 打到 stderr

文生图与图生图是两个独立引擎（sd-realvis / sd-realvis-i2i，各自独立 worker 子进程），
共用同一份本地权重、但互斥加载：任务队列串行，启动本次引擎前会先释放对方。
角色检查放在 worker 里，是为了让「板块与引擎对不上」这种错误在加载模型、吃掉几个
GB 显存之前就被拦下，而不是推理到一半才失败。

与旧 FLUX 适配器的差别只在两点：权重 id/环境变量改名；参数白名单多了 hires
（是否强制走「底图 + 分块高清重绘」，不传就由引擎按目标像素自动决定）。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, FrozenSet, Optional

from app.tasks.models import TaskRequest, TaskResult

# SD 引擎依赖 torch/diffusers，只存在于 .venv-image；为了让桌面 .venv
# 也能导入本模块做离线校验/测试，重依赖全部延迟到 _get_engine 内部导入。
_SD_ENGINE_CLASS = None
_SD_SUPPORTED_MODELS: Dict[str, Any] = {}


def _load_sd_engine_symbols() -> tuple[Any, Dict[str, Any]]:
    global _SD_ENGINE_CLASS, _SD_SUPPORTED_MODELS
    if _SD_ENGINE_CLASS is None:
        from app.engine.image.sd15 import SD15_SUPPORTED_MODELS, SD15ImageEngine

        _SD_ENGINE_CLASS = SD15ImageEngine
        _SD_SUPPORTED_MODELS = SD15_SUPPORTED_MODELS
    return _SD_ENGINE_CLASS, _SD_SUPPORTED_MODELS


_ALLOWED_PARAMS = {
    "width",
    "height",
    "steps",
    "seed",
    "negative_prompt",
    "init_image_path",
    "strength",
    "hires",
}


class SdTaskAdapter:
    """把 SD15ImageEngine 包装成任务系统的 EngineAdapter 协议实现。"""

    def __init__(
        self,
        engine_id: str = "sd-realvis",
        cache_dir: Optional[str] = None,
        output_dir: Optional[str] = None,
        image_role: Optional[str] = None,
    ):
        self.engine_id = engine_id
        self.task_types: FrozenSet[str] = frozenset({"image"})
        self.test_only = False
        self._cache_dir = cache_dir or os.environ.get("CHENGUAI_MODEL_CACHE_DIR", "./models")
        self._output_dir = output_dir or os.environ.get("CHENGUAI_OUTPUT_DIR", "./output")
        # 板块角色：t2i / i2i。空字符串＝不限制（历史行为，测试与自定义引擎走这条）。
        self.image_role = (image_role or os.environ.get("CHENGUAI_SD_IMAGE_ROLE", "")).strip().lower()
        self._engine: Optional[Any] = None

    def _model_id(self) -> str:
        """权重 id：sd-realvis-i2i 这类带板块后缀的引擎复用同一份本地图像权重。

        引擎 id 是「哪个板块的哪个实例」，权重 id 是「加载哪套模型文件」，两者不绑定：
        图生图引擎的权重 id 就是 sd-realvis（权重已经在 models\\sd-realvis 里，不需要
        也不应该再下载第二份），但它的进程与显存占用是独立的。
        """
        _, supported = _load_sd_engine_symbols()
        model_id = os.environ.get("CHENGUAI_SD_MODEL_ID") or self.engine_id
        if model_id not in supported:
            model_id = "sd-realvis"
        return model_id

    def _get_engine(self) -> Any:
        if self._engine is None:
            engine_cls, _ = _load_sd_engine_symbols()
            model_id = self._model_id()
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
            _, supported = _load_sd_engine_symbols()
            repo = supported.get(self._model_id(), {}).get("hf_repo", "?")
            role_note = {
                "t2i": "；板块：文生图（不接受垫图）",
                "i2i": "；板块：图生图（必须有垫图）",
            }.get(self.image_role, "")
            return True, (
                f"CUDA 可用：{name}；模型仓库 {repo}（权重在首次生成时离线加载，"
                "缺权重会直接报错、不会偷偷联网）" + role_note
            )
        except Exception as exc:  # noqa: BLE001 - 健康检查必须返回而非抛出
            return False, f"SD worker 环境异常：{exc}"

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
        if "hires" in params and not isinstance(params["hires"], bool):
            raise ValueError("参数 hires 必须是布尔值（true/false）")

        init = params.get("init_image_path")
        if self.image_role == "i2i" and not (isinstance(init, str) and init.strip()):
            raise ValueError(
                "图生图引擎需要垫图（init_image_path）：请先在「图生图」板块放入参考图；"
                "纯文字生成请用「文生图」板块（引擎 sd-realvis）"
            )
        if self.image_role == "t2i" and init is not None:
            raise ValueError(
                "文生图引擎不接受垫图：带参考图请改用「图生图」板块（引擎 sd-realvis-i2i）"
            )

        if init is not None:
            if not isinstance(init, str) or not init.strip():
                raise ValueError("参数 init_image_path 必须是非空字符串")
            if not Path(init).is_file():
                raise ValueError(f"图生图输入文件不存在：{init}")

    async def generate(self, request: TaskRequest) -> TaskResult:
        engine = self._get_engine()
        # 防御性收口：即便上游 JSON 里混进非字符串提示词，也在进入引擎前规整为合法 str。
        from app.engine.prompt_utils import coerce_prompt_text

        params: Dict[str, Any] = dict(request.parameters or {})
        if "negative_prompt" in params:
            params["negative_prompt"] = coerce_prompt_text(params["negative_prompt"])
        result = await engine.generate_image(
            prompt=coerce_prompt_text(request.prompt) or request.prompt,
            width=params.get("width", 512),
            height=params.get("height", 512),
            steps=params.get("steps"),
            seed=params.get("seed"),
            negative_prompt=params.get("negative_prompt"),
            init_image_path=params.get("init_image_path"),
            strength=params.get("strength", 0.6),
            hires=params.get("hires"),
        )
        return TaskResult(
            output_files=(result["path"],),
            metadata={
                "status": result["status"],
                "seed": result.get("seed"),
                "model_id": engine.model_id,
                "mode": "img2img" if params.get("init_image_path") else "txt2img",
                "hires": result.get("hires"),
                "tiles": result.get("tiles"),
                "base_size": result.get("base_size"),
                "target_size": result.get("target_size"),
                # 中文提示词桥接的痕迹：界面历史记录要能同时看到「用户写的原文」和
                # 「实际喂给 CLIP 的英文关键词」，否则用户会以为自己写的中文被吞了。
                "prompt_original": result.get("prompt_original"),
                "prompt_effective": result.get("prompt_effective"),
                "prompt_translated": result.get("prompt_translated"),
                "prompt_unmapped": result.get("prompt_unmapped") or [],
                "peak_vram_mb": result.get("peak_vram_mb"),
                "offload": result.get("offload"),
            },
        )

    async def shutdown(self) -> None:
        if self._engine is not None:
            await self._engine.shutdown()
            self._engine = None


def create_adapter() -> SdTaskAdapter:
    """process_worker --adapter-factory 的入口工厂。"""
    return SdTaskAdapter(engine_id=os.environ.get("CHENGUAI_SD_ENGINE_ID", "sd-realvis"))
