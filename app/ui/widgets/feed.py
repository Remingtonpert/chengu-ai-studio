"""Centre feed: session batches (user bubble + tags + result grid/skeletons)
with dirty-check re-render and near-bottom auto scroll, mirroring the web
``#feed`` rendering pipeline (feedSig / renderFeed).
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.tasks.models import TaskRecord
from app.ui.widgets.common import fmt_time
from app.ui.widgets.param_panel import MODE_LABELS
from app.ui.widgets.tiles import KIND_LABELS, make_tile

BATCH_MAX_WIDTH = 860
FEED_PADDING = 24
GRID_GAP = 10
ACTIVE_STATUSES = {"queued", "preparing", "running"}


class FeedArea(QScrollArea):
    tileClicked = Signal(str, str)          # task_id, batch_id
    variationRequested = Signal(str, str)   # task_id, batch_id
    upscaleRequested = Signal(str, str)
    cancelRequested = Signal(str)
    retryRequested = Signal(str, str)       # task_id, batch_id
    # 手动「另存」：把这份产物复制一份到用户挑的位置（项目内 output\ 那份始终保留）。
    saveRequested = Signal(str, str)        # task_id, batch_id
    # 卡片上的「▶ 播放」：产物在工具内的播放窗里放（对应网页端灯箱里的播放器）。
    playRequested = Signal(str, str)        # task_id, batch_id

    def __init__(self, tokens: dict[str, str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("feedArea")
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tokens = tokens
        self._last_sig = ""
        self._tiles: list = []

        self.host = QWidget(self.viewport())
        self.host.setObjectName("feedHost")
        self._layout = QVBoxLayout(self.host)
        self._layout.setContentsMargins(FEED_PADDING, 16, FEED_PADDING, 24)
        self._layout.setSpacing(18)
        self.setWidget(self.host)

        self.empty = QWidget(self)
        self.empty.setObjectName("feedEmpty")
        empty_layout = QVBoxLayout(self.empty)
        empty_layout.setContentsMargins(0, 60, 0, 0)
        empty_layout.setSpacing(10)
        logo = QLabel("辰鹄AI创作中心", self.empty)
        logo.setObjectName("feedLogo")
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        line1 = QLabel(
            "在下方输入提示词，Enter 提交。顶部 Tab 切换业务：文生图 / 图生图 / 视频 / AI音乐。",
            self.empty,
        )
        line1.setObjectName("mutedMsg")
        line1.setAlignment(Qt.AlignmentFlag.AlignCenter)
        line1.setWordWrap(True)
        line2 = QLabel(
            "同一会话可混排图片、视频、音频记录 · 悬停结果图显示 V（变化）/ U（放大）",
            self.empty,
        )
        line2.setObjectName("mutedMsg")
        line2.setAlignment(Qt.AlignmentFlag.AlignCenter)
        line2.setWordWrap(True)
        empty_layout.addWidget(logo)
        empty_layout.addWidget(line1)
        empty_layout.addWidget(line2)
        empty_layout.addStretch(1)
        self._layout.addWidget(self.empty)
        self.batch_host = QWidget(self.host)
        self.batch_host.setObjectName("feedBatches")
        self._batch_layout = QVBoxLayout(self.batch_host)
        self._batch_layout.setContentsMargins(0, 0, 0, 0)
        self._batch_layout.setSpacing(18)
        self._layout.addWidget(self.batch_host)

    # ------------------------------------------------------------------ 滚动
    def near_bottom(self) -> bool:
        bar = self.verticalScrollBar()
        return bar.maximum() - bar.value() < 240

    def scroll_to_bottom(self) -> None:
        self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())

    # ------------------------------------------------------------------ 渲染
    @staticmethod
    def _sig(
        session: dict | None,
        tasks: dict[str, TaskRecord],
        retried_ids: set[str],
        queue_map: dict[str, int],
    ) -> str:
        if not session or not session.get("batches"):
            return "empty:" + (session["id"] if session else "none")
        parts = [session["id"]]
        for batch in session["batches"]:
            chunk = [
                "b",
                batch["id"],
                str(len(batch.get("taskIds") or [])),
                str(bool(batch.get("refPath"))),
            ]
            for task_id in batch.get("taskIds") or []:
                rec = tasks.get(task_id)
                if rec is None:
                    chunk.append(task_id + ":missing")
                    continue
                chunk.append(
                    f"{task_id}:{rec.status.value}"
                    f":{round((rec.progress or 0) * 100) if rec.progress else 0}"
                    f":{len(rec.output_files)}"
                    f":{queue_map.get(task_id, '')}"
                )
            parts.append("|".join(chunk))
        parts.append("r" + ",".join(sorted(retried_ids)))
        return ";".join(parts)

    def render(
        self,
        session: dict | None,
        tasks: dict[str, TaskRecord],
        retried_ids: set[str],
        queue_map: dict[str, int],
        force: bool = False,
    ) -> bool:
        sig = self._sig(session, tasks, retried_ids, queue_map)
        if sig == self._last_sig and not force:
            return False
        self._last_sig = sig
        had_focus_bottom = self.near_bottom()

        for tile in self._tiles:
            stopper = getattr(tile, "stop", None)
            if callable(stopper):
                stopper()
            tile.deleteLater()
        self._tiles = []
        while self._batch_layout.count():
            item = self._batch_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        batches = list((session or {}).get("batches") or [])
        self.empty.setVisible(not batches)
        if not batches:
            return True

        content_width = max(
            320,
            min(self.viewport().width() - FEED_PADDING * 2, BATCH_MAX_WIDTH)
            - FEED_PADDING * 2,
        )
        for batch in batches:
            block = self._batch_block(batch, tasks, retried_ids, queue_map, content_width)
            # 与网页 .batch 一致：块级全宽（上限 860 居中），气泡与网格右缘对齐。
            block.setFixedWidth(min(BATCH_MAX_WIDTH, self.viewport().width() - FEED_PADDING * 2))
            self._batch_layout.addWidget(block, 0, Qt.AlignmentFlag.AlignHCenter)
        self._batch_layout.addStretch(1)
        if had_focus_bottom:
            self.scroll_to_bottom()
        return True

    def _batch_block(
        self,
        batch: dict,
        tasks: dict[str, TaskRecord],
        retried_ids: set[str],
        queue_map: dict[str, int],
        content_width: int,
    ) -> QFrame:
        block = QFrame(self.host)
        block.setObjectName("batchBlock")
        layout = QVBoxLayout(block)
        layout.setContentsMargins(FEED_PADDING, 14, FEED_PADDING, 16)
        layout.setSpacing(10)

        bubble_row = QHBoxLayout()
        bubble_row.addStretch(1)
        bubble = QLabel(str(batch.get("prompt") or ""), block)
        bubble.setObjectName("userBubble")
        bubble.setWordWrap(True)
        bubble.setTextFormat(Qt.TextFormat.PlainText)
        bubble.setMaximumWidth(max(240, content_width - 40))
        bubble.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        bubble_row.addWidget(bubble)
        layout.addLayout(bubble_row)

        tag_row = QHBoxLayout()
        tag_row.setSpacing(6)
        kind = str(batch.get("kind") or "imagine")
        tags = [
            KIND_LABELS.get(kind, kind),
            MODE_LABELS.get(str(batch.get("mode") or ""), str(batch.get("mode") or "")),
            fmt_time(batch.get("time")),
        ]
        ref_path = str(batch.get("refPath") or "")
        for index, text in enumerate(tags):
            tag = QLabel(text, block)
            tag.setObjectName("tag")
            tag_row.addWidget(tag)
        if ref_path:
            from pathlib import Path

            tag = QLabel(f"参考图 {Path(ref_path).name}", block)
            tag.setObjectName("tagRef")
            tag_row.addWidget(tag)
        tag_row.addStretch(1)
        layout.addLayout(tag_row)

        grid_host = QWidget(block)
        grid_host.setObjectName("gridHost")
        grid = QGridLayout(grid_host)
        grid.setContentsMargins(0, 2, 0, 0)
        grid.setSpacing(GRID_GAP)
        task_ids = list(batch.get("taskIds") or [])
        params = batch.get("params") or {}
        mode = str(batch.get("mode") or "")

        if mode == "music" or len(task_ids) == 0:
            tile_w = content_width
            columns = 1
        elif len(task_ids) == 1 and (mode in {"t2v", "i2v", "video"} or kind in {"video", "music", "upscale"}):
            tile_w = min(420, content_width)
            columns = 1
        else:
            columns = 4 if self.viewport().width() >= 1100 else 2
            tile_w = (content_width - (columns - 1) * GRID_GAP) // columns

        for index, task_id in enumerate(task_ids):
            rec = tasks.get(task_id)
            if rec is None:
                placeholder = QLabel("任务记录缺失", grid_host)
                placeholder.setObjectName("mutedMsg")
                grid.addWidget(placeholder, index // columns, index % columns)
                continue
            tile = make_tile(
                rec,
                batch,
                tile_w,
                self.tokens,
                retried_ids,
                queue_map.get(task_id),
                full_width=(content_width if columns == 1 and mode == "music" else None),
            )
            self._connect(tile, task_id, str(batch.get("id") or ""))
            self._tiles.append(tile)
            grid.addWidget(tile, index // columns, index % columns, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        for column in range(columns):
            grid.setColumnStretch(column, 1)
        layout.addWidget(grid_host)
        return block

    def _connect(self, tile, task_id: str, batch_id: str) -> None:
        tile.tileClicked.connect(lambda tid: self.tileClicked.emit(tid, batch_id))
        tile.variationRequested.connect(lambda tid: self.variationRequested.emit(tid, batch_id))
        tile.upscaleRequested.connect(lambda tid: self.upscaleRequested.emit(tid, batch_id))
        tile.cancelRequested.connect(self.cancelRequested)
        tile.retryRequested.connect(lambda tid: self.retryRequested.emit(tid, batch_id))
        tile.saveRequested.connect(lambda tid: self.saveRequested.emit(tid, batch_id))
        tile.playRequested.connect(lambda tid: self.playRequested.emit(tid, batch_id))
