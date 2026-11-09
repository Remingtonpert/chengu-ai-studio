"""「AI 智能体」自带的大模型服务：项目内的 llama.cpp（llama-server）+ GGUF 权重。

为什么不再用 Ollama：用户机器上的 Ollama 是别人家的服务，开机自启的是它、端口是它的，
我们那个专用实例没人管，重启电脑后就不在了，界面上就一直是「本机大模型：不可用」。
现在整条链路都在项目内：

* 推理程序  ``tools\\llama_cpp\\llama-server.exe``（脚本 -Setup 自动下载）
* 权重      ``models\\llm\\gguf\\*.gguf``（脚本 -Setup 从 hf-mirror 下载，跟着便携包走）
* 端口      只绑回环 127.0.0.1:11435，OpenAI 兼容 /v1，界面与 llm.py 一行不用改

这里只负责「有没有在跑 / 需要时把它拉起来 / 把我们启动过的停掉」，
绝不碰用户自己的 Ollama（11434）：停机只认我们写的 pid 文件，而且要求进程镜像名就是
llama-server.exe，端口上挂着别人的进程时一律不动。
"""

from __future__ import annotations

import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Mapping

from app.core.paths import AppPaths
from app.core.process_guard import assign_pid_to_kill_job, orphan_pids_by_image

# 项目根（便携包则是包根）：与 app.core.paths.AppPaths.models_dir 同一套判定。
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 自带的服务只用这个端口；换端口只改脚本参数，代码里所有回环护栏都跟着走。
DEFAULT_PORT = 11435
_SERVER_IMAGE = "llama-server.exe"
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}

# ---- 自带大模型的加载参数（只服务「AI 智能体」，五个生成模块与这套参数无关）------
# 上下文 8K → 16K；KV cache 的键与值都量化成 q8_0：16K 上下文的 KV 体积正好减半，
# 精度损失对智能体这种短回答可以忽略，省下来的显存留给生成侧。
LLM_CTX_SIZE = 16384
LLM_KV_CACHE_TYPE = "q8_0"
# 服务起来之后显存里还必须留着多少空余：大模型与图像/视频/音乐引擎抢的是
# 同一块卡，留白不够生成侧就 OOM。按整卡容量的 30% 现算（12GB 卡 = 12288×30% ≈
# 3686 MiB）；读不到显卡容量时退回这个实测值。
LLM_FREE_VRAM_PERCENT = 30
LLM_FREE_VRAM_FALLBACK_MIB = 3686
# 空闲多久自动卸载：自带大模型加载完不退出就会一直占着显存（12GB 单卡上这是真金白银），
# 客户端退出、被强行关掉、或者只是聊完一句就放着不管，都不该让它常驻。
# 与 TaskService.ENGINE_IDLE_LIMIT_SECONDS 同一个约定：3 分钟没有任何任务就交还显存
# （2026-11-09 从 10 分钟收到 3 分钟，单卡上显存等不起十分钟）。
LLM_IDLE_LIMIT_SECONDS = 180.0
# nvidia-smi 一次要几十到几百毫秒，界面刷新会反复问：结果全进程缓存 10 秒。
_VRAM_CACHE_SECONDS = 10.0
_VRAM_CACHE: tuple[float, dict[str, Any]] = (0.0, {})


class LocalLlamaServer:
    """管项目自带的 llama.cpp 推理服务（只绑回环），给智能体当大模型端点。"""

    def __init__(
        self,
        config: Mapping[str, Any] | None = None,
        *,
        paths: AppPaths | None = None,
        logger: Any = None,
    ) -> None:
        self.config = dict(config or {})
        self.paths = paths or AppPaths.from_environment()
        self.logger = logger

        # ---- 工具与权重的默认落点（全部在项目内，便携包能整目录带着走）--------
        self.tool_dir = PROJECT_ROOT / "tools" / "llama_cpp"
        self.binary = self.tool_dir / _SERVER_IMAGE
        self.gguf_dir = self.paths.models_dir / "llm" / "gguf"
        # ---- 手动起的服务（scripts\agent_llm.ps1）复用同一份 pid / 日志目录，
        # 两端互相接班，不会一个以为没起、又起第二个抢显存。
        self.run_dir = PROJECT_ROOT / "logs" / "agent_llm"
        self.pid_file = self.run_dir / "llama_server.pid"
        self.stdout_log = self.run_dir / "llama_server.out.log"
        self.stderr_log = self.run_dir / "llama_server.err.log"
        # 最近一次「用过它」的时间戳：模型加载完就一直占着显存、用户既关不掉也看不见，
        # 空闲满 LLM_IDLE_LIMIT_SECONDS 就自己停掉、当场归还显存（下次要用再拉起来）。
        self._last_used = time.monotonic()

    # ------------------------------------------------------------------ 解析
    def _note(self, message: str) -> None:
        if self.logger is not None:
            self.logger.info(message)

    def endpoint_parts(self) -> tuple[str, int]:
        """从 config 的 llm_endpoint 里取出主机与端口；解析不出来就用默认端口。"""
        raw = str(self.config.get("llm_endpoint") or "").strip()
        if not raw:
            return "127.0.0.1", DEFAULT_PORT
        candidate = raw.split("://", 1)[-1]
        candidate = candidate.split("/", 1)[0]
        host, _, port_text = candidate.rpartition(":")
        host = (host or "127.0.0.1").strip().strip("[]")
        try:
            port = int(port_text) if port_text else DEFAULT_PORT
        except ValueError:
            port = DEFAULT_PORT
        return host, port

    def is_loopback_endpoint(self) -> bool:
        host, _port = self.endpoint_parts()
        return host in _LOOPBACK

    def owns_endpoint(self) -> bool:
        """这个端点是不是「我们该管的那一个」：回环 + 我们自带的端口。

        用户把 llm_endpoint 指到自己的 Ollama 或别的机器上时，起停与探活都不是我们的事。
        """
        _host, port = self.endpoint_parts()
        return self.is_loopback_endpoint() and port == DEFAULT_PORT

    def gguf_path(self) -> Path:
        """配置里写死的权重路径优先，否则挑 models\\llm\\gguf 里最大的那个 GGUF。"""
        configured = str(self.config.get("llm_model_path") or "").strip()
        if configured:
            path = Path(configured)
            if not path.is_absolute():
                path = PROJECT_ROOT / path
            return path
        if not self.gguf_dir.is_dir():
            return self.gguf_dir / "（未下载）"
        candidates = sorted(
            (item for item in self.gguf_dir.glob("*.gguf") if item.is_file()),
            key=lambda item: item.stat().st_size,
            reverse=True,
        )
        return candidates[0] if candidates else self.gguf_dir / "（未下载）"

    def installed(self) -> bool:
        """工具与权重都在位（缺哪一样，界面与脚本都会说清楚缺什么）。"""
        return self.binary.is_file() and self.gguf_path().is_file()

    # ---------------------------------------------------------- 空闲自动卸载
    def mark_used(self) -> None:
        """记一笔「刚用过它」，空闲自动卸载的十分钟从这里重新数。"""
        self._last_used = time.monotonic()

    def idle_seconds(self) -> float:
        """距离上次用到它过了多久（秒）。没在跑时也照样计数，调用方自己先看 running。"""
        return max(0.0, time.monotonic() - float(getattr(self, "_last_used", time.monotonic())))

    def stop_if_idle(self, limit_seconds: float = LLM_IDLE_LIMIT_SECONDS) -> dict[str, Any]:
        """空闲满 limit_seconds 就把服务整个停掉、显存当场归还（只在真的空闲时动手）。

        正在聊天 / 正在加载权重时不能停：那会把用户眼前这一句打断。判断依据是
        ``idle_seconds()``——每次 ``ensure_running`` 与每次智能体处理一句话都会重新计时，
        而一次请求本身最长也就 llm_timeout_seconds（远小于十分钟），不会误杀。
        """
        if not self.owns_endpoint():
            return {"ok": False, "state": "external", "note": ""}
        if not self.port_open():
            self.pid_file.unlink(missing_ok=True)
            return {"ok": False, "state": "none", "note": ""}
        waited = self.idle_seconds()
        if waited < float(limit_seconds):
            return {"ok": False, "state": "busy", "note": f"才空闲 {int(waited)} 秒，没到自动卸载的时间"}
        stopped = self.stop()
        if stopped["ok"]:
            self._note(
                f"自带大模型空闲 {int(waited / 60)} 分钟没有任务，已自动卸载并退出进程，显存当场归还"
            )
            return {
                "ok": True,
                "state": "stopped",
                "note": (
                    f"自带大模型空闲 {int(waited / 60)} 分钟，已自动卸载退出（显存归还给生成引擎）；"
                    "下次聊天会自动重载权重。"
                ),
            }
        return stopped

    def sweep_orphans(self) -> tuple[int, ...]:
        """清掉「上一代客户端留下的、父进程已经没了」的 llama-server 孤儿进程。

        只认镜像名 llama-server.exe，而且要求它的父进程已经不在了：另一个还在跑的
        客户端拉起的、或者用户自己在终端里用脚本跑起来的（父进程还活着）都不动。
        客户端崩溃 / 被任务管理器结束后，显存就是被这些孤儿占着的，下次启动顺手收掉。
        """
        orphans = orphan_pids_by_image(_SERVER_IMAGE)
        swept: list[int] = []
        for pid in orphans:
            if self._image_of(pid) != _SERVER_IMAGE:
                continue
            killed = self._kill_pid(pid)
            if killed:
                swept.append(pid)
                self._note(f"清掉了上一代遗留的大模型服务孤儿进程（PID {pid}），显存已归还")
        if swept:
            recorded = self._pid_from_file()
            if recorded and not self._image_of(recorded):
                self.pid_file.unlink(missing_ok=True)
        return tuple(swept)

    # ------------------------------------------------------------------ 探活
    def port_open(self, *, timeout: float = 0.4) -> bool:
        _host, port = self.endpoint_parts()
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(timeout)
            return probe.connect_ex(("127.0.0.1", port)) == 0

    def model_ready(self, *, timeout: float = 1.5) -> bool:
        """模型真的能用了吗——「端口在听」不等于「能用」。

        llama-server 是**先绑端口、后加载权重**：加载期间 ``/v1/models`` 回 503
        「Loading model」。把端口当成就绪，聊天就会在加载途中撞上 503 被判成「大模型不可用」，
        所以能不能用必须问模型本身。
        """
        host, port = self.endpoint_parts()
        request = urllib.request.Request(f"http://{host}:{port}/v1/models", method="GET")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return int(getattr(response, "status", 200) or 200) == 200
        except urllib.error.HTTPError:
            return False  # 503 = 还在加载权重；404 之类说明端口上挂的不是这个服务
        except (OSError, urllib.error.URLError, ValueError):
            return False

    def _pid_from_file(self) -> int:
        try:
            return int(self.pid_file.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return 0

    def _image_of(self, pid: int) -> str:
        """用 tasklist 查这个 PID 的镜像名（不是进程名就绝不去杀）。"""
        if pid <= 0:
            return ""
        try:
            raw = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return ""
        for line in raw.splitlines():
            parts = [item.strip('"') for item in line.split('","')]
            if len(parts) >= 2 and parts[1] == str(pid):
                return parts[0].lower()
        return ""

    def status(self, *, probe_vram: bool = False) -> dict[str, Any]:
        """给界面的实况快照。``probe_vram=True`` 才现开一次 nvidia-smi（界面上要看的数字），
        其余调用方（包括测试里把子进程换掉的测试）只拿上次探到的值，绝不为状态去起进程。"""
        host, port = self.endpoint_parts()
        gguf = self.gguf_path()
        running = self.port_open()
        # running 只代表端口在听；能不能用要看模型加载完没有（加载期间回 503）。
        ready = running and self.model_ready()
        vram = self.vram_report() if probe_vram else self.cached_vram_report()
        target = self.free_vram_target_mib() if probe_vram else self.cached_free_vram_target_mib()
        return {
            "installed": self.installed(),
            "binary": str(self.binary),
            "gguf": str(gguf),
            "gguf_bytes": gguf.stat().st_size if gguf.is_file() else 0,
            "host": host,
            "port": port,
            "running": running,
            "ready": ready,
            "loading": running and not ready,
            "owned_endpoint": self.owns_endpoint(),
            "autostart": bool(self.config.get("llm_autostart", True)),
            "managed_pid": self._pid_from_file(),
            "download_hint": "跑一次 scripts\\agent_llm.ps1 -Setup（会下 llama.cpp 与 GGUF）",
            # 加载参数与显存实况：界面直接照着这几项写人话，不自己编数字。
            "ctx_size": LLM_CTX_SIZE,
            "kv_cache_type": LLM_KV_CACHE_TYPE,
            "free_vram_target_mib": target,
            "vram": vram,
            "vram_note": self.vram_shortfall_note(vram),
        }

    # ------------------------------------------------------------------ 参数
    def server_args(self) -> list[str]:
        """llama-server 的命令行：只绑回环、模型名跟 config 里的 llm_model 对齐。"""
        _host, port = self.endpoint_parts()
        model_name = str(self.config.get("llm_model") or "").strip() or "chenguai-llm"
        args = [
            str(self.binary),
            "--model",
            str(self.gguf_path()),
            "--alias",
            model_name,
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            # 12GB 单卡上这三条要同时成立：上下文 16K、KV 量化 q8_0、起完还留 ≥30% 空显存。
            # --fit on 让 llama.cpp 按当下空闲显存自己决定塞多少层进 GPU（大模型与
            # 图像/视频/音乐引擎抢同一块卡，写死 -ngl 99 会在显存被占满时起不来）；
            # --fit-target 是硬性留白（整卡 30%，12GB 卡 = 3686 MiB），留不到就少塞几层；
            # --fit-ctx 挡住「fit 为了塞进去偷偷把上下文砍小」——宁可慢一点也不降上下文。
            "--ctx-size",
            str(LLM_CTX_SIZE),
            "--cache-type-k",
            LLM_KV_CACHE_TYPE,
            "--cache-type-v",
            LLM_KV_CACHE_TYPE,
            "--fit",
            "on",
            "--fit-target",
            str(self.free_vram_target_mib()),
            "--fit-ctx",
            str(LLM_CTX_SIZE),
            "--flash-attn",
            "on",
            # Qwen3 的聊天模板烧在 GGUF 里，--jinja 才能拿到正确的多轮格式。
            "--jinja",
        ]
        return args

    def cached_free_vram_target_mib(self) -> int:
        """上次算出来的要求留白，不为此开进程（状态查询不能顺手起子进程）。"""
        cached = getattr(self, "_free_vram_target", None)
        return int(cached) if cached else LLM_FREE_VRAM_FALLBACK_MIB

    def free_vram_target_mib(self) -> int:
        """``--fit-target`` 的留白：整卡容量的 30%；读不到容量就退回 12GB 卡的实测值。"""
        cached = getattr(self, "_free_vram_target", None)
        if cached:
            return int(cached)
        target = LLM_FREE_VRAM_FALLBACK_MIB
        try:
            done = subprocess.run(  # noqa: S603 - 只调系统里的 nvidia-smi，不联网
                ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=6,
                check=False,
            )
            totals = [int(float(line.strip())) for line in (done.stdout or "").splitlines() if line.strip()]
            if totals:
                target = max(1, int(max(totals) * LLM_FREE_VRAM_PERCENT / 100))
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            self._note(f"读不到显卡容量，显存留白按 {LLM_FREE_VRAM_FALLBACK_MIB} MiB 算：{exc}")
        self._free_vram_target = target
        return target

    def vram_report(self) -> dict[str, Any]:
        """显卡现状（整卡 / 已用 / 空闲 / 要求的留白）。读不到就返回空，不猜。

        全进程共享、按 10 秒缓存：界面每次刷新都会问一遍，不该每次都去开一次 nvidia-smi。
        """
        global _VRAM_CACHE
        now = time.monotonic()
        if _VRAM_CACHE[1] and now - _VRAM_CACHE[0] < _VRAM_CACHE_SECONDS:
            return dict(_VRAM_CACHE[1])
        report: dict[str, Any] = {}
        try:
            done = subprocess.run(  # noqa: S603 - 只调系统里的 nvidia-smi，不联网
                [
                    "nvidia-smi",
                    "--query-gpu=memory.total,memory.used,memory.free",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=4,
                check=False,
            )
            rows = []
            for line in (done.stdout or "").splitlines():
                parts = [item.strip() for item in line.split(",")]
                if len(parts) < 3:
                    continue
                try:
                    rows.append(
                        {
                            "total": int(float(parts[0])),
                            "used": int(float(parts[1])),
                            "free": int(float(parts[2])),
                        }
                    )
                except ValueError:
                    continue
            if rows:
                biggest = max(rows, key=lambda row: row["total"])
                report = {**biggest, "target_free": self.free_vram_target_mib()}
        except (OSError, subprocess.SubprocessError) as exc:
            self._note(f"读不到显存占用：{exc}")
        _VRAM_CACHE = (now, report)
        return dict(report)

    def cached_vram_report(self) -> dict[str, Any]:
        """上一次探到的显存现状；不为此开新进程（测试里子进程常被我自己的护栏替掉）。"""
        return dict(_VRAM_CACHE[1])

    def vram_shortfall_note(self, report: Mapping[str, Any] | None = None) -> str:
        """空闲显存够不到要求时的人话说明（要求留白是为了不抢生成引擎的显存）。"""
        report = report if report is not None else self.cached_vram_report()
        if not report:
            return ""
        target = int(report.get("target_free") or 0)
        free = int(report.get("free") or 0)
        if target <= 0 or free >= target:
            return ""
        return (
            f"显卡现在只剩 {free} MiB 空闲，够不到要求留的 {target} MiB"
            f"（整卡 {report.get('total')} MiB，已用 {report.get('used')} MiB）——"
            "显存被别的程序占着，不是模型参数的问题。自带大模型会按这条要求只往显卡里塞塞得下的部分，"
            "回答会慢一些，但不会去抢生成图片 / 视频 / 音乐的显存；"
            "把占显存的程序关掉再发一次就快了。"
        )

    # ------------------------------------------------------------------ 拉起
    def _wait_for_ready(self, seconds: float, *, pid: int = 0) -> dict[str, Any]:
        """陪它等到模型能用；等不到就说「还在加载」。既不谎报可用，也不去起第二个实例抢显存。"""
        who = f"（PID {pid}）" if pid else ""
        if seconds <= 0:
            # 探测路径上用：踩下油门就走，界面靠「重新探测」自己把状态刷绿。
            return {
                "ok": True,
                "state": "loading",
                "note": f"自带大模型正在加载权重{who}，一般 30-90 秒，界面会自动重新探测并变可用",
            }
        deadline = time.monotonic() + float(seconds)
        while time.monotonic() < deadline:
            if self.model_ready():
                return {"ok": True, "state": "running", "note": ""}
            time.sleep(1.0)
        if self.model_ready():
            return {"ok": True, "state": "running", "note": ""}
        return {
            "ok": True,
            "state": "loading",
            "note": (
                f"自带大模型还在加载权重{who}（一般 30-90 秒），{int(seconds)} 秒内没等到；"
                "等它加载完再发一次就行，界面也会自己重新探测"
            ),
        }

    def ensure_running(self, *, wait_seconds: float = 45.0) -> dict[str, Any]:
        """需要大模型时确保它**能用**：在跑就什么都不做，缺工具/权重就老实说明。

        返回值只描述「这次做了什么」，调用方（智能体）据此在界面上写清楚原因，
        绝不假装大模型可用。state 的含义：running 能用；starting / loading 油门踩下了、
        权重还在加载；refused / external / missing_tool / missing_model / autostart_off
        是我们帮不上忙的原因。
        """
        host, port = self.endpoint_parts()
        if self.port_open():
            if not self.owns_endpoint():
                # 端点归别人管（用户自己的 Ollama、别的机器）：在听就没我们的事。
                return {"ok": True, "state": "running", "note": ""}
            if self.model_ready():
                return {"ok": True, "state": "running", "note": ""}
            # llama-server 先绑端口后加载权重：这时端口是开着的，但模型还不能用。
            # 唯一正确的做法是陪它等，绝对不能起第二个实例去抢同一块显存。
            return self._wait_for_ready(wait_seconds, pid=self._pid_from_file())
        if not self.is_loopback_endpoint():
            return {
                "ok": False,
                "state": "refused",
                "note": f"大模型端点 {host}:{port} 不是回环地址，本程序拒绝自动拉起服务（只允许 127.0.0.1）",
            }
        if port != DEFAULT_PORT:
            # 用户把端点到自己的 Ollama / 别的服务器上：那个端口归他管，我们不插手。
            return {
                "ok": False,
                "state": "external",
                "note": (
                    f"端点端口 {port} 不是本程序自带的 {DEFAULT_PORT}，不代为启动；"
                    f"要么自己保证 {host}:{port} 可用，要么把 llm_endpoint 改回 http://127.0.0.1:{DEFAULT_PORT}/v1"
                ),
            }
        if not self.binary.is_file():
            return {
                "ok": False,
                "state": "missing_tool",
                "note": f"项目内没找到推理程序 {self.binary}；跑一次 scripts\\agent_llm.ps1 -Setup 自动下载",
            }
        gguf = self.gguf_path()
        if not gguf.is_file():
            return {
                "ok": False,
                "state": "missing_model",
                "note": f"{self.gguf_dir} 里没有 GGUF 权重；跑一次 scripts\\agent_llm.ps1 -Setup 自动下载（约 4.7GB）",
            }
        if not bool(self.config.get("llm_autostart", True)):
            return {
                "ok": False,
                "state": "autostart_off",
                "note": "已关闭自动拉起（config 里 llm_autostart=false）：需要时双击 scripts\\run_agent_llm.bat 或跑 scripts\\agent_llm.ps1 -Start",
            }

        existing = self._pid_from_file()
        if existing and self._image_of(existing) == _SERVER_IMAGE:
            # 已经在跑（或正在加载权重）：绝不重复起第二个实例去抢同一块显存。
            return self._wait_for_ready(wait_seconds, pid=existing)

        self.run_dir.mkdir(parents=True, exist_ok=True)
        # 显存够不够（要求留整卡 30%）只在 status() 里查一次，界面会说清楚；
        # 这里不起额外的探测进程：正在起服务的这条路径上 subprocess.Popen 常被我自己的测试接管。
        flags = 0
        if os.name == "nt":
            flags = (
                getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
                | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            )
        # 日志句柄直接交给子进程：服务崩了也有 llama.cpp 的原始输出可查。
        with self.stdout_log.open("ab") as out, self.stderr_log.open("ab") as err:
            marker = "\n=== 由「辰鹄 AI 创作中心」按需拉起 llama-server ===\n".encode("utf-8")
            out.write(marker)
            err.write(marker)
            out.flush()
            err.flush()
            try:
                process = subprocess.Popen(  # noqa: S603 - 路径来自项目内 tools 目录
                    self.server_args(),
                    cwd=str(self.tool_dir),
                    stdout=out,
                    stderr=err,
                    stdin=subprocess.DEVNULL,
                    creationflags=flags,
                    close_fds=True,
                )
            except OSError as exc:
                return {"ok": False, "state": "failed", "note": f"拉起自带大模型失败：{exc}"}
        self.pid_file.write_text(str(process.pid), encoding="utf-8")
        # 护栏：把它挂进「随本客户端结束一起被内核收掉」的作业对象。客户端崩溃或被
        # 强行结束时，来不及跑清理代码也不会留下一个常驻显存的 llama-server。
        assign_pid_to_kill_job(process.pid, logger=self.logger)
        self.mark_used()
        self._note(f"已拉起自带大模型服务（PID {process.pid}，127.0.0.1:{self.endpoint_parts()[1]}）")

        if wait_seconds <= 0:
            return {
                "ok": True,
                "state": "starting",
                "note": (
                    f"已自动拉起自带大模型服务（PID {process.pid}），"
                    "首次加载权重要 30-90 秒，界面会自动重新探测并变可用"
                ),
            }
        deadline = time.monotonic() + float(wait_seconds)
        while time.monotonic() < deadline:
            if self.model_ready():
                return {"ok": True, "state": "started", "note": f"自带大模型服务已就位（PID {process.pid}）"}
            if process.poll() is not None:
                return {
                    "ok": False,
                    "state": "crashed",
                    "note": (
                        f"自带大模型服务起不来（退出码 {process.returncode}），"
                        f"详见 {self.stderr_log}"
                    ),
                }
            time.sleep(0.5)
        # 进程还活着、只是慢：说「还在加载」比报「起不来」诚实。
        return self._wait_for_ready(0.0, pid=process.pid)

    # -------------------------------------------------------- 交还显存给生成引擎
    def release_vram(self) -> dict[str, Any]:
        """把大模型占的显存整个还给生成引擎（智能体排队生成前调）。

        llama.cpp b12182 实测**没有** ``/unload`` 与 ``/load`` 这两个路由（都回 404），
        所以唯一的办法是把我们自己起的那个进程停掉——整块显存当场归还。下一次聊天要用时
        ``ensure_running()`` 会把它重新拉起来，代价是 30-90 秒重新加载权重：这跟以前
        Ollama 的 ``keep_alive=0`` 是同一笔交易，只是这次不用装第三方服务。
        """
        if not self.owns_endpoint():
            return {"ok": False, "state": "external", "note": "端点不是本程序自带的服务，交给它自己的卸载接口"}
        if not self.port_open():
            # 本来就没在跑：显存里没有大模型，别对用户吹「已经腾出来了」。
            return {"ok": False, "state": "none", "note": ""}
        stopped = self.stop()
        if stopped["ok"]:
            return {
                "ok": True,
                "state": "stopped",
                "note": "已停掉自带大模型服务，整块显存让给生成引擎；下次聊天会自动重载权重（30-90 秒）。",
            }
        return stopped

    # ------------------------------------------------------------------ 停掉
    def stop(self) -> dict[str, Any]:
        """只停我们写进 pid 文件的那个进程；端口上挂着别人的服务一律不动。"""
        pid = self._pid_from_file()
        if not pid:
            return {"ok": False, "state": "none", "note": "没有由本程序启动过的大模型服务"}
        image = self._image_of(pid)
        if image != _SERVER_IMAGE:
            self.pid_file.unlink(missing_ok=True)
            return {
                "ok": False,
                "state": "foreign",
                "note": f"PID {pid} 现在跑的是 {image or '别的进程'}，不是 {_SERVER_IMAGE}，不碰它",
            }
        if not self._kill_pid(pid):
            return {"ok": False, "state": "failed", "note": f"停止失败：taskkill 没能结束 PID {pid}"}
        self.pid_file.unlink(missing_ok=True)
        return {"ok": True, "state": "stopped", "note": f"已停止自带大模型服务（PID {pid}）"}

    def _kill_pid(self, pid: int) -> bool:
        """用 taskkill 把它连同子进程一起结束。进程本来就不在了也算成功（目的已达成）。"""
        try:
            done = subprocess.run(  # noqa: S603 - 只调系统 taskkill，参数是我们自己拼的 PID
                ["taskkill", "/PID", str(int(pid)), "/T", "/F"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        if done.returncode == 0:
            return True
        # 失败但进程已经不在（PID 复用、竞态）：目的达成，不谎报失败。
        return not self._image_of(int(pid))


def read_pid_file(run_dir: Path) -> int:
    """脚本（PowerShell）与我们共用的 pid 约定，方便两端互相接班。"""
    try:
        return int((run_dir / "llama_server.pid").read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0


def status_snapshot(
    config: Mapping[str, Any],
    paths: AppPaths | None = None,
    *,
    probe_vram: bool = False,
) -> dict[str, Any]:
    """给能力探测用的一行状态，不抛异常。显存数字只有界面要（见 status 的说明）。"""
    try:
        return LocalLlamaServer(config, paths=paths).status(probe_vram=probe_vram)
    except Exception as exc:  # noqa: BLE001 - 状态查询不能把智能体带崩
        return {"installed": False, "running": False, "error": str(exc)}


__all__ = ["DEFAULT_PORT", "LocalLlamaServer", "read_pid_file", "status_snapshot"]
