"""Sana 多语言文生图引擎的任务系统适配器（在 .venv-image 的 worker 子进程内运行）

用法（由 bootstrap 通过 ProcessEngineAdapter 启动）:
    python -m app.tasks.process_worker --adapter-factory app.tasks.sana_worker:create_adapter

环境变量（ProcessEngineAdapter 会把桌面进程的环境透传给子进程）:
    CHENGUAI_SANA_MODEL_CACHE_DIR Sana 权重根目录（注册时注入 models/sana-multiling；
                                  未设置时回落到 CHENGUAI_MODEL_CACHE_DIR/<model_id>）
    CHENGUAI_MODEL_CACHE_DIR  权重缓存根目录（默认 <data_root>/Models）
    CHENGUAI_OUTPUT_DIR       生成结果输出目录（默认 <data_root>/Output）
    CHENGUAI_SANA_MODEL_ID    权重 id（默认 sana-multiling；写了不认识的 id 一律回落，不炸 worker）
    CHENGUAI_SANA_ENGINE_ID   引擎 id（注册时注入）
    CHENGUAI_SANA_DEBUG_PROMPT =1 时把 prompt/negative_prompt 的 type 与 repr 打到 stderr

这个适配器只做文生图：Sana 在 diffusers 0.41 里没有 img2img 管线，带参考图的请求属于
板块用错，必须在加载 9GB 权重之前就被拦下（角色检查放在 worker 里，就是为了不吃显存
不吃内存先报错），而不是推理到一半才失败。

与 SD1.5 适配器（app/tasks/sd_worker.py）的差别：不接受 init_image_path / strength / hires
（没有图生图，也没有分块重绘），参数白名单只剩宽高、步数、种子、负向词。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, FrozenSet, Optional

from app.tasks.models import TaskRequest, TaskResult

# Sana 引擎依赖 torch/diffusers，只存在于 .venv-image；为了让桌面 .venv
# 也能导入本模块做离线校验/测试，重依赖全部延迟到 _get_engine 内部导入。
_SANA_ENGINE_CLASS = None
_SANA_SUPPORTED_MODELS: Dict[str, Any] = {}

SANA_DEFAULT_MODEL_ID = "sana-multiling"


def _load_sana_engine_symbols() -> tuple[Any, Dict[str, Any]]:
    global _SANA_ENGINE_CLASS, _SANA_SUPPORTED_MODELS
    if _SANA_ENGINE_CLASS is None:
        from app.engine.image.sana import SANA_SUPPORTED_MODELS, SanaImageEngine

        _SANA_ENGINE_CLASS = SanaImageEngine
        _SANA_SUPPORTED_MODELS = SANA_SUPPORTED_MODELS
    return _SANA_ENGINE_CLASS, _SANA_SUPPORTED_MODELS


_ALLOWED_PARAMS = {
    "width",
    "height",
    "steps",
    "seed",
    "negative_prompt",
}

# 明确不收的参数：写了就直接报错，而不是静默丢掉。用户以为垫图生效了、结果却和没垫一样，
# 是最难查的一类问题；报错反而能马上把「这个板块没有图生图」这件事讲清楚。
_REJECTED_PARAMS = {
    "init_image_path": "Sana 文生图只有文生图管线，带参考图请改用「图生图」板块（引擎 wan-i2i）",
    "strength": "Sana 没有图生图，不存在去噪强度这个参数",
    "hires": "Sana 没有分块高清重绘；更大画幅由引擎按像素预算自动「出底图 + 放大」，无需此参数",
    "image_path": "Sana 文生图不接受输入图片",
}

# 缺省宽高：与桌面/网页面板的默认档位同一份数字（1024×1024，Sana 的单遍甜点档）。
_DEFAULT_SIDE = 1024


class SanaTaskAdapter:
    """把 SanaImageEngine 包装成任务系统的 EngineAdapter 协议实现。"""

    def __init__(
        self,
        engine_id: str = SANA_DEFAULT_MODEL_ID,
        cache_dir: Optional[str] = None,
        output_dir: Optional[str] = None,
    ):
        self.engine_id = engine_id
        self.task_types: FrozenSet[str] = frozenset({"image"})
        self.test_only = False
        self._cache_dir = cache_dir or os.environ.get("CHENGUAI_MODEL_CACHE_DIR", "./models")
        self._output_dir = output_dir or os.environ.get("CHENGUAI_OUTPUT_DIR", "./output")
        self._engine: Optional[Any] = None

    def _model_id(self) -> str:
        """权重 id：引擎 id 是「哪个板块的哪个实例」，权重 id 是「加载哪套模型文件」。

        两者不绑定（与 SD1.5 适配器同一套约定），所以将来加第二个 Sana 板块不用重下权重。
        认不出的 id 一律回落到 sana-multiling，绝不让 worker 因为一个拼错的 id 崩掉。
        """
        _, supported = _load_sana_engine_symbols()
        model_id = os.environ.get("CHENGUAI_SANA_MODEL_ID") or self.engine_id
        if model_id not in supported:
            model_id = SANA_DEFAULT_MODEL_ID
        return model_id

    def _snapshot_root(self) -> Path:
        """权重根目录（里面是 huggingface_hub 的 models--org--name 布局）。

        注册时直接注入 CHENGUAI_SANA_MODEL_CACHE_DIR（默认 models/sana-multiling），
        这样配置项 image_model_dir 指哪就读哪；没注入时按 <models_dir>/<model_id> 拼，
        与下载脚本 scripts/download_sana_weights.py 的落盘位置一致。
        """
        explicit = str(os.environ.get("CHENGUAI_SANA_MODEL_CACHE_DIR") or "").strip()
        if explicit:
            return Path(explicit)
        return Path(self._cache_dir) / self._model_id()

    def _get_engine(self) -> Any:
        if self._engine is None:
            engine_cls, _ = _load_sana_engine_symbols()
            self._engine = engine_cls(
                model_id=self._model_id(),
                cache_dir=str(self._snapshot_root()),
                output_dir=self._output_dir,
            )
        return self._engine

    async def health_check(self) -> tuple[bool, str]:
        try:
            import torch  # noqa: F401 - 仅探测依赖是否可用

            if not torch.cuda.is_available():
                return False, "worker 所在环境没有可用的 CUDA 设备"
            repo = "Efficient-Large-Model/Sana_1600M_1024px_MultiLing_diffusers"
            from app.engine.image.sana import _pick_local_snapshot  # 延迟导入：只在需要时读磁盘

            snapshot = _pick_local_snapshot(self._snapshot_root(), repo)
            if snapshot is None:
                return False, "本地缺少完整的 Sana 权重快照，请先运行 scripts/download_sana_weights.py"
            return True, f"依赖可用；权重快照 {snapshot.name}"
        except Exception as exc:  # noqa: BLE001 - 健康检查不抛异常，只报告
            return False, f"{type(exc).__name__}: {exc}"

    def _as_text(self, value: Any) -> Optional[str]:
        """把上游 JSON 里混进来的列表/字节规整成干净字符串：Gemma 编码器只吃 str。

        上游（网页端、智能体、旧数据）偶尔会把提示词写成 ["一只","猫"] 或 b"..."，
        直接进 pipeline 会在推理中途炸掉，白等几分钟加载权重，所以在 worker 这一层先收口。
        """
        if value is None:
            return None
        if isinstance(value, str):
            return value
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        if isinstance(value, (list, tuple)):
            return ", ".join(self._as_text(item) or "" for item in value)
        return str(value)

    def validate_request(self, request: TaskRequest) -> None:
        """只校验类型与板块角色，不校验范围：宽高安全线只有 plan.py 一处，worker 再写一份就会漂移。

        越界数字（99999、steps=999）在这里放行，由引擎侧 round_to_multiple 钳位、
        PlanBudgetError/SanaEngineError 报错，这样两端不会各夹一套。
        """
        if not (self._as_text(request.prompt) or "").strip():
            raise ValueError("提示词不能为空：请输入中文或英文描述后重试")
        params = request.parameters or {}
        for name, reason in _REJECTED_PARAMS.items():
            if params.get(name):
                raise ValueError(f"参数 {name} 不被 sana 引擎接受：{reason}")
        unknown = sorted(set(params) - _ALLOWED_PARAMS)
        if unknown:
            raise ValueError(f"sana 引擎不支持的参数：{unknown}")
        for key in ("width", "height", "steps"):
            value = params.get(key)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value <= 0):
                raise ValueError(f"参数 {key} 必须是正整数，收到 {value!r}")
        seed = params.get("seed")
        if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int)):
            raise ValueError(f"参数 seed 必须是整数，收到 {seed!r}")
        negative = params.get("negative_prompt")
        if negative is not None and not isinstance(negative, (str, list, tuple, bytes)):
            raise ValueError(f"参数 negative_prompt 必须是字符串，收到 {negative!r}")

    async def generate(self, request: TaskRequest) -> TaskResult:
        self.validate_request(request)
        params = request.parameters or {}
        engine = self._get_engine()
        result = await engine.generate_image(
            prompt=self._as_text(request.prompt),
            width=int(params.get("width") or _DEFAULT_SIDE),
            height=int(params.get("height") or _DEFAULT_SIDE),
            steps=params.get("steps"),
            seed=params.get("seed"),
            negative_prompt=self._as_text(params.get("negative_prompt")),
        )
        return TaskResult(
            output_files=(str(result["path"]),),
            metadata={
                "engine_id": self.engine_id,
                "model_id": self._model_id(),
                "status": result.get("status") or "generated",
                "seed": result.get("seed"),
                "steps": result.get("steps"),
                "guidance": result.get("guidance"),
                "mode": result.get("mode"),
                # 放大痕迹必须留档：Sana 超预算时是「底图 + Lanczos」，没有重绘，
                # 界面据此区分「原生 1024 直出」和「放大到 1080p/2K」。
                "upscaled": result.get("upscaled"),
                "tiles": result.get("tiles"),
                "base_size": result.get("base_size"),
                "target_size": result.get("target_size"),
                # 提示词原样进模型（编码器原生认中英混合），这几项仍要回传，
                # 历史详情里「用户写的原文」与「实际进模型的词」必须能看到是同一份。
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


def create_adapter() -> SanaTaskAdapter:
    """process_worker --adapter-factory 的入口工厂。"""
    return SanaTaskAdapter(engine_id=os.environ.get("CHENGUAI_SANA_ENGINE_ID", SANA_DEFAULT_MODEL_ID))
