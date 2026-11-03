"""FLUX.1 图片生成引擎实现（文生图 + 图生图）

本模块只在 .venv-image（含 torch / diffusers）中被导入；桌面主进程不加载它。
真实生成通过 app.tasks.flux_worker.FluxTaskAdapter 接入任务系统。
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import PIL.Image
import torch
from diffusers import FluxImg2ImgPipeline, FluxPipeline

from app.engine.abstract import ImageGenerationEngine

# FLUX.1 支持的模型列表
FLUX_SUPPORTED_MODELS = {
    "flux-schnell": {
        "hf_repo": "black-forest-labs/FLUX.1-schnell",
        "license": "Apache-2.0",
        "est_mem_gb": 6.0,
        "default_steps": 4,
        "supported_dtype": ["fp16"],
        "supports_cfg": False,
    },
    "flux-dev": {
        "hf_repo": "black-forest-labs/FLUX.1-dev",
        "license": "FLY-1.1 (需审查)",
        "est_mem_gb": 8.0,
        "default_steps": 25,
        "supported_dtype": ["fp16"],
        "supports_cfg": True,
    },
}

# 生成尺寸必须可被 16 整除（FLUX latent 打包要求）
_MULTIPLE = 16


def _round_to_multiple(value: int, multiple: int = _MULTIPLE) -> int:
    """把尺寸就近取整到 multiple 的倍数，并限制在 [multiple, 4096]。"""
    value = max(multiple, min(int(value), 4096))
    return round(value / multiple) * multiple


class FluxEngineError(RuntimeError):
    """引擎可恢复错误；消息会透传给任务系统作为失败原因。"""


class FluxImageEngine(ImageGenerationEngine):
    """FLUX.1 图片生成引擎（文生图 + 图生图）

    职责:
    - 懒加载 FLUX.1-schnell/dev pipeline（FP16, CUDA）
    - 按请求把结果保存到指定输出目录
    - schnell 无 CFG：不向 pipeline 传 negative_prompt
    """

    def __init__(
        self,
        model_id: str = "flux-schnell",
        cache_dir: Optional[str] = None,
        output_dir: Optional[str] = None,
        dtype_key: str = "fp16",
    ):
        super().__init__("FluxImageEngine")
        if model_id not in FLUX_SUPPORTED_MODELS:
            raise ValueError(f"不支持的模型 ID: {model_id}")

        self.model_id = model_id
        self.model_config = FLUX_SUPPORTED_MODELS[model_id]
        self.cache_dir = Path(cache_dir) if cache_dir else Path("./models") / model_id
        self.output_dir = Path(output_dir) if output_dir else Path("./output")
        self.pipe: Optional[FluxPipeline] = None
        self._load_lock = asyncio.Lock()

        mapping = {"fp16": torch.float16}
        self.current_dtype = mapping.get(dtype_key, torch.float16)

    @property
    def default_steps(self) -> int:
        """返回默认采样步数"""
        return self.model_config.get("default_steps", 4)

    @property
    def memory_usage(self) -> float:
        if not torch.cuda.is_available():
            return 0.0
        return torch.cuda.memory_allocated() / (1024**3)

    @property
    def requires_cuda(self) -> bool:
        return True

    async def load(self) -> None:
        """加载 pipeline（懒加载、幂等；失败抛 FluxEngineError，绝不静默）。"""
        async with self._load_lock:
            if self.pipe is not None:
                return

            repo = self.model_config["hf_repo"]
            try:
                loop = asyncio.get_running_loop()
                self.pipe = await loop.run_in_executor(
                    None,
                    lambda: FluxPipeline.from_pretrained(
                        repo,
                        # diffusers>=0.41 的 from_pretrained 参数名（旧名 torch_dtype 已弃用）
                        dtype=self.current_dtype,
                        cache_dir=str(self.cache_dir),
                    ),
                )
            except Exception as exc:  # noqa: BLE001 - 转换为引擎错误并透传
                self.pipe = None
                raise FluxEngineError(
                    f"加载模型 {repo} 失败：{exc}（请检查网络/HF 镜像，或先手动下载权重到 {self.cache_dir}）"
                ) from exc

            if torch.cuda.is_available():
                # FP16 全量权重约 23GB，需启用模型 CPU offload 才能装进 12GB 显存。
                self.pipe.enable_model_cpu_offload()
            else:
                raise FluxEngineError(
                    "当前环境没有可用的 CUDA 设备；FLUX 引擎需要 NVIDIA GPU（RTX 3060 12GB 起）。"
                )

    async def generate_image(
        self,
        prompt: str,
        width: int = 512,
        height: int = 512,
        steps: Optional[int] = None,
        seed: Optional[int] = None,
        negative_prompt: Optional[str] = None,
        init_image_path: Optional[str] = None,
        strength: float = 0.6,
        output_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        """生成图片（prompt 给 init_image_path 时走 FLUX.1 img2img）

        Args:
            prompt: 提示词
            width, height: 图像尺寸（自动取整到 16 的倍数）
            steps: 采样步数 (None=使用默认)
            seed: 随机种子
            negative_prompt: 仅对支持 CFG 的模型（flux-dev）生效
            init_image_path: 图生图的输入图片路径（None=文生图）
            strength: 图生图强度 [0,1]
            output_dir: 本次生成的输出目录（None=用实例默认值）

        Returns:
            {"path": 文件路径, "pil": PIL.Image, "status": "generated", "seed": 种子}

        Raises:
            FluxEngineError: 模型加载失败 / 无 GPU / 输入图片无效 / pipeline 结果为空
        """
        if init_image_path:
            init = Path(init_image_path)
            if not init.is_file():
                raise FluxEngineError(f"图生图输入文件不存在：{init}")
            # 先验证输入图片有效，再付出加载权重的代价
            init_img = PIL.Image.open(init).convert("RGB")
        else:
            init_img = None

        await self.load()
        assert self.pipe is not None  # load() 成功即非 None

        width = _round_to_multiple(width)
        height = _round_to_multiple(height)
        inference_steps = int(steps) if steps is not None else self.default_steps
        inference_steps = max(1, min(inference_steps, 50))

        generator = None
        if seed is not None:
            device = "cuda" if torch.cuda.is_available() else "cpu"
            generator = torch.Generator(device=device).manual_seed(int(seed))

        kwargs: Dict[str, Any] = dict(
            prompt=prompt,
            height=height,
            width=width,
            num_inference_steps=inference_steps,
            generator=generator,
        )
        if negative_prompt and self.model_config.get("supports_cfg", False):
            kwargs["negative_prompt"] = negative_prompt

        if init_img is not None:
            # FluxImg2ImgPipeline 与 txt2img 共享同一份权重，仅按需实例化包装。
            img2img = FluxImg2ImgPipeline(**self.pipe.components)
            img2img.set_progress_bar_config(disable=True)
            run_pipe: Any = img2img
            kwargs["image"] = init_img
            kwargs["strength"] = max(0.0, min(float(strength), 1.0))
        else:
            run_pipe = self.pipe

        kwargs.pop("guidance_scale", None)  # schnell 无 CFG，保持默认

        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(None, lambda: run_pipe(**kwargs))
        except FluxEngineError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise FluxEngineError(f"生成失败：{exc}") from exc

        if not getattr(result, "images", None):
            raise FluxEngineError("pipeline 返回结果为空")

        img_pil: PIL.Image.Image = result.images[0]
        target_dir = Path(output_dir) if output_dir else self.output_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        mode = "img2img" if init_image_path else "txt2img"
        output_path = target_dir / f"flux_{mode}_{stamp}.png"
        img_pil.save(output_path)

        return {
            "path": str(output_path.resolve()),
            "pil": img_pil,
            "status": "generated",
            "seed": seed,
        }

    async def generate(self, prompt: str, **kwargs) -> list[str]:
        """AIGenerationEngine.generate 的便捷实现：返回输出文件路径列表。"""
        result = await self.generate_image(prompt, **kwargs)
        return [result["path"]]

    async def shutdown(self) -> None:
        """释放 pipeline 与显存（供任务系统适配器调用）。"""
        self.pipe = None
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()
