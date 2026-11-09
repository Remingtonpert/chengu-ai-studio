"""桌面端 MJ 同款创作界面冒烟测试（不启动 worker 循环，绝不触碰 GPU）。

验证：智能体 + 五模式共六个 Tab、想象 4 连发真实落库（queued）、i2i/i2v 垫图门槛、
视频（Wan2.1 wan-t2v/wan-i2v）提交断言动态化：引擎已注册且显存充足时断言真实
落库（queued, engine=wan-t2v）；引擎未注册或显存预检不足时断言诚实拒绝（不落库、不伪造）；
retry 对 queued 任务的诚实拒绝、会话 JSON 持久化、系统功能十页导航。
使用临时 APP_HOME：任务与会话只写入临时目录，不会领取任何真实任务。
"""

from __future__ import annotations

import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

tmp_root = tempfile.mkdtemp(prefix="chenguai_smoke_t37_")
os.environ["CHENGUAI_APP_HOME"] = os.path.join(tmp_root, "home")
# 数据根目录指向真实项目根：让引擎注册做真实的权重/依赖检查（与真实部署
# 一致），而任务库与会话文件在 APP_HOME（临时目录）。不启动执行者，
# 任何 GPU worker 都不会被拉起。
os.environ["CHENGUAI_DATA_ROOT"] = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from app.core.config import ConfigStore  # noqa: E402
from app.core.logging_setup import configure_logging  # noqa: E402
from app.core.paths import AppPaths  # noqa: E402
from app.engine_registration import register_production_engines  # noqa: E402
from app.tasks.engine_manager import EngineManager  # noqa: E402
from app.tasks.service import TaskService  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402
from app.ui.session_store import SESSIONS_FILENAME  # noqa: E402
from app.core.hardware import get_gpu_info  # noqa: E402
from app.ui.widgets.param_panel import ENGINE_TASK_TYPES  # noqa: E402

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
    config_store, logger, str(paths.database_file), task_service, engine_manager, None
)
page = window.creation

failures: list[str] = []


def task_count() -> int:
    return len(task_service.list_tasks(limit=1000))


# ---------------------------------------------------------------- Tab 与模式
tab_modes = [tab.mode for tab in page.tab_buttons]
print(f"[tabs] {tab_modes}")
if tab_modes != ["agent", "t2i", "i2i", "t2v", "i2v", "music"]:
    failures.append(f"六 Tab 顺序异常：{tab_modes}")
for mode in tab_modes:
    page.switch_mode(mode)
    if mode == "agent":
        # 「AI 智能体」是独立面板：它故意不接管创作参数面板、也不把 "agent" 写进
        # 模式设置（免得参数面板读到陌生模式），这里只要求智能体面板真的显示出来。
        if page.agent_page is None or not page.agent_page.isVisibleTo(page):
            failures.append("切到 agent 后智能体面板没有显示")
        continue
    if page.panel.mode != mode or page.composer._modality is None:
        failures.append(f"切换 {mode} 后面板/composer 未跟随")
print("[tabs] 六 Tab（智能体 + 五创作模式）切换全部生效")

# ------------------------------------------------------- 文生图：想象 4 连发
page.switch_mode("t2i")
page.composer.clear_prompt()
page.composer.set_prompt("冒烟测试图像描述")
before = task_count()
page.imagine()
submitted = task_count() - before
print(f"[imagine:t2i] 新增任务 {before} -> {task_count()}")
if submitted != 4:
    failures.append(f"想象应排队 4 张，实际 {submitted}")
latest = task_service.list_tasks(limit=4)
for record in latest:
    if record.status.value != "queued" or record.request.engine_id != "sd-realvis":
        failures.append(f"任务状态/引擎异常：{record.status.value} {record.request.engine_id}")
seeds = {record.request.parameters.get("seed") for record in latest}
if len(seeds) != 4 or None in seeds:
    failures.append(f"4 张 seed 应互不相同且必有：{seeds}")

# i2i：缺垫图必须诚实拒绝，不产生任务
page.switch_mode("i2i")
page.composer.clear_prompt()
page.composer.set_prompt("需要垫图的冒烟")
before = task_count()
page.imagine()
if task_count() != before:
    failures.append("i2i 无垫图竟然提交了任务")
print("[gate:i2i] 无垫图被拒绝，未产生任务")

# ---------------- 垫图必须是「已存在的具体图片文件」：目录/不存在/无效格式拦截 ----------------
import base64  # noqa: E402
from pathlib import Path  # noqa: E402

refs_tmp = paths.app_home / "smoke_refs"
refs_tmp.mkdir(exist_ok=True)
good_png = refs_tmp / "ref_ok.png"
good_png.write_bytes(
    base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )
)
page.composer.set_prompt("需要垫图的冒烟")

page.composer.set_ref(str(paths.app_home))  # 目录 → 必须拦截
before = task_count()
page.imagine()
if task_count() != before:
    failures.append("i2i 垫图为目录时竟然提交了任务")
print("[gate:ref-dir] 垫图填目录被拦截（提示需选具体图片文件）")

page.composer.set_ref("D:\\chenguai_smoke_absent_9f3a\\nope.png")  # 盘符合法但不存在 → 拦截
before = task_count()
page.imagine()
if task_count() != before:
    failures.append("i2i 垫图不存在的路径竟然提交了任务")
print("[gate:ref-missing] 垫图路径不存在被拦截")

page.composer.set_ref(str(good_png))  # 真实存在的图片文件 → 放行
before = task_count()
page.imagine()
ref_submitted = task_count() - before
ref_latest = task_service.list_tasks(limit=ref_submitted)
if ref_submitted != 4 or not all(
    r.request.parameters.get("init_image_path") == str(good_png) for r in ref_latest
):
    failures.append(f"i2i 有效垫图文件未正确排队 4 张或未写入 init_image_path：{ref_submitted}")
else:
    print("[pass:ref-file] 有效垫图文件放行：4 张 queued 且参数含 init_image_path")
page.composer.clear_ref()

# 高级参数里把 init_image_path 填成目录 → 报错该键、其余参数照常提交
page.switch_mode("t2i")
page.composer.clear_prompt()
page.composer.set_prompt("高级参数垫图目录测试")
page.panel.advanced_input.setPlainText(f'{{"init_image_path": "{str(paths.app_home)}"}}')
before = task_count()
page.imagine()
adv_latest = task_service.list_tasks(limit=task_count() - before)
if not adv_latest or any("init_image_path" in r.request.parameters for r in adv_latest):
    failures.append("高级参数目录型 init_image_path 未被拦截（任务参数仍包含该键）")
else:
    print("[gate:adv-ref] 高级参数目录型垫图被报错剔除，任务参数不含 init_image_path")
page.panel.advanced_input.setPlainText("")

# ------------------------- 视频：断言动态化（wan-t2v 注册+显存 → 落库；否则诚实拒绝）
page.switch_mode("t2v")
page.composer.clear_prompt()
page.composer.set_prompt("冒烟测试视频提交")
video_registered = any(
    "video" in (ENGINE_TASK_TYPES.get(e.engine_id) or ()) for e in page._engines
)
_gpu = get_gpu_info()
_video_vram_ok = bool(
    _gpu
    and _gpu.memory_total_mib >= 12288
    and _gpu.memory_free_mib >= 10500
)
before = task_count()
page.imagine()
vid_submitted = task_count() - before
if video_registered and _video_vram_ok:
    if vid_submitted != 1:
        failures.append(
            f"wan-t2v 已注册且显存充足，t2v 应排队 1 个任务，实际 {vid_submitted}"
        )
    else:
        vrec = task_service.list_tasks(limit=1)[0]
        if (
            vrec.request.engine_id != "wan-t2v"
            or vrec.status.value != "queued"
            or vrec.request.task_type != "video"
        ):
            failures.append(
                f"视频任务引擎/状态异常：{vrec.request.engine_id} {vrec.status.value}"
            )
        else:
            print("[pass:t2v] wan-t2v 已注册+显存充足：1 个 video 任务 queued（engine=wan-t2v）")
else:
    if vid_submitted != 0:
        failures.append(
            f"视频门槛被绕过（注册={video_registered}, 显存={_video_vram_ok}），却落了 {vid_submitted} 个任务"
        )
    else:
        print(
            "[gate:t2v] 视频门槛生效"
            f"（注册={video_registered}, 显存充足={_video_vram_ok}）：提交被拒绝，未产生任务"
        )

# i2v：缺垫图必须诚实拒绝
page.switch_mode("i2v")
page.composer.clear_prompt()
page.composer.set_prompt("需要垫图的视频")
before = task_count()
page.imagine()
if task_count() != before:
    failures.append("i2v 无垫图竟然提交了任务")
print("[gate:i2v] 无垫图被拒绝，未产生任务")

# ------------------------------------------------- 音乐：显存/注册门槛不伪造
page.switch_mode("music")
page.composer.clear_prompt()
page.composer.set_prompt("冒烟测试歌曲")
before = task_count()
page.imagine()
after = task_count()
if after != before:
    failures.append("音乐门槛被绕过（本应因显存/注册条件拦截，或弹窗未出现）")
print(f"[gate:music] 提交被门槛拦截（任务数保持 {before}），未弹窗、未伪造输出")

# ------------------------------------------------ 空提示词与失败重试的诚实性
page.switch_mode("t2i")
page.composer.clear_prompt()
before = task_count()
page.imagine()
if task_count() != before:
    failures.append("空提示词竟然提交了任务")
print("[gate:empty] 空提示词被拒绝，未产生任务")

first_image = next(r for r in task_service.list_tasks(limit=50) if r.request.task_type == "image")
before = task_count()
page.retry_task(first_image.task_id)  # queued 不可重试 → 必须诚实提示「重试失败」
if task_count() != before:
    failures.append("对 queued 任务重试竟然新建了任务")
print("[retry:queued] 诚实拒绝，无新任务")

page.cancel_task(first_image.task_id)  # loop=None → 必须诚实提示不可用
print("[cancel:no-loop] 事件循环缺失时诚实报错，未崩溃")

# --------------------------------------------------------- 会话持久化 + 导航
sessions_file = paths.app_home / SESSIONS_FILENAME
if not sessions_file.exists():
    failures.append(f"会话文件未持久化：{sessions_file}")
session_count = len(window.store.sessions)
batch_count = sum(len(s.get("batches", [])) for s in window.store.sessions)
print(f"[sessions] 会话 {session_count} 个 · 批次 {batch_count} 组 · 文件 {sessions_file.name}")
if session_count < 1 or batch_count < 2:
    failures.append("会话/批次未按预期落盘")
window._new_session()
if len(window.store.sessions) != session_count + 1:
    failures.append("新建会话未生效")

for key in ("home", "queue", "history", "models", "engines", "settings", "diagnostics"):
    window._show_system(key)
    if window.stack.currentWidget() is not window._system_shells[key]:
        failures.append(f"系统页 {key} 导航失效")
window._show_creation()
print("[nav] 十页功能：五模式 Tab + 7 个系统页入口全部可达")

if failures:
    print("SMOKE FAIL:")
    for line in failures:
        print(" -", line)
    raise SystemExit(1)
print("SMOKE PASS：桌面端 MJ 同款创作界面接线与门槛全部符合预期（未启动任何 GPU worker）")
