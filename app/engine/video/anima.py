"""Anima 2B 图生视频引擎（Diffusers，RTX 3060 12GB 适配候选）

本模块只在 .venv-image（含 torch / diffusers）中被导入；Web 主进程不加载它。
真实生成通过 app.tasks.video_worker.VideoTaskAdapter 接入任务系统。

模型来源：https://huggingface.co/moovenio/Anima_2B_model
- 官方模型卡明确标注"optimized for consumer GPUs (12GB VRAM)"，并给出 RTX 3060 实测数据；
- 官方推荐 `enable_model_cpu_offload()`（约 8GB）或 fp8（约 6.5GB）低显存路线。
权重许可为社区类条款，商用前须按 docs/LICENSE_AUDIT.md 流程复核。

调用链（官方 README 模式）：
    scheduler = FlowMatchEulerDiscreteScheduler(shift=3)
    pipe = AnimaPipeline.from_pretrained(repo, scheduler=scheduler, dtype=torch.bfloat16)
    pipe.enable_model_cpu_offload()
    image = AutoencoderKLTurboCausal3D.from_pretrained("mit-han-lab/dc-anime-tiny")
    text  = Gemma3ForConditionalGeneration / SigLIP2VisionEncoder from google/gemma-3-4b-it
    pipe = AnimaPipeline.from_pipeline_args(AnimaPipelineComponents(...))
    video = pipe(image=..., prompt=..., height=..., width=..., num_frames=..., num_inference_steps=...).frames[0]
save_video 内部用 imageio_ffmpeg.write_frames 写出 .mp4。

注意：diffusers 的 Anima 支持自 0.38.0 起存在；本项目的 diffusers==0.41.0 已满足，
导入采用延迟/容错策略（_load_anima_symbols），缺符号时给出明确错误而不是崩溃。
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import PIL.Image
import torch

# Anima 官方模型仓库（社区许可，下载前须确认）
ANIMA_HF_REPO = "moovenio/Anima_2B_model"
# 官方 README 推荐的轻量 VAE 与文本编码器组件
ANIMA_VAE_REPO = "mit-han-lab/dc-anime-tiny"
ANIMA_TEXT_ENCODER_REPO = "google/gemma-3-4b-it"

VIDEO_MODEL_REGISTRY: Dict[str, Dict[str, Any]] = {
    "anima-image2video": {
        "hf_repo": ANIMA_HF_REPO,
        "license": "社区类许可（商用前需审查）",
        "est_mem_gb": 8.0,
        "default_steps": 28,
        "supported_dtype": ["bf16"],
        "task": "image-to-video",
    },
}

# Anima latent 打包要求尺寸可被 16 整除（与 FLUX 相同规则）
_MULTIPLE = 16


def _round_to_multiple(value: int, multiple: int = _MULTIPLE) -> int:
    """把尺寸就近取整到 multiple 的倍数，并限制在 [multiple, 4096]。"""
    value = max(multiple, min(int(value), 4096))
    return round(value / multiple) * multiple


def _clamp_frames(value: int) -> int:
    """帧数必须满足 4n+1（3D VAE 时间压缩），限制在 [5, 161]。"""
    value = max(5, min(int(value), 161))
    remainder = (value - 1) % 4
    return value - remainder


class VideoEngineError(RuntimeError):
    """引擎可恢复错误；消息会透传给任务系统作为失败原因。"""


def _load_anima_symbols() -> Dict[str, Any]:
    """延迟导入 Anima 相关符号；diffusers 版本不支持时给出明确错误。"""
    try:
        from diffusers import (
            AnimaPipeline,
            FlowMatchEulerDiscreteScheduler,
            AutoencoderKLTurboCausal3D,
            Gemma3ForConditionalGeneration,
            SigLIP2VisionEncoder,
        )
        from diffusers.pipelines.anima.pipeline_anima import AnimaPipelineComponents
    except ImportError as exc:  # noqa: BLE001 - 转换为引擎错误并透传
        raise VideoEngineError(
            "当前 .venv-image 的 diffusers 不支持 Anima pipeline"
            f"（需要 >=0.38）：{exc}"
        ) from exc
    return {
        "AnimaPipeline": AnimaPipeline,
        "FlowMatchEulerDiscreteScheduler": FlowMatchEulerDiscreteScheduler,
        "AutoencoderKLTurboCausal3D": AutoencoderKLTurboCausal3D,
        "Gemma3ForConditionalGeneration": Gemma3ForConditionalGeneration,
        "SigLIP2VisionEncoder": SigLIP2VisionEncoder,
        "AnimaPipelineComponents": AnimaPipelineComponents,
    }


class AnimaVideoEngine:
    """Anima 2B 图生视频引擎（Diffusers + CPU offload）。

    职责:
    - 懒加载 Anima pipeline（bf16 + enable_model_cpu_offload，适配 12GB VRAM）
    - 按请求把结果保存为 .mp4
    - 文生视频：先用 FLUX 生成首帧图，再走图生视频路径（见 generate_video）
    """

    def __init__(
        self,
        cache_dir: Optional[str] = None,
        output_dir: Optional[str] = None,
        dtype_key: str = "bf16",
        offload: bool = True,
    ):
        self.cache_dir = Path(cache_dir) if cache_dir else Path("./models") / "anima-image2video"
        self.output_dir = Path(output_dir) if output_dir else Path("./output")
        self.pipe: Optional[Any] = None
        self._load_lock = asyncio.Lock()
        self.offload = offload
        mapping = {"bf16": torch.bfloat16, "fp16": torch.float16}
        self.current_dtype = mapping.get(dtype_key, torch.bfloat16)

    @property
    def memory_usage(self) -> float:
        if not torch.cuda.is_available():
            return 0.0
        return torch.cuda.memory_allocated() / (1024**3)

    @property
    def requires_cuda(self) -> bool:
        return True

    async def load(self) -> None:
        """加载 pipeline（懒加载、幂等；失败抛 VideoEngineError，绝不静默）。"""
        async with self._load_lock:
            if self.pipe is not None:
                return
            symbols = _load_anima_symbols()
            loop = asyncio.get_running_loop()

            def _build() -> Any:
                scheduler = symbols["FlowMatchEulerDiscreteScheduler"](shift=3)
                # 官方 README：先按 bf16 加载主仓库，再挂接轻量 VAE / Gemma 文本编码器。
                base = symbols["AnimaPipeline"].from_pretrained(
                    ANIMA_HF_REPO,
                    scheduler=scheduler,
                    dtype=self.current_dtype,
                    cache_dir=str(self.cache_dir),
                )
                vae = symbols["AutoencoderKLTurboCausal3D"].from_pretrained(
                    ANIMA_VAE_REPO,
                    dtype=self.current_dtype,
                    cache_dir=str(self.cache_dir / "vae"),
                )
                text_encoder = symbols["Gemma3ForConditionalGeneration"].from_pretrained(
                    ANIMA_TEXT_ENCODER_REPO,
                    dtype=self.current_dtype,
                    cache_dir=str(self.cache_dir / "text_encoder"),
                ).language_model
                vision_encoder = symbols["SigLIP2VisionEncoder"].from_pretrained(
                    ANIMA_TEXT_ENCODER_REPO,
                    subfolder="vision_tower",
                    dtype=self.current_dtype,
                    cache_dir=str(self.cache_dir / "vision_encoder"),
                )
                pipe = symbols["AnimaPipeline"].from_pipeline_args(
                    base.tokenizer,
                    symbols["AnimaPipelineComponents"](
                        vae=vae,
                        text_encoder=text_encoder,
                        vision_encoder=vision_encoder,
                        transformer=base.transformer,
                        scheduler=scheduler,
                    ),
                )
                if self.offload:
                    pipe.enable_model_cpu_offload()
                else:
                    pipe.to("cuda")
                return pipe

            try:
                self.pipe = await loop.run_in_executor(None, _build)
            except VideoEngineError:
                raise
            except Exception as exc:  # noqa: BLE001 - 转换为引擎错误并透传
                raise VideoEngineError(
                    f"Anima pipeline 加载失败（权重将缓存到 {self.cache_dir}）：{exc}"
                ) from exc

    async def generate_video(
        self,
        prompt: str,
        image_path: str,
        height: int = 384,
        width: int = 512,
        num_frames: int = 33,
        steps: Optional[int] = None,
        seed: Optional[int] = None,
        negative_prompt: Optional[str] = None,
    ) -> Dict[str, Any]:
        """图生视频：以 image_path 为首帧生成 .mp4，返回 {path, status, seed}。"""
        if not prompt or not prompt.strip():
            raise VideoEngineError("提示词不能为空")
        init_file = Path(image_path)
        if not init_file.is_file():
            raise VideoEngineError(f"图生视频输入文件不存在：{image_path}")

        await self.load()
        assert self.pipe is not None

        height = _round_to_multiple(height)
        width = _round_to_multiple(width)
        num_frames = _clamp_frames(num_frames)
        steps = int(steps or 28)
        if steps < 1:
            raise VideoEngineError("steps 必须 >= 1")

        init_image = PIL.Image.open(init_file).convert("RGB").resize((width, height))

        generator = None
        if seed is not None:
            generator = torch.Generator(device="cpu").manual_seed(int(seed))

        loop = asyncio.get_running_loop()

        def _run() -> Any:
            kwargs: Dict[str, Any] = {
                "image": init_image,
                "prompt": prompt,
                "height": height,
                "width": width,
                "num_frames": num_frames,
                "num_inference_steps": steps,
            }
            if generator is not None:
                kwargs["generator"] = generator
            if negative_prompt:
                kwargs["negative_prompt"] = negative_prompt
            return self.pipe(**kwargs)

        try:
            result = await loop.run_in_executor(None, _run)
        except Exception as exc:  # noqa: BLE001 - 转换为引擎错误并透传
            raise VideoEngineError(f"视频生成失败：{exc}") from exc

        frames = getattr(result, "frames", None)
        if not frames:
            raise VideoEngineError("pipeline 未返回帧数据")
        video_frames = frames[0]

        target_dir = self.output_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = target_dir / f"anima_i2v_{stamp}.mp4"

        from diffusers.utils import export_to_video

        export_to_video(video_frames, str(output_path), fps=24)

        return {
            "path": str(output_path.resolve()),
            "status": "generated",
            "seed": seed,
            "num_frames": num_frames,
            "width": width,
            "height": height,
        }

    async def shutdown(self) -> None:
        """释放 pipeline 与显存（供任务系统适配器调用）。"""
        self.pipe = None
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()
