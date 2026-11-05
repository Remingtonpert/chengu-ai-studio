"""生成工作区页面。

引擎注册（权重与依赖在注册时校验）后，生成按钮通过共享服务层
（MainWindow 注入的 on_generate 回调 → TaskService.submit_task）提交真实任务；
本页面不做任何模拟生成，也不重复实现任何引擎逻辑。
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


IMAGE_PLACEHOLDER = "输入图片创作描述（中文或英文均可），例如：古风，青城山山间云雾，写实风景"
VIDEO_PLACEHOLDER = "输入视频内容描述；可指定首帧图片，留空则由引擎按描述生成首帧"
MUSIC_PLACEHOLDER = "输入歌曲风格与主题描述（中文或英文），例如：国风流行抒情，女声，轻盈治愈"
LYRICS_PLACEHOLDER = "填写歌词（支持中文）；留空则自动按纯器乐（instrumental）生成"


class WorkspacePage(QWidget):
    def __init__(
        self,
        title: str,
        description: str,
        can_generate: bool = False,
        engine_ready: bool = False,
        engine_label: str = "",
        kind: str = "",
        on_generate: Callable[[str, str, dict], str] | None = None,
    ):
        super().__init__()
        self._kind = kind
        self._on_generate = on_generate
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 32)
        layout.setSpacing(16)

        heading = QLabel(title)
        heading.setObjectName("pageHeading")
        layout.addWidget(heading)
        layout.addWidget(QLabel(description))

        if can_generate:
            placeholders = {
                "image": IMAGE_PLACEHOLDER,
                "video": VIDEO_PLACEHOLDER,
                "music": MUSIC_PLACEHOLDER,
            }
            self.prompt_input = QPlainTextEdit()
            self.prompt_input.setPlaceholderText(
                placeholders.get(kind, "输入创作描述")
            )
            self.prompt_input.setMaximumHeight(140)
            layout.addWidget(self.prompt_input)

            self.lyrics_input: QPlainTextEdit | None = None
            if kind == "music":
                lyrics_label = QLabel("歌词")
                self.lyrics_input = QPlainTextEdit()
                self.lyrics_input.setPlaceholderText(LYRICS_PLACEHOLDER)
                self.lyrics_input.setMaximumHeight(140)
                layout.addWidget(lyrics_label)
                layout.addWidget(self.lyrics_input)
                layout.addWidget(
                    QLabel(
                        "时长默认 180 秒；12GB 显存档位最长 480 秒（8 分钟），"
                        "超出会被自动截到上限；输出为真实 48kHz wav。"
                    )
                )

            self.init_image_edit: QLineEdit | None = None
            if kind == "video":
                row = QHBoxLayout()
                self.init_image_edit = QLineEdit()
                self.init_image_edit.setPlaceholderText(
                    "首帧图片路径（可选，png/jpg）；留空则自动生成首帧"
                )
                browse = QPushButton("选择图片…")
                browse.clicked.connect(self._browse_init_image)
                row.addWidget(self.init_image_edit, 1)
                row.addWidget(browse)
                layout.addLayout(row)

            if engine_ready and on_generate is not None:
                status = QLabel(
                    f"引擎已接入：{engine_label} · 点击「生成」将提交真实任务进入本地队列"
                )
                status.setObjectName("statusInfo")
            elif engine_ready:
                status = QLabel(
                    f"引擎已接入：{engine_label} · 当前进程未连接任务服务"
                )
                status.setObjectName("statusWarning")
            else:
                status = QLabel("真实引擎尚未安装或注册；不提供模拟生成")
                status.setObjectName("statusWarning")
            layout.addWidget(status)

            self.generate_button = QPushButton("生成")
            self.generate_button.setEnabled(
                engine_ready and on_generate is not None
            )
            if not (engine_ready and on_generate is not None):
                self.generate_button.setToolTip("引擎未注册时不提供生成")
            self.generate_button.clicked.connect(self._submit)
            layout.addWidget(self.generate_button)

            self.result_label = QLabel("")
            self.result_label.setWordWrap(True)
            layout.addWidget(self.result_label)

        layout.addStretch(1)

    def _browse_init_image(self) -> None:
        if self.init_image_edit is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "选择首帧图片", "", "图片 (*.png *.jpg *.jpeg *.webp)"
        )
        if path:
            self.init_image_edit.setText(path)

    def _collect_request(self) -> tuple[str, dict] | None:
        """按引擎 worker 的参数白名单组装载荷；非法输入返回 None 并给出提示。"""
        prompt = self.prompt_input.toPlainText().strip()
        if not prompt:
            self._show_result("请先输入创作描述。", is_error=True)
            return None
        parameters: dict = {}
        if self._kind == "music":
            lyrics = (
                self.lyrics_input.toPlainText().strip()
                if self.lyrics_input is not None
                else ""
            )
            if lyrics:
                if len(lyrics) > 4096:
                    self._show_result("歌词超过 4096 字符，请精简。", is_error=True)
                    return None
                parameters["lyrics"] = lyrics
            else:
                parameters["instrumental"] = True
        elif self._kind == "video":
            init_image = (
                self.init_image_edit.text().strip()
                if self.init_image_edit is not None
                else ""
            )
            if init_image:
                parameters["init_image_path"] = init_image
        return prompt, parameters

    def _submit(self) -> None:
        if self._on_generate is None:
            return
        collected = self._collect_request()
        if collected is None:
            return
        prompt, parameters = collected
        try:
            message = self._on_generate(self._kind, prompt, parameters)
        except Exception as exc:  # 提交失败必须诚实呈现，不做任何兜底
            self._show_result(f"提交失败：{exc}", is_error=True)
            return
        self._show_result(message, is_error=False)

    def _show_result(self, message: str, is_error: bool) -> None:
        self.result_label.setText(message)
        self.result_label.setObjectName("statusWarning" if is_error else "statusInfo")
        # 重新应用样式，让颜色随 objectName 切换
        self.result_label.style().unpolish(self.result_label)
        self.result_label.style().polish(self.result_label)
