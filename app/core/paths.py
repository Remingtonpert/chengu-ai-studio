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
        home = app_home or local_app_data / "ChenguAIStudio"
        configured_data_root = data_root or Path(
            os.environ.get("CHENGUAI_DATA_ROOT", r"D:\ChenguAI")
        )
        return cls(app_home=home, data_root=configured_data_root)

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