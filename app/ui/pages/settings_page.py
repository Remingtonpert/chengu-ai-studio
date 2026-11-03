"""Basic local path settings."""

from collections.abc import Callable

from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.core.config import ConfigError, ConfigStore


class SettingsPage(QWidget):
    def __init__(
        self,
        config_store: ConfigStore,
        notify: Callable[[str, bool], None],
    ) -> None:
        super().__init__()
        self.config_store = config_store
        self.notify = notify
        config = config_store.load()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 32)
        heading = QLabel("系统设置")
        heading.setObjectName("pageHeading")
        layout.addWidget(heading)
        layout.addWidget(QLabel("配置模型目录与生成文件目录。模型不会自动下载。"))

        form = QFormLayout()
        self.models_path = self._path_row(config["models_dir"])
        self.output_path = self._path_row(config["output_dir"])
        form.addRow("模型根目录", self.models_path[0])
        form.addRow("输出目录", self.output_path[0])
        layout.addLayout(form)

        save_button = QPushButton("保存设置")
        save_button.clicked.connect(self.save_settings)
        layout.addWidget(save_button)
        layout.addStretch(1)

    def _path_row(self, value: str) -> tuple[QWidget, QLineEdit]:
        field = QLineEdit(value)
        browse = QPushButton("浏览…")
        browse.clicked.connect(lambda: self._browse(field))
        container = QWidget()
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(field)
        row.addWidget(browse)
        return container, field

    def save_settings(self) -> None:
        try:
            self.config_store.save(
                {
                    "models_dir": self.models_path[1].text(),
                    "output_dir": self.output_path[1].text(),
                }
            )
        except ConfigError as exc:
            self.notify(str(exc), True)
            return
        self.notify("设置已保存", False)

    @staticmethod
    def _browse(field: QLineEdit) -> None:
        selected = QFileDialog.getExistingDirectory(
            field, "选择目录", field.text()
        )
        if selected:
            field.setText(selected)