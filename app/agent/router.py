"""自然语言命令解析：把一句话变成“可执行的任务意图”。

规则优先（零依赖、可预测、中文英文都能跑），大模型只在闲聊/文案/写文档时介入。
覆盖五类动作：
* generate —— 生成图片 / 视频 / 音乐（含图生图、图生视频的垫图判定）
* document —— 做 .docx / .xlsx / .pptx（「做个表格」「写份方案」「做个 PPT」）
* copy     —— 写文案（标题、广告词、小红书笔记…）交给大模型
* chat     —— 普通问答交给大模型
* speak    —— 用系统语音把文本念出来
另外能从话里抠出比例、时长、种子、步数、BPM、纯音乐等参数，并如实记录说明（notes）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

ACTION_GENERATE = "generate"
ACTION_CHAT = "chat"
ACTION_COPY = "copy"
ACTION_SPEAK = "speak"
ACTION_HELP = "help"
ACTION_DOCUMENT = "document"
# 历史遗留动作：曾用于「点名要图片/音乐就如实告知下架」。2026-11-09 重新选型后
# 图片与音乐恢复在岗，router 不再产生这个动作；常量与下游分支保留只为兼容旧调用方。
ACTION_RETIRED = "retired"

# 文档格式（与 app/agent/documents.py 的 FORMAT_* 同名同值；这里只放三个短码，
# 免得 router 为了三个字符串去 import 写库模块）。
FORMAT_DOCX = "docx"
FORMAT_XLSX = "xlsx"
FORMAT_PPTX = "pptx"
_DOCUMENT_LABELS = {FORMAT_DOCX: "Word 文档", FORMAT_XLSX: "Excel 表格", FORMAT_PPTX: "PPT 演示文稿"}

IMAGE_WORDS = (
    "图片", "图像", "照片", "海报", "插画", "壁纸", "头像", "封面", "logo",
    "image", "picture", "photo", "illustration", "wallpaper", "poster",
)
VIDEO_WORDS = (
    "视频", "影片", "短片", "动画", "宣传片", "延时摄影", "运镜", "镜头", "mv",
    "video", "clip", "animation", "footage",
)
MUSIC_WORDS = (
    "音乐", "歌曲", "曲子", "配乐", "伴奏", "单曲", "rap", "歌",
    "music", "song", "track", "melody",
)
EDIT_WORDS = (
    "图生图", "把这张", "这张图", "这张照片", "参考图", "改成", "变成", "重绘",
    "修图", "p图", "加上", "去掉", "换背景", "restyle", "edit this",
)
VERB_WORDS = (
    "生成", "创作", "制作", "绘制", "画", "做", "来", "拍", "整", "给我",
    "generate", "create", "make", "draw", "render", "produce",
)
COPY_WORDS = (
    "文案", "标题", "广告词", "标语", "口号", "slogan", "宣传", "小红书", "推文",
    "朋友圈", "口播", "脚本", "介绍词", "商品描述", "详情页", "copywriting",
)
# 「做个表格」「写份方案」「做个 PPT」→ 落成真文件。词 → 格式，命中最长的词决定格式
# （"excel表格" 比 "表格" 更具体，"pptx" 比 "ppt" 更具体）。
# 注意别把 COPY_WORDS 的词放进来：「写条文案」仍然是文案，不出文件。
DOCUMENT_WORDS: dict[str, str] = {
    "word文档": FORMAT_DOCX, "word": FORMAT_DOCX, "docx": FORMAT_DOCX, "文档": FORMAT_DOCX,
    "说明书": FORMAT_DOCX, "使用手册": FORMAT_DOCX, "手册": FORMAT_DOCX, "指南": FORMAT_DOCX,
    "报告": FORMAT_DOCX, "调查报告": FORMAT_DOCX, "检测报告": FORMAT_DOCX, "计划书": FORMAT_DOCX,
    "规划书": FORMAT_DOCX, "方案": FORMAT_DOCX, "工作方案": FORMAT_DOCX, "策划": FORMAT_DOCX,
    "会议纪要": FORMAT_DOCX, "纪要": FORMAT_DOCX, "工作总结": FORMAT_DOCX, "合同": FORMAT_DOCX,
    "协议": FORMAT_DOCX, "简历": FORMAT_DOCX, "论文": FORMAT_DOCX, "文章": FORMAT_DOCX,
    "document": FORMAT_DOCX, "report": FORMAT_DOCX, "proposal": FORMAT_DOCX, "manual": FORMAT_DOCX,
    "excel表格": FORMAT_XLSX, "电子表格": FORMAT_XLSX, "excel": FORMAT_XLSX, "xlsx": FORMAT_XLSX,
    "xls": FORMAT_XLSX, "表格": FORMAT_XLSX, "清单": FORMAT_XLSX,
    "明细": FORMAT_XLSX, "排期表": FORMAT_XLSX, "计划表": FORMAT_XLSX, "预算表": FORMAT_XLSX,
    "时间表": FORMAT_XLSX, "进度表": FORMAT_XLSX, "对照表": FORMAT_XLSX, "统计表": FORMAT_XLSX,
    "spreadsheet": FORMAT_XLSX, "workbook": FORMAT_XLSX, "worksheet": FORMAT_XLSX,
    "演示文稿": FORMAT_PPTX, "幻灯片": FORMAT_PPTX, "课件": FORMAT_PPTX, "讲义": FORMAT_PPTX,
    "汇报材料": FORMAT_PPTX, "powerpoint": FORMAT_PPTX, "pptx": FORMAT_PPTX, "ppt": FORMAT_PPTX,
    "slides": FORMAT_PPTX, "presentation": FORMAT_PPTX, "deck": FORMAT_PPTX,
}
# 只有「做/写/生成…」这类创作动词在场，才算“要一份文件”而不是在聊天。
DOCUMENT_VERB_WORDS = (
    "做", "写", "生成", "制作", "创建", "起草", "拟", "整理", "列", "弄", "给我", "帮我",
    "出一份", "来一份", "draw up", "write", "create", "make", "build", "generate",
    "prepare", "draft",
)
# 问「怎么做 PPT」「什么是 Excel」是问答，不是要文件（这些词出现就不走文档通道）。
DOCUMENT_ASK_WORDS = (
    "怎么", "怎样", "如何", "什么是", "是什么", "为什么", "能不能", "可不可以", "可以吗",
    "教我", "讲讲", "教程", "举例", "区别", "建议", "how to", "what is", "tutorial",
)
# 上面那份含「建议 / 区别」这类软词，写在正文里也会命中，只适合当「不要落成文件」的理由。
# 这一份是硬问法，另拿它做一次问答判定：「怎么做一份 PPT」既不写文件，也不该顺手去画一张图。
_DOCUMENT_HOWTO_WORDS = (
    "怎么", "怎样", "如何", "什么是", "是什么", "为什么", "能不能", "可不可以", "可以吗",
    "教我", "教程", "how to", "what is",
)
# 抠主题时要去掉的命令外壳（「帮我做一份……的表格」里既没有画面参数也没有生成引擎）。
_DOCUMENT_QUANTIFIERS = ("一份", "一个", "一张", "一组", "一套", "几份", "两份", "两个", "整份")
_DOCUMENT_SHELL_WORDS = ("格式", "文件", "模板", "框架", "大纲", "结构", "一下", "文字", "内容")
SPEAK_WORDS = (
    "朗读", "念一下", "念出来", "读出来", "读一遍", "播报", "播一下", "说这句话",
    "读这段", "speak", "read aloud", "tts",
)
QUESTION_HINTS = (
    "什么", "为什么", "如何", "怎么", "怎样", "是否", "能不能", "可以吗", "行吗",
    "介绍", "解释", "哪些", "多少", "区别", "建议", "帮我看看",
)
# 判定「这句话到底要不要画面」用的两级动词表。
# * 强动词：绘制 / 绘画 / 渲染 / 出图 / draw / paint… —— 单独出现就是要画面；
#   「画」字必须带量词（画一张 / 画只 / 画个），否则「笔画」「山水画」这种词会误伤；
# * 软动词：生成 / 创作 / 做 / generate… —— 太通用（「生成一段文案」「怎么回事」都算），
#   必须配上「一张 / 一幅 / 一组」这种画面量词才算；
# * 两级都不在场（「你好」「在吗」「肚子咕咕叫」）→ 那不是生成需求，走聊天。
_IMAGE_STRONG_VERBS = (
    "绘制", "绘画", "渲染", "出图", "draw", "paint", "sketch", "render", "illustrate",
)
_DRAW_WITH_COUNTER = re.compile(
    r"画\s*(?:[一二两三四五六七八九十\d]+\s*)?(?:张|幅|只|个|组|套|面)"
)
_IMAGE_SOFT_VERBS = (
    "生成", "创作", "制作", "来一", "来两", "给我", "帮我",
    "generate", "create", "produce", "make",
)
_IMAGE_COUNTERS = (
    "一张", "一幅", "两张", "三张", "几张", "组图", "一套", "一组",
    "个画面", "个场景", "个镜头", "个背景", "条壁纸",
    "an image", "a picture", "a photo", "one image",
)

HELP_TEXT = (
    "我可以直接执行这些自然语言命令：\n"
    "· 生成视频：生成一段10秒的海浪拍打礁石延时摄影\n"
    "· 图生视频：让这张照片里的云动起来（先在下方/粘贴处放好垫图）\n"
    "· 生成图片：画一张赛博朋克风格的雨夜街景壁纸（16:9、横版这类比例词能直接抠出来）\n"
    "· 图生图：把这张照片改成水彩风（先放好参考图）\n"
    "· AI 音乐：生成一首60秒的轻快钢琴伴奏（BPM、纯音乐、中文/英文人声都能说）\n"
    "· 文案：写一条小红书文案，介绍这款保温杯\n"
    "· 做文件：做个新品发布会的排期表（xlsx）、写份活动执行方案（docx）、做个产品介绍 PPT（pptx）\n"
    "· 语音：朗读刚才那段文案\n"
    "生成任务会进入与六个板块（AI智能体、文生图、图生图、文生视频、图生视频、AI音乐）"
    "同一条串行队列，可在「任务队列」里看进度。\n"
    "话里有「生成一段…」「来条…视频」「画一张…」「生成一首…」这类说法才会去生成；"
    "只描述画面、没有这类词（比如「你好」「肚子咕咕叫」）按普通聊天回答。"
)

# （2026-11-09 重新选型后图片与音乐已恢复在岗，原先的「已下架」告知文案已移除。）

# 参数抠取：比例 / 时长 / 种子 / 步数 / BPM / 纯音乐 / 人声语言
_ASPECT_WAN = {
    # 与 app/engine/video/budget.py 的 MAX_PIXELS = 832*480*115//100 = 459264 ≈ 0.46MP 同一份预算：
    # Wan2.1 1.3B 是 480p 档模型；超预算**不会自动降档**，而是在提交前就被挡下，
    # 并把这一档的真实上限讲成具体数字（token 上限 MAX_TOKENS 也在那份 budget.py 里）。
    # 图生图（wan-i2i 单帧）与视频共用这份预算和这张表；文生图走 Sana 的 1024 级表
    # （_ASPECT_SANA），不再压到 480p。
    # 桌面端参数面板的 ASPECT_CHIPS_VIDEO 与网页端同名表用的是同一组尺寸，三端不能各写一套。
    "1:1": (624, 624), "2:3": (480, 720), "3:2": (720, 480),
    "16:9": (832, 480), "9:16": (480, 832), "4:3": (640, 480), "3:4": (480, 640),
}
_ASPECT_SANA = {
    # 文生图引擎 sana-multiling（Sana-1.6B 多语言版）：按 32 的倍数出图（DC-AE 32 倍
    # 下采样），单遍像素预算 1_100_000（≈1024 级直出），目标硬上限 4_200_000（2048 级）。
    # 数字与 app/engine/image/plan.py 的 SANA_* 和桌面端 ASPECT_CHIPS_T2I 同一份口径，
    # 两端不能各写一套。1080p（1920×1088）超过单遍预算，引擎按预算出底图再放大。
    "1:1": (1024, 1024), "16:9": (1152, 640), "9:16": (640, 1152),
    "4:3": (1152, 864), "3:4": (864, 1152), "2:3": (768, 1152), "3:2": (1152, 768),
}
_ASPECT_WORDS = {
    "竖版": "9:16", "竖图": "9:16", "竖屏": "9:16", "手机壁纸": "9:16",
    "portrait": "9:16", "vertical": "9:16",
    "横版": "16:9", "横图": "16:9", "横屏": "16:9", "宽屏": "16:9", "电脑壁纸": "16:9",
    "landscape": "16:9", "widescreen": "16:9",
}
_ASPECT_TOKEN = re.compile(r"\b(16\s*[:：]\s*9|9\s*[:：]\s*16|1\s*[:：]\s*1|2\s*[:：]\s*3|3\s*[:：]\s*2|4\s*[:：]\s*3|3\s*[:：]\s*4)\b")
_SECONDS_TOKEN = re.compile(r"(?:时长|大约|约|片长|全长)?\s*(\d{1,3}(?:\.\d)?)\s*(?:秒|s\b)")
_MINUTES_TOKEN = re.compile(r"(\d{1,3})\s*(?:分钟|min\b|minutes?)")
_SEED_TOKEN = re.compile(r"(?:种子|seed)\s*[:：]?\s*(\d{1,10})", re.IGNORECASE)
_STEPS_TOKEN = re.compile(r"(?:步数|steps?)\s*[:：]?\s*(\d{1,3})", re.IGNORECASE)
# AI 音乐在岗（ace-step-15）：BPM / 纯音乐 / 人声语言从话里抠出来，
# 键名与 app/tasks/music_worker.py 的 _ALLOWED_PARAMS 对齐（bpm / instrumental / vocal_language）。
_BPM_TOKEN = re.compile(r"(?:bpm|节拍|速度)\s*[:：]?\s*(\d{2,3})\b", re.IGNORECASE)
_INSTRUMENTAL_WORDS = ("纯音乐", "纯器乐", "无人声", "不要人声", "instrumental")
_VOCAL_LANG_WORDS = {
    "中文人声": "zh", "国语": "zh", "中文": "zh", "chinese": "zh",
    "英文人声": "en", "英语": "en", "英文": "en", "english": "en",
}
# 音乐时长硬边界（music_worker：10-480 秒）；抠出来的秒数先夹好，越界如实说明。
_MUSIC_DURATION_MIN, _MUSIC_DURATION_MAX = 10.0, 480.0
# 话里出现这些词 = 要“动起来” → 走视频通道（图生视频优先）。
_MOTION_WORDS = (
    "动起来", "动一下", "让它动", "让画面动", "活动起来", "动起来看看", "运镜",
    "推镜", "拉镜", "摇镜", "镜头推进", "镜头拉远", "延时", "活起来", "眨眼",
    "animate", "animation", "motion", "parallax", "move",
)
# 改图/图生视频时，把这些“指令性短语”从提示词里剔掉，只留真正的内容描述。
_EDIT_STRIP_WORDS = tuple(
    sorted(
        EDIT_WORDS + ("让这张", "把这张", "这张照片", "这张图片", "这张", "照片里的", "图片里的", "图里的", "里的"),
        key=len,
        reverse=True,
    )
)
_LEAD_EDIT = re.compile(r"^(?:改成|变成|换成|修改为|改为|变成|让|请|把)+")

# 去掉命令前缀（“请帮我生成一张……”），留下真正的画面描述。
_PREFIX_TOKENS = (
    "请", "帮我", "麻烦", "给我", "我要", "想要", "想", "需要", "现在", "立刻",
)
_QUANTIFIERS = ("一张", "一幅", "一段", "一首", "一支", "一条", "一个", "一部", "几张", "两张")
_HEAD_JUNK = "的地之了，,、;；:：- \t"

_VIDEO_SECONDS_DEFAULT = 5.0


@dataclass
class AgentCommand:
    """解析结果：界面与后端共用同一份结构。"""

    action: str
    modality: str = ""            # image | video | music
    mode: str = ""                # t2i | i2i | t2v | i2v | music
    prompt: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)
    reply: str = ""
    notes: tuple[str, ...] = ()
    needs_ref_image: bool = False
    speak_text: str = ""
    document_format: str = ""          # docx | xlsx | pptx（仅 action == document 时有值）

    @property
    def is_generation(self) -> bool:
        return self.action == ACTION_GENERATE and bool(self.modality)

    @property
    def is_document(self) -> bool:
        return self.action == ACTION_DOCUMENT and bool(self.document_format)


def _first_hit(haystack: str, words: tuple[str, ...]) -> str:
    # 长词优先，避免 “视频” 被 “图” 抢先命中。
    for word in sorted(words, key=len, reverse=True):
        if word in haystack:
            return word
    return ""


def _earliest_hit(haystack: str, words: tuple[str, ...]) -> tuple[int, str]:
    """命中位置最靠前的词（同一位置取更长的词）；没命中回 ``(-1, "")``。

    文档与生成的裁决要用「谁先出现」：「画一张海报：新品发布方案」是图片，
    「做个表格，介绍新品」才是表格——只看有没有命中词会判错。
    """
    best_index, best_word = -1, ""
    for word in words:
        index = haystack.find(word.lower())
        if index < 0:
            continue
        if best_index < 0 or index < best_index or (index == best_index and len(word) > len(best_word)):
            best_index, best_word = index, word
    return best_index, best_word


def _document_hit(haystack: str) -> tuple[int, str, str]:
    """话里的文档目标词 → ``(位置, 命中词, 格式)``；同一位置取更长的词（pptx 优先于 ppt）。"""
    best_index, best_word, best_format = -1, "", ""
    for word, fmt in DOCUMENT_WORDS.items():
        index = haystack.find(word)
        if index < 0:
            continue
        if best_index < 0 or index < best_index or (index == best_index and len(word) > len(best_word)):
            best_index, best_word, best_format = index, word, fmt
    return best_index, best_word, best_format


def _document_secondary_hit(haystack: str, chosen_format: str) -> tuple[str, str]:
    """话里另一种格式的词（「做个表格 PPT」）：回 ``(词, 格式)``，没有就回 ``("", "")``。"""
    best_word, best_format = "", ""
    for word, fmt in DOCUMENT_WORDS.items():
        if fmt == chosen_format or word not in haystack:
            continue
        if len(word) > len(best_word):
            best_word, best_format = word, fmt
    return best_word, best_format


def _normalize_aspect(raw: str) -> str:
    # 只判比例词本身合不合法：_ASPECT_WAN 与 _ASPECT_SANA 的比例键完全一致
    # （1:1 / 16:9 / 9:16 / 4:3 / 3:4 / 2:3 / 3:2），只是各自落到不同的尺寸档，
    # 所以按 Wan 表判与按 Sana 表判等价；真正的尺寸在 _extract_params 里按引擎选表。
    compact = re.sub(r"\s+", "", raw).replace("：", ":")
    return compact if compact in _ASPECT_WAN else ""


def _extract_params(
    text: str, modality: str, aspect_table: dict[str, tuple[int, int]] | None = None
) -> tuple[str, dict[str, Any], list[str]]:
    """从话里抠参数，返回（去掉参数词后的描述, 参数字典, 说明）。

    ``aspect_table``：比例词落到哪张尺寸表——文生图（sana-multiling）传 ``_ASPECT_SANA``，
    图生图（wan-i2i）与视频传 ``_ASPECT_WAN``；缺省按 Wan 档（非图片模态就是这样）。
    """

    table = aspect_table or _ASPECT_WAN

    remaining = text
    params: dict[str, Any] = {}
    notes: list[str] = []

    match = _SEED_TOKEN.search(remaining)
    if match:
        params["seed"] = int(match.group(1)) & 0x7FFFFFFF
        remaining = remaining.replace(match.group(0), " ")

    match = _STEPS_TOKEN.search(remaining)
    if match:
        # 步数三类引擎都收：Wan 图片/视频 1.3B 档默认 28 步，ACE-Step turbo 默认 8 步；
        # 话里显式给了就统一夹到 1-50（引擎侧还会再夹一次，这里只是把说明写清楚）。
        params["steps"] = max(1, min(50, int(match.group(1))))
        remaining = remaining.replace(match.group(0), " ")

    match = _ASPECT_TOKEN.search(remaining)
    aspect = ""
    if match:
        aspect = _normalize_aspect(match.group(1))
        remaining = remaining.replace(match.group(1), " ")
    if not aspect:
        for word, value in _ASPECT_WORDS.items():
            if word in remaining.lower():
                aspect = value
                remaining = remaining.replace(word, " ")
                break
    if aspect:
        if modality == "music":
            notes.append("比例词对音乐不适用，已忽略")
        else:
            # 尺寸按调用方选好的表取：文生图（sana-multiling，见 _ASPECT_SANA）走 1024 级档；
            # 图生图与视频（wan-i2i / wan-t2v / wan-i2v）共用同一份 480p 预算
            # （wan.py _MAX_PIXELS），见 _ASPECT_WAN。
            size = table.get(aspect)
            if size:
                params["width"], params["height"] = size
                notes.append(f"比例 {aspect}（{size[0]}×{size[1]}）")

    seconds: float | None = None
    match = _MINUTES_TOKEN.search(remaining)
    if match:
        seconds = float(match.group(1)) * 60.0
        remaining = remaining.replace(match.group(0), " ")
    match = _SECONDS_TOKEN.search(remaining)
    if match:
        seconds = float(match.group(1))
        remaining = remaining.replace(match.group(0), " ")
    if seconds is not None:
        if modality == "music":
            duration = max(_MUSIC_DURATION_MIN, min(_MUSIC_DURATION_MAX, seconds))
            params["duration"] = round(duration, 1)
            capped = (
                f"；ACE-Step 1.5 单曲上限 {int(_MUSIC_DURATION_MAX)} 秒（8 分钟），已夹到上限"
                if seconds > _MUSIC_DURATION_MAX
                else ""
            )
            notes.append(f"时长 {params['duration']} 秒（引擎按秒数生成，读回真实时长）{capped}")
        elif modality == "video":
            frames = int(round(seconds * 16))
            frames = max(5, min(161, frames))
            frames -= (frames - 1) % 4
            params["num_frames"] = frames
            capped = (
                "；Wan2.1 单段最长 161 帧（约 10 秒），更长的镜头要分段生成再剪"
                if seconds * 16 > 161
                else ""
            )
            notes.append(
                f"时长约 {frames / 16:.1f} 秒（Wan2.1 固定 16fps，帧数 {frames}，按 4 的倍数就近取整）{capped}"
            )
        else:
            # 图片是单帧，秒数不适用（引擎参数白名单里也没有 num_frames）：
            # 秒数词已从提示词里去掉，如实说明。
            notes.append("时长说法对单帧图片不适用，已忽略")

    if modality == "music":
        match = _BPM_TOKEN.search(remaining)
        if match:
            bpm = max(30, min(300, int(match.group(1))))
            params["bpm"] = bpm
            remaining = remaining.replace(match.group(0), " ")
            if bpm != int(match.group(1)):
                notes.append(f"BPM 超出 30-300，已夹到 {bpm}")
        for word in _INSTRUMENTAL_WORDS:
            if word in remaining.lower():
                params["instrumental"] = True
                remaining = remaining.replace(word, " ")
                notes.append("纯音乐（无人声）")
                break
        else:
            for word, lang in _VOCAL_LANG_WORDS.items():
                if word in remaining.lower():
                    params["vocal_language"] = lang
                    remaining = remaining.replace(word, " ")
                    notes.append(f"人声语言 {word}（{lang}）")
                    break

    cleaned = re.sub(r"\s{2,}", " ", remaining).strip()
    return cleaned, params, notes


def _strip_command_phrase(text: str, target_word: str) -> str:
    """去掉“请帮我生成一张”这类前缀与目标词，剩下的就是提示词主体。"""
    result = text.strip()
    changed = True
    while changed:
        changed = False
        stripped = result.strip(_HEAD_JUNK)
        if stripped != result:
            result, changed = stripped, True
        for token in _PREFIX_TOKENS + _QUANTIFIERS + VERB_WORDS:
            if result.lower().startswith(token.lower()):
                result = result[len(token):]
                changed = True
                break
        if target_word and result.lower().startswith(target_word.lower()):
            result = result[len(target_word):]
            changed = True
    # 目标词出现在开头附近时也去掉一次（“一张图的赛博朋克”这种写法）。
    if target_word and target_word in result.lower():
        head = result.lower().index(target_word.lower())
        if head <= 6:
            result = (result[:head] + " " + result[head + len(target_word):])
    return result.strip(_HEAD_JUNK).strip()


def _quoted(text: str) -> str:
    for pattern in (r"[“\"'『「]([^”\"'』」]{2,})[”\"'』」]",):
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip()
    return ""


def _document_topic(text: str) -> str:
    """去掉「帮我做一份……的表格」这类命令外壳，剩下真正要写的主题（带引号时优先引号内）。"""
    quoted = _quoted(text)
    if quoted:
        return quoted
    body = text
    shell = (
        _PREFIX_TOKENS
        + DOCUMENT_VERB_WORDS
        + _DOCUMENT_QUANTIFIERS
        + ("个", "份")
        + _DOCUMENT_SHELL_WORDS
        + tuple(DOCUMENT_WORDS)
    )
    for token in shell:
        body = re.sub(re.escape(token), " ", body, flags=re.IGNORECASE)
    body = re.sub(r"\s{2,}", " ", body).strip()
    body = re.sub(r"\s+([，,。.；;：!！?？])", r"\1", body).strip(_HEAD_JUNK).strip()
    return (body or text.strip())[:300]


def parse_command(message: str, *, has_ref_image: bool = False) -> AgentCommand:
    """把一句自然语言解析成 AgentCommand（永远不抛异常，解析不了就回 chat）。"""
    raw = (message or "").strip()
    if not raw:
        return AgentCommand(ACTION_HELP, reply=HELP_TEXT)

    lowered = raw.lower()
    notes: list[str] = []

    # 1) 语音播报
    if _first_hit(lowered, SPEAK_WORDS):
        spoken = _quoted(raw) or re.sub(
            "|".join(re.escape(word) for word in SPEAK_WORDS), "", raw, flags=re.IGNORECASE
        )
        return AgentCommand(ACTION_SPEAK, speak_text=spoken.strip(_HEAD_JUNK), prompt=raw)

    # 生成与文档的命中词先算好，判定顺序才可控：语音 > 文案 > 文档 > 生成。
    video_word = _first_hit(lowered, VIDEO_WORDS)
    music_word = _first_hit(lowered, MUSIC_WORDS)
    image_word = _first_hit(lowered, IMAGE_WORDS)
    wants_edit = bool(_first_hit(lowered, EDIT_WORDS))

    # 2) 文案
    if _first_hit(lowered, COPY_WORDS):
        return AgentCommand(ACTION_COPY, prompt=raw, reply="")

    # 3) 文档：要的是文件，不是画面。三种情况不算要文件——
    #    * 有垫图且话像是改图：用户其实想让引擎改那张图；
    #    * 话里有「怎么/什么是」这类问法：那是问答，不是要文件；
    #    * 媒体目标词比文档词更早出现：「画一张海报，主题新品发布方案」要的是图。
    doc_index, doc_word, doc_format = _document_hit(lowered)
    media_index, _media_word = _earliest_hit(lowered, IMAGE_WORDS + VIDEO_WORDS + MUSIC_WORDS)
    if (
        doc_word
        and _first_hit(lowered, DOCUMENT_VERB_WORDS)
        and not _first_hit(lowered, DOCUMENT_ASK_WORDS)
        and not (wants_edit and has_ref_image)
        and (media_index < 0 or doc_index <= media_index)
    ):
        notes.append(f"按{_DOCUMENT_LABELS[doc_format]}（.{doc_format}）执行，写完直接落输出目录")
        other_word, other_format = _document_secondary_hit(lowered, doc_format)
        if other_word:
            notes.append(
                f"话里还提到「{other_word}」（{_DOCUMENT_LABELS[other_format]}），"
                f"已按最先出现的「{doc_word}」做成{_DOCUMENT_LABELS[doc_format]}"
            )
        return AgentCommand(
            ACTION_DOCUMENT,
            prompt=_document_topic(raw),
            document_format=doc_format,
            notes=tuple(notes),
        )

    # 3b) 话里有「怎么做 / 如何 / 什么是」这种硬问法，又说到了 Word/Excel/PPT：那是来问
    #     方法的，不是来要文件的——也别掉到下面「描述即提示词」去画一张莫名其妙的图。
    if doc_word and media_index < 0 and _first_hit(lowered, _DOCUMENT_HOWTO_WORDS):
        return AgentCommand(ACTION_CHAT, prompt=raw)

    # 4) 生成目标：图片 / 视频 / 音乐都在岗（图片：文生图走 sana-multiling（Sana-1.6B
    #    多语言版，独立 .venv-image），图生图走 Wan2.1 1.3B 单帧 wan-i2i；
    #    音乐走 ACE-Step 1.5 CPU 换入档）。
    #    判定次序不变：视频词最优先（「给这段视频配乐」仍算视频），其次音乐、图片。
    if video_word:
        modality, target = "video", video_word
    elif music_word:
        modality, target = "music", music_word
    elif (image_word or wants_edit) and not (has_ref_image and _first_hit(lowered, _MOTION_WORDS)):
        # 带垫图且说了运动/运镜（「让这张照片里的云动起来」）留给下面的运动判定走图生视频；
        # 其余想画图、想改画面的诉求进图片通道（有垫图走图生图，没有走文生图，mode 步骤定）。
        modality, target = "image", image_word
    elif seconds_hint := _SECONDS_TOKEN.search(lowered):
        # “10秒的海浪”没说“视频”二字，但有秒数 → 视频
        modality, target, _ = "video", "", ""
        notes.append(f"话里没有“视频”，按时长 {seconds_hint.group(1)} 秒判成文生视频")
    else:
        modality, target = "", ""

    # 描述了运动/运镜 → 视频通道（“让这张照片里的云动起来”没有“视频”二字）。
    motion_word = _first_hit(lowered, _MOTION_WORDS)
    if motion_word and modality != "video":
        notes.append(f"话里有「{motion_word}」，按视频生成执行（有垫图走图生视频，没有走文生视频）")
        modality, target = "video", video_word or ""

    # 想改画面但没垫图：先如实说明，后面按文生视频/文生图兜底。
    if wants_edit and not has_ref_image and modality in ("video", "image"):
        notes.append(
            f"这句话像是在改图{'/图生视频' if modality == 'video' else ''}，但没有垫图；"
            f"已按文生{'视频' if modality == 'video' else '图'}执行，需要改画面请先附上垫图"
        )

    # 5) 没有任何明确的生成目标：**绝不兜底成文生图**。
    #    以前这里按「描述即提示词」把剩下所有话都送去画一张图，于是「你好」「在吗」
    #    「肚子咕咕叫」这种根本不是需求的话也被派成文生图，还拿原话当提示词。
    #    现在只有两级动词证据（画/绘制/渲染… 或 生成/创作… ＋ 一张/一幅… 量词）
    #    才立案生成，判不出来就老老实实走聊天，交给大模型回答。
    if not modality:
        strong = _first_hit(lowered, _IMAGE_STRONG_VERBS)
        drawn = _DRAW_WITH_COUNTER.search(lowered)
        soft = _first_hit(lowered, _IMAGE_SOFT_VERBS)
        counter = _first_hit(lowered, _IMAGE_COUNTERS) if soft else ""
        if strong or drawn or (soft and counter):
            # 明确的「画一张」意图：进图片通道（文生图 sana-multiling；带垫图的改图在
            # 分支 4 已按 wants_edit 立案走 wan-i2i，不会掉到这里）。
            modality, target = "image", strong or soft or ""
            notes.append("话里有明确的画图说法（没有「图片」目标词），按文生图立案")
        else:
            looks_like_question = raw.endswith(("?", "？")) or bool(_first_hit(lowered, QUESTION_HINTS))
            reason = "这句话像是在问东西" if looks_like_question else "这句话没有视频/图片/音乐/文案这类明确说法"
            return AgentCommand(
                ACTION_CHAT,
                prompt=raw,
                notes=(
                    f"{reason}，按聊天回答；要画面请说「生成一段…视频」或「画一张…」，要歌请说「做一首…」",
                ),
            )

    # 比例表按「这句话最终会交给哪个引擎」选：图片通道里，带垫图且明确要改画面的走
    # 图生图 wan-i2i（480p 档，_ASPECT_WAN），其余走文生图 sana-multiling（1024 级，
    # _ASPECT_SANA）。mode 在下面几步才最终定，但判定依据（有没有垫图、有没有改画面
    # 意图）到这里已经齐了，所以先把表选好传进 _extract_params。
    aspect_table = (
        _ASPECT_WAN
        if modality != "image" or (has_ref_image and wants_edit)
        else _ASPECT_SANA
    )
    body, params, param_notes = _extract_params(raw, modality, aspect_table)
    notes.extend(param_notes)
    prompt = _strip_command_phrase(body, target)
    if not prompt:
        prompt = raw
    # 清理剥词后留下的空角：「手机 ，星空」→「手机，星空」。
    prompt = re.sub(r"\s+([，,。.；;：!！?？])", r"\1", prompt)

    if modality == "music":
        mode = "music"
    elif modality == "image":
        mode = "i2i" if (has_ref_image and wants_edit) else "t2i"
    else:
        mode = "i2v" if (has_ref_image and (wants_edit or "动起来" in lowered or "让这" in lowered)) else "t2v"

    if mode in ("i2v", "i2i"):
        # 垫图生成：把“把这张图 / 改成 / 让”这类指令性短语从提示词里剔掉，
        # 只留真正的内容描述（引擎不需要知道“这张图”）。
        for word in _EDIT_STRIP_WORDS:
            if word in prompt:
                prompt = prompt.replace(word, " ")
        prompt = re.sub(r"\s{2,}", " ", prompt).strip(_HEAD_JUNK)
        prompt = _LEAD_EDIT.sub("", prompt).strip(_HEAD_JUNK) or raw
        prompt = re.sub(r"\s+([，,。.；;：!！?？])", r"\1", prompt)

    needs_ref = mode in ("i2v", "i2i")
    if needs_ref and not has_ref_image:
        # 意图是垫图生成但没有垫图：如实说明，退化成文生对应模态。
        mode = "t2v" if modality == "video" else "t2i"
        notes.append(
            f"这句话像是图生{'视频' if modality == 'video' else '图'}，但没有垫图；"
            f"已按文生{'视频' if modality == 'video' else '图'}执行，需要改画面请先附垫图"
        )
    elif not needs_ref and has_ref_image and mode in ("t2v", "t2i"):
        # 有垫图但话里没说要改画面：尊重显式意图，只提示一次。
        notes.append(
            f"已附垫图，但这句话没有表达改画面意图，仍按文生{'视频' if mode == 't2v' else '图'}执行"
        )

    if modality == "video":
        if "num_frames" not in params:
            frames = int(round(_VIDEO_SECONDS_DEFAULT * 16))
            frames -= (frames - 1) % 4
            params["num_frames"] = frames
            notes.append(f"未指定时长，默认 {frames / 16:.1f} 秒（{frames} 帧 @16fps）")
        params.setdefault("width", 832)
        params.setdefault("height", 480)
    elif modality == "image":
        # 单帧图片没有帧数概念，尺寸档位按引擎分两套：
        # 文生图（sana-multiling，Sana-1.6B 多语言版）默认 1024×1024，按 32 的倍数出图，
        # 单遍像素预算 1_100_000（≈1024 级直出），最高到 2048 级（超单遍预算按底图+放大）；
        # 图生图（wan-i2i）仍跟视频同一份 480p 预算（Wan 1.3B 档位上限）。
        if mode == "t2i":
            params.setdefault("width", 1024)
            params.setdefault("height", 1024)
            notes.append(
                "图片按 Sana-1.6B 多语言版 1024 级档位出图"
                "（按 32 的倍数，最高 2048 级，比例词可选）"
            )
        else:
            params.setdefault("width", 832)
            params.setdefault("height", 480)
            notes.append("图片按 Wan2.1 480p 档位出图（最高 832×480 级，比例词可选）")
    else:  # music：时长/BPM 在 _extract_params 里处理，缺省交给引擎默认值
        pass

    return AgentCommand(
        ACTION_GENERATE,
        modality=modality,
        mode=mode,
        prompt=prompt,
        parameters=params,
        notes=tuple(notes),
        needs_ref_image=needs_ref,
    )


def describe(command: AgentCommand) -> str:
    """给用户看的一句话回执（不含任何虚假承诺）。"""
    if command.action == ACTION_GENERATE:
        labels = {"t2i": "文生图", "i2i": "图生图", "t2v": "文生视频", "i2v": "图生视频", "music": "AI 音乐"}
        return f"已排入队列：{labels.get(command.mode, command.mode)} · 提示词「{command.prompt}」"
    if command.action == ACTION_RETIRED:
        # 兼容旧调用方：router 已不再产生 retired 动作，保留分支只为防御性读取。
        return command.reply or ""
    if command.action == ACTION_DOCUMENT:
        return f"正在写{_DOCUMENT_LABELS.get(command.document_format, '文档')}：主题「{command.prompt}」"
    return command.reply or ""


def build_task_payload(
    command: AgentCommand,
    *,
    engine_ids: dict[str, str],
    ref_image: str = "",
) -> dict[str, Any]:
    """把 AgentCommand 转成 POST /api/tasks 需要的载荷。

    engine_ids 由调用方（服务端）按已注册引擎解析，键：t2i / i2i / t2v / i2v / music，
    对应的引擎 id 现为 sana-multiling / wan-i2i / wan-t2v / wan-i2v / ace-step-15
    （文生图走 .venv-image + models/sana-multiling；图生图与视频共用 .venv-video +
    models/wan2.1；音乐走 .venv-music）。
    """
    if not command.is_generation:
        raise ValueError("不是生成类命令，无法建任务")
    task_type = command.modality  # image | video | music（与 service 校验、注册表 task_types 同名）
    engine_id = engine_ids.get(command.mode, "")
    if not engine_id:
        raise ValueError(f"没有可用的{ {'image': '图片', 'video': '视频', 'music': '音乐'}.get(task_type, '') }引擎（引擎未注册或被禁用）")
    parameters = dict(command.parameters)
    if command.mode in ("i2v", "i2i"):
        if not ref_image:
            raise ValueError("该命令需要垫图，但没有提供")
        parameters["init_image_path"] = ref_image
    return {
        "task_type": task_type,
        "engine_id": engine_id,
        "prompt": command.prompt,
        "parameters": parameters,
    }


def system_prompt() -> str:
    """聊天/文案的系统提示词：让本机小模型知道边界，不吹牛。"""
    return (
        "你是「辰鹄AI创作中心」内置的中文创作助手，运行在用户本机。"
        "你只负责聊天、写文案、给创作建议（提示词写法、参数建议、构图与镜头语言）。"
        "不要声称自己能直接生成图片或视频——生成由本软件的引擎完成，用户可以直接在对话里输入生成命令。"
        "本软件目前有六个板块：AI智能体、文生图、图生图、文生视频、图生视频、AI音乐；"
        "文生图由 Sana-1.6B 多语言版完成（1024 级档位，按 32 的倍数，最高 2048 级），"
        "图生图与视频由 Wan2.1 1.3B 完成（480p 档位，图生图是单帧），音乐由 ACE-Step 1.5 完成。"
        "给建议时按这些引擎的实际档位说（比如文生图常用 1024×1024、图生图与视频最高 832×480 级），"
        "不要吹 4K、8K 这类做不到的规格。"
        "回答简洁、直接、可用，默认中文；用户用英文提问就用英文回答；"
        "给文案时直接给成品，不要前后加解释。"
        "不确定的事情直说不确定，绝不编造。"
    )
