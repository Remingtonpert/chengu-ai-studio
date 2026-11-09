"""Wan2.1 生成引擎实现（视频：wan-t2v / wan-i2v；图片：wan-t2i / wan-i2i）

2026-11-09 图片引擎重新选型：文生图/图生图不再用 SD1.5（CLIP 只认英文）或 FLUX
（权重 23GB，用户明确否决；且 HF 上已是 gated 仓库，403 拉不动），改为直接复用
已经下载好的 Wan2.1 1.3B：
- wan-t2i：Wan2.1-T2V-1.3B 的 num_frames=1 单帧模式（Wan 官方就支持 T2V 模型出图），
  文本编码器 UMT5-XXL 原生支持中文和英文 Prompt；
- wan-i2i：Wan2.1-VACE-1.3B 的 reference_images 条件（官方 R2V 参考生成），
  以垫图为参考按 Prompt 重画一张，产物是单帧 PNG。
两个模型都是 Apache-2.0、单文件体积最小的 1.3B 档，权重与视频引擎完全共用
（models/wan2.1 一套仓库，零新增下载）。显存策略与视频同一套：bf16 + 逐层换入，
单帧的 token 数只有视频最低档的约 1/13，峰值预期比视频档（真机实测约 2GB）更低
（图片档尚未真机实测，验收时补测）。

wan-i2v 使用官方 Wan2.1-VACE-1.3B（diffusers WanVACEPipeline）：
Wan2.1 官方并没有发布 1.3B 参数的 I2V 模型（I2V 仅有 14B 档，12GB 显存跑不动），
VACE 是官方 1.3B 档唯一支持图像条件（首帧条件即图生视频）的 Apache-2.0 形态。

本模块只在 .venv-video（含 torch / diffusers / imageio-ffmpeg）中被导入；
桌面主进程与 Web 进程不加载它。真实生成经 app.tasks.video_worker.WanTaskAdapter
接入任务系统（JSONL IPC）。

要点（与 app/engine/image/sd15.py 同一范式）：
- 离线优先：权重按 huggingface_hub 缓存布局放在 models/wan2.1，缺失即诚实报错，
  绝不盲目联网重下、绝不伪造输出。
- 两仓库均 Apache-2.0、gated:false，不需要任何 HF token。
- dtype=bfloat16 + enable_sequential_cpu_offload：UMT5-XXL 光文本编码单件就 6.4GB，只有逐层换入 + token 预算才能把峰值压进 6GB（慢约 2-3 倍）。
  只有逐件换入 GPU 才能装进小显存（图像引擎同款策略已在本机验证）。
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
import numpy as np
import os
import sys
import torch
from diffusers import AutoencoderKLWan, UniPCMultistepScheduler, WanPipeline, WanVACEPipeline
from diffusers.utils import export_to_video

from app.engine.abstract import VideoGenerationEngine
from app.engine.memprobe import floor_mb_or, ram_gate_message, ram_gate_ok, read_physical_ram
from app.engine.prompt_utils import contains_lone_surrogate, coerce_prompt_text

# Wan2.1 支持的模型（全部 Apache-2.0，可直接商用与再分发；
# 图片引擎与视频引擎共用同一套 HF 仓库缓存，零新增下载）
WAN_SUPPORTED_MODELS = {
    "wan-t2v": {
        "hf_repo": "Wan-AI/Wan2.1-T2V-1.3B-Diffusers",
        "license": "Apache-2.0",
        "est_mem_gb": 5.4,  # 逐层换入 + token 预算后的实测峰值上限（6GB 铁律档）
        "default_steps": 28,
        "pipeline": "WanPipeline",
    },
    "wan-i2v": {
        "hf_repo": "Wan-AI/Wan2.1-VACE-1.3B-diffusers",
        "license": "Apache-2.0",
        "est_mem_gb": 5.4,
        "default_steps": 28,
        "pipeline": "WanVACEPipeline",
    },
    "wan-t2i": {
        # 单帧生图：与 wan-t2v 同一权重（Wan 官方 T2V 模型 num_frames=1 即出图）。
        "hf_repo": "Wan-AI/Wan2.1-T2V-1.3B-Diffusers",
        "license": "Apache-2.0",
        # 单帧 token 数只有 81 帧视频档的约 1/13，DiT 走同样的逐层换入；
        # 视频档真机实测峰值约 2GB，图片档预期更低——未实测，验收时补测。
        "est_mem_gb": 5.4,
        "default_steps": 28,
        "pipeline": "WanPipeline",
        "output": "image",
    },
    "wan-i2i": {
        # 图生图：与 wan-i2v 同一 VACE-1.3B 权重，reference_images 参考重绘出单帧。
        "hf_repo": "Wan-AI/Wan2.1-VACE-1.3B-diffusers",
        "license": "Apache-2.0",
        "est_mem_gb": 5.4,
        "default_steps": 28,
        "pipeline": "WanVACEPipeline",
        "output": "image",
    },
}

# 引擎 ID 分组：图片档 / 走 VACE 管线的档 / 需要垫图的档
_IMAGE_MODEL_IDS = frozenset({"wan-t2i", "wan-i2i"})
_VACE_MODEL_IDS = frozenset({"wan-i2v", "wan-i2i"})
_REF_IMAGE_MODEL_IDS = frozenset({"wan-i2v", "wan-i2i"})

# 尺寸必须可被 vae_scale_factor_spatial(8) * patch(2) = 16 整除
_MULTIPLE = 16
# Wan 原生帧率与 1.3B 档像素上限（480p 级：832x480；留一点余量到 480*832*1.15）
WAN_FPS = 16
_MAX_PIXELS = int(832 * 480 * 1.15)
_MIN_FRAMES, _MAX_FRAMES = 5, 161
# 6GB 显存铁律下真正的驱动量是 DiT 的 token 数：latent 帧数 × (宽/16) × (高/16)。
# 20000 tokens ≈ 49 帧 @ 512x384 的实测峰值 5GB 上下，留一点余量。
_MAX_TOKENS = 20_000

# Wan 官方 README 的标准负面提示（480p 档），用户未填写时使用
DEFAULT_NEGATIVE_PROMPT = (
    "色调艳丽，过曝，静态，细节模糊不清，字幕，风格，作品，画作，画面，静止，"
    "整体发灰，最差质量，低质量，JPEG压缩残留，丑陋的，残缺的，多余的手指，"
    "画得不好的手部，画得不好的脸部，畸形的，毁容的，形态畸形的肢体，手指融合，"
    "静止不动的画面，杂乱的背景，三条腿，背景人很多，倒着走"
)


# 逐层换入只需要一点余量就能开跑；实测/排障时可用 CHENGUAI_WAN_MIN_FREE_MB 临时压低。
_SEQUENTIAL_MIN_FREE_MB = 1500


def _sequential_floor_mb() -> int:
    raw = os.environ.get("CHENGUAI_WAN_MIN_FREE_MB", "").strip()
    if not raw:
        return _SEQUENTIAL_MIN_FREE_MB
    try:
        return max(256, int(raw))
    except ValueError:
        return _SEQUENTIAL_MIN_FREE_MB


# 加载前的「空闲物理内存」门槛。Wan2.1 的 UMT5-XXL 文本编码器单件就约 11GB，
# 权重要先从磁盘读进物理内存再分片换入显存；内存不够时进程会被系统直接杀掉，
# 连 Python 报错都不会留下（2026-11-08 的 wan-i2v 就是这么静默消失的）。
# 16GB 这个数是按「UMT5 bf16 约 11.4GB + transformer bf16 + fp32 VAE + torch 运行期」留的余量。
_MIN_FREE_RAM_MB = 16_384


def _min_free_ram_mb() -> int:
    """空闲物理内存门槛；排障时可用 CHENGUAI_WAN_MIN_FREE_RAM_MB 临时压低。"""
    return floor_mb_or(os.environ.get("CHENGUAI_WAN_MIN_FREE_RAM_MB"), _MIN_FREE_RAM_MB)


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
    """Wan2.1 引擎（wan-t2v 文生视频 / wan-i2v 图生视频 / wan-t2i 文生图 / wan-i2i 图生图）"""

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
        self.free_ram_mb_at_load: Optional[int] = None
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
                    "当前环境没有可用的 CUDA 设备；Wan2.1 视频引擎需要 NVIDIA GPU"
                    "（按 6GB 显存档位设计：bf16 + 逐层 CPU 卸载，真机实测峰值 2021MB）。"
                )
            repo = self.model_config["hf_repo"]
            if _pick_local_snapshot(self.cache_dir, repo) is None:
                expected = _hub_repo_cache_dir(self.cache_dir, repo) / "snapshots"
                raise WanVideoEngineError(
                    f"未找到模型 {repo} 的完整本地权重快照（期望目录：{expected}）。"
                    "引擎默认离线加载、不会自动联网下载；请先运行 scripts\\download_video_weights.py "
                    "获取权重（Apache-2.0，无需 token），或显式设置 HF_HUB_OFFLINE=0 联网重试。"
                )
            # 先量物理内存再加载：不够的话原生层会把进程直接杀掉，什么 traceback 都没有，
            # 现场看起来像「代码路径坏了」，其实是被内存不足害死（2026-11-08 实测踩过）。
            ram = read_physical_ram()
            self.free_ram_mb_at_load = ram.free_mb
            if not ram_gate_ok(ram, _min_free_ram_mb()):
                raise WanVideoEngineError(
                    ram_gate_message("Wan2.1 视频引擎", _min_free_ram_mb(), ram,
                                     "UMT5-XXL 文本编码单件就要约 11GB 物理内存")
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
                # 6GB 铁律：UMT5-XXL 单件就 6.4GB，逐件换入（model offload）永远压不进 6GB，
                # 只能逐层换入（sequential）：慢约 2-3 倍，但峰值只跟最大的那一层走。
                self._configure_offload()
                # VAE 解码 81 帧 480p 的峰值靠 tiling 压住（float32 VAE 按官方示例保留）。
                try:
                    self.pipe.vae.enable_tiling()
                except Exception:  # noqa: BLE001 - 老版 diffusers 无此方法时忽略
                    pass
            self.pipe.set_progress_bar_config(disable=True)

    def _free_vram_mb(self) -> Optional[int]:
        """当前空闲显存（MB）；拿不到就返回 None（无 GPU 驱动 / 查询失败）。"""
        if not torch.cuda.is_available():  # pragma: no cover - 无 GPU 环境
            return None
        try:
            free_bytes, _total = torch.cuda.mem_get_info()
        except Exception:  # noqa: BLE001 - 驱动查询失败不致命
            return None
        return int(free_bytes // (1024 * 1024))

    def _configure_offload(self) -> None:
        """显存换入策略：6GB 档只有「逐层换入」一条路，不够就点名占用者并报错。"""
        floor = _sequential_floor_mb()
        free_mb = self._free_vram_mb()
        if free_mb is None:
            raise WanVideoEngineError(
                "读不到 GPU 显存信息，无法确认 6GB 峰值档是否够用；请确认 NVIDIA 驱动正常后重试。"
            )
        if free_mb < floor:
            raise WanVideoEngineError(
                f"可用显存不足：当前空闲仅 {free_mb}MB，Wan2.1 逐层换入至少需要 {floor}MB。"
                "端上的「引擎状态」面板（网页端是「硬件」页）会点名是谁占着显存；请先释放："
                "本地大模型与引擎空闲 3 分钟会自动卸载，也可手动跑 scripts\\release_local_engine.bat，"
                "或者关闭占用显存的第三方程序后重试。"
            )
        self.pipe.enable_sequential_cpu_offload()
        self.offload_mode = "sequential"
        print(
            f"[wan] 逐层换入（sequential）已启用，空闲显存 {free_mb}MB；"
            "这是把峰值压进 6GB 的代价：比逐件换入慢约 2-3 倍。",
            file=sys.stderr,
            flush=True,
        )

    @staticmethod
    def _latent_frames(num_frames: int) -> int:
        """Wan 的时间压缩是 4x：81 帧 → 21 个 latent 帧。"""
        return (int(num_frames) - 1) // 4 + 1

    def _fit_token_budget(self, width: int, height: int, num_frames: int) -> tuple[int, int, bool]:
        """画幅缩到 token 预算内（6GB 峰值铁律），返回 (宽, 高, 是否缩过)。"""
        latent = self._latent_frames(num_frames)
        tokens = max(1, (width // _MULTIPLE) * (height // _MULTIPLE) * latent)
        if tokens <= _MAX_TOKENS:
            return width, height, False
        scale = (_MAX_TOKENS / tokens) ** 0.5
        new_w = max(_MULTIPLE * 8, (int(width * scale) // _MULTIPLE) * _MULTIPLE)
        new_h = max(_MULTIPLE * 8, (int(height * scale) // _MULTIPLE) * _MULTIPLE)
        return new_w, new_h, True

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
        if self.model_id in _IMAGE_MODEL_IDS:
            raise WanVideoEngineError(
                f"{self.model_id} 是图片引擎（单帧产物），不走视频通道；视频请用 wan-t2v / wan-i2v"
            )
        prompt = coerce_prompt_text(prompt) or ""
        if not prompt:
            raise WanVideoEngineError("提示词为空：请输入中文或英文描述后重试")
        negative_prompt = coerce_prompt_text(negative_prompt) or DEFAULT_NEGATIVE_PROMPT
        # 与 sd15.py 一致的入口收口：孤立代理码位（U+D800–U+DFFF，进程间编码错乱
        # 留下的坏字符）进 Rust tokenizers 会报 "TextEncodeInput must be
        # Union[TextInputSequence, ...]"，报错完全看不出根因，中文提示词必挂。
        for label, value in (("提示词", prompt), ("负面提示词", negative_prompt)):
            if contains_lone_surrogate(value):
                raise WanVideoEngineError(
                    f"{label}含有无法编码的坏字符（通常是进程间传输编码错乱造成）。"
                    "请清空后重新输入中文提示词再提交；由脚本提交时请确认按 UTF-8 传参。"
                )

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
        # 先按 6GB 的 token 预算缩画幅，而不是甩一句「显存不足」把用户挡在门外。
        raw_w, raw_h = width, height
        width, height, trimmed = self._fit_token_budget(width, height, num_frames)
        if trimmed:
            print(
                f"[wan] 6GB 显存预算（{_MAX_TOKENS} tokens）已满：{raw_w}x{raw_h} @ {num_frames} 帧 "
                f"自动缩到 {width}x{height}（这是当前档位的最大能力；想更大请先减帧数）。",
                file=sys.stderr,
                flush=True,
            )
        inference_steps = int(steps) if steps is not None else self.default_steps
        inference_steps = max(1, min(inference_steps, 50))

        await self.load()
        assert self.pipe is not None

        torch.cuda.reset_peak_memory_stats()
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
                f"显存不足（CUDA OOM）：Wan2.1 在逐层换入下实测峰值只有约 2GB，"
                f"请先释放常驻推理引擎（双击 scripts\\release_local_engine.bat），"
                f"或降低帧数/分辨率后重试。原始错误：{exc}"
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise WanVideoEngineError(f"生成失败：{exc}") from exc

        frames_out = getattr(result, "frames", None)
        if frames_out is None or len(frames_out) == 0:
            raise WanVideoEngineError("pipeline 返回结果为空")

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        mode = "i2v" if is_i2v else "t2v"
        output_path = target_dir / f"wan_{mode}_{stamp}.mp4"
        # 文件名只到秒：同一秒内的第二个任务（连点提交、重试）会覆盖同一个 mp4，
        # 历史列表里两条任务指向同一个文件。
        counter = 1
        while output_path.exists():
            output_path = target_dir / f"wan_{mode}_{stamp}_{counter}.mp4"
            counter += 1
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
            "offload": getattr(self, "offload_mode", None),
            "free_ram_mb_at_load": self.free_ram_mb_at_load,
            "token_budget_trimmed": trimmed,
            "fps": WAN_FPS,
            "seconds": round(num_frames / WAN_FPS, 2),
            "peak_vram_mb": round(torch.cuda.max_memory_allocated() / (1024 * 1024))
            if torch.cuda.is_available()
            else None,
            "width": width,
            "height": height,
        }

    async def generate_image(
        self,
        prompt: str,
        image_path: Optional[str] = None,
        width: int = 832,
        height: int = 480,
        steps: Optional[int] = None,
        seed: Optional[int] = None,
        negative_prompt: Optional[str] = None,
        output_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        """生成单帧图片（wan-t2i 文生图 / wan-i2i 参考图生图；产物 PNG）

        num_frames=1：Wan 的时间 VAE 把 1 帧压成 1 个 latent 帧（(1-1)//4+1=1），
        token 数约为视频最低档（49 帧）的 1/13；同样走逐层换入，视频档真机实测
        峰值约 2GB，图片档预期更低——图片档尚未真机实测，验收时补测，不虚报。

        wan-i2i 用 VACE 的 reference_images 参考条件：按垫图主体 + 提示词重画一张
        新图（VACE R2V 语义），不是垫图逐帧锁定——那是图生视频首帧条件的做法。

        Returns:
            {"path": png路径, "status": "generated", "seed": 种子, "frames": 1,
             "width": 实际宽, "height": 实际高}
        """
        if self.model_id not in _IMAGE_MODEL_IDS:
            raise WanVideoEngineError(
                f"不支持生成图片的模型 ID：{self.model_id}（图片只有 wan-t2i / wan-i2i）"
            )
        prompt = coerce_prompt_text(prompt) or ""
        if not prompt:
            raise WanVideoEngineError("提示词为空：请输入中文或英文描述后重试")
        negative_prompt = coerce_prompt_text(negative_prompt) or DEFAULT_NEGATIVE_PROMPT
        for label, value in (("提示词", prompt), ("负面提示词", negative_prompt)):
            if contains_lone_surrogate(value):
                raise WanVideoEngineError(
                    f"{label}含有无法编码的坏字符（通常是进程间传输编码错乱造成）。"
                    "请清空后重新输入中文提示词再提交；由脚本提交时请确认按 UTF-8 传参。"
                )

        raw_out = output_dir if output_dir else str(self.output_dir)
        target_dir = Path(raw_out).expanduser()
        if not target_dir.is_absolute():
            target_dir = Path.cwd() / target_dir
        target_dir = target_dir.resolve()
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise WanVideoEngineError(f"无法创建输出目录：{target_dir}（{exc}）") from exc

        is_i2i = self.model_id == "wan-i2i"
        ref_img: Optional[PIL.Image.Image] = None
        if is_i2i:
            if not image_path:
                raise WanVideoEngineError("图生图缺少参考图：请先上传/选择参考图再提交")
            ref = Path(str(image_path)).expanduser()
            if not ref.is_file():
                raise WanVideoEngineError(f"图生图参考图文件不存在：{ref}")
            try:
                ref_img = PIL.Image.open(ref).convert("RGB")
            except Exception as exc:  # noqa: BLE001
                raise WanVideoEngineError(f"参考图无法读取：{ref}（{exc}）") from exc
            # 出图尺寸跟随参考图比例（与 i2v 同一套 480p 像素预算 + 16 倍数收口）
            iw, ih = ref_img.size
            budget = _MAX_PIXELS / 1.15
            scale = (budget / max(1, iw * ih)) ** 0.5
            width, height = _fit_pixels(int(iw * scale), int(ih * scale))
        else:
            if image_path:
                raise WanVideoEngineError(
                    "文生图不接受参考图：改图请用图生图引擎（wan-i2i）"
                )
            width, height = _fit_pixels(width, height)

        inference_steps = int(steps) if steps is not None else self.default_steps
        inference_steps = max(1, min(inference_steps, 50))

        await self.load()
        assert self.pipe is not None

        torch.cuda.reset_peak_memory_stats()
        generator = torch.Generator(device="cpu").manual_seed(int(seed)) if seed is not None else None
        kwargs: Dict[str, Any] = dict(
            prompt=prompt,
            negative_prompt=negative_prompt,
            height=height,
            width=width,
            num_frames=1,
            num_inference_steps=inference_steps,
            guidance_scale=5.0,
            generator=generator,
        )
        if ref_img is not None:
            # 参考图收口到出图尺寸：VACE 会按自身尺寸 VAE 编码参考图并作为条件 token
            # 拼进序列（video=None 时管线自动构造全生成 mask，见 pipeline_wan_vace）。
            kwargs["reference_images"] = [ref_img.resize((width, height))]

        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(None, lambda: self.pipe(**kwargs))
        except WanVideoEngineError:
            raise
        except torch.cuda.OutOfMemoryError as exc:  # type: ignore[attr-defined]
            raise WanVideoEngineError(
                f"显存不足（CUDA OOM）：单帧生图的 token 预算本来就只有视频档的零头，"
                f"请先释放常驻推理引擎（双击 scripts\\release_local_engine.bat），"
                f"或降低分辨率后重试。原始错误：{exc}"
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise WanVideoEngineError(f"生成失败：{exc}") from exc

        frames_out = getattr(result, "frames", None)
        if frames_out is None or len(frames_out) == 0 or len(frames_out[0]) == 0:
            raise WanVideoEngineError("pipeline 返回结果为空")

        frame = np.asarray(frames_out[0][0])
        if frame.dtype != np.uint8:
            frame = (np.clip(frame.astype(np.float32), 0.0, 1.0) * 255.0).round().astype(np.uint8)

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        mode = "i2i" if is_i2i else "t2i"
        output_path = target_dir / f"wan_{mode}_{stamp}.png"
        counter = 1
        while output_path.exists():
            output_path = target_dir / f"wan_{mode}_{stamp}_{counter}.png"
            counter += 1
        try:
            PIL.Image.fromarray(frame).save(str(output_path), format="PNG")
        except Exception as exc:  # noqa: BLE001
            raise WanVideoEngineError(f"PNG 导出失败：{exc}") from exc
        if not output_path.is_file() or output_path.stat().st_size < 1024:
            raise WanVideoEngineError("PNG 导出结果异常（文件缺失或过小），已判为失败")

        return {
            "path": str(output_path.resolve()),
            "status": "generated",
            "seed": seed,
            "frames": 1,
            "offload_mode": getattr(self, "offload_mode", None),
            "peak_vram_mb": round(torch.cuda.max_memory_allocated() / (1024 * 1024))
            if torch.cuda.is_available()
            else None,
            "width": width,
            "height": height,
        }

    async def generate(self, prompt: str, **kwargs) -> list[str]:
        """AIGenerationEngine.generate 的便捷实现：返回输出文件路径列表。"""
        if self.model_id in _IMAGE_MODEL_IDS:
            result = await self.generate_image(prompt, **kwargs)
        else:
            result = await self.generate_video(prompt, **kwargs)
        return [result["path"]]

    async def shutdown(self) -> None:
        """释放 pipeline 与显存（供任务系统适配器调用）。"""
        self.pipe = None
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()
