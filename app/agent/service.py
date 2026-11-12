"""AI 智能体服务层：网页端与桌面端共用同一套“理解 → 路由 → 排队”逻辑。

这里不直接调用任何模型权重：生成命令最终变成一条 TaskRequest 进入同一条串行队列，
聊天/文案走本机 OpenAI 兼容端点（默认是项目自带的 llama.cpp 服务，见 app/agent/local_server.py），
语音走系统 SAPI。
所有失败都如实回报（error / notes / degraded），界面照着说，不粉饰。
"""

from __future__ import annotations

import logging
import random
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

from app.agent import documents as docs
from app.agent import speech
from app.agent.llm import LlmUnavailable, LocalLlmClient
from app.agent.local_server import LocalLlamaServer
from app.agent.router import (
    ACTION_CHAT,
    ACTION_COPY,
    ACTION_DOCUMENT,
    ACTION_GENERATE,
    ACTION_HELP,
    ACTION_RETIRED,
    ACTION_SPEAK,
    HELP_TEXT,
    AgentCommand,
    build_task_payload,
    parse_command,
    system_prompt,
)
from app.core.artifacts import copy_artifacts
from app.core.paths import AppPaths
from app.tasks.models import TaskRequest

logger = logging.getLogger("chenguai.agent")

# 2026-11-10 换型记录：文生图换成 Sana-1.6B 多语言（引擎 sana-multiling，走 .venv-image 子进程），
# 图生图保留 Wan2.1 VACE（wan-i2i，单帧出图），文生图不再是 Wan 的单帧副作用（wan-t2i 已下架）。
# 一次 1 张，旧 SD1.5 一次 4 张的 IMAGE_BATCH_SIZE 随旧引擎退场；AI 音乐保留 ACE-Step 1.5
# （CPU 换入档）。三类生成（图片/视频/音乐）与智能体共用同一条串行队列。


def _rand_seed() -> int:
    """与 app/ui/widgets/common.py 的 ``rand_seed`` 同一取值范围。

    这里不 import 那个模块：它在 Qt 层（PySide6），智能体服务层必须保持无 Qt，
    否则网页控制台会被迫依赖桌面端界面库。
    """
    return random.randint(0, 2**31 - 1)


# 各生成通道优先使用的引擎 id；注册表里的 id 若不同，再按关键字模糊匹配。
# 文生图落在 Sana，图生图/视频落在 Wan2.1，音乐落在 ACE-Step。
_ENGINE_CANDIDATES: dict[str, tuple[str, ...]] = {
    "t2v": ("wan-t2v",),
    "i2v": ("wan-i2v",),
    "t2i": ("sana-multiling",),
    "i2i": ("wan-i2i",),
    "music": ("ace-step-15",),
}
# 关键字兜底：t2i 必须带 "sana"，否则注册表里换了 id 就再也匹配不上（"wan" 对
# sana-multiling 这个 id 完全不起作用，兜底会失效）。
_ENGINE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "t2v": ("t2v", "wan"),
    "i2v": ("i2v", "wan"),
    "t2i": ("t2i", "sana"),
    "i2i": ("i2i", "wan"),
    "music": ("music", "ace"),
}
# 能力条与说明里用到的板块名称，顺序即界面顺序（桌面端与 Web 端共用这一份）。
_CAPTION_LABELS: tuple[tuple[str, str], ...] = (
    ("t2i", "文生图"),
    ("i2i", "图生图"),
    ("t2v", "文生视频"),
    ("i2v", "图生视频"),
    ("music", "AI 音乐"),
)
# 默认端点＝项目自带的 llama.cpp 服务（tools\llama_cpp\llama-server.exe，只绑 127.0.0.1）。
# 用户自己的 Ollama 之类的外部端点照样能用：改 config 里的 llm_endpoint 就行。
_DEFAULT_LLM_ENDPOINT = "http://127.0.0.1:11435/v1"
_MAX_HISTORY_MESSAGES = 10
_MAX_MESSAGE_CHARS = 4000
# 写文档比聊天长得多：现在自带服务的上下文是 16K，给到 1200-1800 token 既够一份
# 千字方案也不会让 8B 模型无限说下去（超出部分本来也会被 documents.py 截掉）。
_DOCUMENT_MAX_TOKENS: dict[str, int] = {
    docs.FORMAT_DOCX: 1800,
    docs.FORMAT_PPTX: 1200,
    docs.FORMAT_XLSX: 1400,
}


class AgentService:
    """把一句话变成一次可执行动作（生成任务 / 聊天回复 / 文案 / 语音播报 / 生成文档文件）。"""

    def __init__(
        self,
        *,
        task_service: Any,
        engine_manager: Any,
        config: dict[str, Any] | None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.task_service = task_service
        self.engine_manager = engine_manager
        self.config = dict(config or {})
        self.logger = logger or logging.getLogger("chenguai.agent")
        settings = self.config.get("agent")
        settings = settings if isinstance(settings, dict) else {}
        self.endpoint = str(
            self.config.get("llm_endpoint") or settings.get("llm_endpoint") or _DEFAULT_LLM_ENDPOINT
        )
        self.model = str(self.config.get("llm_model") or settings.get("llm_model") or "")
        self.llm = LocalLlmClient(
            self.endpoint,
            self.model,
            # 大模型冷启动（把 6-8GB 权重搬进显存）本身就可能要十几到几十秒，
            # 90 秒的旧上限会在第一次对话时直接判成「连不上大模型」。
            timeout=float(self.config.get("llm_timeout_seconds", 240.0)),
        )
        # 项目自带的大模型服务（llama.cpp + 项目内 GGUF）：端点连不上时按需把它拉起来，
        # 这样重启电脑之后不用再手动跑脚本，界面过一会儿自己就变绿。
        self.local_llm = LocalLlamaServer(self.config, logger=self.logger)
        self.tts_voice = str(settings.get("tts_voice", ""))
        self.tts_rate = int(settings.get("tts_rate", 0))
        # 空闲自动卸载：TaskService 的收割循环在十分钟没有任何任务时叫这里，把常驻显存的
        # 大模型服务停掉。五个生成模块的引擎由同一处收割，它们的模型与生成逻辑一行没改。
        register_hook = getattr(task_service, "register_idle_hook", None)
        if callable(register_hook):
            try:
                register_hook(self.reap_idle_llm)
            except Exception as exc:  # noqa: BLE001 - 注册失败不能拖垮智能体本身
                self.logger.warning("注册大模型空闲卸载钩子失败：%s", exc)

    # ------------------------------------------------------- 自带大模型服务
    def _ensure_local_llm(self, *, wait_seconds: float) -> dict[str, Any]:
        """要用大模型之前确保它**能用**：没在跑就把项目自带的服务拉起来。

        ``wait_seconds=0`` 表示只踩下油门不等待（能力探测用，界面随后靠「重新探测」自己刷
        绿）；聊天是阻塞调用，给足加载权重的时间。返回 ``ensure_running`` 的结果，其中
        ``state == "running"`` 才算真能用——**端口在听但权重还在加载时不能说「可用」**，
        因为 llama-server 是先绑端口后加载，加载期间请求会撞 503。
        """
        if not self.local_llm.owns_endpoint():
            # 用户把端点指到自己的 Ollama 或别的机器上：起停不归我们管，交给 llm 客户端去连。
            return {"ok": True, "state": "external_owner", "note": ""}
        result = self.local_llm.ensure_running(wait_seconds=wait_seconds)
        self.llm.invalidate()
        if not result["ok"]:
            self.logger.warning("自带大模型服务没能就位：%s", result.get("note"))
        return result

    # -------------------------------------------------- 空闲卸载 / 退出收尾
    def reap_idle_llm(self, idle_seconds: float = 0.0) -> str:
        """十分钟没有任何任务 → 把自带大模型服务整个停掉，显存当场归还。

        由 TaskService 的空闲收割循环调用（桌面端与网页端各自管自己拉起的那一个）。
        正在聊天时不会动手：每次处理一句话都会重新计十分钟，一次请求最长也就
        ``llm_timeout_seconds``，远够不到这条线。
        """
        result = self.local_llm.stop_if_idle()
        if not result.get("ok"):
            return ""
        self.llm.invalidate()
        note = str(result.get("note") or "已卸载自带大模型服务")
        self.logger.info("空闲 %s 秒无任务：%s", int(idle_seconds), note)
        return note

    def stop_llm_for_exit(self) -> str:
        """客户端退出：把它拉起的大模型服务一起结束，不留孤儿进程占显存。"""
        if not self.local_llm.owns_endpoint():
            return ""
        if not self.local_llm.port_open():
            return ""
        stopped = self.local_llm.stop()
        if stopped.get("ok"):
            note = str(stopped.get("note") or "")
            self.logger.info("退出收尾：%s", note)
            return note
        if stopped.get("note"):
            self.logger.warning("退出收尾时大模型服务没能停掉：%s", stopped["note"])
        return ""

    def sweep_orphan_llm(self) -> tuple[int, ...]:
        """启动时清掉上一代客户端遗留的大模型孤儿进程（父进程已经没了的那些）。"""
        swept = self.local_llm.sweep_orphans()
        if swept:
            self.logger.info(
                "启动清扫：结束了遗留的大模型孤儿进程 %s，显存已归还",
                "、".join(f"PID {pid}" for pid in swept),
            )
        return swept

    # ------------------------------------------------------------------ 能力
    def engine_ids(self) -> dict[str, str]:
        """解析当前已注册的生成引擎（智能体只能路由到真实存在的引擎）。"""
        try:
            registered = [status.engine_id for status in self.engine_manager.list_statuses()]
        except Exception as exc:  # pragma: no cover - 引擎管理器异常不应拖垮智能体
            self.logger.warning("读取引擎列表失败：%s", exc)
            registered = []
        resolved: dict[str, str] = {}
        taken: set[str] = set()
        # 第一轮只认精确候选：四个 Wan 引擎共享 "wan" 这个关键字，若边遍历边兜底，
        # 排在前面的 t2v 会把只注册了 wan-t2i 的槽位抢走（t2i 反而报「没有引擎」）。
        for key, candidates in _ENGINE_CANDIDATES.items():
            for candidate in candidates:
                if candidate in registered and candidate not in taken:
                    resolved[key] = candidate
                    taken.add(candidate)
                    break
        # 第二轮才轮到关键字模糊匹配，且只分配还没被精确匹配用掉的引擎。
        for key, candidates in _ENGINE_CANDIDATES.items():
            if key in resolved:
                continue
            for engine_id in registered:
                if engine_id in taken:
                    continue
                lowered = engine_id.lower()
                if any(word in lowered for word in _ENGINE_KEYWORDS[key]):
                    resolved[key] = engine_id
                    taken.add(engine_id)
                    break
        return resolved

    def engine_states(self) -> dict[str, str]:
        """引擎 id → 状态（stopped/starting/ready/busy/failed）。

        界面需要它来区分两件完全不同的事：「这个板块没有引擎」（红）和
        「引擎已经注册、只是现在没加载」（中性灰 + 未启动）。后者不是故障，
        任务提交时会自动拉起，把两者混为一谈就是「网页版说没有引擎」的来源。
        """
        try:
            return {status.engine_id: status.state for status in self.engine_manager.list_statuses()}
        except Exception as exc:  # pragma: no cover - 引擎管理器异常不应拖垮智能体
            self.logger.warning("读取引擎状态失败：%s", exc)
            return {}

    def capabilities(self) -> dict[str, Any]:
        """界面能力清单：有什么、缺什么，一次说清。

        help 是给「帮助」按钮收着的那段完整说明（首屏不再铺开）；notes 是会显示的人话
        提示（缺什么、怎么补）；diagnostics 只有显存读数这类技术细节，界面上开了
        「调试信息」才拼出来。
        """
        engines = self.engine_ids()
        tts = speech.status()
        notes: list[str] = []
        # diagnostics 只装技术读数（显存数字、加载参数）：界面默认不显示，
        # 两端各有一个「调试信息」开关才把它拼出来。缺这个字段的老界面也不会崩。
        diagnostics: list[str] = []
        if not engines:
            notes.append("当前没有可用的生成引擎：智能体只能聊天、写文案和朗读")
        for key, label in _CAPTION_LABELS:
            if key not in engines:
                notes.append(f"{label}引擎未注册（检查 engines_enabled 与对应 venv / 权重）")
        if not tts["available"]:
            notes.append(f"语音播报不可用：{tts['note']}")
        llm = self.llm.status()
        # 显存数字的唯一出口：这里现探一次（内部缓存 10 秒），两端 UI 才不会各自编数。
        # 探到的数只进 diagnostics（默认不显示），真正会影响使用的告警才留在 notes。
        local_llm = self.local_llm.status(probe_vram=True)
        if local_llm.get("running"):
            vram = local_llm.get("vram") or {}
            if vram:
                # 纯技术参数（上下文、KV 量化、整卡/已用/空闲 MiB）：两端界面默认不显示，
                # 点开「调试信息」开关才看得见——正常用户看的是能力胶囊，不是显存读数。
                diagnostics.append(
                    f"自带大模型加载参数：上下文 {local_llm.get('ctx_size')}、KV 量化 {local_llm.get('kv_cache_type')}；"
                    f"显卡整卡 {vram['total']} MiB，已用 {vram['used']} MiB，空闲 {vram['free']} MiB"
                    f"（要求至少留 {vram.get('target_free')} MiB）"
                )
            if local_llm.get("vram_note"):
                # 这条只在「空闲显存够不到要求」时才有内容：那是真问题，不藏进调试信息。
                notes.append(str(local_llm["vram_note"]))
        booting = False
        if not llm["available"]:
            # 连上不了不等于「没装」：更多时候是自带服务没在跑（重启电脑后就是这样）。
            # 这里踩下油门把它拉起来但不等待，界面靠「重新探测」在权重加载完后自动变绿。
            result = self._ensure_local_llm(wait_seconds=0.0)
            boot_note = str(result.get("note") or "")
            if boot_note:
                notes.append(boot_note)
            # 「刚拉起来 / 还在加载权重」都算在路上，这时候不能说大模型不可用吓用户。
            booting = bool(result["ok"]) and str(result.get("state")) in {"starting", "loading"}
            if result["ok"]:
                llm = self.llm.status()
        if not llm["available"] and not booting:
            notes.append(f"聊天与文案需要本机大模型：{llm['note']}")
        return {
            # UI 槽位键约定：两端界面（agent_page.py / agent.js）里文生图槽位叫
            # "image"，而任务路由按 command.mode（"t2i"）查引擎。能力清单出口
            # 做一次性改名，路由内部仍用 t2i，两边都不会错位。
            "engines": {("image" if key == "t2i" else key): engine_id for key, engine_id in engines.items()},
            "engine_states": self.engine_states(),
            "llm": llm,
            "local_llm": local_llm,
            "tts": tts,
            "notes": notes,
            "diagnostics": diagnostics,
            "help": HELP_TEXT,
        }

    # ------------------------------------------------------------------ 执行
    def _clean_history(self, history: Iterable[dict[str, Any]] | None) -> list[dict[str, str]]:
        cleaned: list[dict[str, str]] = []
        for item in list(history or [])[-_MAX_HISTORY_MESSAGES:]:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "")
            content = str(item.get("content") or "")[:_MAX_MESSAGE_CHARS]
            if role in ("user", "assistant") and content.strip():
                cleaned.append({"role": role, "content": content.strip()})
        return cleaned

    def _chat_reply(self, message: str, history: list[dict[str, str]], *, copy_mode: bool) -> dict[str, Any]:
        # 聊天前先确保自带的大模型服务**能用**：没跑就拉起来，并给它把权重加载进显存的时间
        # （冷启动几十秒，比直接回一句「连不上大模型」有用得多）。注意「端口在听」不等于能用：
        # llama-server 先绑端口后加载权重，加载途中发请求只会撞上 503。
        boot = self._ensure_local_llm(wait_seconds=90.0)
        boot_note = str(boot.get("note") or "")
        ready = bool(boot.get("ok")) and str(boot.get("state")) in {"running", "started", "external_owner"}
        if not ready:
            # 模型还没到位：说清楚在等什么、缺什么该跑哪条命令，绝不硬发请求再甩个网络错误。
            label = "文案" if copy_mode else "聊天"
            return {
                "reply": (
                    f"{label}功能需要本机大模型，现在还不能直接用：{boot_note}\n"
                    "我仍然可以直接执行生成命令（例如「画一张雨夜街头，16:9」）和语音朗读。"
                ),
                "model": "",
                "degraded": True,
                "error": boot_note,
                "notes": [boot_note] if boot_note else [],
            }
        try:
            messages = [{"role": "system", "content": system_prompt()}]
            messages.extend(history)
            prompt = message
            if copy_mode:
                prompt = f"请根据下面的需求直接产出可用文案（给标题与正文，不要解释）：\n{message}"
            messages.append({"role": "user", "content": prompt})
            text = self.llm.chat(messages)
            return {
                "reply": text,
                "model": self.llm.resolve_model(),
                "degraded": False,
                "error": "",
                "notes": [boot_note] if boot_note else [],
            }
        except LlmUnavailable as exc:
            hint = (
                "文案功能需要本机大模型"
                if copy_mode
                else "聊天功能需要本机大模型"
            )
            extra = f"\n关于自带大模型：{boot_note}" if boot_note else ""
            return {
                "reply": (
                    f"{hint}，现在连不上：{exc}{extra}\n"
                    "我仍然可以直接执行生成命令（例如「画一张雨夜街头，16:9」）和语音朗读。"
                ),
                "model": "",
                "degraded": True,
                "error": str(exc),
                "notes": [boot_note] if boot_note else [],
            }
        except Exception as exc:  # noqa: BLE001 - 模型返回千奇百怪，界面要的是可读回执
            self.logger.exception("大模型调用失败")
            return {"reply": f"大模型调用失败：{exc}", "model": "", "degraded": True, "error": str(exc)}

    def _speak(self, text: str) -> dict[str, Any]:
        try:
            result = speech.speak(text, voice=self.tts_voice, rate=self.tts_rate)
        except speech.SpeechUnavailable as exc:
            return {"reply": f"语音播报失败：{exc}", "error": str(exc), "speech": {}}
        suffix = "（文本过长，只念了前 1200 字）" if result.get("truncated") else ""
        return {
            "reply": f"已用系统语音「{result['voice']}」念完 {result['characters']} 字{suffix}",
            "error": "",
            "speech": result,
        }

    def _free_llm_vram(self) -> tuple[bool, str]:
        """排队生成前把大模型占的显存腾出来：12GB 单卡上不腾，Worker 起手就是「空闲显存不足」。

        自带服务（llama.cpp b12182）**没有** ``/unload``、``/load`` 路由（实测都回 404），
        所以只能把我们自己起的那个进程停掉，整块显存当场归还；下次聊天要用时
        ``ensure_running()`` 会把它重新拉起来（代价是 30-90 秒重载权重）——这跟以前
        Ollama 的 ``keep_alive=0`` 是同一笔交易。端点是用户自己的 Ollama 时仍走原生接口。
        """
        if self.local_llm.owns_endpoint():
            result = self.local_llm.release_vram()
            if result["ok"]:
                return True, str(result.get("note") or "已让本机大模型交还显存给生成引擎。")
            if str(result.get("state")) != "none":
                self.logger.info("自带大模型没能交还显存：%s", result.get("note"))
            return False, ""
        if bool(self.llm.unload()):
            return True, "已让本机大模型交还显存给生成引擎。"
        return False, ""

    def _generate(self, command: AgentCommand, ref_image: str) -> dict[str, Any]:
        engines = self.engine_ids()
        try:
            payload = build_task_payload(command, engine_ids=engines, ref_image=ref_image)
        except ValueError as exc:
            return {
                "error": str(exc),
                "reply": f"这条命令暂时不能排进队列：{exc}",
                "tasks": [],
            }
        # 生成引擎和本机大模型共用同一块显存：排队的这一刻先把模型请出显存，
        # 否则队列 Worker 起手就报「空闲显存不足」。交还是尽力而为，失败只在回执里说明。
        freed, freed_note = self._free_llm_vram()
        task_type = payload["task_type"]
        # 智能体一次排 1 条（图片走 Wan 单帧，一次 1 张；旧 SD1.5「说一句出 4 张」已随引擎退场）。
        batch_size = 1
        locked_seed = payload["parameters"].get("seed")
        records = []
        submit_errors: list[str] = []
        for index in range(batch_size):
            parameters = dict(payload["parameters"])
            try:
                records.append(
                    self.task_service.submit_task(
                        TaskRequest(
                            task_type=payload["task_type"],
                            engine_id=payload["engine_id"],
                            prompt=payload["prompt"],
                            parameters=parameters,
                        )
                    )
                )
            except Exception as exc:  # noqa: BLE001 - 校验失败的原因直接给用户看
                self.logger.exception("智能体提交任务失败（第 %d 条）", index + 1)
                submit_errors.append(f"第 {index + 1} 条提交失败：{exc}")
        if not records:
            return {
                "error": submit_errors[0] if submit_errors else "提交失败",
                "reply": "提交失败："
                + ("；".join(submit_errors) if submit_errors else "未知原因"),
                "tasks": [],
            }
        label = {"image": "图片", "video": "视频", "music": "音乐"}[task_type]
        count_note = f" {len(records)} 条" if batch_size > 1 else ""
        seed_note = ""
        if isinstance(locked_seed, int) and not isinstance(locked_seed, bool):
            seed_note = f"（种子锁定 {int(locked_seed)}）"
        elif task_type == "video":
            seed_note = "（随机种子，每次画面不同）"
        elif task_type == "image":
            seed_note = "（随机种子，每次出图不同）"
        error_tail = ("部分未成功：" + "；".join(submit_errors) + "。") if submit_errors else ""
        return {
            "error": "；".join(submit_errors),
            "reply": (
                f"已排入队列：{label}{count_note} · 引擎 {records[0].request.engine_id}"
                f" · 提示词「{records[0].request.prompt}」{seed_note}"
                f"（任务 {records[0].task_id[:8]} 起）。生成进度可以在任务流里看。"
                + error_tail
                + (freed_note if freed else "")
            ),
            "freed_vram": freed,
            "tasks": [
                {
                    "task_id": record.task_id,
                    "task_type": record.request.task_type,
                    "engine_id": record.request.engine_id,
                    "status": record.status.value,
                    "prompt": record.request.prompt,
                    "parameters": record.request.parameters,
                }
                for record in records
            ],
        }

    # ------------------------------------------------------------------ 文档
    def _document_output_dir(self) -> Path:
        """文档写进哪个目录：与网页端 ``/outputs/``、worker 的输出目录同一份来源。

        优先 ``config["output_dir"]``，否则回退 ``AppPaths`` 默认——跟 app/web/server.py
        里 ``WebApplication.output_dir`` 的取法严格一致，否则会出现「文件写出来了但
        网页端下载不到」。
        """
        configured = str(self.config.get("output_dir", "")).strip()
        if configured:
            return Path(configured).expanduser()
        return AppPaths.from_environment().output_dir

    def _mirror_document(self, path: Path) -> list[dict[str, str]]:
        """按生成产物的同一套规则再复制：工具内 ``output\\`` → 用户自选的自动另存目录。

        直接复用 app/core/artifacts.py 的 ``copy_artifacts``（重名自动加 -1、-2，失败只记
        日志不抛异常），镜像目录取 TaskService 上当前的两个槽位，所以用户在设置页改完
        立即生效，跟图片/视频/音乐的产物一模一样。
        """
        targets: list[str] = []
        seen: set[str] = set()
        for raw in (
            getattr(self.task_service, "project_output_dir", ""),
            getattr(self.task_service, "auto_save_dir", ""),
        ):
            text = str(raw or "").strip()
            if not text:
                continue
            key = str(Path(text).expanduser().resolve()).casefold()
            if key in seen:
                continue
            seen.add(key)
            targets.append(text)
        if not targets:
            # 拿不到 TaskService 上的槽位（独立跑智能体、或测试里的桩）时退回默认的项目内
            # output\，保证「工具里也有一份」这条规则不因入口不同而失效。
            fallback = AppPaths.from_environment().project_output_dir
            if str(fallback).casefold() != str(path.parent).casefold():
                targets.append(str(fallback))
        mirrored: list[dict[str, str]] = []
        for text in targets:
            mirrored.extend(copy_artifacts([str(path)], text))
        return mirrored

    def _write_document(self, command: AgentCommand) -> dict[str, Any]:
        """「做个表格/写份方案/做个 PPT」：大模型写结构 → 本机写库落成 .docx/.xlsx/.pptx。

        全程离线：内容来自本机 llama.cpp 端点，落盘用 python-docx / openpyxl / python-pptx
        （纯本地写 OOXML，库随便携包一起带走）。不排队、不碰显存、不碰五个生成模块和
        大模型的加载参数——写文档只借聊天那条通道，写文件用的是 CPU。
        """
        fmt = command.document_format or docs.FORMAT_DOCX
        topic = (command.prompt or "").strip() or "未命名文档"
        notes: list[str] = []
        model = ""
        degraded = False

        boot = self._ensure_local_llm(wait_seconds=90.0)
        boot_note = str(boot.get("note") or "")
        ready = bool(boot.get("ok")) and str(boot.get("state")) in {"running", "started", "external_owner"}
        if boot_note:
            notes.append(boot_note)

        text = ""
        if ready:
            try:
                text = self.llm.chat(
                    [
                        {"role": "system", "content": docs.document_system_prompt(fmt)},
                        {"role": "user", "content": docs.document_instruction(fmt, topic)},
                    ],
                    temperature=0.7,
                    max_tokens=_DOCUMENT_MAX_TOKENS.get(fmt, 1400),
                )
                model = self.llm.resolve_model()
            except LlmUnavailable as exc:
                degraded = True
                notes.append(f"大模型连不上，这次只按题目搭了骨架：{exc}")
            except Exception as exc:  # noqa: BLE001 - 模型输出千奇百怪，不能因为写作失败就没有文件
                self.logger.exception("写文档时调用大模型失败")
                degraded = True
                notes.append(f"大模型这次没写好（{exc}），只按题目搭了骨架")
        else:
            degraded = True
            notes.append(
                f"自带大模型还没就位（{boot_note or boot.get('state')}），这次只按题目搭了骨架"
            )

        if text.strip():
            plan = (
                docs.parse_table(text, fallback_title=topic)
                if fmt == docs.FORMAT_XLSX
                else docs.parse_outline(text, fallback_title=topic)
            )
        else:
            degraded = True
            plan = docs.skeleton_plan(topic, fmt)
            notes.append("模型没给出可用内容，文件里只按题目搭了结构，正文标了「（待补充）」")
        notes.extend(str(note) for note in (getattr(plan, "notes", None) or []))

        out_dir = self._document_output_dir()
        try:
            path = docs.new_document_path(out_dir, str(getattr(plan, "title", "") or topic), fmt)
            info = docs.build_document(plan, path, fmt)
        except docs.DocumentEngineMissing as exc:
            self.logger.error("文档写库缺失：%s", exc)
            return {
                "error": str(exc),
                "reply": f"这次生成不了文件：{exc}",
                "documents": [],
                "model": model,
                "degraded": True,
                "notes": notes,
            }
        except OSError as exc:
            self.logger.exception("文档写入失败（输出目录 %s）", out_dir)
            return {
                "error": str(exc),
                "reply": f"文件写不下去，输出目录可能不可写：{out_dir}（{exc}）",
                "documents": [],
                "model": model,
                "degraded": True,
                "notes": notes,
            }

        mirrored = self._mirror_document(path)
        stats = {
            docs.FORMAT_DOCX: f"{info.get('sections', 0)} 个小节、{info.get('paragraphs', 0)} 条内容",
            docs.FORMAT_PPTX: f"共 {info.get('slides', 1)} 页、{info.get('paragraphs', 0)} 条要点",
            docs.FORMAT_XLSX: f"{info.get('rows', 0)} 行 × {info.get('columns', 0)} 列",
        }.get(fmt, "")
        size_kb = max(1, int(path.stat().st_size // 1024)) if path.exists() else 0
        reply = (
            f"已生成{docs.FORMAT_LABELS.get(fmt, fmt)}：{path.name}"
            f"（{stats}，{size_kb} KB）\n存放位置：{path.parent}"
        )
        if mirrored:
            copied_to = "、".join(str(Path(item["target"]).parent) for item in mirrored)
            reply += f"\n同一份也放到了：{copied_to}"

        documents = [
            {
                "name": path.name,
                "path": str(path),
                "url": "/outputs/" + quote(path.name),
                "format": fmt,
                "label": docs.FORMAT_LABELS.get(fmt, fmt),
                "bytes": path.stat().st_size if path.exists() else 0,
                "stats": stats,
                "mirrors": [item["target"] for item in mirrored],
            }
        ]
        return {
            "error": "",
            "reply": reply,
            "documents": documents,
            "model": model,
            "degraded": degraded,
            "notes": notes,
        }

    def handle(
        self,
        message: str,
        *,
        history: Iterable[dict[str, Any]] | None = None,
        ref_image: str = "",
        action_hint: str = "",
    ) -> dict[str, Any]:
        """主入口（同步阻塞：调用方放线程池里跑，网页端用 asyncio.to_thread）。

        action_hint="speak" 时直接把 message 念出来（界面上的 🔊 按钮走这条路，
        不依赖“朗读”关键词解析）。
        """
        # 每处理一句话就把空闲计时归零：十分钟没再用到它，TaskService 的收割循环就把
        # 大模型服务整个停掉、显存归还（一次请求最长 llm_timeout_seconds，远不到十分钟）。
        self.local_llm.mark_used()
        text = str(message or "").strip()[:_MAX_MESSAGE_CHARS]
        if action_hint == "speak":
            result = self._speak(text)
            return {
                "action": ACTION_SPEAK,
                "reply": result["reply"],
                "error": result["error"],
                "tasks": [],
                "documents": [],
                "notes": [],
                "speech": result.get("speech", {}),
                "model": "",
            }
        command = parse_command(text, has_ref_image=bool(ref_image))
        base = {
            "action": command.action,
            "mode": command.mode,
            "modality": command.modality,
            "prompt": command.prompt,
            "parameters": command.parameters,
            "notes": list(command.notes),
            "tasks": [],
            "documents": [],
            "error": "",
            "reply": "",
            "model": "",
        }
        if command.action == ACTION_HELP:
            base["reply"] = command.reply or HELP_TEXT
            return base
        if command.action == ACTION_SPEAK:
            spoken = command.speak_text or command.prompt
            result = self._speak(spoken)
            base["reply"] = result["reply"]
            base["error"] = result["error"]
            base["speech"] = result.get("speech", {})
            return base
        if command.action == ACTION_RETIRED:
            # 兼容旧调用方：2026-11-09 重新选型后 router 已不再产生 retired 动作，
            # 保留分支只为防御——万一收到带 reply 的旧命令，也照实回显、不送进大模型闲聊。
            base["reply"] = command.reply or ""
            return base
        if command.action in (ACTION_CHAT, ACTION_COPY):
            result = self._chat_reply(text, self._clean_history(history), copy_mode=command.action == ACTION_COPY)
            base["reply"] = result["reply"]
            base["model"] = result["model"]
            base["degraded"] = result["degraded"]
            base["error"] = result["error"]
            base["notes"].extend(result.get("notes") or [])
            if result["model"]:
                base["notes"].append(f"回复来自本机模型 {result['model']}")
            return base
        if command.action == ACTION_DOCUMENT:
            result = self._write_document(command)
            base["reply"] = result["reply"]
            base["error"] = result["error"]
            base["documents"] = result["documents"]
            base["model"] = result["model"]
            base["degraded"] = result["degraded"]
            base["notes"].extend(result["notes"])
            if result["model"]:
                base["notes"].append(f"内容由本机模型 {result['model']} 撰写")
            return base
        if command.action == ACTION_GENERATE:
            result = self._generate(command, ref_image)
            base["reply"] = result["reply"]
            base["error"] = result["error"]
            base["tasks"] = result["tasks"]
            if not command.is_generation:
                base["notes"].append("没能判断出要生成什么，请说明是图片、视频还是音乐")
            return base
        base["reply"] = HELP_TEXT
        return base
