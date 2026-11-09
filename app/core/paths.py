"""Application data paths, separate from the source workspace."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppPaths:
    app_home: Path
    data_root: Path

    @classmethod
    def from_environment(
        cls,
        app_home: Path | None = None,
        data_root: Path | None = None,
    ) -> AppPaths:
        local_app_data = Path(
            os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")
        )
        # 便携打包：启动脚本注入 CHENGUAI_APP_HOME / CHENGUAI_DATA_ROOT，
        # 把配置、会话数据库、日志、权重缓存与输出全部放进目录包内的相对目录，
        # 整包复制到其他电脑后数据自动跟随新位置（不写死绝对路径）。
        env_app_home = os.environ.get("CHENGUAI_APP_HOME", "").strip()
        if app_home is not None:
            home = app_home
        elif env_app_home:
            home = Path(env_app_home)
        else:
            home = local_app_data / "ChenguAIStudio"
        env_data_root = os.environ.get("CHENGUAI_DATA_ROOT", "").strip()
        if data_root is not None:
            root = data_root
        elif env_data_root:
            root = Path(env_data_root)
        else:
            # 默认数据根不再写死盘符路径：默认落在项目根目录下的 data\，
            # 保证代码整目录复制后所有路径自动相对化。
            root = Path(__file__).resolve().parents[2] / "data"
        return cls(app_home=home, data_root=root)

    @property
    def config_file(self) -> Path:
        return self.app_home / "config.json"

    @property
    def database_file(self) -> Path:
        return self.app_home / "studio.sqlite3"

    @property
    def log_file(self) -> Path:
        return self.app_home / "logs" / "studio.log"

    @property
    def models_dir(self) -> Path:
        # 权重目录默认落在项目根（便携包则是包根）下的 models\，与
        # app/engine_registration.py 里图像 / 视频 / 音乐引擎的默认权重路径
        # 同源。此前默认是 <data_root>\Models，而权重实际一直放在项目根 models\：
        # 没有 config.json 的全新环境第一次点「文生图」就会报权重目录不存在，
        # 必须手工往 config.json 里补 models_dir 才能跑起来。
        return Path(__file__).resolve().parents[2] / "models"

    @property
    def output_dir(self) -> Path:
        return self.data_root / "Output"

    @property
    def project_output_dir(self) -> Path:
        r"""「工具内 output\」：项目根（便携包则是代码包根）下的 output 目录。

        用户要求产物除了工具里即时显示，还要固定存一份到项目内 output\，不管
        output_dir 被改到哪块盘。与 models_dir 同源，用代码所在位置定位，整目录
        复制到别的电脑后自动跟随新位置（不写死绝对路径）。
        """
        return Path(__file__).resolve().parents[2] / "output"

    def prepare_app_home(self) -> None:
        self.app_home.mkdir(parents=True, exist_ok=True)
        self.log_file.parent.mkdir(parents=True, exist_ok=True)