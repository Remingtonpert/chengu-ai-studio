"""Basic local path settings."""

from collections.abc import Callable

from PySide6.QtCore import Qt
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
from app.core.paths import AppPaths


class SettingsPage(QWidget):
    def __init__(
        self,
        config_store: ConfigStore,
        notify: Callable[[str, bool], None],
        on_saved: Callable[[dict], None] | None = None,
    ) -> None:
        super().__init__()
        self.config_store = config_store
        self.notify = notify
        # 保存成功后把最新配置回传给主窗口（例如让任务服务立刻按新的「自动另存目录」
        # 复制副本），这样改设置不用重启程序。
        self.on_saved = on_saved
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
        # 产物写进「输出目录」（本工具里即时显示、任务队列按它管理的那一份），每次生成完成
        # 还固定复制一份到项目内 output\；下面这项是用户自选的第三处，留空＝不额外复制。
        project_output = AppPaths.from_environment().project_output_dir
        self.project_output_label = QLabel(str(project_output))
        self.project_output_label.setObjectName("metaDim")
        self.project_output_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.auto_save_path = self._path_row(config.get("auto_save_dir", ""))
        form.addRow("模型根目录", self.models_path[0])
        form.addRow("输出目录", self.output_path[0])
        form.addRow("工具内 output（固定存一份）", self.project_output_label)
        form.addRow("自动另存目录（可留空）", self.auto_save_path[0])
        layout.addLayout(form)
        layout.addWidget(
            QLabel(
                "生成好的内容在本工具里即时显示，产物写在「输出目录」，并且每次生成完成都会"
                "固定复制一份到上面那个「工具内 output」目录（输出目录本来就指着它时不会重复复制）。"
                "填了「自动另存目录」，则再复制一份到那里（可以是移动硬盘上的绝对路径，重名自动加"
                " -1、-2，不覆盖已有文件）。想当场挑位置：在结果上点「⬇ 另存」。\n"
                "该设置与网页端（http://127.0.0.1:8765 的「产物保存」区）共用同一份 config.json，"
                "在哪一端改都生效。"
            )
        )

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
            # 先读回当前配置，再叠加本页面这三项：ConfigStore.save() 会按 defaults 补全
            # 缺失的键，只传两个目录就会把 llm_* / video_* / music_* / auto_save_dir 等
            # 其它已经在用的设置悄悄重置回默认值。
            merged = {
                **self.config_store.load(),
                "models_dir": self.models_path[1].text(),
                "output_dir": self.output_path[1].text(),
                "auto_save_dir": self.auto_save_path[1].text().strip(),
            }
            self.config_store.save(merged)
        except ConfigError as exc:
            self.notify(str(exc), True)
            return
        if self.on_saved is not None:
            try:
                self.on_saved(merged)
            except Exception as exc:  # noqa: BLE001 - 配置已存好，运行期刷新失败要看得见
                self.notify(f"设置已保存，但运行期刷新失败：{exc}", True)
                return
        auto = merged["auto_save_dir"]
        self.notify(
            "设置已保存"
            + (
                f"；之后的生成会再自动另存一份到 {auto}"
                if auto
                else "；之后的生成只存「输出目录」+「工具内 output」两处"
            ),
            False,
        )

    @staticmethod
    def _browse(field: QLineEdit) -> None:
        selected = QFileDialog.getExistingDirectory(
            field, "选择目录", field.text()
        )
        if selected:
            field.setText(selected)