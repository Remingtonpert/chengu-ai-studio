"""共享的生成引擎注册逻辑（不依赖 Qt，桌面端与 Web 端共用）。

真实引擎（视频 wan-t2v / wan-i2v，Wan2.1 1.3B + VACE-1.3B；图片 wan-t2i / wan-i2i，
与视频共用同一套 Wan2.1 权重；音乐 ace-step-15，ACE-Step 1.5）运行在 .venv-video /
.venv-music 的独立 worker 子进程里；主进程（桌面或 Web 服务器）只通过
ProcessEngineAdapter 的 JSON-lines IPC 通信，因此主进程虚拟环境（.venv）不需要 torch/diffusers。

2026-11-09 下架与重新选型记录：文生图 / 图生图（SD1.5 + RealVis V5.1）与 AI 音乐
（ACE-Step 1.5）曾按「峰值显存不超过 6GB、大模型原生支持中英文 Prompt、权重可商用」
三条要求整体下架。同日重新选型后恢复注册：
- 图片改用 Wan2.1 1.3B 单帧生成（wan-t2i 文生图 / wan-i2i 图生图）：Apache-2.0、
  文本编码器 UMT5-XXL 原生支持中英文、与视频引擎共用 models/wan2.1 权重（零新增下载），
  显存策略与视频同一套（逐层换入；视频档真机实测峰值约 2GB，图片单帧 token 只有其零头，
  图片档峰值预期更低但尚未真机实测，验收时补测）。FLUX.1-schnell 被否决：权重 23GB
  体量过大，且 HF 上已是 gated 仓库（403）。
- 音乐保留 ACE-Step 1.5（MIT、原生中英文歌词）：LM 与 DiT 全部 CPU 换入，
  显存门槛从整机 12GB 降到 6GB 档（目标峰值 <6GB 为预期值，未实测，验收时补测；
  空闲显存不足时引擎会诚实拒绝而不是硬扛 OOM）。
已经生成过的历史记录与产物一直可读、可打开。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from app.core.paths import AppPaths
from app.tasks.engine_manager import EngineManager
from app.tasks.process_adapter import ProcessEngineAdapter

# 真实生成一次可能耗时数分钟到十几分钟（模型加载 + RTX 3060 推理），
# 远超 ProcessEngineAdapter 默认的 30s，因此显式放宽。
# 必须**大于** TaskService 的 task_timeout_seconds（默认 3600s）：超时该由任务层
# 判定并给出「任务超过 N 秒超时限制」这种可理解的错误；如果 IPC 层先超时，
# 它会直接掐掉正在推理的 worker，用户只看到「引擎 IPC generate 请求超时」，
# 连跑到哪一步的线索都没有。
GENERATION_REQUEST_TIMEOUT_SECONDS = 4200.0

VIDEO_T2V_ENGINE_ID = "wan-t2v"
VIDEO_I2V_ENGINE_ID = "wan-i2v"
# 图片引擎与视频共用 Wan2.1 1.3B 权重：wan-t2i = 单帧文生图；wan-i2i = VACE 参考图生图
IMAGE_T2I_ENGINE_ID = "wan-t2i"
IMAGE_I2I_ENGINE_ID = "wan-i2i"
MUSIC_ENGINE_ID = "ace-step-15"


def default_video_venv_python() -> Path:
    """默认的视频引擎虚拟环境解释器（项目根目录下的 .venv-video）。"""
    project_root = Path(__file__).resolve().parents[1]
    return project_root / ".venv-video" / "Scripts" / "python.exe"


def default_video_model_dir() -> Path:
    """默认的 Wan2.1 权重目录（项目内 models/wan2.1，HF 缓存布局，两仓库共用根）。"""
    return Path(__file__).resolve().parents[1] / "models" / "wan2.1"


def default_music_venv_python() -> Path:
    """默认的音乐引擎虚拟环境解释器（项目根目录下的 .venv-music）。"""
    project_root = Path(__file__).resolve().parents[1]
    return project_root / ".venv-music" / "Scripts" / "python.exe"


def default_music_repo_dir() -> Path:
    """默认的 ACE-Step-1.5 代码仓库目录（项目内 vendor/ACE-Step-1.5）。"""
    return Path(__file__).resolve().parents[1] / "vendor" / "ACE-Step-1.5"


def default_music_model_dir() -> Path:
    """默认的 ACE-Step 1.5 权重目录（项目内 models/ace-step-1.5）。"""
    return Path(__file__).resolve().parents[1] / "models" / "ace-step-1.5"


def _disabled_modalities() -> frozenset[str]:
    """启动前置硬件检测通过环境变量声明的「被禁用模态」（便携包按显存/内存分级）。

    CHENGUAI_DISABLE_MODALITIES 形如 "video"：被禁模态的引擎直接不注册，
    前端与接口层自然呈现「未注册引擎」状态，不会提交任务——分级只影响注册
    这一步，不改动视频模块的生成逻辑。开发环境不注入该变量，行为与之前完全一致。
    """
    raw = os.environ.get("CHENGUAI_DISABLE_MODALITIES", "").strip().lower()
    return frozenset(p.strip() for p in raw.split(",") if p.strip())


def _make_worker_factory(
    python_exe: Path,
    project_root: Path,
    config: dict[str, object],
    engine_id: str,
    factory_spec: str,
    task_types: frozenset[str],
    extra_env: dict[str, str] | None = None,
    log_dir: Path | None = None,
) -> callable:
    def factory() -> ProcessEngineAdapter:
        # ProcessEngineAdapter 本身不注入环境变量；这里把配置映射为 worker
        # 约定读取的 CHENGUAI_* 变量。
        #
        # 关键是这些变量只能作为「本次 spawn 的一次性环境」传下去，绝不能写进
        # 主进程的全局 os.environ。原先的写法是在 factory 里直接改全局环境：
        # wan-t2v 与 wan-i2v 的 CHENGUAI_VIDEO_ENGINE_ID 互相覆盖、最后一个注册
        # 的 id 留在环境里；worker 崩溃后透明重启时读到的就是别人的 id，
        # 文生视频的进程会悄悄变成图生视频（还会报「缺少首帧图片」）。
        child_env: dict[str, str] = {
            "CHENGUAI_MODEL_CACHE_DIR": str(config.get("models_dir", "")),
            "CHENGUAI_OUTPUT_DIR": str(config.get("output_dir", "")),
        }
        if extra_env:
            child_env.update(extra_env)
        # 强制 worker 离线加载本地权重：视频权重已提前下载到本地，
        # huggingface_hub 在 from_pretrained 前会先联网查 revision，无网络/
        # 无 token 时表现为 [WinError 10060] 超时或 401，即使本地缓存完整。
        # 需要在线下载权重时，由启动端显式设 HF_HUB_OFFLINE=0 覆盖
        # （os.environ 里已有的显式值优先于这里的默认值）。
        for offline_key, offline_value in (
            ("HF_HUB_OFFLINE", "1"),
            ("HF_DATASETS_OFFLINE", "1"),
            ("HF_HUB_DISABLE_TELEMETRY", "1"),
        ):
            child_env.setdefault(offline_key, os.environ.get(offline_key, offline_value))
        return ProcessEngineAdapter(
            engine_id=engine_id,
            command=[
                str(python_exe),
                "-m",
                "app.tasks.process_worker",
                "--adapter-factory",
                factory_spec,
            ],
            task_types=task_types,
            cwd=project_root,
            request_timeout_seconds=GENERATION_REQUEST_TIMEOUT_SECONDS,
            env=child_env,
            # worker 的 stderr 落到日志文件：以前直接进 DEVNULL，CUDA OOM、
            # 权重缺失、tokenizer 编码错误的 traceback 全部看不到，UI 只剩
            # 「引擎子进程意外退出」一句废话。
            stderr_log_path=(
                log_dir / f"engine-{engine_id}.stderr.log"
                if log_dir is not None
                else None
            ),
        )

    return factory


def register_production_engines(
    engine_manager: EngineManager,
    config: dict[str, object],
    paths: AppPaths,
    logger: logging.Logger,
) -> None:
    """按配置注册真实引擎（视频 wan-t2v / wan-i2v、图片 wan-t2i / wan-i2i 共用
    .venv-video worker；音乐 ace-step-15 走 .venv-music worker）。

    仅当 engines_enabled=true 时注册；默认关闭，避免在用户确认模型下载/许可之前
    触发权重下载。主进程保持轻量——torch/diffusers 只存在于 .venv-video。
    """
    if not config.get("engines_enabled"):
        logger.info("真实生成引擎未启用（配置 engines_enabled=false），生成任务将保持排队校验后失败")
        return

    disabled = _disabled_modalities()
    project_root = Path(__file__).resolve().parents[1]
    log_dir = paths.log_file.parent

    # ---- Wan2.1 视频引擎（wan-t2v 文生视频 / wan-i2v 图生视频，均 Apache-2.0）----
    # 独立 .venv-video 环境（torch cu130 + diffusers 0.41）。
    # wan-i2v 采用官方 Wan2.1-VACE-1.3B：Wan 官方没有 1.3B 的 I2V 模型（I2V 仅 14B 档，
    # 12GB 显存跑不动），VACE 是 1.3B 档唯一带图像条件的 Apache-2.0 官方形态。
    # 权重缺失时照常注册：worker 的 health_check 会诚实报告「权重缺失」并给出下载命令，
    # 不会下载、不会伪造输出。CHENGUAI_DISABLE_MODALITIES 含 video 时整段跳过。
    #
    # ---- Wan2.1 图片引擎（wan-t2i 文生图 / wan-i2i 图生图，2026-11-09 重新选型）----
    # 与视频引擎共用同一 .venv-video 与 models/wan2.1 权重，零新增下载：
    # wan-t2i = T2V-1.3B num_frames=1 单帧出图；wan-i2i = VACE-1.3B reference_images
    # 参考重绘。文本编码器 UMT5-XXL 原生支持中英文 Prompt。显存策略与视频同一套
    # （bf16 + 逐层换入 + VAE tiling）：视频档真机实测峰值约 2GB，单帧 token 只有
    # 其零头，图片档峰值预期更低（尚未真机实测，验收时用任务元数据里的
    # peak_vram_mb 核对，不伪造数字）。
    video_python_value = str(config.get("video_venv_python") or "").strip()
    video_python = Path(video_python_value) if video_python_value else default_video_venv_python()
    if video_python_value and not video_python.is_file():
        logger.warning(
            "配置的 video_venv_python 不存在：%s；回退到项目默认环境 %s",
            video_python,
            default_video_venv_python(),
        )
        video_python = default_video_venv_python()
    video_models = Path(str(config.get("video_model_dir") or default_video_model_dir()))
    if "video" in disabled:
        logger.info("视频模块已被启动硬件检测自动禁用（显存/内存不足），不注册 wan-t2v / wan-i2v")
    elif video_python.is_file():
        for video_engine_id in (VIDEO_T2V_ENGINE_ID, VIDEO_I2V_ENGINE_ID):
            engine_manager.register_factory(
                video_engine_id,
                _make_worker_factory(
                    video_python,
                    project_root,
                    config,
                    video_engine_id,
                    "app.tasks.video_worker:create_adapter",
                    frozenset({"video"}),
                    extra_env={
                        "CHENGUAI_VIDEO_ENGINE_ID": video_engine_id,
                        "CHENGUAI_VIDEO_MODEL_CACHE_DIR": str(video_models),
                    },
                    log_dir=log_dir,
                ),
            )
        logger.info(
            "Wan2.1 视频引擎已注册（wan-t2v / wan-i2v，worker：%s，权重：%s）",
            video_python,
            video_models,
        )
    else:
        logger.info(
            "Wan2.1 视频引擎未注册（缺少 %s）。请先运行 scripts\\setup_video_venv.ps1 创建 .venv-video",
            video_python,
        )

    # 图片引擎独立于 video 开关：显存检测禁用的是视频模态（81 帧序列），
    # 单帧图片预算小得多；两者共享 worker 环境但各自可被 CHENGUAI_DISABLE_MODALITIES 禁用。
    if "image" in disabled:
        logger.info("图片模块已被启动硬件检测自动禁用（显存/内存不足），不注册 wan-t2i / wan-i2i")
    elif video_python.is_file():
        for image_engine_id in (IMAGE_T2I_ENGINE_ID, IMAGE_I2I_ENGINE_ID):
            engine_manager.register_factory(
                image_engine_id,
                _make_worker_factory(
                    video_python,
                    project_root,
                    config,
                    image_engine_id,
                    "app.tasks.video_worker:create_adapter",
                    frozenset({"image"}),
                    extra_env={
                        "CHENGUAI_VIDEO_ENGINE_ID": image_engine_id,
                        "CHENGUAI_VIDEO_MODEL_CACHE_DIR": str(video_models),
                    },
                    log_dir=log_dir,
                ),
            )
        logger.info(
            "Wan2.1 图片引擎已注册（wan-t2i / wan-i2i，worker：%s，权重：%s）",
            video_python,
            video_models,
        )
    else:
        logger.info(
            "Wan2.1 图片引擎未注册（缺少 %s）。请先运行 scripts\\setup_video_venv.ps1 创建 .venv-video",
            video_python,
        )

    # ---- ACE-Step 1.5 音乐引擎（ace-step-15，权重 MIT，2026-11-09 恢复注册）----
    # 独立 .venv-music；DiT 与 LM 权重全部 CPU 换入（6GB 显存档位，
    # 目标峰值为预期值未实测——生成任务的元数据里会带实测 peak_vram_mb）。
    # 权重与代码仓库缺失时不注册（health_check 之外没有可诚实运行的路径），
    # 界面上呈现「未注册引擎」。CHENGUAI_DISABLE_MODALITIES 含 music 时整段跳过。
    music_python_value = str(config.get("music_venv_python") or "").strip()
    music_python = Path(music_python_value) if music_python_value else default_music_venv_python()
    if music_python_value and not music_python.is_file():
        logger.warning(
            "配置的 music_venv_python 不存在：%s；回退到项目默认环境 %s",
            music_python,
            default_music_venv_python(),
        )
        music_python = default_music_venv_python()
    music_repo = Path(str(config.get("music_repo_dir") or default_music_repo_dir()))
    music_models = Path(str(config.get("music_model_dir") or default_music_model_dir()))
    if "music" in disabled:
        logger.info("音乐模块已被启动硬件检测自动禁用（显存/内存不足），不注册 ace-step-15")
    elif music_python.is_file() and (music_repo / "acestep").is_dir():
        engine_manager.register_factory(
            MUSIC_ENGINE_ID,
            _make_worker_factory(
                music_python,
                project_root,
                config,
                MUSIC_ENGINE_ID,
                "app.tasks.music_worker:create_adapter",
                frozenset({"music"}),
                extra_env={
                    "CHENGUAI_MUSIC_CHECKPOINTS_DIR": str(music_models),
                    "CHENGUAI_MUSIC_REPO_DIR": str(music_repo),
                    "CHENGUAI_MUSIC_LM_MODEL": str(
                        config.get("music_lm_model") or "acestep-5Hz-lm-1.7B"
                    ),
                    "CHENGUAI_MUSIC_MAX_TASKS_PER_WORKER": str(
                        int(config.get("music_max_tasks_per_worker") or 4)
                    ),
                },
                log_dir=log_dir,
            ),
        )
        logger.info(
            "ACE-Step 1.5 音乐引擎已注册（worker：%s，权重：%s，代码：%s）",
            music_python,
            music_models,
            music_repo,
        )
    else:
        logger.info(
            "ACE-Step 音乐引擎未注册（缺少 %s 或代码仓库 %s）。"
            "请先运行 scripts\\setup_music_venv.ps1 与 scripts\\download_music_weights.py",
            music_python,
            music_repo,
        )
