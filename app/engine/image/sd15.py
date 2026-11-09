"""SD1.5（RealVis V5.1）图片生成引擎：文生图 + 图生图 + 分块高清重绘出真 1080p。

替代原先 31GB 的 FLUX：本引擎三份权重合计 4.4GB（模型 4.0GB + VAE 0.3GB + LCM-LoRA 0.1GB），
运行时峰值显存按「不超过 6GB」设计，取舍如下：

- 权重只加载半精度（fp16），并用 CPU offload 逐件换入换出：常驻显存的只有当前那一件，
  UNet 半精度 1.6GB 是最大单件；
- 单张直接进 UNet 的分辨率上限 _SINGLE_PASS_MAX_PIXELS（约 0.85MP）——再大激活显存就
  顶到 6GB 以上，所以 1080p/2K 一律走「先出底图 → Lanczos 放大 → 分块重绘」；分块见
  app/engine/image/hires.py，每次进 UNet 的只有 512×512，峰值与出小图时一样；
- 显存不够时自动降级：free >= 3.6GB 用整件换入（快）；free 不足则退回逐层换入（慢约
  两三倍，但峰值只剩 1-2GB）；连 1.5GB 都腾不出就直接报错并点名谁占着显存，绝不 OOM。

本模块只在 .venv-image（含 torch / diffusers）中被导入；桌面主进程不加载它。
真实生成通过 app.tasks.sd_worker.SdTaskAdapter 接入任务系统。
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import PIL.Image
import torch
from diffusers import (
    AutoencoderKL,
    DPMSolverMultistepScheduler,
    LCMScheduler,
    StableDiffusionImg2ImgPipeline,
    StableDiffusionPipeline,
)

from app.engine.abstract import ImageGenerationEngine
from app.engine.memprobe import floor_mb_or, ram_gate_message, ram_gate_ok, read_physical_ram
from app.engine.image.hires import (
    DEFAULT_OVERLAP,
    DEFAULT_TILE,
    TileBlender,
    feather_mask,
    tile_grid,
    upscale_lanczos,
)
from app.engine.image.plan import (
    HIRES_STRENGTH as _HIRES_STRENGTH,
    MAX_SIDE as _MAX_SIDE,
    MULTIPLE as _MULTIPLE,
    PlanBudgetError,
    resolve_output_plan,
    round_to_multiple,
)
from app.engine.prompt_bilingual import to_clip_prompt
from app.engine.prompt_utils import coerce_prompt_text, contains_lone_surrogate

# 本地权重仓库（三件套），全部公开无需 token，离线下载脚本：scripts/download_image_weights.py
SD15_SUPPORTED_MODELS: Dict[str, Any] = {
    "sd-realvis": {
        "hf_repo": "SG161222/Realistic_Vision_V5.1_noVAE",
        "vae_repo": "stabilityai/sd-vae-ft-mse",
        "vae_cache_name": "sd-vae-ft-mse",
        "lora_repo": "latent-consistency/lcm-lora-sdv1-5",
        "lora_cache_name": "lcm-lora-sd15",
        "license": "CreativeML-OpenRAIL-M（模型）+ MIT（VAE）+ OpenRAIL++（LCM-LoRA）",
        "est_mem_gb": 4.0,
        "default_steps": 6,
        "supported_dtype": ["fp16"],
        "supports_cfg": True,
    },
}

# 分辨率档位与显存预算的常量都来自 app/engine/image/plan.py：网页端参数面板、桌面端
# 参数面板和引擎共用同一套数字，tests/test_sd15_plan.py 会核对三端是否一致。
_LCM_MIN_STEPS, _LCM_MAX_STEPS = 1, 12
_NO_LORA_MIN_STEPS, _NO_LORA_MAX_STEPS = 10, 40
# LCM 档默认 guidance 1.0（不做 CFG，所以 6 步就快）。用户填了负向提示词时抬到
# 2.0 走 LCM-CFG：负向词真正参与引导，代价是每步算两遍、耗时约翻倍。
_LCM_GUIDANCE_WITH_NEGATIVE = 2.0
# offload 策略门槛（MB）：见模块开头说明。
_MODEL_OFFLOAD_MIN_FREE_MB = 3600
_SEQUENTIAL_MIN_FREE_MB = 1500

# 加载前要求的空闲物理内存：RealVis 3.97GB + VAE 319MB + LoRA 129MB 的权重要先读进内存，
# torch/diffusers 运行期再加约 3-4GB。空闲内存不够时 worker 会被系统直接杀掉，
# 现场只留下「日志断在半路」——2026-11-08 视频引擎就是这么掉的，图像引擎同理。
_MIN_FREE_RAM_MB = 6_144


def _min_free_ram_mb() -> int:
    """加载前要求的空闲物理内存（MB）；排障时可用 CHENGUAI_SD_MIN_FREE_RAM_MB 临时压低。"""
    return floor_mb_or(os.environ.get("CHENGUAI_SD_MIN_FREE_RAM_MB"), _MIN_FREE_RAM_MB)


def _round_to_multiple(value: int, multiple: int = _MULTIPLE) -> int:
    """把尺寸就近取整到 multiple 的倍数，并限制在 [multiple, _MAX_SIDE]。"""
    return round_to_multiple(value, multiple, _MAX_SIDE)


def _sequential_floor_mb() -> int:
    """逐层换入所要求的最低空闲显存（MB）。

    正常走 _SEQUENTIAL_MIN_FREE_MB；实测/排障时可用 CHENGUAI_SD_MIN_FREE_MB 临时压低，
    比如显存被第三方程序占走 10GB、只剩 1.1GB 时验证「逐层换入到底还能不能出图」。
    """
    raw = os.environ.get("CHENGUAI_SD_MIN_FREE_MB", "").strip()
    if not raw:
        return _SEQUENTIAL_MIN_FREE_MB
    try:
        return max(256, int(raw))
    except ValueError:
        return _SEQUENTIAL_MIN_FREE_MB


def _hub_repo_cache_dir(cache_dir: Path, repo_id: str) -> Path:
    """huggingface_hub 标准缓存布局中该仓库的目录（models--org--name）。"""
    return cache_dir / f"models--{repo_id.replace('/', '--')}"


def _pick_local_snapshot(
    cache_dir: Path,
    repo_id: str,
    probe: Callable[[Path], bool],
) -> Optional[Path]:
    """返回最完整的本地快照目录；probe 判定「这份快照算完整」，缺件的不算。"""
    snapshots = _hub_repo_cache_dir(cache_dir, repo_id) / "snapshots"
    if not snapshots.is_dir():
        return None
    candidates = [d for d in snapshots.iterdir() if d.is_dir() and probe(d)]
    if not candidates:
        return None
    return max(candidates, key=lambda d: d.stat().st_mtime_ns)


def _has_index(snapshot: Path) -> bool:
    return (snapshot / "model_index.json").is_file()


def _has_weight(snapshot: Path) -> bool:
    return any(snapshot.rglob("diffusion_pytorch_model*.safetensors")) or any(
        snapshot.rglob("model.safetensors")
    )


def _has_lora(snapshot: Path) -> bool:
    return bool(list(snapshot.rglob("*lora_weights*.safetensors")))


class SD15EngineError(RuntimeError):
    """引擎可恢复错误；消息会透传给任务系统作为失败原因。"""


class SD15ImageEngine(ImageGenerationEngine):
    """SD1.5 图片引擎（文生图 + 图生图 + 分块高清重绘）

    职责：
    - 懒加载 Diffusers 三件套（模型 + ft-mse VAE + LCM-LoRA），离线优先、缺权重直接报错；
    - 按目标像素自动选「单遍直出」或「底图 + 分块重绘」，保证峰值显存在预算内；
    - 结果按请求保存到指定输出目录，文件名带模式/是否高清/时间戳，不覆盖历史产物。
    """

    def __init__(
        self,
        model_id: str = "sd-realvis",
        cache_dir: Optional[str] = None,
        output_dir: Optional[str] = None,
        dtype_key: str = "fp16",
    ):
        super().__init__("SD15ImageEngine")
        if model_id not in SD15_SUPPORTED_MODELS:
            raise ValueError(f"不支持的模型 ID: {model_id}")

        self.model_id = model_id
        self.model_config = SD15_SUPPORTED_MODELS[model_id]
        self.cache_dir = Path(cache_dir) if cache_dir else Path("./models") / model_id
        self.output_dir = Path(output_dir) if output_dir else Path("./output")
        # VAE 与 LoRA 是各自独立的缓存目录，与模型缓存目录同级（models/ 下）。
        self.vae_cache_dir = Path(
            os.environ.get("CHENGUAI_SD_VAE_CACHE_DIR") or (self.cache_dir.parent / self.model_config["vae_cache_name"])
        )
        self.lora_cache_dir = Path(
            os.environ.get("CHENGUAI_SD_LORA_CACHE_DIR")
            or (self.cache_dir.parent / self.model_config["lora_cache_name"])
        )
        self.pipe: Optional[StableDiffusionPipeline] = None
        self.offload_mode: Optional[str] = None
        self.uses_lcm = False
        self._load_lock = asyncio.Lock()

        mapping = {"fp16": torch.float16}
        self.current_dtype = mapping.get(dtype_key, torch.float16)

    @property
    def default_steps(self) -> int:
        """默认采样步数（LCM-LoRA 官方推荐区间中段）。"""
        return int(self.model_config.get("default_steps", 6))

    @property
    def memory_usage(self) -> float:
        if not torch.cuda.is_available():
            return 0.0
        return torch.cuda.memory_allocated() / (1024**3)

    @property
    def peak_memory_usage(self) -> float:
        """本次进程内 torch 自己用过的显存峰值（GB）——排他统计，不含别的进程的占用。"""
        if not torch.cuda.is_available():
            return 0.0
        return torch.cuda.max_memory_allocated() / (1024**3)

    @property
    def requires_cuda(self) -> bool:
        return True

    def _free_vram_mb(self) -> Optional[int]:
        if not torch.cuda.is_available():
            return None
        free_bytes, _total = torch.cuda.mem_get_info()
        return int(free_bytes / (1024 * 1024))

    async def load(self) -> None:
        """加载 pipeline（懒加载、幂等；失败抛 SD15EngineError，绝不静默）。"""
        async with self._load_lock:
            if self.pipe is not None:
                return

            repo = self.model_config["hf_repo"]
            vae_repo = self.model_config["vae_repo"]
            lora_repo = self.model_config["lora_repo"]

            # 离线优先：权重快照缺失时直接报错并列出期望位置，绝不盲目联网重下。
            ckpt = _pick_local_snapshot(self.cache_dir, repo, _has_index)
            if ckpt is None:
                expected = _hub_repo_cache_dir(self.cache_dir, repo) / "snapshots"
                raise SD15EngineError(
                    f"未找到模型 {repo} 的完整本地权重快照（期望目录：{expected}）。"
                    "引擎默认离线加载、不会自动联网下载；请先运行 scripts/download_image_weights.py "
                    "获取权重（走 hf-mirror 镜像、支持断点续传），或显式设置 HF_HUB_OFFLINE=0 再重试。"
                )
            vae_snapshot = _pick_local_snapshot(self.vae_cache_dir, vae_repo, _has_weight)
            lora_snapshot = _pick_local_snapshot(self.lora_cache_dir, lora_repo, _has_lora)

            # CUDA 检查必须在 from_pretrained 之前：原顺序是先把几个 GB 权重从磁盘读进
            # 内存（几分钟 + 内存压力），最后才发现没有 GPU 并抛错。
            if not torch.cuda.is_available():
                raise SD15EngineError(
                    "当前环境没有可用的 CUDA 设备；SD1.5 引擎需要 NVIDIA GPU"
                    "（按 6GB 显存档位设计：fp16 + 卸载 + tiling，真机实测峰值 2051MB）。"
                    "若本机有其他程序占用全部显存（如本地大模型引擎），先释放显存再生成。"
                )
            # 权重读进内存之前先量一次空闲物理内存：不够就别加载，免得进程被系统静默杀掉。
            ram = read_physical_ram()
            self.free_ram_mb_at_load = ram.free_mb
            if not ram_gate_ok(ram, _min_free_ram_mb()):
                raise SD15EngineError(
                    ram_gate_message("SD1.5（RealVis V5.1）图像引擎", _min_free_ram_mb(), ram,
                                     "三件套权重 4.41GB 要先读进物理内存，再加 torch/diffusers 运行期约 3-4GB")
                )
            try:
                loop = asyncio.get_running_loop()
                self.pipe = await loop.run_in_executor(None, lambda: self._build_pipeline(ckpt, vae_snapshot))
            except SD15EngineError:
                self.pipe = None
                raise
            except Exception as exc:  # noqa: BLE001 - 转换为引擎错误并透传
                self.pipe = None
                raise SD15EngineError(
                    f"加载模型 {repo} 失败：{exc}（权重在 {self.cache_dir}，"
                    "可用 scripts/download_image_weights.py 重下补齐）"
                ) from exc

            if lora_snapshot is not None:
                try:
                    await loop.run_in_executor(None, lambda: self._apply_lcm_lora(lora_snapshot))
                except Exception as exc:  # noqa: BLE001 - LoRA 装不上只影响速度，不影响能不能出图
                    print(
                        f"[sd15] LCM-LoRA 装载失败，退回常规采样器（会更慢）：{exc}",
                        file=sys.stderr,
                        flush=True,
                    )
                    self.uses_lcm = False
                    self._configure_default_scheduler()
            else:
                print(
                    f"[sd15] 未找到 LCM-LoRA（期望目录 "
                    f"{_hub_repo_cache_dir(self.lora_cache_dir, lora_repo) / 'snapshots'}），"
                    "改用常规采样器：出图更慢、步数下限更高，但同样能出。",
                    file=sys.stderr,
                    flush=True,
                )
                self._configure_default_scheduler()

            self._configure_offload()

    def _build_pipeline(self, ckpt: Path, vae_snapshot: Optional[Path]) -> StableDiffusionPipeline:
        pipe = StableDiffusionPipeline.from_pretrained(
            str(ckpt),
            # diffusers>=0.41 的 from_pretrained 参数名（旧名 torch_dtype 已弃用）
            dtype=self.current_dtype,
            # 内容安全审查模块（1GB 级、且会把正常人物图误判）本端不需要；关掉它省显存。
            safety_checker=None,
            requires_safety_checker=False,
            local_files_only=True,
        )
        if vae_snapshot is not None:
            # RealVis 自带的 VAE 质量偏差（仓库名里的 noVAE 就是说这事），换成官方微调版。
            pipe.vae = AutoencoderKL.from_pretrained(str(vae_snapshot), dtype=self.current_dtype)
        pipe.set_progress_bar_config(disable=True)
        return pipe

    def _apply_lcm_lora(self, lora_snapshot: Path) -> None:
        assert self.pipe is not None
        self.pipe.load_lora_weights(str(lora_snapshot))
        self.pipe.scheduler = LCMScheduler.from_config(self.pipe.scheduler.config)
        self.uses_lcm = True

    def _configure_default_scheduler(self) -> None:
        """没有 LCM-LoRA 时退回 DPM++ 2M Karras（RealVis 官方推荐的常规档）。"""
        assert self.pipe is not None
        try:
            self.pipe.scheduler = DPMSolverMultistepScheduler.from_config(
                self.pipe.scheduler.config,
                use_karras_sigmas=True,
            )
        except Exception:  # noqa: BLE001 - 仓库自带调度器也能跑，不为了调度器把生成卡死
            pass

    def _configure_offload(self) -> None:
        """按当前真正可用的显存挑 offload 策略；两者都远低于 6GB 峰值预算。"""
        assert self.pipe is not None
        free_mb = self._free_vram_mb()
        if free_mb is None:
            raise SD15EngineError("加载后 CUDA 不可用，无法继续。")
        # VAE 解码按块走：最后那一步（latent → 像素）也不随画幅线性涨。注意这是
        # AutoencoderKL 上的方法，pipeline 本身没有 enable_vae_tiling（diffusers 0.41）。
        try:
            self.pipe.vae.enable_tiling()
        except Exception:  # pragma: no cover - 版本差异，缺了不影响出图
            pass
        floor = _sequential_floor_mb()
        if free_mb >= _MODEL_OFFLOAD_MIN_FREE_MB:
            self.pipe.enable_model_cpu_offload()
            self.offload_mode = "model"
        elif free_mb >= floor:
            # 逐层换入：峰值只剩一两 GB，代价是慢两三倍。显存被第三方程序占满时
            # （本机常见：某个常驻进程占走 10GB）这是唯一还能出图且不 OOM 的路。
            self.pipe.enable_sequential_cpu_offload()
            self.offload_mode = "sequential"
            print(
                f"[sd15] 可用显存仅 {free_mb}MB，改用逐层换入（慢约 2-3 倍，峰值更低）。"
                "若想让速度回来，先腾出 3.6GB 以上显存再生成。",
                file=sys.stderr,
                flush=True,
            )
        else:
            self.pipe = None
            raise SD15EngineError(
                f"可用显存不足：当前空闲仅 {free_mb}MB，SD1.5 引擎至少需要 {floor}MB。"
                "网页端「硬件」面板会点名是谁占着显存；请先释放（本地大模型空闲 10 分钟会自动卸载，"
                "也可点「释放空闲引擎」），或关闭占用显存的第三方程序后重试。"
            )

    def _steps_and_guidance(self, steps: Optional[int], has_negative: bool = False) -> tuple[int, float]:
        """步数与 CFG 联动：LCM 档 4-8 步、guidance 1.0（无 CFG 才快）。

        填了负向提示词就抬到 2.0（LCM-CFG），否则负向词等于没写——界面上那些
        「模糊、水印」全都不生效，用户会以为功能坏了。
        """
        raw = int(steps) if steps is not None else self.default_steps
        if self.uses_lcm:
            guidance = _LCM_GUIDANCE_WITH_NEGATIVE if has_negative else 1.0
            return max(_LCM_MIN_STEPS, min(raw, _LCM_MAX_STEPS)), guidance
        return max(_NO_LORA_MIN_STEPS, min(raw, _NO_LORA_MAX_STEPS)), 7.0

    def _resolve_plan(self, width: int, height: int, hires: Optional[bool]) -> tuple[int, int, int, int, bool]:
        """返回 (目标宽高, 底图宽高, 是否走高清重绘)。规则本体在 app/engine/image/plan.py。"""
        try:
            made = resolve_output_plan(width, height, hires)
        except PlanBudgetError as exc:
            raise SD15EngineError(str(exc)) from exc
        return made.target_width, made.target_height, made.base_width, made.base_height, made.use_hires

    def _img2img_pipeline(self) -> StableDiffusionImg2ImgPipeline:
        """从已加载的组件里就地组一个 img2img 包装（与文生图共用同一份权重）。

        CPU offload 的钩子是挂在模块上的，新建的 pipeline 复用同一批模块，
        换入换出的策略自动生效，不需要也不应该再调一次 enable_*_offload。
        """
        assert self.pipe is not None
        pipe = StableDiffusionImg2ImgPipeline(**self.pipe.components)
        pipe.set_progress_bar_config(disable=True)
        return pipe

    async def _refine_tiles(
        self,
        pipe_i2i: StableDiffusionImg2ImgPipeline,
        base_img: PIL.Image.Image,
        target_w: int,
        target_h: int,
        prompt: str,
        negative_prompt: Optional[str],
        steps: int,
        guidance: float,
        seed: Optional[int],
        strength: float,
    ) -> tuple[PIL.Image.Image, int]:
        """底图放大 -> 分块重绘 -> 羽化拼接。每块只有 512x512，峰值与出小图时一致。"""
        upscaled = upscale_lanczos(base_img, target_w, target_h)
        specs = tile_grid(target_w, target_h, DEFAULT_TILE, DEFAULT_OVERLAP)
        blender = TileBlender(target_w, target_h)
        device = "cuda" if torch.cuda.is_available() else "cpu"
        loop = asyncio.get_running_loop()
        for index, spec in enumerate(specs, start=1):
            crop = upscaled.crop((spec.x, spec.y, spec.right, spec.bottom))
            generator = None
            if seed is not None:
                # 每块种子不同但可复现：同一 seed 下整幅拼图的结果是确定的。
                generator = torch.Generator(device=device).manual_seed(int(seed) + index)
            kwargs: Dict[str, Any] = dict(
                prompt=prompt,
                image=crop,
                strength=strength,
                num_inference_steps=max(4, min(int(steps), 8)),
                guidance_scale=guidance,
                height=spec.height,
                width=spec.width,
                generator=generator,
            )
            if negative_prompt:
                kwargs["negative_prompt"] = negative_prompt
            try:
                result = await loop.run_in_executor(None, lambda: pipe_i2i(**kwargs))
            except Exception as exc:  # noqa: BLE001
                raise SD15EngineError(
                    f"高清重绘第 {index}/{len(specs)} 块失败（块位置 {spec.x},{spec.y} "
                    f"{spec.width}x{spec.height}）：{exc}"
                ) from exc
            if not getattr(result, "images", None):
                raise SD15EngineError(f"高清重绘第 {index}/{len(specs)} 块返回空结果")
            blender.paste(
                result.images[0],
                spec,
                feather_mask(spec.width, spec.height, DEFAULT_OVERLAP),
            )
            print(
                f"[sd15] 高清重绘 {index}/{len(specs)} 块完成（{target_w}x{target_h}）",
                file=sys.stderr,
                flush=True,
            )
        return blender.result(), len(specs)

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
        hires: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """生成图片（给了 init_image_path 就走 img2img；目标像素大就自动高清重绘）

        Returns:
            {"path", "pil", "status", "seed", "mode", "hires", "tiles",
             "base_size", "target_size", "peak_vram_mb", "offload"}

        Raises:
            SD15EngineError: 权重缺失 / 无 GPU / 显存不足 / 输入图片无效 / 结果为空
        """
        prompt = coerce_prompt_text(prompt) or ""
        if not prompt:
            raise SD15EngineError("提示词为空：请输入中文或英文描述后重试")
        negative_prompt = coerce_prompt_text(negative_prompt)

        # 编码完整性防线（2026-11-06 中文提示词全批失败的根因）：上游若把中文解成带
        # 孤立代理码位的坏 str，tokenizers 只会报一句看不懂的类型错，这里提前点名。
        if os.environ.get("CHENGUAI_SD_DEBUG_PROMPT") == "1":
            print(
                f"[sd15.debug] prompt type={type(prompt).__name__} repr={prompt!r}\n"
                f"[sd15.debug] negative_prompt type={type(negative_prompt).__name__} "
                f"repr={negative_prompt!r}",
                file=sys.stderr,
                flush=True,
            )
        for name, value in (("prompt", prompt), ("negative_prompt", negative_prompt)):
            if contains_lone_surrogate(value):
                raise SD15EngineError(
                    f"{name} 在进程间传输中被损坏（含非法代理码位，常见于管道按 GBK/cp936 "
                    "解码 UTF-8 字节）：请确认 worker 的 stdin/stdout 已按 UTF-8 配置后重试"
                    f"（诊断片段：{value[:40]!r}）"
                )

        # SD1.5 的文本编码器是 CLIP，词表里没有中文字：实测中文提示词会出成
        # 没有主体的空场景。这里做一层「中文 → CLIP 英文关键词」桥接，喂给模型的是
        # 英文关键词，用户写的中文原样保留在返回的元数据里给界面显示。
        # （视频用 UMT5、音乐用 ACE-Step，都原生多语，不需要这层。）
        bridged = to_clip_prompt(prompt)
        bridged_negative = to_clip_prompt(negative_prompt) if negative_prompt else None
        clip_prompt = bridged.text
        clip_negative = bridged_negative.text if bridged_negative else None
        if bridged.unmapped or (bridged_negative and bridged_negative.unmapped):
            unknown = list(bridged.unmapped) + list(bridged_negative.unmapped if bridged_negative else ())
            print(
                "[sd15] 词表没收录的中文描述词（不影响其它词，可按英文重写）："
                + "、".join(unknown[:12]),
                file=sys.stderr,
                flush=True,
            )

        raw_out = output_dir if output_dir else str(self.output_dir)
        target_dir = Path(raw_out).expanduser()
        if not target_dir.is_absolute():
            target_dir = Path.cwd() / target_dir
        target_dir = target_dir.resolve()
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise SD15EngineError(f"无法创建输出目录：{target_dir}（{exc}）") from exc

        init_img: Optional[PIL.Image.Image] = None
        if init_image_path:
            init = Path(str(init_image_path)).expanduser()
            if not init.is_file():
                raise SD15EngineError(f"图生图输入文件不存在：{init}")
            try:
                init_img = PIL.Image.open(init).convert("RGB")
            except (OSError, ValueError) as exc:
                raise SD15EngineError(
                    f"图生图输入图片无法读取：{init}（{type(exc).__name__}: {exc}）。"
                    "请确认文件确实是 PNG/JPG/WebP 图片、路径可访问，"
                    "或先把图片复制到不含中文的输出目录再重试。"
                ) from exc

        target_w, target_h, base_w, base_h, use_hires = self._resolve_plan(width, height, hires)
        await self.load()
        assert self.pipe is not None

        inference_steps, guidance = self._steps_and_guidance(steps, bool(clip_negative))
        loop = asyncio.get_running_loop()
        if torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()

        try:
            if init_img is not None:
                pipe = self._img2img_pipeline()
                # 底图这一遍：图生图按 strength 决定「离原图多远」。
                resized_init = init_img.resize((base_w, base_h), PIL.Image.LANCZOS)
                kwargs: Dict[str, Any] = dict(
                    prompt=clip_prompt,
                    image=resized_init,
                    strength=max(0.0, min(float(strength), 1.0)),
                    num_inference_steps=inference_steps,
                    guidance_scale=guidance,
                    height=base_h,
                    width=base_w,
                )
            else:
                pipe = self.pipe
                kwargs = dict(
                    prompt=clip_prompt,
                    num_inference_steps=inference_steps,
                    guidance_scale=guidance,
                    height=base_h,
                    width=base_w,
                )
            if clip_negative:
                kwargs["negative_prompt"] = clip_negative
                device = "cuda" if torch.cuda.is_available() else "cpu"
                kwargs["generator"] = torch.Generator(device=device).manual_seed(int(seed))

            result = await loop.run_in_executor(None, lambda: pipe(**kwargs))
            if not getattr(result, "images", None):
                raise SD15EngineError("pipeline 返回结果为空")
            image: PIL.Image.Image = result.images[0]

            tiles = 0
            if use_hires:
                image, tiles = await self._refine_tiles(
                    self._img2img_pipeline(),
                    image,
                    target_w,
                    target_h,
                    clip_prompt,
                    clip_negative,
                    steps,
                    guidance,
                    seed,
                    _HIRES_STRENGTH,
                )
        except SD15EngineError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise SD15EngineError(f"生成失败：{exc}") from exc

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        mode = "img2img" if init_image_path else "txt2img"
        stem = f"sd_{mode}_hires_{stamp}" if use_hires else f"sd_{mode}_{stamp}"
        output_path = target_dir / f"{stem}.png"
        # 文件名只精确到秒：连续提交或重试落在同一秒时会写成同一个文件，
        # 前一张图被覆盖，历史记录里两条任务指向同一个产物。
        counter = 1
        while output_path.exists():
            output_path = target_dir / f"{stem}_{counter}.png"
            counter += 1
        image.save(output_path)

        return {
            "path": str(output_path.resolve()),
            "pil": image,
            "status": "generated",
            "seed": seed,
            "mode": mode,
            "hires": use_hires,
            "tiles": tiles,
            "base_size": f"{base_w}x{base_h}",
            "target_size": f"{target_w}x{target_h}",
            "steps": inference_steps,
            "guidance": guidance,
            "offload": self.offload_mode,
            "peak_vram_mb": round(self.peak_memory_usage * 1024),
            "lcm": self.uses_lcm,
            **bridged.as_dict(),
            "prompt_negative_effective": clip_negative,
            "prompt_negative_translated": bool(bridged_negative and bridged_negative.translated),
        }

    async def generate(self, prompt: str, **kwargs) -> list[str]:
        """AIGenerationEngine.generate 的便捷实现：返回输出文件路径列表。"""
        result = await self.generate_image(prompt, **kwargs)
        return [result["path"]]

    async def shutdown(self) -> None:
        """释放 pipeline 与显存（供任务系统适配器调用）。"""
        self.pipe = None
        self.offload_mode = None
        self.uses_lcm = False
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()

