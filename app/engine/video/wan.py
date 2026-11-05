"""Wan2.1 视频生成引擎实现（wan-t2v 文生视频 + wan-i2v 图生视频）

wan-i2v 使用官方 Wan2.1-VACE-1.3B（diffusers WanVACEPipeline）：
Wan2.1 官方并没有发布 1.3B 参数的 I2V 模型（I2V 仅有 14B 档，12GB 显存跑不动），
VACE 是官方 1.3B 档唯一支持图像条件（首帧条件即图生视频）的 Apache-2.0 形态。

本模块只在 .venv-video（含 torch / diffusers / imageio-ffmpeg）中被导入；
桌面主进程与 Web 进程不加载它。真实生成经 app.tasks.video_worker.WanTaskAdapter
接入任务系统（JSONL IPC）。

要点（与 app/engine/image/flux.py 同一范式）：
- 离线优先：权重按 huggingface_hub 缓存布局放在 models/wan2.1，缺失即诚实报错，
  绝不盲目联网重下、绝不伪造输出。
- 两仓库均 Apache-2.0、gated:false，不需要任何 HF token。
- dtype=bfloat16 + enable_model_cpu_offload：UMT5-XXL 文本编码单件约 11GB，
  只有逐件换入 GPU 才能装进 12GB 显存（FLUX 同款策略已在本机验证）。
- Wan VAE 按官方示例保持 float32 并启用 tiling，限制解码显存峰值。
- 1.3B 为 480p 档模型：尺寸取整到 16 的倍数并限制像素上限；帧数取整到 4n+1；
  导出 16fps（Wan 原生帧率）。
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import PIL.Image
import torch
from diffusers import AutoencoderKLWan, UniPCMultistepScheduler, WanPipeline, WanVACEPipeline
from diffusers.utils import export_to_video

from app.engine.abstract import VideoGenerationEngine
from app.engine.prompt_utils import coerce_prompt_text

# Wan2.1 支持的视频模型（全部 Apache-2.0，可直接商用与再分发）
WAN_SUPPORTED_MODELS = {
    "wan-t2v": {
        "hf_repo": "Wan-AI/Wan2.1-T2V-1.3B-Diffusers",
        "license": "Apache-2.0",
        "est_mem_gb": 11.4,  # UMT5-XXL bf16 单件换入峰值（model offload）
        "default_steps": 28,
        "pipeline": "WanPipeline",
    },
    "wan-i2v": {
        "hf_repo": "Wan-AI/Wan2.1-VACE-1.3B-diffusers",
        "license": "Apache-2.0",
        "est_mem_gb": 11.4,
        "default_steps": 28,
        "pipeline": "WanVACEPipeline",
    },
}

# 尺寸必须可被 vae_scale_factor_spatial(8) * patch(2) = 16 整除
_MULTIPLE = 16
# Wan 原生帧率与 1.3B 档像素上限（480p 级：832x480；留一点余量到 480*832*1.15）
WAN_FPS = 16
_MAX_PIXELS = int(832 * 480 * 1.15)
_MIN_FRAMES, _MAX_FRAMES = 5, 161

# Wan 官方 README 的标准负面提示（480p 档），用户未填写时使用
DEFAULT_NEGATIVE_PROMPT = (
    "色调艳丽，过曝，静态，细节模糊不清，字幕，风格，作品，画作，画面，静止，"
    "整体发灰，最差质量，低质量，JPEG压缩残留，丑陋的，残缺的，多余的手指，"
    "画得不好的手部，画得不好的脸部，畸形的，毁容的，形态畸形的肢体，手指融合，"
    "静止不动的画面，杂乱的背景，三条腿，背景人很多，倒着走"
)


def _round_to_multiple(value: int, multiple: int = _MULTIPLE) -> int:
    """把尺寸就近取整到 multiple 的倍数，并限制在 [multiple*8, 2048]。"""
    value = max(multiple * 8, min(int(value), 2048))
    return round(value / multiple) * multiple


def _snap_frames(value: Any, default: int = 81) -> int:
    """Wan 时序 VAE 要求帧数为 4n+1；就近取整并夹在 [5, 161]。"""
    try:
        frames = int(value)
    except (TypeError, ValueError):
        frames = default
    frames = max(_MIN_FRAMES, min(frames, _MAX_FRAMES))
    snapped = ((frames - 1) // 4) * 4 + 1
    if frames % 4 != 1 and snapped + 4 <= _MAX_FRAMES and (snapped + 4 - frames) <= (frames - snapped):
        snapped += 4
    return snapped


def _fit_pixels(width: int, height: int) -> tuple[int, int]:
    """按请求比例缩到不超过 _MAX_PIXELS（1.3B 是 480p 档模型，超限只降规模不改比例）。"""
    w, h = _round_to_multiple(width), _round_to_multiple(height)
    if w * h > _MAX_PIXELS:
        scale = (_MAX_PIXELS / (w * h)) ** 0.5
        w = max(_MULTIPLE, _round_to_multiple(w * scale))
        h = max(_MULTIPLE, _round_to_multiple(h * scale))
    return w, h


def _hub_repo_cache_dir(cache_dir: Path, repo_id: str) -> Path:
    """huggingface_hub 标准缓存布局中该仓库的目录（models--org--name）。"""
    return cache_dir / f"models--{repo_id.replace('/', '--')}"


def _pick_local_snapshot(cache_dir: Path, repo_id: str) -> Optional[Path]:
    """返回最完整的本地快照目录；缺 model_index.json 的快照视为不完整。"""
    snapshots = _hub_repo_cache_dir(cache_dir, repo_id) / "snapshots"
    if not snapshots.is_dir():
        return None
    candidates = [
        d for d in snapshots.iterdir() if d.is_dir() and (d / "model_index.json").is_file()
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda d: d.stat().st_mtime_ns)


class WanVideoEngineError(RuntimeError):
    """引擎可恢复错误；消息会透传给任务系统作为失败原因。"""


class WanVideoEngine(VideoGenerationEngine):
    """Wan2.1 视频引擎（wan-t2v=文生视频；wan-i2v=VACE 首帧条件图生视频）"""

    def __init__(
        self,
        model_id: str = "wan-t2v",
        cache_dir: Optional[str] = None,
        output_dir: Optional[str] = None,
    ):
        super().__init__("WanVideoEngine")
        if model_id not in WAN_SUPPORTED_MODELS:
            raise ValueError(f"不支持的模型 ID: {model_id}")
        self.model_id = model_id
        self.model_config = WAN_SUPPORTED_MODELS[model_id]
        self.cache_dir = Path(cache_dir) if cache_dir else Path("./models") / "wan2.1"
        self.output_dir = Path(output_dir) if output_dir else Path("./output")
        self.pipe: Optional[Any] = None
        self._load_lock = asyncio.Lock()

    @property
    def default_steps(self) -> int:
        return self.model_config.get("default_steps", 28)

    @property
    def fps(self) -> int:
        return WAN_FPS

    @property
    def memory_usage(self) -> float:
        if not torch.cuda.is_available():
            return 0.0
        return torch.cuda.memory_allocated() / (1024**3)

    @property
    def requires_cuda(self) -> bool:
        return True

    async def load(self) -> None:
        """加载 pipeline（懒加载、幂等；失败抛 WanVideoEngineError，绝不静默）。"""
        async with self._load_lock:
            if self.pipe is not None:
                return
            if not torch.cuda.is_available():
                raise WanVideoEngineError(
                    "当前环境没有可用的 CUDA 设备；Wan2.1 视频引擎需要 NVIDIA GPU（RTX 3060 12GB 级）。"
                )
            repo = self.model_config["hf_repo"]
            if _pick_local_snapshot(self.cache_dir, repo) is None:
                expected = _hub_repo_cache_dir(self.cache_dir, repo) / "snapshots"
                raise WanVideoEngineError(
                    f"未找到模型 {repo} 的完整本地权重快照（期望目录：{expected}）。"
                    "引擎默认离线加载、不会自动联网下载；请先运行 scripts\\download_video_weights.py "
                    "获取权重（Apache-2.0，无需 token），或显式设置 HF_HUB_OFFLINE=0 联网重试。"
                )
            pipeline_cls = WanVACEPipeline if self.model_config["pipeline"] == "WanVACEPipeline" else WanPipeline
            try:
                loop = asyncio.get_running_loop()
                self.pipe = await loop.run_in_executor(
                    None,
                    lambda: self._load_pipeline(pipeline_cls, repo),
                )
            except Exception as exc:  # noqa: BLE001 - 转换为引擎错误并透传
                self.pipe = None
                raise WanVideoEngineError(f"加载模型 {repo} 失败：{exc}") from exc

            if torch.cuda.is_available():
                # UMT5-XXL 编码单件约 11.4GB（bf16），必须逐件 CPU offload 才能进 12GB 显存。
                self.pipe.enable_model_cpu_offload()
                # VAE 解码 81 帧 480p 的峰值靠 tiling 压住（float32 VAE 按官方示例保留）。
                try:
                    self.pipe.vae.enable_tiling()
                except Exception:  # noqa: BLE001 - 老版 diffusers 无此方法时忽略
                    pass
            self.pipe.set_progress_bar_config(disable=True)

    def _load_pipeline(self, pipeline_cls: Any, repo: str) -> Any:
        """同步加载（在线程池里执行）：bf16 主干 + fp32 VAE（官方示例范式）+ flow_shift。"""
        vae = AutoencoderKLWan.from_pretrained(
            repo, subfolder="vae", dtype=torch.float32, cache_dir=str(self.cache_dir)
        )
        pipe = pipeline_cls.from_pretrained(
            repo, vae=vae, dtype=torch.bfloat16, cache_dir=str(self.cache_dir)
        )
        # 480p 档 flow_shift=3.0（720p 才用 5.0）；本项目 1.3B 固定 480p 级。
        pipe.scheduler = UniPCMultistepScheduler.from_config(pipe.scheduler.config, flow_shift=3.0)
        return pipe

    def _prepare_vace_conditioning(
        self, init_img: PIL.Image.Image, height: int, width: int, num_frames: int
    ) -> tuple[list, list]:
        """按 diffusers 官方示例构造 VACE 首帧条件：首帧=参考图（mask=0），其余帧生成（mask=255）。"""
        first = init_img.resize((width, height))
        filler = PIL.Image.new("RGB", (width, height), (128, 128, 128))
        frames = [first] + [filler] * (num_frames - 1)
        mask_keep = PIL.Image.new("L", (width, height), 0)
        mask_gen = PIL.Image.new("L", (width, height), 255)
        mask = [mask_keep] + [mask_gen] * (num_frames - 1)
        return frames, mask

    async def generate_video(
        self,
        prompt: str,
        image_path: Optional[str] = None,
        num_frames: int = 81,
        width: int = 512,
        height: int = 384,
        steps: Optional[int] = None,
        seed: Optional[int] = None,
        negative_prompt: Optional[str] = None,
        output_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        """生成视频（wan-i2v 需要 image_path 作为首帧；产物为 16fps mp4）

        Returns:
            {"path": mp4路径, "status": "generated", "seed": 种子, "frames": 帧数,
             "fps": 16, "width": 实际宽, "height": 实际高, "seconds": 时长}

        Raises:
            WanVideoEngineError: 输入无效 / 显存不足 / 权重缺失 / pipeline 结果为空
        """
        prompt = coerce_prompt_text(prompt) or ""
        if not prompt:
            raise WanVideoEngineError("提示词为空：请输入中文或英文描述后重试")
        negative_prompt = coerce_prompt_text(negative_prompt) or DEFAULT_NEGATIVE_PROMPT

        raw_out = output_dir if output_dir else str(self.output_dir)
        target_dir = Path(raw_out).expanduser()
        if not target_dir.is_absolute():
            target_dir = Path.cwd() / target_dir
        target_dir = target_dir.resolve()
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise WanVideoEngineError(f"无法创建输出目录：{target_dir}（{exc}）") from exc

        is_i2v = self.model_id == "wan-i2v"
        init_img: Optional[PIL.Image.Image] = None
        if is_i2v:
            if not image_path:
                raise WanVideoEngineError("图生视频缺少首帧图片：请先上传/选择参考图再提交")
            init = Path(str(image_path)).expanduser()
            if not init.is_file():
                raise WanVideoEngineError(f"图生视频首帧文件不存在：{init}")
            try:
                init_img = PIL.Image.open(init).convert("RGB")
            except Exception as exc:  # noqa: BLE001
                raise WanVideoEngineError(f"首帧图片无法读取：{init}（{exc}）") from exc
            # 生成尺寸跟随首帧比例（1.3B 是 480p 档：按首帧长短边映射到 ~480p 像素预算）
            iw, ih = init_img.size
            budget = _MAX_PIXELS / 1.15  # 目标像素预算（480p 级）
            scale = (budget / max(1, iw * ih)) ** 0.5
            width, height = _fit_pixels(int(iw * scale), int(ih * scale))
        else:
            if image_path:
                raise WanVideoEngineError("文生视频不接受参考图：请在图生视频标签页使用图生视频引擎")
            width, height = _fit_pixels(width, height)

        num_frames = _snap_frames(num_frames)
        inference_steps = int(steps) if steps is not None else self.default_steps
        inference_steps = max(1, min(inference_steps, 50))

        await self.load()
        assert self.pipe is not None

        generator = torch.Generator(device="cpu").manual_seed(int(seed)) if seed is not None else None
        kwargs: Dict[str, Any] = dict(
            prompt=prompt,
            negative_prompt=negative_prompt,
            height=height,
            width=width,
            num_frames=num_frames,
            num_inference_steps=inference_steps,
            guidance_scale=5.0,
            generator=generator,
        )
        if init_img is not None:
            kwargs["video"], kwargs["mask"] = self._prepare_vace_conditioning(
                init_img, height, width, num_frames
            )

        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(None, lambda: self.pipe(**kwargs))
        except WanVideoEngineError:
            raise
        except torch.cuda.OutOfMemoryError as exc:  # type: ignore[attr-defined]
            raise WanVideoEngineError(
                f"显存不足（CUDA OOM）：Wan2.1 编码阶段单件需要约 11GB 空闲显存，"
                f"请先释放常驻推理引擎（双击 scripts\\release_local_engine.bat），"
                f"或降低帧数/分辨率后重试。原始错误：{exc}"
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise WanVideoEngineError(f"生成失败：{exc}") from exc

        frames_out = getattr(result, "frames", None)
        if not frames_out or not frames_out[0]:
            raise WanVideoEngineError("pipeline 返回结果为空")

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        mode = "i2v" if is_i2v else "t2v"
        output_path = target_dir / f"wan_{mode}_{stamp}.mp4"
        try:
            export_to_video(frames_out[0], str(output_path), fps=WAN_FPS)
        except Exception as exc:  # noqa: BLE001
            raise WanVideoEngineError(f"mp4 导出失败：{exc}") from exc
        if not output_path.is_file() or output_path.stat().st_size < 1024:
            raise WanVideoEngineError("mp4 导出结果异常（文件缺失或过小），已判为失败")

        return {
            "path": str(output_path.resolve()),
            "status": "generated",
            "seed": seed,
            "frames": num_frames,
            "fps": WAN_FPS,
            "seconds": round(num_frames / WAN_FPS, 2),
            "width": width,
            "height": height,
        }

    async def generate(self, prompt: str, **kwargs) -> list[str]:
        """AIGenerationEngine.generate 的便捷实现：返回输出文件路径列表。"""
        result = await self.generate_video(prompt, **kwargs)
        return [result["path"]]

    async def shutdown(self) -> None:
        """释放 pipeline 与显存（供任务系统适配器调用）。"""
        self.pipe = None
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()
