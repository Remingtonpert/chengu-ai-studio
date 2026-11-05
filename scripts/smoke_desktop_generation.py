"""桌面端生成接线冒烟测试（不启动 worker 循环，绝不触碰 GPU）。

验证：MainWindow 三模态（图像/视频/音乐）的引擎状态映射、生成按钮启用状态，
以及「生成」按钮 → TaskService.submit_task → SQLite 排队这条真实提交路径。
使用临时数据根目录，不会写入真实任务库，也不会领取任何真实任务。
"""

from __future__ import annotations

import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

tmp_root = tempfile.mkdtemp(prefix="chenguai_smoke_t32_")
os.environ["CHENGUAI_APP_HOME"] = os.path.join(tmp_root, "home")
# 数据根目录指向真实项目根：让引擎注册做真实的权重/依赖检查（Windows 下
# Models 与 models 同路径），而任务库在 APP_HOME（临时目录），冒烟提交只会
# 写入临时库；由于不启动执行者，任何 GPU worker 都不会被拉起。
os.environ["CHENGUAI_DATA_ROOT"] = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from app.core.config import ConfigStore  # noqa: E402
from app.core.logging_setup import configure_logging  # noqa: E402
from app.core.paths import AppPaths  # noqa: E402
from app.engine_registration import register_production_engines  # noqa: E402
from app.tasks.engine_manager import EngineManager  # noqa: E402
from app.tasks.service import TaskService  # noqa: E402
from app.ui.main_window import PAGES, MainWindow  # noqa: E402
from app.ui.pages.workspace_page import WorkspacePage  # noqa: E402

paths = AppPaths.from_environment()
paths.prepare_app_home()
logger = configure_logging(paths)
config_store = ConfigStore(paths)
config = config_store.load()
# 冒烟镜像真实部署配置：真实 config.json 已开启 engines_enabled。
config["engines_enabled"] = True
config_store.save(config)
engine_manager = EngineManager()
register_production_engines(engine_manager, config, paths, logger)
# 故意不调用 task_service.start()：提交路径可验证，且不会有执行者领取任务。
task_service = TaskService(paths.database_file, engine_manager)

from PySide6.QtWidgets import QApplication  # noqa: E402

application = QApplication.instance() or QApplication([sys.argv[0], "-platform", "offscreen"])
window = MainWindow(
    config_store, logger, str(paths.database_file), task_service, engine_manager
)

failures: list[str] = []

page_by_key: dict[str, WorkspacePage] = {}
for index, (key, _title, _desc, is_generator) in enumerate(PAGES):
    widget = window.stack.widget(index)
    if is_generator and isinstance(widget, WorkspacePage):
        page_by_key[key] = widget

for key in ("image", "video", "music"):
    page = page_by_key[key]
    ready = window._workspace_engine_ready(key)
    label = window._workspace_engine_label(key)
    enabled = page.generate_button.isEnabled()
    print(f"[page:{key}] ready={ready} label={label!r} button_enabled={enabled}")
    if ready and not (label and enabled):
        failures.append(f"{key}: 已注册但按钮未启用或标签为空")

music_page = page_by_key["music"]
if window._music_ready and "ACE-Step 1.5" not in window._page_description("music", ""):
    failures.append("music 页描述未反映 ACE-Step 1.5 已接入")

# 真实提交路径：UI 回调 → TaskService → SQLite（queued），不启动执行者。
for kind, prompt, params in (
    ("image", "冒烟测试图像描述", {}),
    ("music", "冒烟测试歌曲", {"instrumental": True}),
    ("video", "冒烟测试视频", {"init_image_path": "C:/definitely-not-exist/frame.png"}),
):
    before = len(task_service.list_tasks(limit=1000))
    window._submit_generation(kind, prompt, params)  # pylint: disable=protected-access
    record = task_service.list_tasks(limit=1)[0]
    print(f"[submit:{kind}] status={record.status.value} engine={record.request.engine_id}")
    assert record.status.value == "queued"
    assert len(task_service.list_tasks(limit=1000)) == before + 1

# 非法输入：空描述必须诚实报错，不产生任务
before = len(task_service.list_tasks(limit=1000))
music_page.prompt_input.setPlainText("")
music_page.lyrics_input.setPlainText("")
music_page._submit()  # pylint: disable=protected-access
result_text = music_page.result_label.text()
if len(task_service.list_tasks(limit=1000)) != before:
    failures.append("空描述提交竟然产生了任务")
print(f"[reject:empty] result_label={result_text!r}")

# 歌词超长必须被拒绝（与 worker 白名单一致）
music_page.prompt_input.setPlainText("冒烟")
music_page.lyrics_input.setPlainText("啊" * 4097)
music_page._submit()
if "4096" not in music_page.result_label.text():
    failures.append("超长歌词未被拒绝")
print(f"[reject:lyrics] result_label={music_page.result_label.text()!r}")

if failures:
    print("SMOKE FAIL:")
    for line in failures:
        print(" -", line)
    raise SystemExit(1)
print("SMOKE PASS：桌面端三模态接线与输入校验全部符合预期（未启动任何 GPU worker）")
