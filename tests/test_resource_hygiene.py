"""资源卫生：显存判定要诚实、空闲要自动让路、退出别留孤儿。

这一组测试不碰 GPU、不起引擎、不结束任何真实进程：按进程的显存数据全部喂进去，
护栏只验「读」的那一半（读进程表、找孤儿 PID），不真去结束进程。
"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

import pytest

from app.agent.local_server import DEFAULT_PORT, LocalLlamaServer
from app.core.hardware import (
    GpuInfo,
    VramHolder,
    effective_free_mib,
    forget_vram_cache,
    holders_summary,
    vram_holders_cached,
)
from app.core.process_guard import orphan_pids_by_image, parent_pid_of, process_alive
from app.web import server as web_server

# 本机实况（RTX 3060 12GB，第三方推理程序占走约 10GB）：nvidia-smi 说只剩 1420MB，
# 但按进程实测加起来是 10110MB，也就是其实还能用 2178MB——差的那 758MB 是 Windows
# 把驱动保留、以及已经结束但驱动还没回收的占用一并算进 used 造成的虚高。
RTX_3060 = GpuInfo(
    name="NVIDIA GeForce RTX 3060",
    memory_total_mib=12288,
    memory_free_mib=1420,
    memory_used_mib=10694,
)
HOLDERS = (
    VramHolder(35688, "strata.exe", 10081),
    VramHolder(4321, "Doubao.exe", 15),
    VramHolder(5678, "TextInputHost.exe", 14),
)


# ------------------------------------------------------------------ 显存判定
def test_effective_free_is_never_worse_than_nvidia_free() -> None:
    """按进程实测把被冤枉的那部分显存还回来，但绝不编得比 nvidia-smi 更乐观。"""
    assert effective_free_mib(RTX_3060, HOLDERS) == 2178
    assert effective_free_mib(RTX_3060, HOLDERS) > RTX_3060.memory_free_mib


def test_unknown_holders_fall_back_to_nvidia_free() -> None:
    """拿不到按进程的数据（非 Windows、没有计数器）就退回 nvidia-smi 的 free，不猜数。"""
    assert effective_free_mib(RTX_3060, None) == 1420
    assert effective_free_mib(RTX_3060, ()) == 1420
    assert effective_free_mib(None, HOLDERS) == -1  # -1＝连显卡都探不到，由调用方决定放行


def test_our_own_workers_do_not_count_against_us() -> None:
    """自家还没停干净的引擎 worker 的显存是「马上能要回来」的，不能算成别人占走的。"""
    holders = HOLDERS + (VramHolder(7777, "python.exe", 5000),)
    assert effective_free_mib(RTX_3060, holders) == 1420  # 别人 + 自己 > 总量，只能退回 free
    assert effective_free_mib(RTX_3060, holders, ours=(7777,)) == 2178  # 12288 − 别人占的 10110


def test_holders_summary_names_the_culprit() -> None:
    """拦下任务时要点名是谁占着显存，而不是让用户去瞎猜、去双击脚本。"""
    holders = HOLDERS + (VramHolder(999, "chrome.exe", 300),)
    assert holders_summary(holders) == "strata.exe 10081MB、Doubao.exe 15MB、TextInputHost.exe 14MB"
    assert holders_summary(holders, limit=1) == "strata.exe 10081MB"
    assert holders_summary(None) == "没查到是哪个程序占着显存"
    assert holders_summary(()) == "没查到是哪个程序占着显存"


def test_vram_cache_remeasures_after_we_release_engines(monkeypatch) -> None:
    """刚释放过引擎就必须重新实测；顺手确认它不会挂到仪表盘那种高频轮询上（吃缓存）。"""
    calls: list[int] = []

    def fake_query() -> tuple[VramHolder, ...]:
        calls.append(1)
        return HOLDERS

    monkeypatch.setattr("app.core.hardware.process_vram_holders", fake_query)
    forget_vram_cache()
    assert vram_holders_cached(20.0)[0].memory_mib == 10081
    vram_holders_cached(20.0)
    assert len(calls) == 1  # 第二次吃缓存，不反复查计数器
    forget_vram_cache()
    assert vram_holders_cached(0.0) is not None
    assert len(calls) == 2  # 让完路立刻重测


# --------------------------------------------------------- 空闲自动卸载大模型
def _server(config_endpoint: str, tmp_path) -> LocalLlamaServer:
    server = LocalLlamaServer({"llm_endpoint": config_endpoint})
    server.pid_file = tmp_path / "llama_server.pid"  # 绝不碰项目里真那份 pid 文件
    return server


def test_idle_reap_never_touches_someone_elses_endpoint(tmp_path) -> None:
    """端点不是我们自带的端口（比如用户自己的 Ollama）＝我们没资格停它。"""
    server = _server("http://127.0.0.1:11434/v1", tmp_path)
    server.port_open = lambda: True
    server.stop = lambda: pytest.fail("不许对别人的端点动手")
    result = server.stop_if_idle(0)
    assert result["ok"] is False and result["state"] == "external"


def test_idle_reap_waits_until_it_is_really_idle(tmp_path) -> None:
    """刚聊过一句就去收割＝不许停：那会把用户眼前这一句打断。"""
    server = _server(f"http://127.0.0.1:{DEFAULT_PORT}/v1", tmp_path)
    server.port_open = lambda: True
    server.stop = lambda: pytest.fail("才空闲几秒，没到自动卸载的时间")
    result = server.stop_if_idle(600)
    assert result["ok"] is False and result["state"] == "busy"


def test_idle_reap_unloads_after_the_limit(tmp_path) -> None:
    """十分钟没人用就把服务整个停掉，并如实说明下次会自动重载。"""
    server = _server(f"http://127.0.0.1:{DEFAULT_PORT}/v1", tmp_path)
    server.port_open = lambda: True
    server._last_used -= 3600  # 一小时没人碰它
    calls: list[str] = []
    server.stop = lambda: (calls.append("stop") or {"ok": True, "note": "已停止"})
    result = server.stop_if_idle(600)
    assert calls == ["stop"] and result["ok"] is True
    assert "自动卸载" in str(result["note"])
    assert "下次聊天会自动重载" in str(result["note"])


def test_idle_reap_clears_stale_pid_file_when_nothing_is_listening(tmp_path) -> None:
    """端口没人听＝服务早就不在了：只清掉过期的 pid 文件，不去结束任何 PID。"""
    server = _server(f"http://127.0.0.1:{DEFAULT_PORT}/v1", tmp_path)
    server.port_open = lambda: False
    server.pid_file.write_text("999999", encoding="utf-8")
    result = server.stop_if_idle(600)
    assert result["state"] == "none" and result["note"] == ""
    assert not server.pid_file.exists()


def test_agent_unloads_llm_and_drops_its_cached_endpoint() -> None:
    """空闲卸载之后要把大模型客户端的缓存一并作废，否则下次还往死端点上打。"""
    from app.agent.service import AgentService

    events: list[str] = []
    agent = AgentService(
        task_service=SimpleNamespace(),
        engine_manager=SimpleNamespace(list_statuses=lambda: []),
        config={"llm_endpoint": f"http://127.0.0.1:{DEFAULT_PORT}/v1"},
    )
    agent.local_llm = SimpleNamespace(
        stop_if_idle=lambda: (events.append("stop_if_idle") or {"ok": True, "note": "已卸载自带大模型服务"}),
        owns_endpoint=lambda: True,
        port_open=lambda: True,
        stop=lambda: (events.append("stop") or {"ok": True, "note": "已停止"}),
        sweep_orphans=lambda: (events.append("sweep") or (4242,)),
    )
    agent.llm = SimpleNamespace(
        invalidate=lambda: events.append("invalidate"),
        resolve_model=lambda: "unused",
    )
    note = agent.reap_idle_llm(600)
    assert events[:2] == ["stop_if_idle", "invalidate"]
    assert "已卸载" in note
    assert agent.stop_llm_for_exit() == "已停止"  # 退出时同一个动作再做一遍
    assert agent.sweep_orphan_llm() == (4242,)


# ---------------------------------------------------------------- 进程护栏
def test_process_guard_reads_the_process_table_only() -> None:
    assert process_alive(os.getpid()) is True
    assert process_alive(2147483644) is False  # 不可能存在的 PID
    assert parent_pid_of(os.getpid()) > 0


def test_orphan_sweep_matches_our_own_image_name_only() -> None:
    """按镜像名找孤儿：名字对不上就一个都不动（绝不按 python.exe 乱杀，那是用户自己的解释器）。"""
    assert orphan_pids_by_image("chenguai-不存在的镜像名.exe") == ()


def test_web_hardware_endpoint_passes_detailed_through_the_thread_pool(monkeypatch) -> None:
    """网页端 /api/hardware 的调用形状：detailed 要能穿过线程池拿到按进程实测的数。

    这里曾经把 detailed 当位置参数交给 asyncio.to_thread（而 _hardware_snapshot 只有
    关键字参数），结果整个端点 500——仪表盘和显存门槛一起挂。测试就钉住这条路径。
    """

    class FakeCompleted:
        stdout = "NVIDIA GeForce RTX 3060, 12288, 1420, 10694\n"
        returncode = 0

    def fake_run(command, *args, **kwargs):
        assert "--query-gpu=name,memory.total,memory.free,memory.used" in command
        return FakeCompleted()

    monkeypatch.setattr(web_server.subprocess, "run", fake_run)
    monkeypatch.setattr(
        web_server,
        "vram_holders_cached",
        lambda max_age_seconds=20.0: (VramHolder(35688, "strata.exe", 10081),),
    )

    # 不建 socket，只把要用的两个缓存字段摆好：走的就是线上那条 _hardware_async
    handler = web_server.WebApplication.__new__(web_server.WebApplication)
    handler._hardware_cache = None
    handler._hardware_at = 0.0

    detailed = asyncio.run(handler._hardware_async(detailed=True))
    gpu = detailed["gpu"]
    assert gpu["memory_used_mb"] == 10694, "nvidia-smi 的 used 要带出来，但只当参考"
    assert gpu["memory_free_effective_mb"] == 2207, "按进程实测还能用的空闲＝12288−10081"
    assert gpu["memory_holders"] == "strata.exe 10081MB"
    assert gpu["memory_holders_list"][0]["pid"] == 35688

    # 默认路径（仪表盘每几秒轮询）绝不去起 PowerShell 实测
    handler._hardware_cache = None
    plain = asyncio.run(handler._hardware_async())
    assert "memory_holders" not in plain["gpu"]
    assert plain["gpu"]["memory_free_effective_mb"] == 1420

    # detailed=True 不吃 TTL 缓存：刚把自己引擎停掉，显存数必须现查
    monkeypatch.setattr(
        web_server,
        "vram_holders_cached",
        lambda max_age_seconds=20.0: (VramHolder(4242, "someone.exe", 100),),
    )
    again = asyncio.run(handler._hardware_async(detailed=True))
    assert again["gpu"]["memory_free_effective_mb"] == 12188
