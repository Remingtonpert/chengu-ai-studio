"""Pages shown before a real generation engine has passed validation."""

from PySide6.QtWidgets import (
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class WorkspacePage(QWidget):
    def __init__(
        self,
        title: str,
        description: str,
        can_generate: bool = False,
        engine_ready: bool = False,
        engine_label: str = "",
    ):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 32)
        layout.setSpacing(16)

        heading = QLabel(title)
        heading.setObjectName("pageHeading")
        layout.addWidget(heading)
        layout.addWidget(QLabel(description))

        if can_generate:
            self.prompt_input = QPlainTextEdit()
            self.prompt_input.setPlaceholderText("输入创作描述")
            self.prompt_input.setMaximumHeight(180)
            layout.addWidget(self.prompt_input)

            if engine_ready:
                status = QLabel(
                    f"引擎已接入：{engine_label} · 真机验收完成后启用生成按钮"
                )
                status.setObjectName("statusInfo")
            else:
                status = QLabel("真实引擎尚未通过安装与离线推理验收")
                status.setObjectName("statusWarning")
            layout.addWidget(status)

            self.generate_button = QPushButton("生成")
            self.generate_button.setEnabled(False)
            self.generate_button.setToolTip("真机验收完成后开放生成")
            layout.addWidget(self.generate_button)

        layout.addStretch(1)