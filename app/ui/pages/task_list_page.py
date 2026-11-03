"""SQLite-backed task queue and history views."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget

from app.tasks.models import TaskStatus
from app.tasks.service import TaskService


_STATUS_LABELS = {
    TaskStatus.QUEUED: "排队中",
    TaskStatus.PREPARING: "准备中",
    TaskStatus.RUNNING: "运行中",
    TaskStatus.COMPLETED: "已完成",
    TaskStatus.FAILED: "失败",
    TaskStatus.CANCELLED: "已取消",
}


class TaskListPage(QWidget):
    def __init__(self, task_service: TaskService | None, active: bool) -> None:
        super().__init__()
        self.task_service = task_service
        self.active = active
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 32)
        layout.setSpacing(14)

        heading = QLabel("任务队列" if active else "生成历史")
        heading.setObjectName("pageHeading")
        layout.addWidget(heading)

        self.summary = QLabel()
        layout.addWidget(self.summary)
        self.items = QListWidget()
        self.items.setAccessibleName("任务记录")
        layout.addWidget(self.items, 1)
        refresh = QPushButton("刷新")
        refresh.clicked.connect(self.refresh)
        layout.addWidget(refresh, alignment=Qt.AlignmentFlag.AlignRight)
        self.refresh()

    def refresh(self) -> None:
        self.items.clear()
        if self.task_service is None:
            self.summary.setText("任务服务尚未初始化")
            return

        records = self.task_service.list_tasks(limit=1000)
        if self.active:
            records = tuple(
                record
                for record in records
                if record.status
                in {TaskStatus.QUEUED, TaskStatus.PREPARING, TaskStatus.RUNNING}
            )
        else:
            records = tuple(
                record
                for record in records
                if record.status
                in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}
            )

        self.summary.setText(f"{len(records)} 条记录")
        if not records:
            self.items.addItem("暂无任务记录")
            return

        for record in records:
            label = _STATUS_LABELS[record.status]
            text = f"{label}  ·  {record.request.task_type}  ·  {record.request.prompt[:80]}"
            item = QListWidgetItem(text)
            details = [
                f"任务 ID：{record.task_id}",
                f"引擎：{record.request.engine_id}",
                f"创建时间：{record.created_at}",
            ]
            if record.retry_of:
                details.append(f"重试自：{record.retry_of}")
            if record.error_message:
                details.append(f"错误：{record.error_message}")
            if record.output_files:
                details.extend(record.output_files)
            if record.result_metadata.get("test_only"):
                details.append("测试适配器记录；未生成媒体文件")
            item.setToolTip("\n".join(details))
            self.items.addItem(item)