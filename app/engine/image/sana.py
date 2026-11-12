"""Sana-1.6B 多语言版（Efficient-Large-Model）文生图引擎：原生中英混合提示词。

选型对照三条硬标准（2026-11-10 复核，取证全部来自 huggingface.co 原始仓库与模型卡原文）：

1. 可商用：仓库 license=apache-2.0（HF 元数据 gated:false，无需 token）。文本编码器是
   Gemma-2-2B-IT，额外受 Gemma Terms of Use 约束（允许商用，附禁止用途条款）；权重已内置
   在本仓库 text_encoder/ 下，不需要登录 Google 的 gated 仓库。
2. 原生中英文：模型卡原文「This model is fine-tuned from the base model
   Efficient-Large-Model/Sana_1600M_1024px_BF16 and it supports Emoji, Chinese and English
   and all mixed prompts.」——中文提示词直接进编码器，**不需要** SD1.5/CLIP 那层中英桥接。
3. 峰值显存 ≤6GB：1.6B Linear-DiT（fp16 3.06GB）+ DC-AE 32 倍下采样 VAE（fp16 1.19GB）
   + Gemma-2-2B 编码器（fp16 4.99GB）。编码器是最大单件，所以默认走「逐模块换入换出」：
   任一时刻只有一件在显存里，峰值按最大单件 4.99GB + 激活算，稳在 6GB 内；显存更紧张时
   退回逐层换入（峰值约 1-2GB，慢约两三倍）。真机峰值由本模块写进任务元数据 peak_vram_mb。

与 SD1.5 引擎（app/engine/image/sd15.py）的关键差别：
- 分辨率必须对齐 32（DC-AE 32 倍下采样），单遍像素预算比 SD1.5 宽得多（1024 级直出）；
- 没有 img2img / 分块重绘（diffusers 0.41 的 Sana 只有 t2i 与 ControlNet），要更大画幅时
  只能「预算内出底图 → Lanczos 放大」，元数据里如实标 upscaled=true、不谎称重绘；
- 提示词原样进模型，prompt_translated 恒为 false。

本模块只在 .venv-image（含 torch / diffusers / transformers）中被导入；桌面主进程不加载它。
真实生成通过 app.tasks.sana_worker.SanaTaskAdapter 接入任务系统。
权重下载：scripts/download_sana_weights.py（走 hf-mirror、只取 fp16 变体，约 9.3GB）。
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import PIL.Image
import torch
from diffusers import SanaPipeline

from app.engine.abstract import ImageGenerationEngine
from app.engine.image.plan import (
    SANA_MAX_SIDE,
    SANA_MULTIPLE,
    PlanBudgetError,
    resolve_output_plan,
    round_to_multiple,
)
from app.engine.memprobe import floor_mb_or, ram_gate_message, ram_gate_ok, read_physical_ram
from app.engine.prompt_utils import coerce_prompt_text, contains_lone_surrogate

SANA_SUPPORTED_MODELS: Dict[str, Any] = {
    "sana-multiling": {
        "hf_repo": "Efficient-Large-Model/Sana_1600M_1024px_MultiLing_diffusers",
        "license": "Apache-2.0（模型与代码）+ Gemma Terms of Use（编码器 Gemma-2-2B-IT）",
        "est_mem_gb": 5.0,
        "default_steps": 20,
        "guidance": 4.5,
        "supported_dtype": ["fp16"],
        "supports_cfg": True,
        "supports_negative": True,
        "native_languages": "中文、英文、Emoji 及中英混合（模型卡原文声明）",
    },
}

# 步数区间：模型卡与官方示例用 20 步、guidance 4.5（Sana 必须开 CFG，和 LCM 那种
# guidance=1 的路子不一样），所以这里不用 LCM 的 1-12 档。
_MIN_STEPS, _MAX_STEPS = 10, 40
_DEFAULT_GUIDANCE = 4.5

# 显存卸载策略门槛（MB）：编码器 fp16 单件 4.99GB 是最大件，逐模块换入至少要能塞下它。
_MODEL_OFFLOAD_MIN_FREE_MB = 5_120
_SEQUENTIAL_MIN_FREE_MB = 1_500

# 加载前要求的空闲物理内存：fp16 三件权重 9.06GB 要先读进内存，再加 torch/transformers
# 运行期开销。空闲物理内存不够时进程会被系统直接杀掉，现场只留下「日志断在半路」。
_MIN_FREE_RAM_MB = 16_384


def _min_free_ram_mb() -> int:
    """加载前要求的空闲物理内存（MB）；排障时可用 CHENGUAI_SANA_MIN_FREE_RAM_MB 临时压低。"""
    return floor_mb_or(os.environ.get("CHENGUAI_SANA_MIN_FREE_RAM_MB"), _MIN_FREE_RAM_MB)


def _sequential_floor_mb() -> int:
    """逐层换入所要求的最低空闲显存（MB）；排障时可用 CHENGUAI_SANA_MIN_FREE_MB 临时压低。"""
    return floor_mb_or(os.environ.get("CHENGUAI_SANA_MIN_FREE_MB"), _SEQUENTIAL_MIN_FREE_MB)


def _round_to_multiple(value: int, multiple: int = SANA_MULTIPLE) -> int:
    """把尺寸就近取整到 multiple 的倍数（DC-AE 是 32 倍下采样），并限制在 [multiple, SANA_MAX_SIDE]。"""
    return round_to_multiple(value, multiple, SANA_MAX_SIDE)


def _hub_repo_cache_dir(cache_dir: Path, repo_id: str) -> Path:
    """huggingface_hub 标准缓存布局中该仓库的目录（models--org--name）。"""
    return cache_dir / f"models--{repo_id.replace('/', '--')}"


def _snapshot_is_complete(snapshot: Path) -> bool:
    """快照算不算完整：model_index.json + fp16 三件权重 + 编码器的分片 index。

    分片权重的 index 文件名在这个仓库里是 text_encoder/model.safetensors.index.fp16.json
    （非常规命名），缺它的话 diffusers 会在 from_pretrained 里报一句很难懂的错，所以在这里
    先把它当作「完整性」的一部分检查掉，缺件直接给出可执行的补救指引。
    """
    required = [
        snapshot / "model_index.json",
        snapshot / "transformer" / "diffusion_pytorch_model.fp16.safetensors",
        snapshot / "vae" / "diffusion_pytorch_model.fp16.safetensors",
        snapshot / "tokenizer" / "tokenizer.model",
    ]
    if not all(p.is_file() for p in required):
        return False
    shards = list((snapshot / "text_encoder").glob("model.fp16-*-of-*.safetensors"))
    index = list((snapshot / "text_encoder").glob("model.safetensors.index.fp16.json"))
    return bool(shards) and bool(index)


def _pick_local_snapshot(cache_dir: Path, repo_id: str) -> Optional[Path]:
    """返回最完整、最新的本地快照目录；不完整的一律不选（宁可报缺权重也不半份加载）。"""
    snapshots = _hub_repo_cache_dir(cache_dir, repo_id) / "snapshots"
    if not snapshots.is_dir():
        return None
    candidates = [d for d in snapshots.iterdir() if d.is_dir() and _snapshot_is_complete(d)]
    if not candidates:
        return None
    return max(candidates, key=lambda d: d.stat().st_mtime_ns)


class SanaEngineError(RuntimeError):
    """引擎可恢复错误；消息会透传给任务系统作为失败原因。"""


class SanaImageEngine(ImageGenerationEngine):
    """Sana 多语言文生图引擎（1024 级单遍直出 + 必要时放大）

    职责：
    - 懒加载 Diffusers 三件（Gemma-2 编码器 / Linear-DiT / DC-AE），离线优先、缺权重直接报错；
    - 按目标像素选「预算内直出」或「底图 + Lanczos 放大」，保证峰值显存在 6GB 预算内；
    - 结果按请求保存到指定输出目录，文件名带时间戳，不覆盖历史产物。
    """

    def __init__(
        self,
        model_id: str = "sana-multiling",
        cache_dir: Optional[str] = None,
        output_dir: Optional[str] = None,
        dtype_key: str = "fp16",
    ):
        super().__init__("SanaImageEngine")
        if model_id not in SANA_SUPPORTED_MODELS:
            raise ValueError(f"不支持的模型 ID: {model_id}")

        self.model_id = model_id
        self.model_config = SANA_SUPPORTED_MODELS[model_id]
        self.cache_dir = Path(cache_dir) if cache_dir else Path("./models") / model_id
        self.output_dir = Path(output_dir) if output_dir else Path("./output")
        self.pipe: Optional[SanaPipeline] = None
        self.offload_mode: Optional[str] = None
        self.free_ram_mb_at_load: Optional[int] = None
        self._load_lock = asyncio.Lock()

        # 权重仓库里只有 fp16 变体被下载（fp32 那 12GB 没下），所以 fp16 是唯一支持的精度。
        self.current_dtype = torch.float16 if dtype_key == "fp16" else torch.float16

    @property
    def default_steps(self) -> int:
        """默认采样步数（模型卡官方示例：20 步 + guidance 4.5）。"""
        return int(self.model_config.get("default_steps", 20))

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
        """加载 pipeline（懒加载、幂等；失败抛 SanaEngineError，绝不静默）。"""
        async with self._load_lock:
            if self.pipe is not None:
                return

            repo = self.model_config["hf_repo"]

            # 离线优先：权重快照缺失或不完整时直接报错并点名期望目录，绝不盲目联网重下。
            ckpt = _pick_local_snapshot(self.cache_dir, repo)
            if ckpt is None:
                expected = _hub_repo_cache_dir(self.cache_dir, repo) / "snapshots"
                raise SanaEngineError(
                    f"未找到模型 {repo} 的完整本地权重快照（期望目录：{expected}）。"
                    "引擎默认离线加载、不会自动联网下载；请先运行 scripts/download_sana_weights.py "
                    "获取权重（走 hf-mirror 镜像、只取 fp16 变体约 9.3GB、支持断点续传）。"
                )

            # CUDA 检查必须在 from_pretrained 之前：原顺序是先把 9GB 权重从磁盘读进内存
            # （几分钟 + 内存压力），最后才发现没有 GPU 并抛错。
            if not torch.cuda.is_available():
                raise SanaEngineError(
                    "当前环境没有可用的 CUDA 设备；Sana 文生图引擎需要 NVIDIA GPU"
                    "（按 6GB 显存档位设计：fp16 + 逐模块卸载 + DC-AE 分块解码）。"
                    "若本机有其他程序占用全部显存（如本地大模型引擎），先释放显存再生成。"
                )
            # 权重读进内存之前先量一次空闲物理内存：不够就别加载，免得进程被系统静默杀掉。
            ram = read_physical_ram()
            self.free_ram_mb_at_load = ram.free_mb
            if not ram_gate_ok(ram, _min_free_ram_mb()):
                raise SanaEngineError(
                    ram_gate_message(
                        "Sana-1.6B 多语言文生图引擎",
                        _min_free_ram_mb(),
                        ram,
                        "fp16 三件权重 9.06GB 要先读进物理内存，再加 torch/transformers 运行期开销",
                    )
                )
            try:
                loop = asyncio.get_running_loop()
                self.pipe = await loop.run_in_executor(None, lambda: self._build_pipeline(ckpt))
            except SanaEngineError:
                self.pipe = None
                raise
            except Exception as exc:  # noqa: BLE001 - 转换为引擎错误并透传
                self.pipe = None
                raise SanaEngineError(
                    f"加载模型 {repo} 失败：{type(exc).__name__}: {exc}（权重在 {self.cache_dir}，"
                    "可用 scripts/download_sana_weights.py 重下补齐）"
                ) from exc

            self._configure_offload()

    def _build_pipeline(self, ckpt: Path) -> SanaPipeline:
        # variant="fp16"：本仓库同时有 fp32 与 fp16 两套权重，下载脚本只取 fp16，
        # 所以必须显式指定变体，否则 diffusers 会去找没下载的 fp32 文件。
        pipe = SanaPipeline.from_pretrained(
            str(ckpt),
            dtype=self.current_dtype,
            variant="fp16",
            use_safetensors=True,
            local_files_only=True,
        )
        # DC-AE 是 32 倍下采样的 VAE，解码那一步按块走，画幅再大也不会把显存线性顶穿。
        try:
            pipe.vae.enable_tiling()
        except Exception:  # pragma: no cover - 版本差异，缺了不影响出图
            pass
        pipe.set_progress_bar_config(disable=True)
        return pipe

    def _configure_offload(self) -> None:
        """按当前真正可用的显存挑 offload 策略；两者都压在 6GB 峰值预算内。"""
        assert self.pipe is not None
        free_mb = self._free_vram_mb()
        if free_mb is None:
            raise SanaEngineError("加载后 CUDA 不可用，无法继续。")
        if free_mb >= _MODEL_OFFLOAD_MIN_FREE_MB:
            # 逐模块换入：显存里同时只有当前那一件（最大单件是 Gemma-2 编码器 fp16 4.99GB）。
            self.pipe.enable_model_cpu_offload()
            self.offload_mode = "model"
        elif free_mb >= _sequential_floor_mb():
            # 逐层换入：峰值只剩一两 GB，代价是慢两三倍。显存被第三方程序占满时
            # （本机常见：某个常驻进程占走 10GB 以上）这是唯一还能出图且不 OOM 的路。
            self.pipe.enable_sequential_cpu_offload()
            self.offload_mode = "sequential"
            print(
                f"[sana] 可用显存仅 {free_mb}MB，改用逐层换入（慢约 2-3 倍，峰值更低）。"
                f"若想让速度回来，先腾出 {_MODEL_OFFLOAD_MIN_FREE_MB}MB 以上显存再生成。",
                file=sys.stderr,
                flush=True,
            )
        else:
            self.pipe = None
            raise SanaEngineError(
                f"可用显存不足：当前空闲仅 {free_mb}MB，Sana 引擎至少需要 {_sequential_floor_mb()}MB。"
                "网页端「硬件」面板会点名是谁占着显存；请先释放（本地大模型空闲 10 分钟会自动卸载，"
                "也可点「释放空闲引擎」），或关闭占用显存的第三方程序后重试。"
            )

    def _steps_and_guidance(self, steps: Optional[int]) -> tuple[int, float]:
        """步数夹在 [10,40]，CFG 固定 4.5（官方示例值）。

        Sana 的 CFG 是常开的：负向提示词靠它才真正参与引导，不存在 SD1.5-LCM 那种
        「guidance=1 时负向词无效」的坑，所以这里不随负向词调 guidance。
        """
        raw = int(steps) if steps is not None else self.default_steps
        guidance = float(os.environ.get("CHENGUAI_SANA_GUIDANCE") or self.model_config.get("guidance", _DEFAULT_GUIDANCE))
        return max(_MIN_STEPS, min(raw, _MAX_STEPS)), guidance

    def _resolve_plan(self, width: int, height: int) -> tuple[int, int, int, int, bool]:
        """返回 (目标宽高, 底图宽高, 是否需要放大)。规则本体在 app/engine/image/plan.py。"""
        try:
            made = resolve_output_plan(width, height, profile="sana")
        except PlanBudgetError as exc:
            raise SanaEngineError(str(exc)) from exc
        return made.target_width, made.target_height, made.base_width, made.base_height, made.upscale_only

    def _check_prompt(self, prompt: str, negative_prompt: Optional[str]) -> None:
        """编码完整性防线：上游若把中文解成带孤立代理码位的坏 str，tokenizers 只会报
        一句看不懂的类型错，这里提前点名（2026-11-06 SD1.5 中文提示词全批失败的教训）。
        """
        if os.environ.get("CHENGUAI_SANA_DEBUG_PROMPT") == "1":
            print(
                f"[sana.debug] prompt type={type(prompt).__name__} repr={prompt!r}\n"
                f"[sana.debug] negative_prompt type={type(negative_prompt).__name__} "
                f"repr={negative_prompt!r}",
                file=sys.stderr,
                flush=True,
            )
        for name, value in (("prompt", prompt), ("negative_prompt", negative_prompt)):
            if contains_lone_surrogate(value):
                raise SanaEngineError(
                    f"{name} 在进程间传输中被损坏（含非法代理码位，常见于管道按 GBK/cp936 "
                    "解码 UTF-8 字节）：请确认 worker 的 stdin/stdout 已按 UTF-8 配置后重试"
                    f"（诊断片段：{value[:40]!r}）"
                )

    async def generate_image(
        self,
        prompt: str,
        width: int = 1024,
        height: int = 1024,
        steps: Optional[int] = None,
        seed: Optional[int] = None,
        negative_prompt: Optional[str] = None,
        init_image_path: Optional[str] = None,
        output_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        """生成图片（Sana 只做文生图；给了垫图直接报错并让用户改用图生图板块）

        Returns:
            {"path", "pil", "status", "seed", "mode", "upscaled", "tiles",
             "base_size", "target_size", "steps", "guidance", "peak_vram_mb", "offload", ...}

        Raises:
            SanaEngineError: 权重缺失 / 无 GPU / 显存或内存不足 / 不接受垫图 / 结果为空
        """
        prompt = coerce_prompt_text(prompt) or ""
        if not prompt:
            raise SanaEngineError("提示词为空：请输入中文或英文描述后重试")
        negative_prompt = coerce_prompt_text(negative_prompt)
        self._check_prompt(prompt, negative_prompt)

        if init_image_path:
            raise SanaEngineError(
                "Sana 文生图引擎不接受垫图：它只有文生图管线（没有 img2img）。"
                "带参考图请改用「图生图」板块（引擎 wan-i2i，VACE 参考重绘）"
            )

        raw_out = output_dir if output_dir else str(self.output_dir)
        target_dir = Path(raw_out).expanduser()
        if not target_dir.is_absolute():
            target_dir = Path.cwd() / target_dir
        target_dir = target_dir.resolve()
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise SanaEngineError(f"无法创建输出目录：{target_dir}（{exc}）") from exc

        target_w, target_h, base_w, base_h, upscale_only = self._resolve_plan(width, height)
        await self.load()
        assert self.pipe is not None

        inference_steps, guidance = self._steps_and_guidance(steps)
        loop = asyncio.get_running_loop()
        if torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()

        try:
            kwargs: Dict[str, Any] = dict(
                # 提示词原样进模型：这个变体的编码器原生认中英混合，不做任何翻译/改写。
                prompt=prompt,
                num_inference_steps=inference_steps,
                guidance_scale=guidance,
                height=base_h,
                width=base_w,
                # 生成器放 CPU：CPU 卸载模式下 CUDA 生成器不参与，放 CPU 才可复现。
                generator=torch.Generator(device="cpu").manual_seed(int(seed)) if seed is not None else None,
            )
            if negative_prompt:
                kwargs["negative_prompt"] = negative_prompt
            result = await loop.run_in_executor(None, lambda: self.pipe(**kwargs))
            if not getattr(result, "images", None):
                raise SanaEngineError("pipeline 返回结果为空")
            image: PIL.Image.Image = result.images[0]
            if upscale_only:
                # 超出单遍像素预算时只放大、不重绘：Sana 在 diffusers 0.41 里没有 img2img，
                # 所以绝不谎称「高清重绘」，元数据里写清 upscaled=true / tiles=0。
                # 放大走「等比放大 + 居中裁边」，不走 resize((target_w, target_h))：
                # 底图边长必须取整到 32 的倍数，与目标的比例偏差不可能为零
                # （1920x1088 的底图是 1376x768，比例差 1.5%），直接 resize 就是各向异性拉伸，
                # 人脸会被拉宽；等比放大后只裁掉最多几个像素的边，画面不变形。
                scale = max(target_w / base_w, target_h / base_h)
                stage_w = max(target_w, round(base_w * scale))
                stage_h = max(target_h, round(base_h * scale))
                stage = image.resize((stage_w, stage_h), PIL.Image.LANCZOS)
                left = (stage_w - target_w) // 2
                top = (stage_h - target_h) // 2
                image = stage.crop((left, top, left + target_w, top + target_h))
        except SanaEngineError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise SanaEngineError(f"生成失败：{type(exc).__name__}: {exc}") from exc

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        stem = f"sana_txt2img_upscaled_{stamp}" if upscale_only else f"sana_txt2img_{stamp}"
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
            "mode": "txt2img",
            "upscaled": upscale_only,
            "tiles": 0,
            "base_size": f"{base_w}x{base_h}",
            "target_size": f"{target_w}x{target_h}",
            "steps": inference_steps,
            "guidance": guidance,
            "offload": self.offload_mode,
            "peak_vram_mb": round(self.peak_memory_usage * 1024),
            "prompt_original": prompt,
            "prompt_effective": prompt,
            "prompt_translated": False,
            "prompt_unmapped": [],
            "prompt_negative_effective": negative_prompt,
        }

    async def generate(self, prompt: str, **kwargs) -> list[str]:
        """AIGenerationEngine.generate 的便捷实现：返回输出文件路径列表。"""
        result = await self.generate_image(prompt, **kwargs)
        return [result["path"]]

    async def shutdown(self) -> None:
        """释放 pipeline 与显存（供任务系统适配器调用）。"""
        self.pipe = None
        self.offload_mode = None
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()
