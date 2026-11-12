"""AI 智能体命令路由 + 界面语言词典的离线回归测试。

不启动引擎、不联网、不碰 GPU：只验证「一句话 → 任务意图」的判定，以及桌面端与
网页端共用的那份词典（app/web/static/i18n）是否与中文源清单严格对齐。
"""

from __future__ import annotations

import io
import json
import logging
import urllib.error
import urllib.request
from types import SimpleNamespace

import pytest

from app.agent.llm import LlmUnavailable, LocalLlmClient
from app.agent.router import (
    ACTION_CHAT,
    ACTION_GENERATE,
    ACTION_SPEAK,
    parse_command,
)
from app.agent.service import AgentService
from app.core import i18n
from app.web.server import HttpError, WebApplication


@pytest.fixture
def restore_language():
    original = i18n.current_language()
    yield
    i18n.set_language(original)


# ------------------------------------------------------------------ 命令路由
def test_router_image_command_reads_aspect_ratio() -> None:
    command = parse_command("画一张赛博朋克雨夜街头，16:9")
    assert command.action == ACTION_GENERATE
    assert command.mode == "t2i"
    # 文生图（sana-multiling）走 1024 级档：16:9 → 1152×640，与 router._ASPECT_SANA
    # 同一份口径（32 的倍数、单遍预算 1_100_000px），不再压到 480p 档。
    from app.agent.router import _ASPECT_SANA

    assert (command.parameters["width"], command.parameters["height"]) == _ASPECT_SANA["16:9"]
    assert command.parameters["width"] == 1152
    assert command.parameters["height"] == 640
    assert command.parameters["width"] % 32 == 0 and command.parameters["height"] % 32 == 0


def test_router_portrait_wallpaper_and_seed() -> None:
    command = parse_command("竖版手机壁纸，一只戴帽子的柴犬，种子 123")
    assert command.action == ACTION_GENERATE and command.mode == "t2i"
    assert command.parameters["height"] > command.parameters["width"]
    assert command.parameters["seed"] == 123


def test_router_video_duration_becomes_snapped_frames() -> None:
    command = parse_command("生成一段10秒的海浪拍打礁石延时摄影")
    assert command.mode == "t2v"
    frames = int(command.parameters["num_frames"])
    assert frames == 157          # 10 秒 × 16fps，向下取到 4n+1
    assert (frames - 1) % 4 == 0  # Wan 只接受 4n+1 帧


def test_router_music_parameters() -> None:
    command = parse_command("生成一首轻快的钢琴曲，纯音乐，90秒，bpm 96")
    assert command.mode == "music"
    assert float(command.parameters["duration"]) == 90.0
    assert int(command.parameters["bpm"]) == 96
    assert command.parameters["instrumental"] is True


def test_router_reference_image_that_moves_goes_to_i2v() -> None:
    command = parse_command("让这张照片里的云动起来", has_ref_image=True)
    assert command.mode == "i2v"
    assert command.needs_ref_image
    assert "里的" not in command.prompt


def test_router_edit_command_strips_instruction_words() -> None:
    command = parse_command("把这张图改成水彩风", has_ref_image=True)
    assert command.mode == "i2i"
    assert "这张图" not in command.prompt
    assert "改成" not in command.prompt


def test_router_question_goes_to_chat_not_generation() -> None:
    command = parse_command("FLUX 的步数一般设多少？")
    assert command.action == ACTION_CHAT


def test_router_read_aloud_is_speak_action() -> None:
    command = parse_command("朗读这段文字：今天适合生成一张海报")
    assert command.action == ACTION_SPEAK
    assert command.speak_text


# ------------------------------------------------ 判不出明确生成意图时绝不兜底成文生图
def test_router_small_talk_never_becomes_text_to_image() -> None:
    """「你好」「在吗」「肚子咕咕叫」不是生成需求：判不出明确意图一律走聊天。

    以前 router 结尾有一条「描述即提示词」兜底：任何没命中关键词的话都被派成文生图，
    还把原话当提示词去画，用户截图里那几张莫名其妙的图就是这么来的。现在这条兜底
    已经删掉，没有明确动词证据就是普通聊天。
    """
    for text in ("你好", "在吗", "肚子咕咕叫", "我刚吃完饭", "今天天气怎么样"):
        command = parse_command(text)
        assert command.action == ACTION_CHAT, text
        assert not command.is_generation, text
        assert command.notes, text  # 界面要能说明「为什么没去画」


def test_router_description_alone_is_chat_not_a_prompt() -> None:
    """只有一段画面描述、没有任何「画/生成」说法：不猜意图，走聊天。"""
    for text in ("赛博朋克雨夜街头", "一只戴着宇航头盔的猫在月球上散步"):
        command = parse_command(text)
        assert command.action == ACTION_CHAT, text


def test_router_explicit_drawing_request_still_generates() -> None:
    """明确说法（画一只 / 生成一张 / 绘制 / draw）照常派给文生图，一条都不能掉进聊天。"""
    for text in (
        "画一只柴犬",
        "画个柴犬",
        "生成一张赛博朋克雨夜街头",
        "绘制未来城市",
        "draw a cat",
    ):
        command = parse_command(text)
        assert command.action == ACTION_GENERATE, text
        assert command.mode == "t2i", text


def test_router_video_and_music_signals_are_not_stepped_on() -> None:
    """删掉文生图兜底不能牵连视频/音乐：秒数、时长、媒体词照旧命中。"""
    seconds = parse_command("海浪拍打礁石，10秒")
    assert seconds.action == ACTION_GENERATE and seconds.mode == "t2v"
    music = parse_command("生成一首轻快的钢琴曲，纯音乐，90秒")
    assert music.action == ACTION_GENERATE and music.mode == "music"


# ---------------------------------------------------------------------- 词典
def test_every_dictionary_has_the_source_keys_in_order() -> None:
    source = [
        line
        for line in i18n.SOURCE_LIST.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]
    assert len(source) > 100
    entries = i18n.languages()
    codes = [entry["code"] for entry in entries]
    assert "zh" in codes and len(codes) == len(set(codes)) >= 15
    for entry in entries:
        assert entry["native"] and entry["english"]
        if entry["code"] == "zh":
            continue
        path = i18n.CATALOG_DIR / f"{entry['code']}.json"
        assert path.is_file(), f"缺少词典：{path}"
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        assert list(data) == source, f"{entry['code']} 的 key 顺序与 source-zh.txt 不一致"
        # 没有词条就等于没翻：value 不得与中文 key 相同
        assert all(value != key for key, value in data.items()), entry["code"]


def test_unknown_language_code_falls_back_to_zh(restore_language) -> None:
    assert i18n.set_language("klingon") == "zh"
    assert i18n.tr("文生图") == "文生图"


def test_translation_exact_and_fragment_modes(restore_language) -> None:
    i18n.set_language("en")
    assert i18n.tr("文生图") == "Text to Image"
    line = i18n.tr("排队中 · 第 3 位")
    assert "排队" not in line and "3" in line
    i18n.set_language("ar")
    assert i18n.is_rtl()
    assert "排队" not in i18n.tr("排队中 · 第 3 位")


def test_unregistered_text_is_left_in_chinese(restore_language) -> None:
    i18n.set_language("en")
    # 引擎回报的长错误没有登记过，就不该被「翻译」成看起来像译文的中文
    assert not i18n.is_source("显存不足，无法加载 FLUX.1-schnell")
    assert i18n.tr("我自定义的会话标题") == "我自定义的会话标题"


# ------------------------------------------------------ 大模型：超时与显存交还
class _FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False


def test_llm_says_weights_are_loading_instead_of_blaming_the_endpoint(monkeypatch) -> None:
    """加载权重期间 llama-server 整个端点回 503「Loading model」：这是「还没好」不是「坏了」。

    照原样把 503 端出去，用户看到的是「本机大模型端点返回 503」，以为装坏了要重装；
    真实情况是再等几十秒就好。话要说成「正在加载权重，稍等几秒再发一次」。
    """

    def fake_urlopen(request, timeout=None):  # noqa: ANN001 - 替身按 urllib 签名
        body = b'{"error":{"message":"Loading model","type":"unavailable_error","code":503}}'
        raise urllib.error.HTTPError(request.full_url, 503, "Service Unavailable", {}, io.BytesIO(body))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = LocalLlmClient("http://127.0.0.1:11435/v1")
    with pytest.raises(LlmUnavailable) as caught:
        client.list_models(force=True)
    message = str(caught.value)
    assert "加载权重" in message and "30-90" in message
    assert "503" not in message  # 不再把像故障一样的状态码甩给用户


def test_llm_unload_prefers_llama_cpp_unload_endpoint(monkeypatch) -> None:
    """端点要是支持 ``POST /unload`` 就用它（只腾显存、进程还在）。

    实测 llama.cpp b12182 **没有**这个路由（回 404），所以自家服务的交还显存走
    ``LocalLlamaServer.release_vram()`` 停进程，见 test_release_vram_knows_whose_endpoint_it_is；
    这条测试保的是「服务端支持时优先用它，别多打一次」。
    """
    calls: list[tuple[str, dict, object]] = []

    def fake_urlopen(request, timeout=None):  # noqa: ANN001 - 替身按 urllib 签名
        calls.append((request.full_url, json.loads((request.data or b"{}").decode("utf-8")), timeout))
        return _FakeResponse(b"{}")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = LocalLlmClient("http://127.0.0.1:11435/v1")
    assert client.unload() is True
    url, _payload, timeout = calls[0]
    # /v1 要换算成服务根路径：llama-server 的 /unload 挂在根上，不在 /v1 下
    assert url == "http://127.0.0.1:11435/unload"
    assert len(calls) == 1  # llama.cpp 认了就不要再多打一次
    assert isinstance(timeout, (int, float)) and 0 < timeout <= 20


def _ollama_style_urlopen(calls: list) -> object:
    """模拟「用户把端点指回自己的 Ollama」：没有 /unload，只认原生 /api/generate。"""

    def fake_urlopen(request, timeout=None):  # noqa: ANN001 - 替身按 urllib 签名
        url = request.full_url
        if url.endswith("/unload"):
            raise urllib.error.HTTPError(url, 404, "not found", {}, None)
        calls.append((url, json.loads((request.data or b"{}").decode("utf-8")), timeout))
        return _FakeResponse(b"{}")

    return fake_urlopen


def test_llm_unload_falls_back_to_ollama_keep_alive(monkeypatch) -> None:
    calls: list[tuple[str, dict, object]] = []
    monkeypatch.setattr(urllib.request, "urlopen", _ollama_style_urlopen(calls))
    client = LocalLlmClient("http://127.0.0.1:11434/v1")
    assert client.unload(model="qwen3.5:9b") is True
    url, payload, timeout = calls[-1]
    # keep_alive 只有 Ollama 原生接口认，OpenAI 兼容端点不认：必须换算成 /api/generate
    assert url == "http://127.0.0.1:11434/api/generate"
    assert payload == {"model": "qwen3.5:9b", "keep_alive": 0}
    assert isinstance(timeout, (int, float)) and 0 < timeout <= 20


def test_llm_unload_without_endpoint_is_noop(monkeypatch) -> None:
    seen: list[object] = []
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda request, timeout=None: seen.append(request) or _FakeResponse(b"{}"),
    )
    assert LocalLlmClient("").unload() is False
    assert seen == []


def test_llm_unload_without_model_name_still_asks_llama_server(monkeypatch) -> None:
    """没记过模型名也照样请求交还显存：llama.cpp 的 /unload 不需要点名。"""
    seen: list[str] = []

    def fake_urlopen(request, timeout=None):  # noqa: ANN001 - 替身按 urllib 签名
        seen.append(request.full_url)
        return _FakeResponse(b"{}")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert LocalLlmClient("http://127.0.0.1:11435/v1").unload() is True
    assert seen == ["http://127.0.0.1:11435/unload"]


def test_resolve_model_remembers_the_choice_so_the_fallback_can_name_it(monkeypatch) -> None:
    """生成前排队的「交还显存」不带参数，所以选型必须把名字记下来（Ollama 回退路径要点名）。"""
    calls: list[dict] = []
    monkeypatch.setattr(urllib.request, "urlopen", _ollama_style_urlopen(calls))
    client = LocalLlmClient("http://127.0.0.1:11434/v1", model="qwen2.5:7b-instruct")
    client._models = ["qwen3.5:9b", "qwen2.5:14b-instruct"]
    client._probed_at = float("inf")  # 让 list_models 直接吃缓存，不发请求
    assert client.resolve_model() == "qwen2.5:14b-instruct"  # 配置的 7b 不存在 → 同族回退
    assert client.unload() is True
    assert [item[1] for item in calls] == [{"model": "qwen2.5:14b-instruct", "keep_alive": 0}]


def test_llm_unload_failure_is_swallowed(monkeypatch) -> None:
    def boom(request, timeout=None):  # noqa: ANN001 - 替身
        raise OSError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    assert LocalLlmClient("http://127.0.0.1:11434/v1").unload(model="qwen3.5:9b") is False


def test_llm_timeout_leaves_room_for_cold_model_start() -> None:
    # 6-8GB 权重冷启动就可能几十秒，旧的 90 秒上限会把第一次对话判成「连不上端点」
    service = AgentService(
        task_service=None,
        engine_manager=None,
        config={"llm_endpoint": "http://127.0.0.1:11434/v1"},
    )
    assert service.llm.timeout >= 180.0


def test_llm_unreachable_message_names_the_dedicated_launcher(monkeypatch) -> None:
    """专用实例没起时，报错要直接给出可跑的那条命令，而不是泛泛的「检查网络」。"""
    def refuse(request, timeout=None):  # noqa: ANN001 - 替身
        raise urllib.error.URLError("ConnectionRefusedError(10061)")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    client = LocalLlmClient("http://127.0.0.1:11435/v1", model="qwen3:8b", timeout=30.0)
    from app.agent.llm import LlmUnavailable

    with pytest.raises(LlmUnavailable) as caught:
        client.list_models(force=True)
    message = str(caught.value)
    assert "http://127.0.0.1:11435/v1" in message
    assert "run_agent_llm.bat" in message
    assert "agent_llm.ps1" in message
    assert "-Setup" in message          # 第一次用（工具与权重还没下）要能看到该跑哪一条
    assert "ollama serve" not in message.lower()  # 不再是「自己去把 Ollama 起起来」那套教程


def test_agent_llm_scripts_stay_loopback_and_project_local(tmp_path) -> None:
    """智能体自带大模型链路的三条硬边界：只绑回环、工具与权重都在项目内、不碰 Ollama。

    脚本改坏了这几点（顺手绑 0.0.0.0、把权重下到项目外、或者又绕回 Ollama 模型库）
    就该在这里红。
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    script = (root / "scripts" / "agent_llm.ps1").read_text(encoding="utf-8-sig")
    launcher = (root / "scripts" / "run_agent_llm.bat").read_text(encoding="utf-8-sig")

    assert "127.0.0.1" in script and "127.0.0.1" in launcher
    assert '$bindAddress = "127.0.0.1"' in script             # 绑定地址写死回环
    assert '"--host", $bindAddress' in script                 # 起服务时真的用这个地址
    assert r"models\llm\gguf" in script                       # 权重在项目内，便携包能带着走
    assert r"tools\llama_cpp" in script and "llama-server.exe" in script  # 推理程序也在项目内
    assert "--alias" in script and "--fit" in script          # 模型名对齐 config；显存不够会自己降级
    assert "-Setup" in script and "hf-mirror" in script       # 第一次用的下载入口与国内可达的源
    assert "11435" in script                                  # 与用户自己的 Ollama 11434 分开
    assert "ollama_models" not in script and "OLLAMA_MODELS" not in script  # 不再依赖 Ollama 模型库
    assert not re.search(r"0\.0\.0\.0|\*:", script), "自带服务不得允许非回环绑定"

    # config 默认端点 / 模型名必须就是脚本起的那一套，否则换机后两头对不上
    from app.core.config import ConfigStore
    from app.core.paths import AppPaths

    defaults = ConfigStore(AppPaths.from_environment(app_home=tmp_path, data_root=tmp_path)).defaults
    assert defaults["llm_endpoint"] == "http://127.0.0.1:11435/v1"
    assert defaults["llm_model"] in script
    assert defaults["llm_autostart"] is True            # 重启电脑后程序自己把服务拉起来
    assert "llama-server.exe" in (root / "app" / "agent" / "local_server.py").read_text(encoding="utf-8")
    assert "Qwen3-8B-Q4_K_M.gguf" in script


def test_agent_llm_load_parameters_keep_vram_headroom(monkeypatch) -> None:
    """加载参数的三条要求：上下文 16384、KV 的键与值都量化成 q8_0、起完还留三成显存。

    这三条是 12GB 单卡上「智能体与生成引擎抢同一块卡」实测出来的底线：少任何一条，
    要么多轮对话被截断，要么起服务时把生成图片 / 视频 / 音乐要用的显存吃光。
    """
    from pathlib import Path

    from app.agent import local_server as local_llm

    root = Path(__file__).resolve().parents[1]
    script = (root / "scripts" / "agent_llm.ps1").read_text(encoding="utf-8-sig")

    assert local_llm.LLM_CTX_SIZE == 16384
    assert local_llm.LLM_KV_CACHE_TYPE == "q8_0"
    assert local_llm.LLM_FREE_VRAM_PERCENT == 30
    assert local_llm.LLM_FREE_VRAM_FALLBACK_MIB == 3686  # 12288 MiB 卡的 30%

    server = local_llm.LocalLlamaServer(
        {"llm_endpoint": "http://127.0.0.1:11435/v1", "llm_model": "qwen3:8b"}
    )
    args = server.server_args()

    def value_after(flag: str) -> str:
        assert flag in args, f"llama-server 少了 {flag}"
        return args[args.index(flag) + 1]

    assert value_after("--host") == "127.0.0.1"
    assert value_after("--ctx-size") == "16384"
    assert value_after("--cache-type-k") == "q8_0"  # KV 的键
    assert value_after("--cache-type-v") == "q8_0"  # KV 的值：两边都量化才真省掉一半
    assert value_after("--fit") == "on"
    assert value_after("--fit-ctx") == "16384"  # 挡住「fit 为了塞进去偷偷砍上下文」
    assert int(value_after("--fit-target")) > 0  # 留白由整卡容量算出来，不许写死成 0

    # 留白 = 整卡容量的 30%：伪造 nvidia-smi 的输出，不依赖测试机上有没有显卡
    class _FakeNvidiaSmi:
        stdout = "24576\n"
        returncode = 0

    monkeypatch.setattr(local_llm.subprocess, "run", lambda *a, **k: _FakeNvidiaSmi())
    assert local_llm.LocalLlamaServer({}).free_vram_target_mib() == 7372

    # 状态查询本身不许顺手起进程：另一条测试会把 subprocess.Popen 换掉，那时它必须还能跑。
    monkeypatch.setattr(local_llm.subprocess, "Popen", None)
    quiet = local_llm.LocalLlamaServer({"llm_endpoint": "http://127.0.0.1:11435/v1"}).status()
    assert quiet["ctx_size"] == 16384 and quiet["kv_cache_type"] == "q8_0"
    assert quiet["free_vram_target_mib"] == local_llm.LLM_FREE_VRAM_FALLBACK_MIB
    # vram / vram_note 只带上次探到的值（可能是空），关键是它没有为此去起进程
    assert set(("vram", "vram_note")) <= set(quiet)

    # 空闲够不到要求时，界面要拿到一句人话说明（不是让用户自己猜为什么慢）
    tight = server.vram_shortfall_note({"total": 12288, "used": 11800, "free": 300, "target_free": 3686})
    assert "300 MiB" in tight and "3686 MiB" in tight
    assert server.vram_shortfall_note({"total": 12288, "used": 4000, "free": 8000, "target_free": 3686}) == ""

    # 手动脚本与代码用的是同一套参数，用户自己起和程序自动起不会有两种显存行为
    for token in ("--cache-type-k", "--cache-type-v", "--fit-target", "--fit-ctx"):
        assert token in script
    assert "$CtxSize = 16384" in script and '"q8_0"' in script and "3686" in script


def test_local_llm_server_refuses_non_loopback_and_foreign_ports(tmp_path, monkeypatch) -> None:
    """自动拉起自带服务的护栏：非回环不拉、别人的端口不插手、缺东西就把该跑的命令说出来。"""
    from app.agent import local_server as local_llm
    from app.core.paths import AppPaths

    def make(endpoint: str) -> local_llm.LocalLlamaServer:
        server = local_llm.LocalLlamaServer(
            {
                "llm_endpoint": endpoint,
                "llm_model": "qwen3:8b",
                # 指向一个不存在的权重：只验护栏，不真去加载 4.7GB
                "llm_model_path": str(tmp_path / "nope.gguf"),
            },
            paths=AppPaths.from_environment(app_home=tmp_path, data_root=tmp_path),
        )
        # 本机此刻端口上有什么不算数：护栏必须跟真机状态无关
        monkeypatch.setattr(server, "port_open", lambda *, timeout=0.4: False)
        monkeypatch.setattr(server, "_image_of", lambda pid: "")
        return server

    lan = make("http://192.168.1.20:11435/v1")
    assert lan.is_loopback_endpoint() is False
    refused = lan.ensure_running(wait_seconds=0)
    assert refused["state"] == "refused" and refused["ok"] is False
    assert "127.0.0.1" in refused["note"]

    # 用户把端点指回自己的 Ollama：那个端口归他管，我们绝不代为启动
    foreign = make("http://127.0.0.1:11434/v1")
    assert foreign.is_loopback_endpoint() is True
    assert foreign.ensure_running(wait_seconds=0)["state"] == "external"

    missing = make("http://127.0.0.1:11435/v1")
    state = missing.ensure_running(wait_seconds=0)
    assert state["state"] in {"missing_tool", "missing_model"}
    assert "agent_llm.ps1" in state["note"]  # 缺哪一步就说出该跑的那一条命令
    assert missing.gguf_path().name == "nope.gguf"  # config 写死权重路径时照用


def _server_pointed_at_our_port(tmp_path):  # noqa: ANN001 - 测试夹具，返回 LocalLlamaServer
    from app.agent import local_server as local_llm
    from app.core.paths import AppPaths

    return local_llm.LocalLlamaServer(
        {
            "llm_endpoint": "http://127.0.0.1:11435/v1",
            "llm_model": "qwen3:8b",
            "llm_model_path": str(tmp_path / "nope.gguf"),
        },
        paths=AppPaths.from_environment(app_home=tmp_path, data_root=tmp_path),
    )


def test_a_listening_port_is_not_yet_a_usable_model(tmp_path, monkeypatch) -> None:
    """llama-server **先绑端口、后加载权重**：加载期间 /v1/models 回 503。

    把「端口连得上」当成「能用」，聊天就会在加载途中撞上 503 被判定成「大模型不可用」；
    更糟的是自动拉起以为服务没跑，再起一个实例去抢同一块显存（12GB 卡上直接双双崩掉）。
    所以就绪与否必须问模型本身，端口在听但没就绪时只能陪它等。
    """
    from app.agent import local_server as local_llm

    server = _server_pointed_at_our_port(tmp_path)
    monkeypatch.setattr(server, "port_open", lambda *, timeout=0.4: True)
    monkeypatch.setattr(server, "model_ready", lambda *, timeout=1.5: False)
    started: list[list[str]] = []
    monkeypatch.setattr(local_llm.subprocess, "Popen", lambda *args, **kw: started.append(list(args[0])))

    snap = server.status()
    assert snap["running"] is True and snap["ready"] is False and snap["loading"] is True

    result = server.ensure_running(wait_seconds=0.0)
    assert result["ok"] is True and result["state"] == "loading"
    assert "加载权重" in result["note"]
    assert not started  # 最关键的一条：绝不因为「还没能用」就起第二个实例抢显存

    monkeypatch.setattr(server, "model_ready", lambda *, timeout=1.5: True)
    assert server.ensure_running(wait_seconds=0.0)["state"] == "running"
    ready_snap = server.status()
    assert ready_snap["ready"] is True and ready_snap["loading"] is False


def test_stop_only_kills_the_process_we_started(tmp_path, monkeypatch) -> None:
    """交还显存靠停进程，所以「停错人」就是事故：只认 pid 文件里、镜像名对得上的那一个。"""
    from app.agent import local_server as local_llm

    server = _server_pointed_at_our_port(tmp_path)
    # pid 文件在 tmp 里：测试不能去动真机那个正在跑的服务
    monkeypatch.setattr(server, "pid_file", tmp_path / "llama_server.pid")
    monkeypatch.setattr(server, "_pid_from_file", lambda: 4242)
    commands: list[list[str]] = []

    def fake_run(argv, **kwargs):  # noqa: ANN001, ANN003 - 替身只记账不真跑外部命令
        commands.append(list(argv))
        return SimpleNamespace(stdout="", returncode=0)

    monkeypatch.setattr(local_llm.subprocess, "run", fake_run)

    monkeypatch.setattr(server, "_image_of", lambda pid: "ollama.exe")  # 端口上挂的是用户自己的服务
    result = server.stop()
    assert result["ok"] is False and result["state"] == "foreign"
    assert not commands  # 别人家的进程绝不动手

    monkeypatch.setattr(server, "_image_of", lambda pid: "llama-server.exe")
    stopped = server.stop()
    assert stopped["ok"] is True and stopped["state"] == "stopped"
    assert [item[0] for item in commands] == ["taskkill"]  # 自家进程才用 taskkill
    assert "4242" in stopped["note"]


def test_release_vram_knows_whose_endpoint_it_is(tmp_path, monkeypatch) -> None:
    """交还显存分两家：自家服务实测没有 /unload，只能停进程；用户自己的端点归它自己。"""
    from app.agent import local_server as local_llm

    ours = _server_pointed_at_our_port(tmp_path)
    monkeypatch.setattr(ours, "port_open", lambda *, timeout=0.4: False)
    idle = ours.release_vram()
    assert idle["ok"] is False and idle["state"] == "none"  # 本来就没占显存，不吹「已经腾出来了」

    monkeypatch.setattr(ours, "port_open", lambda *, timeout=0.4: True)
    monkeypatch.setattr(ours, "stop", lambda: {"ok": True, "state": "stopped", "note": "已停止自带大模型服务（PID 1）"})
    freed = ours.release_vram()
    assert freed["ok"] is True and "显存" in freed["note"]

    from app.core.paths import AppPaths

    theirs = local_llm.LocalLlamaServer(
        {"llm_endpoint": "http://127.0.0.1:11434/v1", "llm_model": "qwen3.5:9b"},
        paths=AppPaths.from_environment(app_home=tmp_path, data_root=tmp_path),
    )
    calls: list[int] = []
    monkeypatch.setattr(theirs, "stop", lambda: calls.append(1))
    foreign = theirs.release_vram()
    assert foreign["ok"] is False and foreign["state"] == "external"
    assert not calls  # 用户自己的 Ollama：交还是它原生接口的事，不是我们停进程的事


def test_capabilities_kicks_the_local_llm_when_endpoint_is_down(monkeypatch) -> None:
    """端点连不上多半不是「没装」而是「没在跑」：探测要顺手踩下油门，并把话说明白。"""
    service = AgentService(
        task_service=None,
        engine_manager=None,
        config={"llm_endpoint": "http://127.0.0.1:11435/v1", "llm_model": "qwen3:8b"},
    )
    kicks: list[float] = []
    monkeypatch.setattr(service.local_llm, "port_open", lambda *, timeout=0.4: False)
    monkeypatch.setattr(
        service.local_llm,
        "ensure_running",
        lambda *, wait_seconds: kicks.append(wait_seconds)
        or {"ok": True, "state": "starting", "note": "已自动拉起自带大模型服务，稍后自动重新探测"},
    )
    # status 的桩要跟真的签名一致：能力面板会带 probe_vram=True 来要显存数字
    status_calls: list[dict] = []

    def fake_status(**kwargs):
        status_calls.append(kwargs)
        return {"installed": True, "running": False, "port": 11435}

    monkeypatch.setattr(service.local_llm, "status", fake_status)
    monkeypatch.setattr(service.llm, "status", lambda: {"available": False, "model": "", "models": [], "note": "连不上"})
    caps = service.capabilities()
    assert kicks == [0.0]  # 探测里只踩油门不傻等，界面靠「重新探测」自己刷绿
    assert any("已自动拉起自带大模型服务" in note for note in caps["notes"])
    assert caps["local_llm"]["port"] == 11435
    # 显存数字只在能力面板里现探（两端 UI 共用这一个出口，别的调用方不去起进程）
    assert status_calls and status_calls[0].get("probe_vram") is True


def test_chat_uses_the_local_server_note_instead_of_blaming_the_network(monkeypatch) -> None:
    """聊天前会先把服务拉起来；拉不起来时要把该跑的命令写进回复，而不是只说「连不上」。"""
    from app.agent.llm import LlmUnavailable

    service = AgentService(
        task_service=None,
        engine_manager=None,
        config={"llm_endpoint": "http://127.0.0.1:11435/v1", "llm_model": "qwen3:8b"},
    )
    waits: list[float] = []

    def fake_ensure(*, wait_seconds: float) -> dict[str, object]:
        waits.append(wait_seconds)
        return {"ok": False, "state": "missing_model", "note": "缺权重：跑 scripts\\agent_llm.ps1 -Setup"}

    monkeypatch.setattr(service.local_llm, "port_open", lambda *, timeout=0.4: False)
    monkeypatch.setattr(service.local_llm, "ensure_running", fake_ensure)
    monkeypatch.setattr(service.local_llm, "status", lambda: {"installed": False, "running": False})

    def boom(messages):  # noqa: ANN001 - 替身按 llm.chat 签名
        raise LlmUnavailable("连不上本机大模型端点 http://127.0.0.1:11435/v1")

    monkeypatch.setattr(service.llm, "chat", boom)
    result = service.handle("FLUX 的步数一般设多少？")
    assert waits and waits[0] >= 60.0  # 聊天是阻塞调用，值得给它加载权重的时间
    assert result["degraded"] is True
    assert "agent_llm.ps1" in result["reply"]
    assert any("Setup" in note for note in result["notes"])


def test_agent_capability_bar_can_recheck_without_restart() -> None:
    """能力探测不是一次性快照：两端都要自己重试，并留一个手动「重新探测」入口。

    程序刚起来的头几秒引擎还在后台注册，给智能体专配的大模型实例也可能晚几秒才就位。
    把「开机那一刻」的结果钉死在界面上，用户就会一直看到「无引擎 / 本机大模型：不可用」，
    明明模型已经下载好却以为没有——所以降级要自动再探，界面上也要能手动再探。
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    desktop = (root / "app" / "ui" / "pages" / "agent_page.py").read_text(encoding="utf-8")
    web = (root / "app" / "web" / "static" / "agent.js").read_text(encoding="utf-8")

    for label, source in (("桌面端", desktop), ("网页端", web)):
        assert "重新探测" in source, f"{label} 缺手动「重新探测」入口"
        assert "正在重新探测…" in source, f"{label} 缺探测进行中的文案"
        assert "PROBE_RETRIES" in source, f"{label} 缺自动重试次数上限"
        assert "PROBE_RETRY_MS" in source, f"{label} 缺自动重试间隔"
        assert "degraded" in source.lower(), f"{label} 缺「探测结果降级」判定"

    # 桌面端：按钮常驻在能力条里，胶囊每次重建只清自己的子容器，不会把按钮 deleteLater
    assert "probe_button" in desktop and "_cap_pills" in desktop
    # 网页端：缓存不得永久早退，降级后要安排下一次探测
    assert "scheduleProbeRetry" in web and "probeRetries" in web and "probing" in web

    # 这两条文案进了两端共用的词典，切语言后按钮不会掉回中文
    source_keys = i18n.SOURCE_LIST.read_text(encoding="utf-8-sig").splitlines()
    for key in ("重新探测", "正在重新探测…"):
        assert key in source_keys, f"中文源清单缺 {key}"


def test_agent_capability_lists_every_slot_on_both_ends() -> None:
    """AI 智能体的能力条两端都要列全五个板块（含图生图），并按引擎状态分三态。

    出过两次事故：一是网页端顶栏在 AI 智能体页固定显示红色「无引擎」——前端按
    MODALITY 过滤引擎，而 agent 模式没有 modality 映射，列表被清空后就走了「无引擎」
    分支，用户以为引擎根本没装；二是能力条漏掉图生图槽，看不出图生图有自己的引擎。
    引擎「已注册未启动」是正常状态，不能显示成缺失，所以两端都用中性胶囊 +
    「…· 未启动」，只有真的没注册引擎才标红。
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    desktop = (root / "app" / "ui" / "pages" / "agent_page.py").read_text(encoding="utf-8")
    app_js = (root / "app" / "web" / "static" / "app.js").read_text(encoding="utf-8")
    agent_js = (root / "app" / "web" / "static" / "agent.js").read_text(encoding="utf-8")
    html = (root / "app" / "web" / "static" / "index.html").read_text(encoding="utf-8")
    panel_js = (root / "app" / "ui" / "widgets" / "param_panel.py").read_text(encoding="utf-8")

    for label, source in (("桌面端 agent_page.py", desktop), ("网页端 agent.js", agent_js)):
        for slot in ("文生图", "图生图", "文生视频", "图生视频", "音乐"):
            assert slot in source, f"{label} 能力条缺 {slot} 槽位"
        assert "engine_states" in source, f"{label} 没按引擎实际状态区分「未启动」"
        assert "未启动" in source, f"{label} 缺「已注册但未启动」的中性文案"

    # 网页端顶栏：agent tab 有专门的引擎总览，不能因 MODALITY 没有映射就报「无引擎」
    assert "renderAgentEngineOverview" in app_js
    assert "已注册未启动不等于没有引擎" in app_js
    # 图生图绑到自己的引擎槽，两端都不能沿用文生图引擎（否则两个模型同时进显存）
    assert 'i2i:["wan-i2i"]' in app_js.replace(" ", "")
    # 2026-11-10 换型：文生图 = Sana-1.6B 多语言版（wan-t2i 已下架，网页端不得再出现它）
    assert 't2i:["sana-multiling"]' in app_js.replace(" ", "")
    assert "wan-t2i" not in app_js, "网页端还残留已下架的 wan-t2i，两端引擎清单不一致"
    from app.ui.widgets.param_panel import MODE_ENGINE_ALLOW

    assert MODE_ENGINE_ALLOW["t2i"] == ("sana-multiling",)
    assert MODE_ENGINE_ALLOW["i2i"] == ("wan-i2i",)
    assert '"i2i":("wan-i2i",)' in panel_js.replace(" ", "").replace("'", '"')
    assert '"t2i":("sana-multiling",)' in panel_js.replace(" ", "").replace("'", '"')

    # 网页端「产物保存」面板的元素 id 必须与 app.js 用的一致，否则面板空白
    for element_id in ("output-dir-label", "project-output-label", "autosave-dir", "autosave-state"):
        assert f'id="{element_id}"' in html, f"index.html 缺 #{element_id}"
        assert f'"{element_id}"' in app_js, f"app.js 没用 #{element_id}"


def test_image_and_image_to_image_use_distinct_engine_slots() -> None:
    """文生图与图生图必须是两个引擎槽，且智能体按板块各自命中自己的那个。"""
    from app.agent import service as agent_service

    assert agent_service._ENGINE_CANDIDATES["t2i"] != agent_service._ENGINE_CANDIDATES["i2i"]
    assert "wan-i2i" in agent_service._ENGINE_CANDIDATES["i2i"]
    assert "wan-i2i" not in agent_service._ENGINE_CANDIDATES["t2i"]
    # 文生图槽位指向 Sana（wan-t2i 已下架）；关键字兜底也必须换成 "sana"，
    # 否则注册表里 id 一变，兜底匹配就拿不到引擎了。
    assert "sana-multiling" in agent_service._ENGINE_CANDIDATES["t2i"]
    assert "wan-t2i" not in agent_service._ENGINE_CANDIDATES["t2i"]
    assert "sana" in agent_service._ENGINE_KEYWORDS["t2i"]


def test_config_keeps_llm_timeout_seconds(tmp_path) -> None:
    from app.core.config import ConfigStore
    from app.core.paths import AppPaths

    store = ConfigStore(AppPaths.from_environment(app_home=tmp_path))
    config = store.load()
    assert int(config["llm_timeout_seconds"]) >= 180
    config["llm_timeout_seconds"] = 300
    store.save(config)
    assert store.load()["llm_timeout_seconds"] == 300


# ------------------------------------------------ 排生成前先让模型交还显存
class _FakeEngineManager:
    def __init__(self, engine_ids: tuple[str, ...] = ("sana-multiling",)) -> None:
        self._engine_ids = engine_ids

    def list_statuses(self):
        return [SimpleNamespace(engine_id=engine_id) for engine_id in self._engine_ids]


class _FakeTaskService:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.requests: list[object] = []

    def submit_task(self, request):  # noqa: ANN001 - 替身按 TaskRequest 调用
        self.events.append("submit")
        self.requests.append(request)
        return SimpleNamespace(
            task_id=f"task-{len(self.requests):04d}",
            status=SimpleNamespace(value="queued"),
            request=request,
        )


def test_agent_asks_llm_to_free_vram_before_queueing() -> None:
    events: list[str] = []
    service = AgentService(
        task_service=_FakeTaskService(events),
        engine_manager=_FakeEngineManager(),
        config={"llm_endpoint": "http://127.0.0.1:11434/v1", "llm_model": "qwen3.5:9b"},
    )

    def unload() -> bool:
        events.append("unload")
        return True

    service.llm = SimpleNamespace(
        unload=unload,
        resolve_model=lambda: "qwen3.5:9b",
    )
    result = service.handle("画一张赛博朋克雨夜街头，16:9")
    assert events[0] == "unload"  # 交还显存必须排在提交之前，否则 Worker 起手就 OOM
    assert "submit" in events
    assert result["tasks"][0]["task_id"] == "task-0001"
    assert "交还显存" in result["reply"]


def _agent_with_fake_llm(events: list[str], engine_ids: tuple[str, ...] = ("sana-multiling",)) -> AgentService:
    """把智能体接到替身上：大模型只留一个 unload 探针，绝不真发请求。"""
    service = AgentService(
        task_service=_FakeTaskService(events),
        engine_manager=_FakeEngineManager(engine_ids),
        config={"llm_endpoint": "http://127.0.0.1:11434/v1", "llm_model": "qwen3.5:9b"},
    )
    service.llm = SimpleNamespace(
        unload=lambda: (events.append("unload") or True),
        resolve_model=lambda: "qwen3.5:9b",
    )
    return service


def test_agent_image_command_queues_single_task() -> None:
    """文生图走 Sana（sana-multiling）：智能体一次只排 1 条，旧 SD1.5「说一句出 4 张」随引擎退场。"""
    events: list[str] = []
    service = _agent_with_fake_llm(events)
    result = service.handle("画一张赛博朋克雨夜街头，16:9")
    assert events.count("submit") == 1, events
    assert len(result["tasks"]) == 1
    assert result["tasks"][0]["task_type"] == "image"
    assert result["tasks"][0]["engine_id"] == "sana-multiling"
    assert "已排入队列" in result["reply"]


def test_agent_image_command_with_locked_seed_uses_that_seed() -> None:
    """话里给了种子＝锁定：这条任务直接用该 seed（Sana 一次 1 张，连号已无意义）。"""
    events: list[str] = []
    service = _agent_with_fake_llm(events)
    result = service.handle("画一只柴犬，种子 123")
    assert [task["parameters"]["seed"] for task in result["tasks"]] == [123]
    assert "种子锁定 123" in result["reply"]


def test_agent_video_and_music_stay_single_task() -> None:
    """一次 4 张只属于图片通道：视频、音乐仍然一条任务（时长/成本都不允许批量）。"""
    events: list[str] = []
    service = _agent_with_fake_llm(events, ("sana-multiling", "wan-t2v", "ace-step-15"))
    video = service.handle("生成一段10秒的海浪拍打礁石延时摄影")
    assert events.count("submit") == 1, events
    assert len(video["tasks"]) == 1
    music = service.handle("生成一首轻快的钢琴曲，纯音乐，90秒")
    assert events.count("submit") == 2, events
    assert len(music["tasks"]) == 1


# ----------------------------------------------------------- 网页端「仅本机」边界
class _StubApp:
    """只带 _check_local_only 需要的属性：不为一条边界检查去起整个服务。"""

    _bound_host = "127.0.0.1"
    logger = logging.getLogger("test.local_only")
    _check_local_only = WebApplication._check_local_only
    _host_of = staticmethod(WebApplication._host_of)
    _port_of = staticmethod(WebApplication._port_of)
    _is_local_host = staticmethod(WebApplication._is_local_host)


def _request(host: str, origin: str = "") -> SimpleNamespace:
    headers = {"host": host}
    if origin:
        headers["origin"] = origin
    return SimpleNamespace(headers=headers)


def test_loopback_host_and_origin_with_different_names_pass() -> None:
    # 本机代理会把 Host 改写成 127.0.0.1，浏览器仍发 Origin: http://localhost:8765；
    # 旧实现按字符串严格相等比较，把这种正常请求判成跨站，整个网页端拿不到数据。
    app = _StubApp()
    app._check_local_only(_request("127.0.0.1:8765", "http://localhost:8765"))
    app._check_local_only(_request("localhost:8765", "http://127.0.0.1:8765"))
    app._check_local_only(_request("[::1]:8765", "http://localhost:8765"))
    app._check_local_only(_request("127.0.0.53:8765"))


def test_foreign_host_or_origin_is_rejected() -> None:
    app = _StubApp()
    with pytest.raises(HttpError):
        app._check_local_only(_request("evil.example.com:8765"))
    with pytest.raises(HttpError):
        app._check_local_only(_request("127.0.0.1:8765", "http://evil.example.com:8765"))
    with pytest.raises(HttpError):
        app._check_local_only(_request(""))


def test_wildcard_bind_still_requires_same_origin() -> None:
    app = _StubApp()
    app._bound_host = "0.0.0.0"
    app._check_local_only(_request("192.168.1.20:8765", "http://192.168.1.20:8765"))
    with pytest.raises(HttpError):
        app._check_local_only(_request("192.168.1.20:8765", "http://evil.example.com"))


# --------------------------------------------- 真走 OpenAI 兼容协议（本地假端点）
def test_agent_chat_goes_through_the_local_llm_endpoint() -> None:
    """智能体的聊天/文案必须真的调用大模型协议，而不是悄悄退回模板。

    用一个只监听 127.0.0.1 随机端口的假端点：不占显存、不联网，但把
    「有没有真的发 /models 与 /chat/completions、模型名怎么选定」全部钉住。
    """
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    seen: dict[str, object] = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:  # 测试里不要刷屏
            return

        def _json(self, payload: dict) -> None:
            raw = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 命名
            if self.path.endswith("/models"):
                self._json({"data": [{"id": "qwen3.5:9b"}, {"id": "llama3.2:3b"}]})
            else:
                self.send_error(404)

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 命名
            length = int(self.headers.get("content-length") or 0)
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            if self.path.endswith("/chat/completions"):
                seen["model"] = body.get("model")
                seen["messages"] = body.get("messages")
                self._json({"choices": [{"message": {"content": "步数 4 步起步，细节多就加到 8-12。"}}]})
            else:
                self.send_error(404)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        service = AgentService(
            task_service=None,
            engine_manager=None,
            config={
                "llm_endpoint": f"http://127.0.0.1:{port}/v1",
                "llm_model": "qwen3.5:9b",
            },
        )
        result = service.handle("FLUX 的步数一般设多少？")
        assert result["degraded"] is False, result["error"]
        assert result["model"] == "qwen3.5:9b"
        assert "8-12" in result["reply"]
        assert seen["model"] == "qwen3.5:9b"
        messages = seen["messages"] or []
        assert messages and messages[0]["role"] == "system"
        assert any("步数" in str(item.get("content", "")) for item in messages)
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)

def test_agent_reaps_its_own_llm_when_the_queue_goes_idle() -> None:
    """十分钟没任务：TaskService 叫智能体把 llama-server 整个停掉，显存当场归还。"""
    events: list[str] = []
    service = _agent_with_fake_llm(events)
    service.local_llm = SimpleNamespace(
        stop_if_idle=lambda: (events.append("stop_if_idle") or {"ok": True, "note": "已卸载自带大模型服务"}),
        owns_endpoint=lambda: True,
        port_open=lambda: True,
        stop=lambda: (events.append("stop") or {"ok": True, "note": "已停止"}),
        sweep_orphans=lambda: (events.append("sweep") or (4242,)),
    )
    service.llm = SimpleNamespace(
        unload=lambda: True,
        resolve_model=lambda: "qwen3.5:9b",
        invalidate=lambda: events.append("invalidate"),
    )
    note = service.reap_idle_llm(600)
    assert events[:2] == ["stop_if_idle", "invalidate"]  # 卸完还要作废大模型端点缓存
    assert "已卸载" in note
    assert service.stop_llm_for_exit() == "已停止"  # 退出时把自家进程一并结束，不留孤儿
    assert events.count("stop") == 1
    assert service.sweep_orphan_llm() == (4242,)


def test_agent_hooks_itself_into_the_task_service_idle_reaper() -> None:
    """任务服务支持挂针时，智能体必须把自己挂上去——否则空闲显存永远还不回来。"""

    class _HookCapableTaskService(_FakeTaskService):
        def __init__(self, events: list[str]) -> None:
            super().__init__(events)
            self.hooks: list[object] = []

        def register_idle_hook(self, callback) -> None:  # noqa: ANN001 - 与 TaskService 同签名
            self.hooks.append(callback)

    events: list[str] = []
    task_service = _HookCapableTaskService(events)
    service = AgentService(
        task_service=task_service,
        engine_manager=_FakeEngineManager(),
        config={"llm_endpoint": "http://127.0.0.1:11434/v1", "llm_model": "qwen3.5:9b"},
    )
    assert len(task_service.hooks) == 1
    assert getattr(task_service.hooks[0], "__self__", None) is service