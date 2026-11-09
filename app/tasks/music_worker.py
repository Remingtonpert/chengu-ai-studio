"""ACE-Step 1.5 音乐引擎的任务系统适配器（在 .venv-music 的 worker 子进程内运行）。

用法（由引擎注册通过 ProcessEngineAdapter 启动）:
    python -m app.tasks.process_worker --adapter-factory app.tasks.music_worker:create_adapter

模型：ACE-Step 1.5（官方仓库 ace-step/ACE-Step-1.5，权重 MIT，HuggingFace
ACE-Step/Ace-Step1.5），本地离线推理，绝不访问任何云端 API。

环境变量（主进程注册引擎时注入，全部可配置、相对路径化，便于打包迁移）:
    CHENGUAI_MUSIC_ENGINE_ID              引擎 id（默认 ace-step-15）
    CHENGUAI_MUSIC_CHECKPOINTS_DIR        权重目录（默认 <项目根>/models/ace-step-1.5）
    CHENGUAI_MUSIC_REPO_DIR               ACE-Step-1.5 代码仓库根（默认 <项目根>/vendor/ACE-Step-1.5）
    CHENGUAI_MUSIC_LM_MODEL               LM 子模型目录名（默认 acestep-5Hz-lm-1.7B）
    CHENGUAI_MUSIC_DIT_CONFIG             DiT 档位（默认 acestep-v15-turbo）
    CHENGUAI_MUSIC_MAX_TASKS_PER_WORKER   每进程最多处理的任务数，达到后空闲时
                                          自行退出、由 IPC 层透明重启（默认 4；
                                          上游 Windows 已知问题 #1338：连续 8-9
                                          次生成后原生崩溃，主动轮换进程规避）
    CHENGUAI_OUTPUT_DIR                   生成结果输出目录

能力边界（2026-11-09 起按 6GB 显存档位运行）：
    - 支持带人声完整乐曲（人声+器乐）与纯器乐；BPM 30-300、时长 10-480 秒可显式指定。
    - DiT 与 LM 的权重全部留在物理内存、按层换入显存（ACESTEP_OFFLOAD_TO_CPU /
      ACESTEP_OFFLOAD_DIT_TO_CPU 强制开启），与视频引擎同一套「逐层换入」策略；
      整机显存门槛 6GB，生成前还要求空闲显存不少于 6GB，不够就诚实拒绝、不硬扛 OOM。
      注意：目标峰值 <6GB 是按 offload 策略推出的**预期值，尚未真机实测**；
      每次生成都会把真实 peak_vram_mb 记进任务元数据，验收时用它核对。
    - 不支持超长多轨道复杂编曲；带 LM 的时长上限 8 分钟，
      超出会被引擎内部截短——worker 读回真实时长而不是假设。
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Dict, FrozenSet, Optional

from app.engine.memprobe import floor_mb_or, ram_gate_message, ram_gate_ok, read_physical_ram
from app.tasks.models import TaskRequest, TaskResult

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

MUSIC_MIN_VRAM_MB = 6_144  # 整机显存门槛：6GB 档（DiT/LM 权重 CPU 换入，逐层上卡）
MUSIC_MIN_FREE_RAM_MB = 14_336  # 加载前要求的空闲物理内存：9.40GB 权重 + DiT/LM 运行期缓冲
                               # （显存省下来的代价就是权重常驻内存，这道门槛不能降）


def _music_min_free_ram_mb() -> int:
    """空闲物理内存门槛；排障时可用 CHENGUAI_MUSIC_MIN_FREE_RAM_MB 临时压低。"""
    return floor_mb_or(os.environ.get("CHENGUAI_MUSIC_MIN_FREE_RAM_MB"), MUSIC_MIN_FREE_RAM_MB)
MUSIC_MAX_DURATION_SEC = 480.0  # 带 LM 的 12GB 档位官方上限（8 分钟）
MUSIC_MIN_DURATION_SEC = 10.0

_ALLOWED_PARAMS = {
    "bpm",
    "duration",
    "instrumental",
    "vocal_language",
    "keyscale",
    "timesignature",
    "lyrics",
    "seed",
    "steps",
}


def _music_env() -> Dict[str, str]:
    """读取 CHENGUAI_MUSIC_* 配置（默认全部为项目相对路径，便于便携打包）。"""
    root = str(_PROJECT_ROOT)
    return {
        "engine_id": os.environ.get("CHENGUAI_MUSIC_ENGINE_ID", "ace-step-15"),
        "checkpoints_dir": os.environ.get("CHENGUAI_MUSIC_CHECKPOINTS_DIR")
        or os.path.join(root, "models", "ace-step-1.5"),
        "repo_dir": os.environ.get("CHENGUAI_MUSIC_REPO_DIR")
        or os.path.join(root, "vendor", "ACE-Step-1.5"),
        "lm_model": os.environ.get("CHENGUAI_MUSIC_LM_MODEL", "acestep-5Hz-lm-1.7B"),
        "dit_config": os.environ.get("CHENGUAI_MUSIC_DIT_CONFIG", "acestep-v15-turbo"),
        "max_tasks": int(os.environ.get("CHENGUAI_MUSIC_MAX_TASKS_PER_WORKER", "4")),
    }


def _apply_acestep_defaults(env: Dict[str, str]) -> None:
    """在 import acestep 之前设置其运行环境（离线、pt 后端、强制 offload）。

    依据上游文档与 issue #1337/#1360 的 Windows 稳定配法：
    - LM backend 用 pt（vllm 不支持 Windows）；
    - compile_model=False / 不装 triton（官方 Windows 本就不装 triton）；
    - offload_to_cpu + offload_dit_to_cpu 必须开，否则 12GB 下 VAE 解码掉到
      CPU 会慢到不可用（#1337 实测 20 秒音频解码 26 分钟）。
    """
    os.environ.setdefault("ACESTEP_CHECKPOINTS_DIR", env["checkpoints_dir"])
    os.environ.setdefault("ACESTEP_LM_BACKEND", "pt")
    os.environ.setdefault("ACESTEP_LM_MODEL_PATH", env["lm_model"])
    os.environ.setdefault("ACESTEP_CONFIG_PATH", env["dit_config"])
    os.environ.setdefault("ACESTEP_COMPILE_MODEL", "False")
    os.environ.setdefault("ACESTEP_OFFLOAD_TO_CPU", "true")
    os.environ.setdefault("ACESTEP_OFFLOAD_DIT_TO_CPU", "true")
    os.environ.setdefault("ACESTEP_DOWNLOAD_SOURCE", "none")


def _expected_weight_files(env: Dict[str, str]) -> list[Path]:
    ck = Path(env["checkpoints_dir"])
    return [
        ck / env["dit_config"] / "model.safetensors",
        ck / "vae" / "diffusion_pytorch_model.safetensors",
        ck / "Qwen3-Embedding-0.6B" / "model.safetensors",
        ck / env["lm_model"] / "model.safetensors",
    ]


class MusicTaskAdapter:
    """把 ACE-Step 1.5 包装成任务系统的 EngineAdapter 协议实现。"""

    def __init__(self, engine_id: Optional[str] = None):
        env = _music_env()
        self.engine_id = engine_id or env["engine_id"]
        self.task_types: FrozenSet[str] = frozenset({"music"})
        self.test_only = False
        self._env = env
        self._output_dir = os.environ.get("CHENGUAI_OUTPUT_DIR", "./output")
        self._handlers: Optional[tuple[Any, Any]] = None
        self._gen_count = 0
        # 由 app.tasks.process_worker 的 IPC 宿主读取：True 表示「本次响应写完后
        # 进程应优雅退出」，用于达到轮换阈值时的自我更换。
        self.should_recycle = False

    # ------------------------------------------------------------------ 生命周期

    def _load_handlers_sync(self) -> tuple[Any, Any]:
        """阻塞式加载 DiT + LM（首次约 1-2 分钟，10GB 权重从本地读盘）。"""
        _apply_acestep_defaults(self._env)
        from acestep.handler import AceStepHandler
        from acestep.llm_inference import LLMHandler

        dit = AceStepHandler()
        ok, message = dit.initialize_service(
            project_root=self._env["repo_dir"],
            config_path=self._env["dit_config"],
            device="cuda",
            use_flash_attention=False,
            compile_model=False,
            offload_to_cpu=True,
            offload_dit_to_cpu=True,
            quantization=None,
        )
        if not ok:
            raise RuntimeError(f"ACE-Step DiT 初始化失败：{message}")
        llm = LLMHandler(persistent_storage_path=None)
        ok, message = llm.initialize(
            checkpoint_dir=self._env["checkpoints_dir"],
            lm_model_path=self._env["lm_model"],
            backend="pt",
            device="cuda",
            offload_to_cpu=True,
            dtype=None,
        )
        if not ok:
            raise RuntimeError(f"ACE-Step LM 初始化失败：{message}")
        return dit, llm

    async def _get_handlers(self) -> tuple[Any, Any]:
        if self._handlers is None:
            self._handlers = await asyncio.to_thread(self._load_handlers_sync)
        return self._handlers

    async def health_check(self) -> tuple[bool, str]:
        try:
            import torch  # noqa: F401 - 探测依赖（.venv-music 内应有 torch cu130）
        except Exception as exc:  # noqa: BLE001
            return False, f".venv-music 环境缺少 torch：{exc}"
        if not torch.cuda.is_available():
            return False, "worker 环境没有可用的 CUDA 设备（AI音乐需要 NVIDIA GPU，最低 6GB 显存档位）"
        props = torch.cuda.get_device_properties(0)
        total_mb = int(props.total_memory / (1024 * 1024))
        if total_mb < MUSIC_MIN_VRAM_MB:
            return False, (
                f"显卡 {props.name} 显存 {total_mb} MB，低于 ACE-Step 1.5 的 6GB 档门槛 "
                f"{MUSIC_MIN_VRAM_MB} MB，AI音乐不可用"
            )
        free_mb, _total = torch.cuda.mem_get_info()
        if int(free_mb / (1024 * 1024)) < MUSIC_MIN_VRAM_MB:
            return False, (
                f"当前空闲显存仅 {int(free_mb / (1024*1024))} MB，不足 6GB 档运行预算；"
                "请先释放显存（如关闭本机 LLM 引擎或第三方占用程序）再提交音乐任务"
            )
        # 权重（DiT + 1.7B LM，合计 9.40GB）要先读进物理内存；内存不够时进程会被系统
        # 直接杀掉、十几分钟之后连错误都拿不到，所以在 health_check 里就拦住。
        ram = read_physical_ram()
        if not ram_gate_ok(ram, _music_min_free_ram_mb()):
            return False, ram_gate_message(
                "ACE-Step 1.5 音乐引擎",
                _music_min_free_ram_mb(),
                ram,
                "9.40GB 权重（DiT + acestep-5Hz-lm-1.7B）要先读进物理内存，再加运行期缓冲",
            )
        missing = [str(p) for p in _expected_weight_files(self._env) if not p.is_file()]
        if missing:
            return False, (
                "ACE-Step 1.5 权重不完整，缺少：" + "；".join(missing)
                + "。可运行 scripts\\download_music_weights.py 下载"
            )
        repo_ok = (Path(self._env["repo_dir"]) / "acestep").is_dir()
        if not repo_ok:
            return False, f"未找到 ACE-Step-1.5 代码仓库：{self._env['repo_dir']}"
        return True, (
            f"ACE-Step 1.5（{self._env['dit_config']} + {self._env['lm_model']}，本地离线）："
            f"{props.name} {total_mb} MB"
        )

    # ------------------------------------------------------------------ 校验

    def validate_request(self, request: TaskRequest) -> None:
        if not request.prompt or not request.prompt.strip():
            raise ValueError("音乐风格描述（caption）不能为空")
        if len(request.prompt) > 500:
            raise ValueError("风格描述过长（上限 500 字符），请精简或拆段")
        # 孤立代理码位（U+D800–U+DFFF）是进程间传输编码错乱留下的坏字符：它会一路
        # 穿到 ACE-Step 的 tokenizer，在十几分钟生成之后才炸，报错还是
        # 「TextEncodeInput must be …」这种看不出根因的信息。在校验阶段就拒绝。
        from app.engine.prompt_utils import contains_lone_surrogate

        candidates: list[tuple[str, object]] = [("风格描述", request.prompt)]
        candidates += [
            (f"参数 {key}", value)
            for key, value in (request.parameters or {}).items()
            if isinstance(value, str)
        ]
        bad_fields = [name for name, value in candidates if contains_lone_surrogate(value)]
        if bad_fields:
            raise ValueError(
                f"{'、'.join(bad_fields)} 含有无法编码的坏字符（通常是进程间传输编码错乱造成）。"
                "请清空后重新输入中文描述再提交；由脚本提交时请确认按 UTF-8 传参。"
            )
        params = request.parameters or {}
        unknown = set(params) - _ALLOWED_PARAMS
        if unknown:
            raise ValueError(f"不支持的参数：{', '.join(sorted(unknown))}")
        for key in ("bpm", "seed", "steps"):
            if key in params and not isinstance(params[key], int):
                raise ValueError(f"参数 {key} 必须是整数")
        if "duration" in params and not isinstance(params["duration"], (int, float)):
            raise ValueError("参数 duration 必须是数字")
        bpm = params.get("bpm")
        if bpm is not None and not (30 <= int(bpm) <= 300):
            raise ValueError("参数 bpm 需在 30-300 之间")
        duration = params.get("duration")
        if duration is not None and not (
            MUSIC_MIN_DURATION_SEC <= float(duration) <= 600.0
        ):
            raise ValueError("参数 duration 需在 10-600 之间（超过 480 秒会被截到 8 分钟上限）")
        lyrics = params.get("lyrics")
        if lyrics is not None and (not isinstance(lyrics, str) or len(lyrics) > 4096):
            raise ValueError("参数 lyrics 需为不超过 4096 字符的字符串")

    # ------------------------------------------------------------------ 生成

    async def generate(self, request: TaskRequest) -> TaskResult:
        from acestep.inference import GenerationConfig, GenerationParams, generate_music

        dit, llm = await self._get_handlers()
        params: Dict[str, Any] = dict(request.parameters or {})

        requested_duration = float(params.get("duration") or 180.0)
        duration = min(max(requested_duration, MUSIC_MIN_DURATION_SEC), MUSIC_MAX_DURATION_SEC)
        instrumental = bool(params.get("instrumental", False))
        lyrics = params.get("lyrics") or ""
        if instrumental and not lyrics.strip():
            lyrics = "[Instrumental]"

        gen_params = GenerationParams(
            task_type="text2music",
            caption=request.prompt.strip(),
            lyrics=lyrics,
            instrumental=instrumental,
            bpm=int(params["bpm"]) if params.get("bpm") is not None else None,
            duration=duration,
            keyscale=str(params.get("keyscale") or ""),
            timesignature=str(params.get("timesignature") or ""),
            vocal_language=str(params.get("vocal_language") or "zh"),
            inference_steps=int(params.get("steps") or 8),  # turbo 档位固定 8 步最优
            seed=int(params["seed"]) if params.get("seed") is not None else None,
            enable_normalization=True,
        )
        gen_config = GenerationConfig(
            batch_size=1,
            audio_format="wav",  # 用户要求：标准 wav 产物
            thinking=True,
            duration_jitter_sec=0.0,
            use_random_seed=gen_params.seed is None,
        )

        result = await asyncio.to_thread(
            generate_music, dit, llm, gen_params, gen_config,
            save_dir=self._output_dir, progress=None,
        )
        if not getattr(result, "success", False) or not getattr(result, "audios", None):
            raise RuntimeError(f"ACE-Step 生成失败：{getattr(result, 'error', '未知错误')}")

        audio_path = str(result.audios[0]["path"])

        # 真实显存峰值入账：6GB 档是按 offload 策略推出的目标峰值（未实测），
        # 每次生成都把实测值写进元数据，验收/排障直接看这个数，不用猜。
        peak_vram_mb: Optional[int] = None
        try:
            import torch as _torch

            if _torch.cuda.is_available():
                peak_vram_mb = round(_torch.cuda.max_memory_allocated() / (1024 * 1024))
                _torch.cuda.reset_peak_memory_stats()
        except Exception:  # noqa: BLE001 - 记账失败不影响任务成功
            pass

        actual_duration: Optional[float] = None
        try:
            import soundfile as sf

            info = sf.info(audio_path)
            actual_duration = round(float(info.frames) / float(info.samplerate or 48000), 2)
        except Exception:  # noqa: BLE001 - 读回真实时长失败不影响任务成功
            pass

        self._gen_count += 1
        max_tasks = self._env["max_tasks"]
        if self._gen_count >= max_tasks:
            # 达到轮换阈值：不自杀，只挂一个「本轮响应写完后退出」的标记。
            # app.tasks.process_worker 的 IPC 宿主在把本次响应刷到 stdout 之后
            # 检查 should_recycle 并正常退出进程；下一个任务到来时
            # ProcessEngineAdapter 会检测到进程退出并透明重启，重新加载权重
            # （约 1-2 分钟），以此规避上游 #1338 的连续生成崩溃。
            #
            # 这里绝不能用 threading.Timer(2.0, os._exit)：计时器在「响应写出后、
            # 下一请求已到达」的窗口里同样会触发，而那个时刻 worker 正在生成下一
            # 首曲子——max_tasks=4 时第 5 首会在 2 秒后连同权重加载一起被杀掉，
            # 表现为「第 5 次生成必失败」。
            self.should_recycle = True

        return TaskResult(
            output_files=(audio_path,),
            metadata={
                "status": "success",
                "model_id": f"ace-step-1.5/{self._env['dit_config']}",
                "seed": gen_params.seed,
                "bpm": gen_params.bpm,
                "requested_duration_sec": requested_duration,
                "duration_sec": actual_duration if actual_duration is not None else duration,
                "instrumental": instrumental,
                "sample_rate": 48000,
                "peak_vram_mb": peak_vram_mb,
                "worker_generations_served": self._gen_count,
            },
        )

    async def shutdown(self) -> None:
        if self._handlers is not None:
            try:
                import torch

                torch.cuda.empty_cache()
            except Exception:  # noqa: BLE001
                pass
            self._handlers = None


def create_adapter() -> MusicTaskAdapter:
    """process_worker --adapter-factory 的入口工厂。"""
    return MusicTaskAdapter()
