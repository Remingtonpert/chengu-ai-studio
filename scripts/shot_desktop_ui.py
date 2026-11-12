"""桌面端界面离屏截图（验收用，不启动 worker、不触碰 GPU）。

用法：python scripts\\shot_desktop_ui.py [dark|light|system]
在临时 APP_HOME 里构造完整窗口：想象 4 连发后把任务诚实标记为 completed
并挂上真实存在的产物图（验收图），另留 1 个 queued 任务展示骨架屏；
输出 .tmp_shots\\desktop_<mode>.png。不写入真实任务库。
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

MODE = (sys.argv[1] if len(sys.argv) > 1 else "dark").lower()
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["CHENGUAI_THEME"] = "light" if MODE == "light" else "dark"

PROJECT_ROOT = Path(__file__).resolve().parent.parent
tmp_root = tempfile.mkdtemp(prefix=f"chenguai_shot_{MODE}_")
os.environ["CHENGUAI_APP_HOME"] = os.path.join(tmp_root, "home")
os.environ["CHENGUAI_DATA_ROOT"] = str(PROJECT_ROOT)

from app.core.config import ConfigStore  # noqa: E402
from app.core.logging_setup import configure_logging  # noqa: E402
from app.core.paths import AppPaths  # noqa: E402
from app.engine_registration import register_production_engines  # noqa: E402
from app.tasks.engine_manager import EngineManager  # noqa: E402
from app.tasks.models import TaskRequest  # noqa: E402
from app.tasks.service import TaskService  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402
from app.ui.session_store import new_id  # noqa: E402

DEMO_OUTPUTS = [
    Path(r"D:\ChenguAI\Output\sana_txt2img_20261110_102400.png"),
    Path(r"D:\ChenguAI\Output\sana_txt2img_20261110_102630.png"),
    Path(r"D:\ChenguAI\Output\sana_txt2img_upscaled_20261110_103110.png"),
    Path(r"D:\ChenguAI\Output\wan_i2i_20261110_104022.png"),
]
missing = [p for p in DEMO_OUTPUTS if not p.exists()]

paths = AppPaths.from_environment()
paths.prepare_app_home()
logger = configure_logging(paths)
config_store = ConfigStore(paths)
config = config_store.load()
config["engines_enabled"] = True
config_store.save(config)
engine_manager = EngineManager()
register_production_engines(engine_manager, config, paths, logger)
task_service = TaskService(paths.database_file, engine_manager)

from PySide6.QtWidgets import QApplication  # noqa: E402

application = QApplication.instance() or QApplication([sys.argv[0], "-platform", "offscreen"])
window = MainWindow(
    config_store, logger, str(paths.database_file), task_service, engine_manager, None
)
page = window.creation
page._timer.stop()  # 截图是静态验收，不跑轮询

page.switch_mode("t2i")
page.composer.clear_prompt()
page.composer.set_prompt("一座被雾环绕的白色塔楼，晨光，极简构图")
page.imagine()

if not missing:
    latest = list(task_service.list_tasks(limit=4))
    with sqlite3.connect(paths.database_file) as connection:
        for record, output in zip(latest, DEMO_OUTPUTS, strict=True):
            connection.execute(
                "UPDATE tasks SET status = 'completed', finished_at = ?, progress = NULL,"
                " output_files_json = ? WHERE task_id = ?",
                (
                    datetime.now(timezone.utc).isoformat(),
                    json.dumps([str(output)]),
                    record.task_id,
                ),
            )
        connection.commit()
    # 再挂 1 个保持 queued 的任务（无执行者，永远不会被领取），展示骨架屏。
    demo = task_service.submit_task(
        TaskRequest(
            task_type="image",
            engine_id="sana-multiling",
            prompt="骨架屏演示任务（保持排队）",
            parameters={"width": 1024, "height": 1024, "steps": 4, "seed": 7},
        )
    )
    session = window.store.ensure_active("一座被雾环绕的白色塔楼，晨光，极简构图")
    window.store.add_batch(
        session["id"],
        {
            "id": new_id(),
            "kind": "imagine",
            "mode": "t2i",
            "prompt": "骨架屏演示任务（保持排队）",
            "time": datetime.now(timezone.utc).isoformat(),
            "params": {"width": 1024, "height": 1024, "steps": 4, "seed": 7},
            "taskIds": [demo.task_id],
        },
    )
else:
    print(f"[warn] 验收图缺失 {len(missing)} 张，跳过 completed 演示：{missing}")

if MODE == "system":
    window._show_system("queue")
    out_name = "desktop_system"
else:
    out_name = f"desktop_{MODE}"

window.resize(1360, 860)
window.show()
for _ in range(12):
    application.processEvents()
page.poll()
for _ in range(24):
    application.processEvents()

shots_dir = PROJECT_ROOT / ".tmp_shots"
shots_dir.mkdir(exist_ok=True)
target = shots_dir / f"{out_name}.png"
pixmap = window.grab()
pixmap.save(str(target))
print(f"[shot] saved {target} ({pixmap.width()}x{pixmap.height()})")
