"""Application startup, local services, and Qt lifecycle."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import subprocess
import sys
import threading
import urllib.request
from collections.abc import Callable, Sequence
from concurrent.futures import TimeoutError as FutureTimeoutError
from pathlib import Path

from PySide6.QtWidgets import QApplication

from app.core.config import ConfigStore
from app.core.console import ensure_utf8_console
from app.core.logging_setup import configure_logging
from app.core.paths import AppPaths
from app.engine_registration import register_production_engines
from app.tasks.engine_manager import EngineManager
from app.tasks.models import TaskStatus
from app.tasks.service import TaskService
from app.ui.main_window import MainWindow


def _port_in_use(host: str, port: int, timeout: float = 0.8) -> bool:
    """端口上有没有服务在监听（0.8 秒探一次，探不通就当没有）。"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _own_console_listening(host: str, port: int, timeout: float = 1.5) -> bool:
    """这个端口上的服务是不是自家的网页端（认 /api/health 里的自家标记）。

    自家的 → 桌面端不重复起，退出时也不去动它；别人家的（或者根本不是 HTTP 服务）
    → 不算数，让网页端自己往后挪一个端口。只靠「端口通不通」判断会把别人的服务当成自己，
    既可能让网页端起不来，也可能在退出时代杀掉别人家的程序。
    """
    from app.web.server import WEB_SERVICE_ID  # 惰性导入：桌面端启动时不必为此加载整个 Web 层

    try:
        with urllib.request.urlopen(f"http://{host}:{port}/api/health", timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
    except (OSError, ValueError):
        return False
    return isinstance(payload, dict) and str(payload.get("service", "")) == WEB_SERVICE_ID


def _hinted_console_target(paths: AppPaths) -> tuple[str, int] | None:
    """上一次网页端实际在听哪个端口（被占过端口它就会挪一格，地址记在这里）。"""
    hint_file = paths.log_file.parent / "web-console.port"
    try:
        host, _, port = hint_file.read_text(encoding="utf-8").strip().rpartition(":")
        return (host or "127.0.0.1", int(port))
    except (OSError, ValueError):
        return None


def _tasks_in_flight(task_service: TaskService) -> int:
    """还有多少任务在排队/执行；查不动就返回 -1，调用方按「当作有」处理，宁可不杀。"""
    try:
        return sum(
            len(task_service.list_tasks(status=status, limit=200))
            for status in (TaskStatus.QUEUED, TaskStatus.PREPARING, TaskStatus.RUNNING)
        )
    except Exception:  # noqa: BLE001 - 退出路径上不能因为查状态把退出卡住
        return -1


def start_web_console(
    paths: AppPaths,
    config: dict,
    task_service: TaskService,
    logger,
) -> Callable[[], None]:
    """随桌面端把网页控制台一起带起来，返回「退出时是否顺手关掉它」的回调。

    为什么必须带起来：两端是同一条 SQLite 队列的两个客户端，用户只开桌面端时
    8765 上没有任何监听，浏览器打开 http://127.0.0.1:8765 只会看到「连不上」，
    于是永远被当成「网页端坏了」。桌面端起来就把网页端也起来，两端才真的都在。

    不重复起、不代杀：只有「端口上确实是自家的网页端」（/api/health 里的自家标记）才不重复起，
    退出时也不去动它；端口被别的程序占着就不管它，网页端自己往后挪一格端口起来
    （config.json 的 ``web_port_fallback``，默认开），真实地址记在 logs\\web-console.port。
    桌面端自己不监听任何端口，所以两端本来就不互抢端口；会撞上的只有第三方程序。
    退出时只有在「没有任务在排队/执行」的情况下才关掉它，否则留着它把队列跑完，
    免得把正在跑的出图/出视频拦腰砍断。要彻底关掉这个行为：config.json 里
    ``web_autostart`` 改成 ``false``。
    """
    forced = os.environ.get("CHENGUAI_FORCE_WEB_AUTOSTART") == "1"
    if not forced and (
        os.environ.get("CHENGUAI_DISABLE_WEB_AUTOSTART") == "1" or os.environ.get("PYTEST_CURRENT_TEST")
    ):
        # 测试里不能顺手占用户的 8765 端口，也不能凭空多出一个服务子进程；
        # 自动化打包/无人值守脚本想彻底关掉这个行为就设 CHENGUAI_DISABLE_WEB_AUTOSTART=1，
        # 测试想反过来验证「真的带起来了」就设 CHENGUAI_FORCE_WEB_AUTOSTART=1（配合临时端口与临时应用目录）。
        logger.info("当前在测试环境或已设 CHENGUAI_DISABLE_WEB_AUTOSTART=1：本次不自动带起网页端")
        return lambda: None
    if not bool(config.get("web_autostart", True)):
        logger.info("配置项 web_autostart 为 false：本次不自动带起网页端（需要时双击 scripts\\run_web.bat）")
        return lambda: None
    host = str(config.get("web_host") or "127.0.0.1")
    port = int(config.get("web_port") or 8765)
    targets = [(host, port)]
    hinted = _hinted_console_target(paths)
    if hinted and hinted not in targets:
        targets.append(hinted)  # 上次被占过端口、网页端挪到过别的端口
    for target_host, target_port in targets:
        if _own_console_listening(target_host, target_port):
            logger.info("网页端已经在 http://%s:%d 上监听，桌面端不重复启动", target_host, target_port)
            return lambda: None
    if _port_in_use(host, port):
        logger.info(
            "http://%s:%d 上坐着别的程序：网页端会自己往后挪一格端口再起来，真实地址看 logs\\web-console.port",
            host,
            port,
        )
    root = Path(__file__).resolve().parents[1]
    err_log = paths.log_file.parent / "web-console.err.log"
    creationflags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
    err_handle = open(err_log, "a", encoding="utf-8", errors="replace")
    try:
        process = subprocess.Popen(
            [sys.executable, "-m", "app.web.server"],
            cwd=str(root),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=err_handle,
            creationflags=creationflags,
        )
    except OSError as exc:
        logger.warning("网页端自动带起失败：%s（可以手动双击 scripts\\run_web.bat）", exc)
        return lambda: None
    finally:
        # 子进程已继承这个句柄，本进程这边必须关掉，不然每次启动都漏一个文件句柄。
        err_handle.close()
    logger.info(
        "网页端已随桌面端启动：http://%s:%d（PID %s）。那个端口要是被人占了它会自动挪一格，"
        "真实地址看 logs\\web-console.port；不想要这个行为就把 config.json 里的 web_autostart 改成 false",
        host,
        port,
        process.pid,
    )

    def stop() -> None:
        if process.poll() is not None:
            return
        busy = _tasks_in_flight(task_service)
        if busy != 0:
            logger.warning(
                "退出时还有任务在排队/执行（约 %s 个），网页端不跟着桌面端退出，免得把任务拦腰砍断；"
                "它跑完后自己会退出，要手动停：powershell -File scripts\\run_web.ps1 -Restart",
                "未知" if busy < 0 else busy,
            )
            return
        process.terminate()
        try:
            process.wait(timeout=8)
            logger.info("网页端已随桌面端退出（PID %s）", process.pid)
        except subprocess.TimeoutExpired:
            process.kill()
            logger.warning("网页端 8 秒内没退出，已强制结束（PID %s）", process.pid)

    return stop


def start_task_service_loop(
    task_service: TaskService, logger
) -> tuple[Callable[[], None], asyncio.AbstractEventLoop | None]:
    """在后台线程运行 asyncio 事件循环，驱动与 Web 控制台完全相同的 TaskService。

    桌面端不再自带独立的任务执行逻辑：worker 循环、SQLite FIFO 队列、引擎
    子进程协议与 Web 模式一行不差；若另一个进程（Web 控制台）已经是任务
    执行者，TaskService 会自动降级为「只提交」模式，队列 5 秒内被执行者领走。
    返回 (优雅停止回调, 服务事件循环)：GUI 用该 loop 回投 cancel_task /
    restart_engine 等协程（asyncio.run_coroutine_threadsafe）。
    """
    loop_holder: dict[str, asyncio.AbstractEventLoop | None] = {"loop": None}
    loop_ready = threading.Event()

    async def _boot() -> None:
        try:
            await task_service.start()
        except Exception:
            logger.exception("任务服务启动失败")

    async def _graceful_shutdown() -> None:
        try:
            await task_service.shutdown()
        finally:
            asyncio.get_running_loop().stop()

    def _runner() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop_holder["loop"] = loop
        loop.create_task(_boot())
        loop_ready.set()
        loop.run_forever()

    thread = threading.Thread(target=_runner, name="chenguai-task-loop", daemon=True)
    thread.start()
    loop_ready.wait(timeout=10)
    logger.info("任务服务事件循环已在后台线程启动")

    def stop() -> None:
        loop = loop_holder["loop"]
        if loop is None:
            return
        try:
            future = asyncio.run_coroutine_threadsafe(_graceful_shutdown(), loop)
            future.result(timeout=15)
            logger.info("任务服务已随应用优雅停止")
        except FutureTimeoutError:
            # 引擎 worker 还在收尾（比如正在把权重卸载干净）就会走到这里。
            # 这是退出路径上的超时，不是故障：打一行能看懂的话，别甩整面 traceback，
            # 它拉起的子进程由内核护栏（作业对象 + 父进程看门狗）负责一并回收。
            logger.warning(
                "任务服务 15 秒内没有优雅停止（引擎 worker 大概还在收尾），已继续退出；"
                "它带起来的子进程会被内核护栏一并回收"
            )
        except Exception:
            logger.exception("停止任务服务失败")

    return stop, loop_holder["loop"]


def create_application(
    paths: AppPaths | None = None,
    argv: Sequence[str] | None = None,
) -> tuple[QApplication, MainWindow]:
    paths = paths or AppPaths.from_environment()
    paths.prepare_app_home()
    logger = configure_logging(paths)
    config_store = ConfigStore(paths)
    config = config_store.load()
    engine_manager = EngineManager()
    register_production_engines(engine_manager, config, paths, logger)
    task_service = TaskService(paths.database_file, engine_manager)
    # 自动另存目录（配置项 auto_save_dir）：两端共用同一份 config.json，真正执行任务的
    # 那个进程按它复制副本，所以从桌面端还是网页端提交的任务表现一致。
    task_service.set_auto_save_dir(str(config.get("auto_save_dir") or ""))
    # 「工具内 output\」固定镜像：产物写进 output_dir（工具里即时显示的那一份）之后，
    # 再复制一份到项目根下的 output\，这样不管用户把 output_dir 改到哪块盘，项目内
    # output\ 里始终有一份；output_dir 本来就指着这里时不会重复复制（见 core/artifacts.py）。
    task_service.set_project_output_dir(str(paths.project_output_dir))
    # 中断任务恢复与 worker 循环统一由 task_service.start() 完成，
    # 并由跨进程执行者锁保证只有「首个启动的进程」执行恢复，
    # 不会把另一个进程正在运行的任务误标为失败。
    stop_task_loop, service_loop = start_task_service_loop(task_service, logger)
    # 桌面端一起来就把网页控制台带起来：两端是同一条队列的两个客户端，
    # 只开桌面端时 8765 上没人监听，浏览器只会看到「连不上」，网页端就永远显得坏了。
    stop_web_console = start_web_console(paths, config, task_service, logger)

    application = QApplication.instance() or QApplication(list(argv or sys.argv))
    window = MainWindow(
        config_store,
        logger,
        str(paths.database_file),
        task_service,
        engine_manager,
        service_loop,
    )
    application.aboutToQuit.connect(
        lambda: logger.info("应用正常退出")
    )
    # 先停网页端（它自己会走 task_service.shutdown 与引擎收尾），再停本进程的任务循环。
    application.aboutToQuit.connect(stop_web_console)
    application.aboutToQuit.connect(stop_task_loop)
    # 退出时把智能体拉起的大模型服务进程一并结束：不然 llama-server 变成孤儿，
    # 显存一直占着，下次进来只会看到「显存不知道被谁吃了」。挂在 stop_task_loop
    # 之后 = 生成引擎的 worker 先走、大模型进程后走，顺序可控。
    application.aboutToQuit.connect(window.shutdown_services)
    # 上次被强杀留下的孤儿 llama-server 在下次启动时清掉（只认自家镜像名）。
    window.sweep_orphan_llm()
    return application, window


def main() -> int:
    logging.raiseExceptions = False
    ensure_utf8_console()
    application, window = create_application()
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
