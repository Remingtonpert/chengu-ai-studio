"""Read-only engine lifecycle status view."""

from PySide6.QtWidgets import QLabel, QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from app.tasks.engine_manager import EngineManager


class EngineStatusPage(QWidget):
    def __init__(self, engine_manager: EngineManager | None) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 32)
        layout.setSpacing(14)
        heading = QLabel("引擎管理")
        heading.setObjectName("pageHeading")
        layout.addWidget(heading)
        statuses = engine_manager.list_statuses() if engine_manager else ()
        if statuses:
            intro = "引擎生命周期接口已就绪；下列引擎已注册（真机验收进行中）。"
        else:
            intro = (
                "引擎生命周期接口已就绪；尚无已注册引擎，"
                "请检查 engines_enabled 配置与 .venv-image 环境。"
            )
        layout.addWidget(QLabel(intro))
        self.items = QListWidget()
        self.items.setAccessibleName("引擎状态")
        layout.addWidget(self.items)

        if not statuses:
            self.items.addItem("尚无已注册引擎")
        for status in statuses:
            item = QListWidgetItem(f"{status.engine_id}  ·  {status.state}")
            item.setToolTip(status.message)
            self.items.addItem(item)