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
        return self.data_root / "Models"

    @property
    def output_dir(self) -> Path:
        return self.data_root / "Output"

    def prepare_app_home(self) -> None:
        self.app_home.mkdir(parents=True, exist_ok=True)
        self.log_file.parent.mkdir(parents=True, exist_ok=True)