"""Right-hand parameter panel, the Qt counterpart of the web console's
``aside.panel``: engine select, size/aspect chips, frames, style/genre chips,
music options, steps, seed, negative, advanced JSON. Five generation boards
(``t2i`` / ``i2i`` / ``t2v`` / ``i2v`` / ``music``) plus the ``agent`` view
entry (2026-11-09 重新选型：图片走 Wan2.1 单帧、音乐走 ACE-Step 1.5，全部按
6GB 显存档位设计；旧的 FLUX/SD1.5 档位与 12GB 门槛已随引擎替换而退役。
2026-11-10 文生图换型：文生图走 Sana-1.6B 多语言版（sana-multiling，独立
.venv-image 与 models/sana-multiling，1024 级单遍、超预算按底图+放大），
图生图仍是 Wan2.1-VACE（wan-i2i，与视频共用权重、留在 480p 档）。
All parameter buckets live in the shared settings dict
(``settings["modes"][mode]``) exactly like ``state.settings.modes`` in
app.js, so nothing about quality semantics changes.
"""

from __future__ import annotations

from math import sqrt

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.engine.video.budget import MAX_TOKENS
from app.engine.video.budget import MAX_PIXELS as VIDEO_MAX_PIXELS
from app.engine.video.budget import evaluate as budget_evaluate
from app.engine.video.budget import explain_over as budget_explain
from app.ui.widgets.common import rand_seed

MODES = ("t2i", "i2i", "t2v", "i2v", "music")
MODALITY = {
    "t2i": "image",
    "i2i": "image",
    "t2v": "video",
    "i2v": "video",
    "music": "music",
}
MODE_LABELS = {
    "t2i": "文生图",
    "i2i": "图生图",
    "t2v": "文生视频",
    "i2v": "图生视频",
    "music": "AI音乐",
}
ENGINE_TASK_TYPES = {
    "sana-multiling": ("image",),
    "wan-i2i": ("image",),
    "wan-t2v": ("video",),
    "wan-i2v": ("video",),
    "ace-step-15": ("music",),
}
MODE_ENGINE_ALLOW = {
    # 引擎与标签页一一对应：文生图 sana-multiling（Sana-1.6B 多语言版，独立
    # .venv-image 与 models\sana-multiling，1024 级单遍、超预算按底图+放大）、
    # 图生图 wan-i2i（VACE 参考重绘）、文生视频 wan-t2v、图生视频 wan-i2v（VACE）
    # ——图生图与视频共用 .venv-video 与 models\wan2.1 权重，但三个引擎各自独立
    # 进程、互斥加载，切换板块不会把多份权重同时搬进显存。音乐 ace-step-15（ACE-Step 1.5）。
    "t2i": ("sana-multiling",),
    "i2i": ("wan-i2i",),
    "t2v": ("wan-t2v",),
    "i2v": ("wan-i2v",),
    "music": ("ace-step-15",),
}
ENGINE_STATE_LABELS = {"stopped": "未启动", "ready": "就绪", "failed": "失败"}

# 左侧栏（视图）合法取值：AI智能体 + 五个生成板块。「agent」只切视图，不产生
# 任何生成参数桶，所以参数栏在 agent 视图下整块收起。
VIEW_MODES = ("agent",) + MODES

# 这条线只在 budget.MAX_PIXELS 一处定义（= 832*480*115//100 = 459264，约 0.46MP，
# 整数运算；浮点写法 int(832*480*1.15) 会得到 459263，差的那 1 像素就是当年三端
# 分叉的源头）。1.3B 是 480p 档模型，本文件直接用上面 import 进来的 VIDEO_MAX_PIXELS 别名，
# app/engine/video/wan.py 的 _MAX_PIXELS 与 app/web/static/app.js 的 VIDEO_MAX_PIXELS
# 也同一份账，不再各写一套（以前本文件自己重算一遍、还另设 459000 的「预警线」，
# 三个数差几百像素，临界档位会打架；tests/test_mode_theme.py 现在会钉住三端同值）。
# 超过这条线的档位，面板在提交前挡下并给具体出路（不自动降档）；只有「放大」那条
# 重提交路径先把目标尺寸等比压到预算内，所以引擎永远收不到越线的请求。

# 出图 / 出视频分辨率只按引擎真正跑得动的档位给。图生图与视频是同一张 Wan2.1
# 1.3B 权重、同一个 480p 预算：宽高都是 16 的倍数（VAE 空间 8× + 时间 4× 压缩），
# 且不超过 VIDEO_MAX_PIXELS；要 1024 级的高像素出图走「文生图」板块（Sana 档）。
ASPECT_CHIPS_VIDEO = [
    (832, 480, "16:9 480p"),
    (480, 832, "9:16 480p"),
    (624, 624, "1:1 480p"),
    (640, 480, "4:3 480p"),
    (480, 640, "3:4 480p"),
]

# --------------------------------------------------------------------------- 文生图（Sana）
# 与 app/engine/image/plan.py 的 SANA_* 同一份数字，两端（桌面 / 网页）不能各写一套。
# 倍数 32：Sana 用的 DC-AE 是 32 倍空间下采样，边长不是 32 的倍数引擎会自己对齐，
# 界面上提前对齐才不会显示跑不出来的假档位。
IMAGE_T2I_MULTIPLE = 32
# 单遍像素预算 1_100_000（≈1024 级）：1024×1024（1.05MP）与 1152×896 / 1280×832
# 这类档位都放得下，一次直出。超过这条线引擎不再单遍跑，而是按预算出底图 +
# Lanczos 放大到目标尺寸（元数据如实标 upscaled=true、tiles=0）。
IMAGE_T2I_MAX_PIXELS = 1_100_000
# 目标像素硬上限 4_200_000 = 2048×2048：超过就纯属插值放大、没有细节可谈，直接拒。
IMAGE_T2I_MAX_TARGET_PIXELS = 4_200_000
# 宽高滑杆范围 320-2048：上限取 SANA_MAX_SIDE（2048×2048 正好也是上面的目标硬上限那级）；
# 下限 320 = 32×10，再小连 10 个 DC-AE 下采样格都凑不齐，出图基本是糊的。
IMAGE_T2I_MIN_SIDE = 320
IMAGE_T2I_MAX_SIDE = 2_048

# 文生图档位表：前五档都在 1024 级单遍预算内直出，最后一档 1920×1088 是「放大」
# 路径（1080p 由 1024 级底图 Lanczos 放大而来，标签里就写清楚，不假装高清重绘）。
ASPECT_CHIPS_T2I = [
    (1024, 1024, "1:1 1024"),
    (1152, 640, "16:9 1024级"),
    (640, 1152, "9:16 1024级"),
    (1152, 864, "4:3 1024级"),
    (864, 1152, "3:4 1024级"),
    (1920, 1088, "16:9 1080p（放大）"),
]

STYLE_CHIPS = [
    ("摄影", "photorealistic, professional photography, natural lighting"),
    ("动漫", "anime key visual, clean line art, cel shading"),
    ("赛博朋克", "cyberpunk, neon lights, cinematic, high detail"),
    ("油画", "oil painting, impasto brushwork, classical composition"),
    ("水彩", "watercolor illustration, soft washes, paper texture"),
    ("3D", "3d render, octane, studio lighting, ultra detailed"),
    ("极简", "minimalist, flat design, generous negative space"),
]
GENRE_CHIPS = [
    ("电子", "synthwave, retro analog synths, driving beat"),
    ("Lo-Fi", "lo-fi hip hop, dusty drums, mellow, chillhop"),
    ("管弦", "epic orchestral, cinematic strings, powerful brass"),
    ("民谣", "acoustic folk, warm guitar, intimate"),
    ("爵士", "jazz piano trio, swing, smoky lounge"),
    ("氛围", "ambient, drone, meditative, slow evolving"),
    ("金属", "metal, distorted guitars, double kick, aggressive"),
]

# 诚实文案：文生图 / 图生图 / 音乐档的峰值显存是按 6GB 档位设计后的「预期值」，
# 尚未在这台机器上实测（验收要看任务元数据里的 peak_vram_mb）；视频档的 2021MB
# 是真机实测值。文生图与图生图现在是两个不同的引擎（Sana / Wan-VACE），
# 两套权重两套 venv，所以文案必须分成 t2i 与 i2i 两段讲，不能混在一段里。
ENGINE_MODE_HINTS = {
    # 与网页端 app.js 的 t2i 段逐字一致（改一处就要同步另一处）。
    "t2i": (
        "Sana-1.6B 多语言版（Apache-2.0；文本编码器是 Gemma-2-2B-IT，受 Gemma Terms of Use "
        "约束；本地离线推理，无云端 API）：文生图引擎 sana-multiling，输出 PNG。"
        "中文、英文、中英混合与 Emoji 提示词原生支持（模型卡原文声明 it supports Emoji, "
        "Chinese and English and all mixed prompts），不做任何翻译改写，提示词原样进编码器。"
        "按 6GB 显存档位设计：fp16 三件权重（编码器 4.99GB / Linear-DiT 3.06GB / DC-AE VAE "
        "1.19GB）走逐模块 CPU 换入，任一时刻显存里只有当前那一件，峰值按最大单件加激活压在 "
        "6GB 内——这是按权重体积推算的预期值，尚未真机实测，验收看任务元数据 peak_vram_mb；"
        "空闲显存低于约 5GB 会自动退到逐层换入（峰值更低、慢两三倍），低于约 1500MB 会点名"
        "占用者并明确拒绝，不会硬扛 OOM。加载权重需要约 16GB 空闲物理内存，不足会在加载前"
        "拦截。分辨率必须按 32 的倍数（DC-AE 32 倍下采样），单遍上限约 1.10MP（1024 级直出）；"
        "超过单遍预算走「预算内出底图 + Lanczos 放大」，1080p 就是这条路径，元数据里如实标 "
        "upscaled=true、tiles=0，不谎称高清重绘。CFG 固定 4.5、不是用户参数，所以界面上没有 "
        "CFG 滑杆。权重在 models\\sana-multiling（约 9.3GB，"
        "只含 fp16 变体），缺失时用 scripts\\download_sana_weights.py 补。出图前先释放本机 "
        "LLM 引擎占的显存（scripts\\release_local_engine.bat）。与视频/音乐共用 FIFO 串行队列，"
        "一次 1 张。"
    ),
    # 与网页端 app.js 的 i2i 段逐字一致。
    "i2i": (
        "Wan2.1-VACE-1.3B 参考重绘（Apache-2.0，本地离线推理，无云端 API）：图生图引擎 "
        "wan-i2i，必须上传参考图，输出 PNG。文本编码器 UMT5-XXL 原生支持中英文提示词。"
        "与视频引擎共用 .venv-video 与 models\\wan2.1 权重：bf16 + 逐层 CPU 卸载 + VAE tiling，"
        "视频档真机实测峰值 2021MB；单帧的 token 约为 81 帧视频的 1/13，图生图档峰值预期"
        "低于该实测值（尚未实测，验收看任务元数据 peak_vram_mb）；逐层卸载需要约 16GB 空闲"
        "物理内存。分辨率停在 480p 档（宽高 16 的倍数，约 0.46MP 像素预算）；超预算"
        "「不会自动降档」，提交前会被挡下并给出这一比例的最大可用档位。要 1024 级的高"
        "像素出图请用「文生图」板块（Sana 多语言版）。"
    ),
    # 与网页端 app.js 的 video 段逐字一致（门槛数字 2000 与 app.js / 本文件上面的
    # VIDEO_FREE_FLOOR_MB 同步；改文案或改门槛都要同时改两处）。
    "video": (
        "Wan2.1 1.3B（Apache-2.0，本地离线）：文生视频 wan-t2v / 图生视频 wan-i2v（VACE），"
        "导出 16fps mp4，帧数 5-161（自动取 4n+1）。分辨率停在 480p 档（宽高 16 的倍数，"
        "约 0.46MP 像素预算），1.3B 档最高约 480p，要 1080p 得另配超分或更大的模型。"
        "按 6GB 显存档位设计：bf16 + 逐层 CPU 卸载，真机实测峰值 2021MB，起跑只需约 "
        "1500MB 空闲显存；逐层卸载还需要约 16GB 空闲物理内存。档位钉死：显存上限由 DiT "
        "token 数决定（宽/16 × 高/16 × 压缩帧数，压缩帧数 =（帧数-1)/4+1，上限 20000），"
        "超线的组合不会自动降分辨率也不会偷改帧数，而是在提交前就被挡下，提示里写清"
        "保持分辨率的最长帧数与保持帧数的最大分辨率（面板上实时显示当前档位的这两个上限）。"
        "与智能体排的任务共用 FIFO 串行队列。提交前本端会先把自己拉起的空闲引擎与大模型"
        "释放掉再实测空闲显存，空闲低于 2000MB 会拦下提交并点名是哪个程序占着显存；"
        "要手动清可以跑 scripts\\release_local_engine.bat。"
    ),
    "music": (
        "ACE-Step 1.5（MIT，本地离线，无云端 API）：48kHz wav，带人声（中英文）或纯器乐，"
        "10-480 秒。按 6GB 显存档位设计：5Hz 语言模型与 DiT 走 CPU 卸载，峰值显存为预期值"
        "（未实测，验收看任务元数据 peak_vram_mb）；turbo 默认 8 步。权重在 models\\ace-step-1.5"
        "（约 9.4GB，占物理内存），首次加载 1-3 分钟。与图片/视频共用 FIFO 串行队列。"
        "提交前本端会先把自己拉起的空闲引擎释放掉再实测空闲显存，空闲低于 6144MB 会拦下"
        "提交并点名占用者。"
    ),
}


def mode_hint_key(mode: str) -> str:
    """板块 → 引擎说明文案的键。文生图和图生图现在是两个不同的引擎，必须分开讲。"""
    if mode == "t2i":
        return "t2i"
    if mode == "i2i":
        return "i2i"
    if mode == "music":
        return "music"
    return "video"


# 文生图（Sana-1.6B 多语言版）的步数说明：范围与引擎侧的夹取口径一致（10-40），
# 滑到范围外只是让引擎把数改写回边界，没有意义。CFG 在 Sana 里固定 4.5、不是用户
# 参数，界面上也就没有那个滑杆——这点在提示里写明白，省得用户以为少了个旋钮。
# 步数提示与网页端 #steps-hint 逐字一致（改一处要同步另一处）；「没有 CFG 滑杆」这条
# 事实写在上面的 t2i 引擎说明段里，两端都有，不重复写在步数提示里。
STEPS_HINT_T2I = "文生图（Sana）默认 20 步，引擎范围 10-40；6GB 上步数直接决定等待时长。"


MUSIC_PANEL_WARNING = (
    "已接入 ACE-Step 1.5（权重 MIT，本地离线推理，无云端 API；按 6GB 显存档位设计，"
    "LM/DiT 走 CPU 卸载，峰值为预期值未实测）。支持带人声（中英文）或纯器乐乐曲，"
    "10-480 秒，输出 48kHz wav；不支持超长多轨道复杂编曲。显存不足会被引擎明确拦截，"
    "不会伪造输出。"
)


def default_mode_params(mode: str) -> dict:
    # 文生图默认 1024×1024 / 20 步：1024 级正好落在 Sana 的单遍像素预算内
    # （1.05MP < 1_100_000），20 步是 Sana 官方推荐的采样数（引擎按 10-40 夹取）。
    # 其余板块留在 Wan 的 480p 档默认值（832×480 / 28 步）；音乐 turbo 推荐 8 步。
    t2i = mode == "t2i"
    p = {
        "width": 1024 if t2i else 832,
        "height": 1024 if t2i else 480,
        "frames": 33,
        "steps": 8 if mode == "music" else (20 if t2i else 28),
        "seedLock": False,
        "seed": None,
        "negative": "",
        "advanced": "",
    }
    return p


def default_settings() -> dict:
    return {
        "mode": "agent",
        "refDir": "",
        "vengine": "",
        "mengine": "ace-step-15",
        "bpm": 120,
        "musicDuration": 180,
        "instrumental": False,
        "vocalLanguage": "zh",
        "modes": {mode: default_mode_params(mode) for mode in MODES},
    }


def mode_label(mode: object) -> str:
    """板块的显示名；陌生取值原样返回，历史记录不会渲染成空白或 KeyError。"""

    key = str(mode or "")
    return MODE_LABELS.get(key) or key


def coerce_view_mode(value: object) -> str:
    """把 settings["mode"]（视图板块）收敛到合法值。

    2026-11-09 重新选型后 t2i / i2i / music 三个板块重新在岗，旧存档里存的
    这三个值直接有效；只有真正的陌生值（含空）才回落到「AI智能体」。
    """

    mode = str(value or "")
    if mode in VIEW_MODES:
        return mode
    return "agent"


def _snap_multiple(value, default: int, low: int, high: int, multiple: int) -> int:
    try:
        n = int(round(float(value)))
    except (TypeError, ValueError):
        n = int(default)
    n = max(low, min(high, n))
    n -= n % multiple
    return max(low, n)


def _clamp_wh_wan(p: dict) -> None:
    """图生图 / 视频档的宽高：16 的倍数（VAE 空间 8× + 时间 4× 压缩）、边长 256-1024、
    超过 VIDEO_MAX_PIXELS（= budget.MAX_PIXELS，全仓一份数）等比缩到预算内再回到
    16 的倍数。这条缩只发生在「面板上的档位本身放不下」和「放大重提交」两种场合，
    为的是引擎永远收不到越线请求；用户已经选好且在预算内的档位不会被改动（档位钉死，
    超线组合由 _sync_size 在提交前挡下，不自动降档）。"""

    width = _snap_multiple(p.get("width"), 832, 256, 1024, 16)
    height = _snap_multiple(p.get("height"), 480, 256, 1024, 16)
    pixels = width * height
    if pixels > VIDEO_MAX_PIXELS:
        factor = sqrt(VIDEO_MAX_PIXELS / pixels)
        width = _snap_multiple(width * factor, width, 256, 1024, 16)
        height = _snap_multiple(height * factor, height, 256, 1024, 16)
    p["width"], p["height"] = width, height


def _clamp_wh_sana(p: dict) -> None:
    """文生图档（Sana）的宽高：32 的倍数（DC-AE 32 倍下采样）、边长 320-2048，
    目标超过 IMAGE_T2I_MAX_TARGET_PIXELS（2048×2048 那级）等比缩回硬上限内——再高
    就纯属插值，引擎侧直接拒收，界面上不给跑不出来的假档位。

    注意这里**不**按单遍预算 IMAGE_T2I_MAX_PIXELS 往下缩：1080p（1920×1088）这类
    超过单遍预算的目标是合法档位，引擎会按预算出底图再 Lanczos 放大过去
    （元数据如实标 upscaled=true、tiles=0），不是把尺寸偷偷改掉。"""

    width = _snap_multiple(
        p.get("width"), 1024, IMAGE_T2I_MIN_SIDE, IMAGE_T2I_MAX_SIDE, IMAGE_T2I_MULTIPLE
    )
    height = _snap_multiple(
        p.get("height"), 1024, IMAGE_T2I_MIN_SIDE, IMAGE_T2I_MAX_SIDE, IMAGE_T2I_MULTIPLE
    )
    pixels = width * height
    if pixels > IMAGE_T2I_MAX_TARGET_PIXELS:
        factor = sqrt(IMAGE_T2I_MAX_TARGET_PIXELS / pixels)
        width = _snap_multiple(
            width * factor, width, IMAGE_T2I_MIN_SIDE, IMAGE_T2I_MAX_SIDE, IMAGE_T2I_MULTIPLE
        )
        height = _snap_multiple(
            height * factor, height, IMAGE_T2I_MIN_SIDE, IMAGE_T2I_MAX_SIDE, IMAGE_T2I_MULTIPLE
        )
    p["width"], p["height"] = width, height


def _clamp_video(p: dict, mode: str = "t2v") -> dict:
    """图生图 / 视频 / 音乐板块的参数夹进引擎真能吃的范围（与 app/engine/video/wan.py、
    app/tasks/music_worker.py 对齐）。文生图走 :func:`_clamp_t2i`（Sana 档）。"""

    _clamp_wh_wan(p)
    p["frames"] = snap_frames(p.get("frames"))
    try:
        steps = int(p.get("steps"))
    except (TypeError, ValueError):
        steps = 28
    p["steps"] = max(1, min(100 if mode != "music" else 50, steps))
    _coerce_seed_and_text(p)
    return p


def _clamp_t2i(p: dict) -> dict:
    """文生图（sana-multiling，Sana-1.6B 多语言版）的参数夹进引擎真能吃的范围，
    数字与 app/engine/image/plan.py 的 SANA_* 同一份。"""

    _clamp_wh_sana(p)
    p["frames"] = snap_frames(p.get("frames"))
    try:
        steps = int(p.get("steps"))
    except (TypeError, ValueError):
        steps = 20
    # 引擎按 10-40 夹取（Sana 官方推荐 20 步）：界面用同一个范围，滑到引擎会改写的位置没意义。
    p["steps"] = max(10, min(40, steps))
    _coerce_seed_and_text(p)
    return p


def _coerce_seed_and_text(p: dict) -> None:
    """seed / 负面 / 高级参数的通用清洗：两个夹取函数（Wan 档与 Sana 档）共用同一份口径。"""

    seed = p.get("seed")
    if seed is not None:
        try:
            p["seed"] = int(seed)
        except (TypeError, ValueError):
            p["seed"] = None
    p["seedLock"] = bool(p.get("seedLock"))
    p["negative"] = str(p.get("negative") or "")
    p["advanced"] = str(p.get("advanced") or "")


def normalize_settings(raw: dict | None) -> dict:
    base = default_settings()
    if isinstance(raw, dict):
        for key, value in base.items():
            if key == "modes":
                continue
            if key in raw and isinstance(raw[key], type(value) if value is not None else object):
                base[key] = raw[key]
        modes_raw = raw.get("modes") or {}
        for mode in MODES:
            stored = modes_raw.get(mode)
            merged = dict(base["modes"][mode])
            if isinstance(stored, dict):
                merged.update({k: v for k, v in stored.items() if k in merged})
            # 两套引擎两套档位：文生图（Sana）按 32 倍数与 1024 级预算夹，
            # 图生图 / 视频 / 音乐仍按 Wan 的 16 倍数与 480p 预算夹。
            base["modes"][mode] = _clamp_t2i(merged) if mode == "t2i" else _clamp_video(merged, mode)
    base["mode"] = coerce_view_mode(base.get("mode"))
    # 音乐档的顶层参数（BPM / 时长 / 纯器乐 / 人声语言）：夹进引擎白名单范围。
    try:
        base["bpm"] = max(30, min(300, int(base.get("bpm") or 120)))
    except (TypeError, ValueError):
        base["bpm"] = 120
    try:
        base["musicDuration"] = max(10, min(480, int(base.get("musicDuration") or 180)))
    except (TypeError, ValueError):
        base["musicDuration"] = 180
    base["instrumental"] = bool(base.get("instrumental"))
    if base.get("vocalLanguage") not in ("zh", "en"):
        base["vocalLanguage"] = "zh"
    return base


def snap_frames(value) -> int:
    try:
        n = max(5, min(161, int(round(float(value)))))
    except (TypeError, ValueError):
        n = 33
    return n - ((n - 1) % 4)


class _Section(QFrame):
    def __init__(self, modes: set[str], parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("panelSection")
        self.modes = modes
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 8, 0, 12)
        self._layout.setSpacing(8)

    def add_title(self, text: str) -> QLabel:
        label = QLabel(text, self)
        label.setObjectName("sectionTitle")
        self._layout.addWidget(label)
        return label

    def add_value_row(self, text: str) -> tuple[QLabel, QLabel]:
        row = QHBoxLayout()
        row.setSpacing(6)
        title = QLabel(text, self)
        title.setObjectName("sectionTitle")
        value = QLabel("", self)
        value.setObjectName("metaDim")
        row.addWidget(title)
        row.addWidget(value)
        row.addStretch(1)
        self._layout.addLayout(row)
        return title, value

    def add_hint(self, text: str, warn: bool = False) -> QLabel:
        hint = QLabel(text, self)
        hint.setWordWrap(True)
        hint.setTextFormat(Qt.TextFormat.PlainText)
        if warn:
            hint.setObjectName("hintWarn")
        else:
            hint.setObjectName("hint")
        self._layout.addWidget(hint)
        return hint


class ParamPanel(QScrollArea):
    engine_changed = Signal(str)          # engine_id chosen in the select
    restart_requested = Signal(str)       # restart this engine id
    params_changed = Signal()             # any bucket/settings value edited
    style_toggled = Signal(str, bool)     # (suffix, checked)

    def __init__(self, settings: dict, persist, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("panelColumn")
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.settings = settings
        self.persist = persist
        self._syncing = False
        self._engines: list = []
        self.mode = coerce_view_mode(settings.get("mode"))

        body = QWidget(self)
        body.setObjectName("panelBody")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(14, 12, 14, 18)
        layout.setSpacing(0)

        head = QLabel("参数设置", body)
        head.setObjectName("panelTitle")
        layout.addWidget(head)

        # ---------------------------------------------------------------- 引擎
        sec_engine = _Section(set(MODES), body)
        sec_engine.add_title("引擎")
        self.engine_select = QComboBox(sec_engine)
        self.engine_select.setObjectName("engineSelect")
        self.engine_select.currentIndexChanged.connect(self._on_engine_selected)
        sec_engine._layout.addWidget(self.engine_select)
        state_row = QHBoxLayout()
        state_row.setSpacing(8)
        self.engine_state = QLabel("", sec_engine)
        self.engine_state.setObjectName("metaDim")
        self.engine_state.setWordWrap(True)
        restart = QPushButton("重启引擎", sec_engine)
        restart.setObjectName("btnMini")
        restart.clicked.connect(
            lambda: self.restart_requested.emit(self.engine_id())
        )
        state_row.addWidget(self.engine_state, 1)
        state_row.addWidget(restart)
        sec_engine._layout.addLayout(state_row)
        self.engine_mode_hint = sec_engine.add_hint(
            ENGINE_MODE_HINTS[mode_hint_key(self._engine_mode())]
        )
        layout.addWidget(sec_engine)

        # ------------------------------------------------ 尺寸（文生图：Sana 1024 级档）
        # 文生图跑 sana-multiling（独立 .venv-image 与 models\sana-multiling），跟 Wan
        # 的 480p 完全不是一套档位，必须单独一段：宽高超齐取 32 的倍数（DC-AE 32 倍
        # 下采样），范围 320-2048；1024 级（IMAGE_T2I_MAX_PIXELS）之内单遍直出，
        # 超过这条线的目标（1080p 档）由引擎按「预算内底图 + Lanczos 放大」出。
        self.t2i_size_modes = {"t2i"}
        sec_t2i_size = _Section(self.t2i_size_modes, body)
        self.t2i_size_title = sec_t2i_size.add_title("生成尺寸（1024 级，可到 1080p）")
        self.aspect_chips_t2i = self._chip_row(
            sec_t2i_size,
            [(label, (w, h)) for w, h, label in ASPECT_CHIPS_T2I],
            kind="aspect",
        )
        self.iwidth_range, self.iwidth_num = self._size_row(
            sec_t2i_size,
            "宽",
            IMAGE_T2I_MIN_SIDE,
            IMAGE_T2I_MAX_SIDE,
            IMAGE_T2I_MULTIPLE,
            IMAGE_T2I_MIN_SIDE,
            IMAGE_T2I_MAX_SIDE,
        )
        self.iheight_range, self.iheight_num = self._size_row(
            sec_t2i_size,
            "高",
            IMAGE_T2I_MIN_SIDE,
            IMAGE_T2I_MAX_SIDE,
            IMAGE_T2I_MULTIPLE,
            IMAGE_T2I_MIN_SIDE,
            IMAGE_T2I_MAX_SIDE,
        )
        self.image_hint = sec_t2i_size.add_hint("")
        layout.addWidget(sec_t2i_size)

        # -------------------------------------------- 尺寸（图生图/视频：Wan 480p 档）
        # 图生图（wan-i2i）与视频跑的是同一张 Wan2.1 1.3B 权重、同一个 480p 像素预算，
        # 所以这三个档共用这一组档位与滑杆；只有帧数（时间维）是视频档独有的。
        self.size_modes = {"i2i", "t2v", "i2v"}
        sec_video_size = _Section(self.size_modes, body)
        self.size_title = sec_video_size.add_title("生成尺寸（480p 档）")
        self.aspect_chips_video = self._chip_row(
            sec_video_size,
            [(label, (w, h)) for w, h, label in ASPECT_CHIPS_VIDEO],
            kind="aspect",
        )
        self.vwidth_range, self.vwidth_num = self._size_row(
            sec_video_size, "宽", 256, 1024, 16, 256, 1024
        )
        self.vheight_range, self.vheight_num = self._size_row(
            sec_video_size, "高", 256, 1024, 16, 256, 1024
        )
        self.video_hint = sec_video_size.add_hint("")
        layout.addWidget(sec_video_size)

        # ---------------------------------------------------------------- 时长
        self.frames_modes = {"t2v", "i2v"}
        sec_frames = _Section(self.frames_modes, body)
        _, self.frames_label = sec_frames.add_value_row("时长")
        self.frames_range = self._slider(sec_frames, 5, 161, 4, 33)
        self.frames_range.valueChanged.connect(self._on_frames)
        sec_frames.add_hint(
            "导出固定 16fps（Wan 原生）；帧数自动取到 4n+1（时间压缩约束），越长越吃显存与时间。"
        )
        layout.addWidget(sec_frames)

        # ------------------------------------------------------ 风格 / 曲风 chip
        sec_style = _Section(set(MODES), body)
        sec_style.add_title("风格 / 曲风（点击追加到提示词）")
        self.style_chips = self._chip_row(
            sec_style,
            [(label, suffix) for label, suffix in STYLE_CHIPS],
            kind="style",
        )
        self.genre_chips = self._chip_row(
            sec_style,
            [(label, suffix) for label, suffix in GENRE_CHIPS],
            kind="genre",
        )
        self.genre_chips_container.setVisible(False)
        layout.addWidget(sec_style)

        # ---------------------------------------------------------------- 音乐
        self.music_modes = {"music"}
        sec_music = _Section(self.music_modes, body)
        sec_music.add_title("音乐选项")
        sec_music.add_hint(MUSIC_PANEL_WARNING, warn=True)
        _, self.bpm_label = sec_music.add_value_row("BPM")
        self.bpm_range = self._slider(sec_music, 60, 180, 1, 120)
        self.bpm_range.valueChanged.connect(self._on_bpm)
        _, self.music_dur_label = sec_music.add_value_row("目标时长")
        self.music_dur_range = self._slider(sec_music, 30, 480, 30, 180)
        self.music_dur_range.valueChanged.connect(self._on_music_duration)
        self.instrumental_toggle = QPushButton("纯器乐（不要人声）", sec_music)
        self.instrumental_toggle.setObjectName("chip")
        self.instrumental_toggle.setCheckable(True)
        self.instrumental_toggle.toggled.connect(self._on_instrumental)
        sec_music._layout.addWidget(self.instrumental_toggle)
        lang_row = QHBoxLayout()
        lang_row.setSpacing(8)
        lang_caption = QLabel("人声语言", sec_music)
        lang_caption.setObjectName("sliderLabel")
        self.vocal_lang_select = QComboBox(sec_music)
        self.vocal_lang_select.setObjectName("engineSelect")
        self.vocal_lang_select.addItem("中文（zh）", "zh")
        self.vocal_lang_select.addItem("English (en)", "en")
        self.vocal_lang_select.currentIndexChanged.connect(self._on_vocal_language)
        lang_row.addWidget(lang_caption)
        lang_row.addWidget(self.vocal_lang_select, 1)
        sec_music._layout.addLayout(lang_row)
        sec_music.add_hint(
            "时长 30-480 秒（引擎白名单 10-480，超出会被截到上限，产物按真实时长为准）；"
            "BPM 60-180（引擎可到 30-300，要更极端的速度写进高级参数）。"
        )
        layout.addWidget(sec_music)

        # --------------------------------------------------------------- steps
        # 步数范围按引擎分开：文生图（Sana-1.6B）10-40、默认 20（引擎自己也是按
        # 10-40 夹取，滑到范围外只是让引擎改写你的数）；图生图 / 视频 / 音乐保持
        # 原来的滑杆范围与默认 28 步不变。
        sec_steps = _Section(set(MODES), body)
        _, self.steps_label = sec_steps.add_value_row("Steps")
        self.steps_range = self._slider(sec_steps, 1, 50, 1, 28)
        self.steps_range.valueChanged.connect(self._on_steps)
        self.steps_hint_default = STEPS_HINT_T2I
        self.steps_hint = sec_steps.add_hint(self.steps_hint_default)
        layout.addWidget(sec_steps)

        # ---------------------------------------------------------------- seed
        sec_seed = _Section(set(MODES), body)
        sec_seed.add_title("Seed")
        seed_row = QHBoxLayout()
        seed_row.setSpacing(8)
        self.seed_lock = QPushButton("固定", sec_seed)
        self.seed_lock.setObjectName("chip")
        self.seed_lock.setCheckable(True)
        self.seed_lock.toggled.connect(self._on_seed_lock)
        self.seed_num = QSpinBox(sec_seed)
        self.seed_num.setObjectName("numWide")
        self.seed_num.setRange(-1, 2_147_483_647)
        self.seed_num.setValue(-1)
        self.seed_num.setSpecialValueText("随机")
        self.seed_num.valueChanged.connect(self._on_seed_num)
        dice = QPushButton("🎲", sec_seed)
        dice.setObjectName("btnMini")
        dice.clicked.connect(self._on_seed_dice)
        seed_row.addWidget(self.seed_lock)
        seed_row.addWidget(self.seed_num, 1)
        seed_row.addWidget(dice)
        sec_seed._layout.addLayout(seed_row)
        self.seed_hint = sec_seed.add_hint(
            "不固定 = 每次随机 seed；固定 = 可复现同一张图 / 同一段视频 / 同一段音乐。"
        )
        layout.addWidget(sec_seed)

        # -------------------------------------------------------- 负面提示词
        self.negative_modes = {"t2i", "i2i", "t2v", "i2v"}
        sec_negative = _Section(self.negative_modes, body)
        sec_negative.add_title("负面提示词")
        self.negative_input = QPlainTextEdit(sec_negative)
        self.negative_input.setObjectName("textareaSm")
        self.negative_input.setFixedHeight(56)
        self.negative_input.setPlaceholderText("模糊、低质量、水印…")
        self.negative_input.textChanged.connect(self._on_negative)
        sec_negative._layout.addWidget(self.negative_input)
        layout.addWidget(sec_negative)

        # ---------------------------------------------------------- 高级参数
        self.advanced_modes = {"t2i", "i2i", "t2v", "i2v"}
        sec_advanced = _Section(self.advanced_modes, body)
        sec_advanced.add_title("高级参数（JSON，可选）")
        self.advanced_input = QPlainTextEdit(sec_advanced)
        self.advanced_input.setObjectName("textareaSm")
        self.advanced_input.setFixedHeight(56)
        self.advanced_input.setPlaceholderText('{"num_frames": 49, "steps": 30}')
        self.advanced_input.textChanged.connect(self._on_advanced)
        sec_advanced._layout.addWidget(self.advanced_input)
        self.advanced_hint = sec_advanced.add_hint(
            "白名单：width height num_frames steps seed negative_prompt init_image_path"
        )
        layout.addWidget(sec_advanced)

        layout.addStretch(1)
        self.setWidget(body)
        self._sections = [
            sec_engine,
            sec_t2i_size,
            sec_video_size,
            sec_frames,
            sec_style,
            sec_music,
            sec_steps,
            sec_seed,
            sec_negative,
            sec_advanced,
        ]
        self.set_mode(self.mode)
        self._size_handlers(self.vwidth_range, self.vwidth_num, "width")
        self._size_handlers(self.vheight_range, self.vheight_num, "height")
        # 文生图那组滑杆/数字框走的是同一套读写（按 mode 挑桶），只是对齐倍数与
        # 范围不同，所以挂在同样的 _size_handlers 上。
        self._size_handlers(self.iwidth_range, self.iwidth_num, "width")
        self._size_handlers(self.iheight_range, self.iheight_num, "height")

    # ------------------------------------------------------------------ 构造辅助
    def _chip_row(
        self, section: _Section, items, kind: str = ""
    ) -> list[QPushButton]:
        container = QWidget(section)
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        buttons: list[QPushButton] = []
        for label, payload in items:
            chip = QPushButton(label, container)
            chip.setObjectName("chip")
            chip.setCheckable(True)
            chip._payload = payload  # type: ignore[attr-defined]
            chip.clicked.connect(lambda _checked=False, c=chip: self._on_chip(c))
            row.addWidget(chip)
            buttons.append(chip)
        row.addStretch(1)
        section._layout.addWidget(container)
        if kind == "style":
            self.style_chips_container = container
        elif kind == "genre":
            self.genre_chips_container = container
        return buttons

    def _slider(
        self, section: _Section, low: int, high: int, step: int, value: int
    ) -> QSlider:
        slider = QSlider(Qt.Orientation.Horizontal, section)
        slider.setRange(low, high)
        if step > 1:
            slider.setSingleStep(step)
            slider.setPageStep(step)
        slider.setValue(value)
        section._layout.addWidget(slider)
        return slider

    def _size_row(
        self,
        section: _Section,
        label: str,
        slider_low: int,
        slider_high: int,
        slider_step: int,
        spin_low: int,
        spin_high: int,
    ) -> tuple[QSlider, QSpinBox]:
        row = QHBoxLayout()
        row.setSpacing(8)
        caption = QLabel(label, section)
        caption.setObjectName("sliderLabel")
        slider = QSlider(Qt.Orientation.Horizontal, section)
        slider.setRange(slider_low, slider_high)
        slider.setSingleStep(slider_step)
        spin = QSpinBox(section)
        spin.setObjectName("num")
        spin.setRange(spin_low, spin_high)
        spin.setSingleStep(slider_step)
        row.addWidget(caption)
        row.addWidget(slider, 1)
        row.addWidget(spin)
        section._layout.addLayout(row)
        return slider, spin

    # ------------------------------------------------------------------ 模式切换
    def current_params(self) -> dict:
        if self.mode not in MODES:
            return {}
        return self.settings["modes"][self.mode]

    def advanced_text(self) -> str:
        return self.advanced_input.toPlainText().strip()

    def set_mode(self, mode: str) -> None:
        mode = coerce_view_mode(mode)
        self.mode = mode
        self.settings["mode"] = mode
        # agent 视图下没有可编辑的生成参数：整栏自己收起（主窗口同样按
        # mode != "agent" 处理，这里保证启动落在「AI智能体」时右栏不会空着）。
        generating = mode in MODES
        self.setVisible(generating)
        for section in self._sections:
            section.setVisible(generating and mode in section.modes)
        if generating:
            modality = MODALITY[mode]
            # 文生图（Sana，1024 级）与图生图 / 视频（Wan，480p）是两套档位：这个标题
            # 只属于图生图 / 视频那栏，文生图那栏的标题在构造时就固定好了。
            # 措辞与网页端 #size-title 逐字一致（两端都写「生成尺寸（480p 档像素预算）」，
            # 不再一端写「出图尺寸」一端写「视频尺寸」）。
            self.size_title.setText("生成尺寸（480p 档像素预算）")
            self.style_chips_container.setVisible(modality != "music")
            self.genre_chips_container.setVisible(modality == "music")
            # 文生图与图生图现在是两个不同的引擎（Sana / Wan-VACE），两套权重两套 venv，
            # 说明文案按板块分开取，不能再按 modality 共用一段。
            self.engine_mode_hint.setText(ENGINE_MODE_HINTS[mode_hint_key(mode)])
        self.sync_all()
        self.refresh_engines(self._engines)
        self.persist()

    # ------------------------------------------------------------------ 参数同步
    def _bucket(self) -> dict:
        if self.mode not in MODES:
            return {}
        return self.settings["modes"][self.mode]

    def _engine_mode(self) -> str:
        """引擎上下文用哪个板块：agent 视图下按默认生成板块给状态。"""

        return self.mode if self.mode in MODES else MODES[0]

    def _size_controls(self) -> tuple:
        """当前板块这一栏用哪组尺寸控件（滑杆 / 数字框 / 档位 chip / 提示）：
        文生图走 Sana 的 1024 级档（i* 那组），图生图与视频走 Wan 的 480p 档（v* 那组）。
        读写逻辑两边共用，只是对齐倍数、范围与档位表不同。"""

        if self.mode == "t2i":
            return (
                self.iwidth_range,
                self.iwidth_num,
                self.iheight_range,
                self.iheight_num,
                self.aspect_chips_t2i,
                self.image_hint,
            )
        return (
            self.vwidth_range,
            self.vwidth_num,
            self.vheight_range,
            self.vheight_num,
            self.aspect_chips_video,
            self.video_hint,
        )

    def sync_all(self) -> None:
        self._syncing = True
        try:
            if self.mode not in MODES:
                return
            p = self._bucket()
            modality = MODALITY[self.mode]
            if modality != "music":
                self._sync_size(p, *self._size_controls())
                if modality == "video":
                    self._sync_frames()
                self.negative_input.setPlainText(p.get("negative", ""))
                self.advanced_input.setPlainText(p.get("advanced", ""))
                self.advanced_hint.setText(
                    "白名单：width height num_frames steps seed negative_prompt init_image_path"
                )
            else:
                self._sync_music()
            self._sync_steps()
            self._sync_seed()
        finally:
            self._syncing = False

    def _sync_music(self) -> None:
        bpm = int(self.settings.get("bpm") or 120)
        self.bpm_range.setValue(max(self.bpm_range.minimum(), min(self.bpm_range.maximum(), bpm)))
        self.bpm_label.setText(f"{bpm} BPM")
        duration = int(self.settings.get("musicDuration") or 180)
        self.music_dur_range.setValue(
            max(self.music_dur_range.minimum(), min(self.music_dur_range.maximum(), duration))
        )
        self.music_dur_label.setText(f"{duration} 秒 · {duration / 60:.1f} 分钟")
        self.instrumental_toggle.setChecked(bool(self.settings.get("instrumental")))
        lang = str(self.settings.get("vocalLanguage") or "zh")
        index = self.vocal_lang_select.findData(lang)
        self.vocal_lang_select.setCurrentIndex(max(0, index))

    def _sync_size(
        self,
        p: dict,
        w_slider: QSlider,
        w_spin: QSpinBox,
        h_slider: QSlider,
        h_spin: QSpinBox,
        chips: list[QPushButton],
        hint: QLabel,
    ) -> None:
        width = int(p.get("width") or 0)
        height = int(p.get("height") or 0)
        w_slider.setValue(max(w_slider.minimum(), min(w_slider.maximum(), width)))
        h_slider.setValue(max(h_slider.minimum(), min(h_slider.maximum(), height)))
        w_spin.setValue(max(w_spin.minimum(), min(w_spin.maximum(), width)))
        h_spin.setValue(max(w_spin.minimum(), min(w_spin.maximum(), height)))
        for chip in chips:
            chip_w, chip_h = chip._payload
            chip.setChecked(chip_w == width and chip_h == height)
        pixels = width * height
        is_video = MODALITY.get(self.mode) == "video"
        is_t2i = self.mode == "t2i"
        # 文生图（Sana）与图生图 / 视频（Wan）是两套预算：Sana 的 1_100_000 是「单遍」
        # 预算（≈1024 级直出），Wan 的是 480p 档的 459264（= budget.MAX_PIXELS，本文件
        # 上面的 VIDEO_MAX_PIXELS 就是它的 import 别名，不再有第二个「预警线」数字），
        # 另加一份 20000 token 的显存档位上限（app/engine/video/budget.py，与引擎和网页端同一份账）。
        budget = IMAGE_T2I_MAX_PIXELS if is_t2i else VIDEO_MAX_PIXELS
        wan_frames = snap_frames(p.get("frames") or 33) if is_video else 1
        report = None if is_t2i else budget_evaluate(width, height, wan_frames)
        if pixels > budget or (report is not None and not report["ok"]):
            hint.setObjectName("hintWarn")
            if is_video:
                # 不写「引擎会自动缩」这种话：面板上说的就是引擎会用的数，
                # 超线只会发生在提交前，并被讲成具体数字。
                hint.setText(budget_explain(report, "视频"))
            elif is_t2i:
                hint.setText(
                    f"出图 {width}×{height} 超过 1024 级单遍预算 {budget // 1000}K 像素："
                    "引擎会按预算出底图再放大到目标尺寸（元数据 upscaled=true）。"
                )
            else:
                hint.setText(budget_explain(report, "出图"))
        else:
            hint.setObjectName("hint")
            if is_video:
                hint.setText(
                    f"视频档位 {width}×{height} @ {wan_frames} 帧"
                    f"（{wan_frames / 16:.2f} 秒）= {report['tokens']} tokens / 上限 {MAX_TOKENS}；"
                    f"这一比例最长 {report['frames_cap']} 帧（{report['frames_cap_seconds']:.2f} 秒），"
                    f"这个帧数下最大 {report['size_cap'][0]}×{report['size_cap'][1]}。"
                    "档位钉死：按你选的档位原样出片，不会自动降档，超线提交前就挡下。"
                    "另需约 16GB 空闲物理内存。"
                )
            elif is_t2i:
                hint.setText(
                    f"出图像素 {pixels / 1e6:.2f}MP · Sana-1.6B 多语言版按 32 的倍数出图，"
                    f"单遍预算 {budget // 1000}K（1024 级直出），再高按底图 + Lanczos 放大；"
                    "fp16 逐模块 CPU 换入，峰值按 6GB 档设计（未实测，验收看 peak_vram_mb），"
                    "另需约 16GB 空闲物理内存。"
                )
            else:
                hint.setText(
                    f"出图像素 {pixels / 1e6:.2f}MP · 与视频同一张 Wan2.1 权重、同一像素预算，"
                    "单帧 token 约为 81 帧视频的 1/13（预期峰值更低，未实测），"
                    "另需约 16GB 空闲物理内存。"
                )
        hint.style().unpolish(hint)
        hint.style().polish(hint)

    def _sync_steps(self) -> None:
        p = self._bucket()
        steps = int(p.get("steps") or 1)
        # 步数范围按引擎切：文生图（Sana-1.6B）10-40，与 _clamp_t2i 同一口径（引擎自己
        # 也按 10-40 夹取，官方推荐 20 步）；音乐按 music_worker 的 1-50；图生图与视频
        # 按 _clamp_video 的 1-100（滑杆以前只到 50，够不到引擎真能吃的上限）。先把范围
        # 切好、再把桶里的步数夹进新范围回填到滑杆上，否则旧值会留在范围外，
        # 用户一动滑杆就被 Qt 悄悄改写成一个他没选过的数。
        if self.mode == "t2i":
            low, high = 10, 40
        elif MODALITY.get(self.mode) == "music":
            low, high = 1, 50
        else:
            low, high = 1, 100
        self.steps_range.setRange(low, high)
        self.steps_range.setValue(max(low, min(high, steps)))
        self.steps_label.setText(f"{steps} 步")
        if MODALITY.get(self.mode) == "music":
            self.steps_hint.setText(
                "音乐 turbo 权重默认 8 步（推荐）；最多 50 步，步数越多等待越长。"
            )
        elif self.mode == "t2i":
            self.steps_hint.setText(STEPS_HINT_T2I)
        elif self.mode == "i2i":
            self.steps_hint.setText(
                "图生图推荐 28 步（默认）；6GB 上步数直接决定等待时长。"
            )
        else:
            self.steps_hint.setText(
                "视频推荐 28 步（默认）；6GB 上步数直接决定等待时长。"
            )

    def _sync_seed(self) -> None:
        p = self._bucket()
        self.seed_lock.setChecked(bool(p.get("seedLock")))
        self.seed_num.setEnabled(bool(p.get("seedLock")))
        seed = p.get("seed")
        self.seed_num.setValue(int(seed) if isinstance(seed, (int, float)) else -1)
        self.seed_hint.setText(
            "不固定 = 每次随机 seed；固定 = 可复现同一张图 / 同一段视频 / 同一段音乐。"
        )

    def _sync_frames(self) -> None:
        p = self._bucket()
        frames = snap_frames(p.get("frames") or 33)
        self.frames_range.setValue(frames)
        self.frames_label.setText(f"{frames / 16:.2f} 秒 · {frames} 帧 @16fps")

    # ------------------------------------------------------------------ 交互
    def _commit(self) -> None:
        if not self._syncing:
            self.params_changed.emit()

    def _set_wh(self, key: str, value: int) -> None:
        p = self._bucket()
        if not p:
            return
        p[key] = int(value)
        self._clamp_into_budget(p)
        self._syncing = True
        try:
            self._sync_size(p, *self._size_controls())
        finally:
            self._syncing = False
        self._commit()
        self.persist()

    def _clamp_into_budget(self, p: dict) -> None:
        """宽高改完后自动回到当前引擎真能吃的倍数与预算内（引擎同样会做这步）：
        文生图按 32 的倍数与 2048 级目标硬上限（Sana 档），图生图 / 视频按 16 的倍数
        与 480p 像素预算（Wan 档）。"""

        if self.mode == "t2i":
            _clamp_wh_sana(p)
        else:
            _clamp_wh_wan(p)

    def _size_handlers(self, slider: QSlider, spin: QSpinBox, key: str) -> None:
        slider.valueChanged.connect(
            lambda value: self._size_edge(slider, spin, key, value, from_slider=True)
        )
        spin.valueChanged.connect(
            lambda value: self._size_edge(slider, spin, key, value, from_slider=False)
        )

    def _size_edge(
        self,
        slider: QSlider,
        spin: QSpinBox,
        key: str,
        value: int,
        from_slider: bool,
    ) -> None:
        if self._syncing or not self._bucket():
            return
        self._set_wh(key, int(value))

    def _on_chip(self, chip: QPushButton) -> None:
        payload = chip._payload
        p = self._bucket()
        if not p:
            return
        if chip.objectName() == "chip" and isinstance(payload, tuple) and len(payload) == 2:
            width, height = payload
            p["width"], p["height"] = int(width), int(height)
            self._clamp_into_budget(p)
            self._syncing = True
            try:
                self._sync_size(p, *self._size_controls())
            finally:
                self._syncing = False
            self._commit()
            self.persist()
            return
        # 风格 / 曲风 chip：追加/移除 suffix（由创作页改写提示词）
        suffix = str(payload)
        on = chip.isChecked()
        self.style_toggled.emit(suffix, on)

    def _on_frames(self, value: int) -> None:
        if self._syncing:
            return
        p = self._bucket()
        if not p:
            return
        p["frames"] = snap_frames(value)
        self._syncing = True
        try:
            self._sync_frames()
        finally:
            self._syncing = False
        self._commit()
        self.persist()

    def _on_steps(self, value: int) -> None:
        if self._syncing:
            return
        p = self._bucket()
        if not p:
            return
        p["steps"] = int(value)
        self.steps_label.setText(f"{value} 步")
        self._commit()
        self.persist()

    def _on_seed_lock(self, checked: bool) -> None:
        if self._syncing:
            return
        p = self._bucket()
        if not p:
            return
        p["seedLock"] = bool(checked)
        if not checked:
            p["seed"] = None
        self._syncing = True
        try:
            self._sync_seed()
        finally:
            self._syncing = False
        self._commit()
        self.persist()

    def _on_seed_num(self, value: int) -> None:
        if self._syncing:
            return
        p = self._bucket()
        if not p:
            return
        p["seed"] = None if value < 0 else int(value)
        self._commit()
        self.persist()

    def _on_seed_dice(self) -> None:
        p = self._bucket()
        if not p:
            return
        p["seedLock"] = True
        p["seed"] = rand_seed()
        self._syncing = True
        try:
            self._sync_seed()
        finally:
            self._syncing = False
        self._commit()
        self.persist()

    def _on_negative(self) -> None:
        if self._syncing:
            return
        p = self._bucket()
        if not p:
            return
        p["negative"] = self.negative_input.toPlainText()
        self._commit()
        self.persist()

    def _on_advanced(self) -> None:
        if self._syncing:
            return
        p = self._bucket()
        if not p:
            return
        p["advanced"] = self.advanced_input.toPlainText()
        self._commit()
        self.persist()

    def _on_bpm(self, value: int) -> None:
        if self._syncing:
            return
        self.settings["bpm"] = int(value)
        self.bpm_label.setText(f"{value} BPM")
        self._commit()
        self.persist()

    def _on_music_duration(self, value: int) -> None:
        if self._syncing:
            return
        self.settings["musicDuration"] = int(value)
        self.music_dur_label.setText(f"{value} 秒 · {value / 60:.1f} 分钟")
        self._commit()
        self.persist()

    def _on_instrumental(self, checked: bool) -> None:
        if self._syncing:
            return
        self.settings["instrumental"] = bool(checked)
        self._commit()
        self.persist()

    def _on_vocal_language(self, _index: int) -> None:
        if self._syncing:
            return
        self.settings["vocalLanguage"] = str(self.vocal_lang_select.currentData() or "zh")
        self._commit()
        self.persist()

    def sync_style_chips(self, prompt_text: str) -> None:
        self._syncing = True
        try:
            for chip in self.style_chips + self.genre_chips:
                chip.setChecked(str(chip._payload) in (prompt_text or ""))
        finally:
            self._syncing = False

    # ------------------------------------------------------------------ 引擎
    def _engine_setting_key(self) -> str:
        """当前板块的引擎选择存在哪个 settings 键：音乐一档独立、其余共用 vengine。"""

        return "mengine" if MODALITY.get(self._engine_mode()) == "music" else "vengine"

    def _modality_engines(self, engines: list, mode: str | None = None) -> list:
        modality = MODALITY.get(mode if mode in MODES else self._engine_mode(), "image")
        options = [
            e
            for e in engines
            if modality in (ENGINE_TASK_TYPES.get(e.engine_id) or ())
        ]
        allow = MODE_ENGINE_ALLOW.get(mode if mode in MODES else self._engine_mode())
        if allow:
            options = [e for e in options if e.engine_id in allow]
        return options

    def refresh_engines(self, engines: list) -> None:
        """``engines``: objects with engine_id/state/message (EngineStatus)."""

        self._engines = list(engines)
        mode = self._engine_mode()
        modality = MODALITY[mode]
        options = self._modality_engines(self._engines, mode)
        key = "mengine" if modality == "music" else "vengine"
        self._syncing = True
        try:
            self.engine_select.blockSignals(True)
            self.engine_select.clear()
            if not options:
                # 图片板块现在是两个引擎：文生图走 Sana（独立 .venv-image +
                # models\sana-multiling），图生图仍是 Wan VACE（与视频共用 .venv-video +
                # models\wan2.1）。缺引擎的文案必须按板块分开写，否则会把用户指向错的 venv。
                missing = {
                    "music": "音乐引擎未注册 · 需 .venv-music 与 models\\ace-step-1.5",
                    "video": "视频引擎未注册 · 需 .venv-video 与 models\\wan2.1",
                    "t2i": "文生图引擎未注册 · 需 .venv-image 与 models\\sana-multiling",
                    "image": "图片引擎未注册 · 需 .venv-video 与 models\\wan2.1",
                }[mode if mode == "t2i" else modality]
                self.engine_select.addItem(missing)
                self.engine_state.setText(
                    {
                        "music": (
                            "音乐引擎未注册：需 .venv-music（scripts\\setup_music_venv.ps1）与 "
                            "models\\ace-step-1.5 权重（scripts\\download_music_weights.py），"
                            "未就绪前不会提交、不伪造输出"
                        ),
                        "video": (
                            "视频引擎未注册：需 .venv-video（scripts\\setup_video_venv.ps1）与 "
                            "models\\wan2.1 权重（scripts\\download_video_weights.py），"
                            "未就绪前不会提交、不伪造输出"
                        ),
                        "t2i": (
                            "文生图引擎未注册：需独立 .venv-image（scripts\\release_local_engine.bat）"
                            "与 models\\sana-multiling 权重（scripts\\download_sana_weights.py），"
                            "未就绪前不会提交、不伪造输出"
                        ),
                        "image": (
                            "图生图引擎未注册：与视频共用 .venv-video（scripts\\setup_video_venv.ps1）"
                            "与 models\\wan2.1 权重（scripts\\download_video_weights.py），"
                            "未就绪前不会提交、不伪造输出"
                        ),
                    }[mode if mode == "t2i" else modality]
                )
                self.engine_select.setEnabled(False)
            else:
                ids = [e.engine_id for e in options]
                if self.settings.get(key) not in ids:
                    self.settings[key] = ids[0]
                for engine in options:
                    self.engine_select.addItem(engine.engine_id)
                self.engine_select.setCurrentIndex(ids.index(self.settings.get(key)))
                self.engine_select.setEnabled(True)
                current = next(
                    (e for e in options if e.engine_id == self.settings.get(key)),
                    None,
                )
                label = (
                    ENGINE_STATE_LABELS.get(current.state, current.state)
                    if current
                    else "?"
                )
                self.engine_state.setText((current.message or label) if current else label)
        finally:
            self.engine_select.blockSignals(False)
            self._syncing = False

    def engine_id(self) -> str:
        if not self._engines:
            return ""
        options = [e.engine_id for e in self._modality_engines(self._engines)]
        chosen = self.settings.get(self._engine_setting_key())
        return chosen if chosen in options else (options[0] if options else "")

    def engine_pill(self) -> tuple[str, str]:
        """(text, kind) for the topbar engine pill."""

        options = self._modality_engines(self._engines)
        if not options:
            return "无引擎", "bad"
        engine_id = self.engine_id()
        current = next((e for e in options if e.engine_id == engine_id), None)
        label = (
            ENGINE_STATE_LABELS.get(current.state, current.state) if current else "?"
        )
        kind = (
            "ok"
            if current and current.state == "ready"
            else "bad"
            if current and current.state == "failed"
            else ""
        )
        return f"{engine_id} · {label}", kind

    def _on_engine_selected(self, _index: int) -> None:
        if self._syncing:
            return
        text = self.engine_select.currentText()
        if any(e.engine_id == text for e in self._engines):
            self.settings[self._engine_setting_key()] = text
            self.refresh_engines(self._engines)
            self.engine_changed.emit(text)
            self.persist()
