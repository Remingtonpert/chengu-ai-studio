"""共享的生成引擎注册逻辑（不依赖 Qt，桌面端与 Web 端共用）。

真实引擎（FLUX 图像、Anima 视频）都运行在 .venv-image 的独立 worker 子进程里，
主进程（桌面或 Web 服务器）只通过 ProcessEngineAdapter 的 JSON-lines IPC 通信，
因此主进程虚拟环境（.venv）不需要 torch/diffusers。
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
VIDEO_ENGINE_ID = "anima-image2video"


def default_image_venv_python() -> Path:
    """默认的图片/视频引擎虚拟环境解释器（项目根目录下的 .venv-image）。"""
    project_root = Path(__file__).resolve().parents[1]
    return project_root / ".venv-image" / "Scripts" / "python.exe"


def _make_worker_factory(
    python_exe: Path,
    project_root: Path,
    config: dict[str, object],
    engine_id: str,
    factory_spec: str,
    task_types: frozenset[str],
) -> callable:
    def factory() -> ProcessEngineAdapter:
        # ProcessEngineAdapter 本身不注入环境变量；这里把配置映射为 worker
        # 约定读取的 CHENGUAI_* 变量，子进程会继承主进程的环境。
        os.environ["CHENGUAI_MODEL_CACHE_DIR"] = str(config.get("models_dir", ""))
        os.environ["CHENGUAI_OUTPUT_DIR"] = str(config.get("output_dir", ""))
        os.environ["CHENGUAI_FLUX_ENGINE_ID"] = IMAGE_ENGINE_ID
        os.environ["CHENGUAI_VIDEO_ENGINE_ID"] = VIDEO_ENGINE_ID
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
    """按配置注册真实引擎（FLUX 图像 worker + Anima 视频 worker）。

    仅当 engines_enabled=true 时注册；默认关闭，避免在用户确认模型下载/许可之前
    触发权重下载。主进程保持轻量——torch/diffusers 只存在于 .venv-image。
    """
    if not config.get("engines_enabled"):
        logger.info("真实生成引擎未启用（配置 engines_enabled=false），生成任务将保持排队校验后失败")
        return

    venv_value = str(config.get("image_venv_python") or "").strip()
    python_exe = Path(venv_value) if venv_value else default_image_venv_python()
    if not python_exe.is_file():
        logger.error(
            "图片/视频引擎虚拟环境解释器不存在：%s；跳过真实引擎注册。"
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
    engine_manager.register_factory(
        VIDEO_ENGINE_ID,
        _make_worker_factory(
            python_exe,
            project_root,
            config,
            VIDEO_ENGINE_ID,
            "app.tasks.video_worker:create_adapter",
            frozenset({"video"}),
        ),
    )
    logger.info(
        "已注册 FLUX 图像引擎与 Anima 视频引擎（worker：%s）",
        python_exe,
    )
