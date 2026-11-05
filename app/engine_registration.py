"""共享的生成引擎注册逻辑（不依赖 Qt，桌面端与 Web 端共用）。

真实引擎（FLUX 图像）运行在 .venv-image 的独立 worker 子进程里，
视频引擎（Wan2.1 wan-t2v / wan-i2v）运行在 .venv-video 的独立 worker 子进程里，
音乐引擎（ACE-Step 1.5）运行在 .venv-music 的独立 worker 子进程里；
主进程（桌面或 Web 服务器）只通过 ProcessEngineAdapter 的 JSON-lines IPC 通信，
因此主进程虚拟环境（.venv）不需要 torch/diffusers/acestep。
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
GENERATION_REQUEST_TIMEOUT_SECONDS = 1800.0

IMAGE_ENGINE_ID = "flux-schnell"
VIDEO_T2V_ENGINE_ID = "wan-t2v"
VIDEO_I2V_ENGINE_ID = "wan-i2v"
MUSIC_ENGINE_ID = "ace-step-15"


def default_image_venv_python() -> Path:
    """默认的图片引擎虚拟环境解释器（项目根目录下的 .venv-image）。"""
    project_root = Path(__file__).resolve().parents[1]
    return project_root / ".venv-image" / "Scripts" / "python.exe"


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

    CHENGUAI_DISABLE_MODALITIES 形如 "video,music"：被禁模态的引擎直接不注册，
    前端与接口层自然呈现「未注册引擎」状态，不会提交任务——分级只影响注册
    这一步，不改动任何图片/视频/音乐模块的生成逻辑。开发环境不注入该变量，
    行为与之前完全一致。
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
) -> callable:
    def factory() -> ProcessEngineAdapter:
        # ProcessEngineAdapter 本身不注入环境变量；这里把配置映射为 worker
        # 约定读取的 CHENGUAI_* 变量，子进程会继承主进程的环境。
        os.environ["CHENGUAI_MODEL_CACHE_DIR"] = str(config.get("models_dir", ""))
        os.environ["CHENGUAI_OUTPUT_DIR"] = str(config.get("output_dir", ""))
        os.environ["CHENGUAI_FLUX_ENGINE_ID"] = IMAGE_ENGINE_ID
        os.environ["CHENGUAI_MUSIC_ENGINE_ID"] = MUSIC_ENGINE_ID
        if extra_env:
            os.environ.update(extra_env)
        # 强制 worker 离线加载本地权重：FLUX.1-schnell 是 gated 仓库，
        # huggingface_hub 在 from_pretrained 前会先联网查 revision，无网络/
        # 无 token 时表现为 [WinError 10060] 超时或 401，即使本地缓存完整。
        # 这些变量必须在主进程（启动 worker 子进程之前）设置——huggingface_hub
        # 在 import 时读取一次。需要在线下载权重时，由启动端显式设
        # HF_HUB_OFFLINE=0 覆盖（setdefault 保留启动端的显式值）。
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("HF_DATASETS_OFFLINE", "1")
        os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
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
        )

    return factory


def register_production_engines(
    engine_manager: EngineManager,
    config: dict[str, object],
    paths: AppPaths,
    logger: logging.Logger,
) -> None:
    """按配置注册真实引擎（FLUX 图像 worker + Wan2.1 视频 workers + ACE-Step 音乐 worker）。

    仅当 engines_enabled=true 时注册；默认关闭，避免在用户确认模型下载/许可之前
    触发权重下载。主进程保持轻量——torch/diffusers 只存在于 .venv-image / .venv-video。
    """
    if not config.get("engines_enabled"):
        logger.info("真实生成引擎未启用（配置 engines_enabled=false），生成任务将保持排队校验后失败")
        return

    disabled = _disabled_modalities()
    if "image" in disabled:
        logger.info(
            "启动硬件检测评级为低配（CHENGUAI_DISABLE_MODALITIES 含 image）：全部生成引擎不注册，仅保留浏览与历史记录"
        )
        return

    venv_value = str(config.get("image_venv_python") or "").strip()
    python_exe = Path(venv_value) if venv_value else default_image_venv_python()
    if venv_value and not python_exe.is_file():
        # 历史上曾有机器把这里配成系统 Python 或路径写废，导致 worker 用错环境；
        # 配置值不可用时不硬失败，回退到项目默认 .venv-image 并记录告警。
        logger.warning(
            "配置的 image_venv_python 不存在：%s；回退到项目默认环境 %s",
            python_exe,
            default_image_venv_python(),
        )
        python_exe = default_image_venv_python()
    if not python_exe.is_file():
        logger.error(
            "图片引擎虚拟环境解释器不存在：%s；跳过真实引擎注册。"
            "请先创建 .venv-image 或在配置中设置 image_venv_python",
            python_exe,
        )
        return

    project_root = Path(__file__).resolve().parents[1]

    engine_manager.register_factory(
        IMAGE_ENGINE_ID,
        _make_worker_factory(
            python_exe,
            project_root,
            config,
            IMAGE_ENGINE_ID,
            "app.tasks.flux_worker:create_adapter",
            frozenset({"image"}),
        ),
    )
    # ---- Wan2.1 视频引擎（wan-t2v 文生视频 / wan-i2v 图生视频，均 Apache-2.0）----
    # 独立 .venv-video 环境（torch cu130 + diffusers 0.41，与图片环境同版本但物理隔离）。
    # wan-i2v 采用官方 Wan2.1-VACE-1.3B：Wan 官方没有 1.3B 的 I2V 模型（I2V 仅 14B 档，
    # 12GB 显存跑不动），VACE 是 1.3B 档唯一带图像条件的 Apache-2.0 官方形态。
    # 权重缺失时照常注册：worker 的 health_check 会诚实报告「权重缺失」并给出下载命令，
    # 不会下载、不会伪造输出。CHENGUAI_DISABLE_MODALITIES 含 video 时整段跳过。
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
        logger.info(
            "已注册 FLUX 图像引擎；视频模块已被启动硬件检测自动禁用（显存/内存不足）"
        )
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
                ),
            )
        logger.info(
            "已注册 FLUX 图像引擎 + Wan2.1 视频引擎（wan-t2v / wan-i2v，worker：%s，权重：%s）",
            python_exe,
            video_models,
        )
    else:
        logger.info(
            "已注册 FLUX 图像引擎；Wan2.1 视频引擎未注册（缺少 %s）。"
            "请先运行 scripts\\setup_video_venv.ps1 创建 .venv-video",
            video_python,
        )

    # ---- AI 音乐引擎（ACE-Step 1.5，官方 MIT 权重，本地离线推理）----
    # 使用独立的 .venv-music：ACE-Step 1.5 要求 transformers<4.58，与图片/视频
    # 引擎环境（transformers 5.x）存在硬冲突，必须环境隔离；图片/视频模块代码
    # 不受任何影响。路径全部为可配置项（默认相对项目根目录），便于打包迁移；
    # 环境或代码仓库缺失时不注册，前端音乐入口会诚实提示，不会伪造输出。
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
        logger.info("音乐模块已被启动硬件检测自动禁用（显存/内存不足）")
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
            ),
        )
        logger.info(
            "ACE-Step 1.5 音乐引擎已注册（worker：%s，权重：%s）",
            music_python,
            music_models,
        )
    else:
        logger.info(
            "ACE-Step 1.5 音乐引擎未注册（缺少 %s 或 %s）；音乐任务将在校验阶段诚实报错",
            music_python,
            music_repo,
        )
