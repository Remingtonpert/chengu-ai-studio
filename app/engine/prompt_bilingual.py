"""中文提示词 → CLIP 英文关键词的本地桥接（零依赖、零显存、完全离线、可商用）。

为什么需要这一层：SD1.5 的文本编码器是 CLIP-ViT-L/1.1，词表里**没有中日韩字符**。
中文提示词喂进去不会报错，但会被拆成无意义的 byte-pair 片段。本机实测（2026-11-08）：
提示词「一只在窗台上的橘猫，自然光，照片质感」出来的是一张没有主体的空街景。

视频模块（Wan2.1 用 UMT5，原生多语，中文直接可用）与音乐模块（ACE-Step 支持中文歌词）
都没有这个问题，只有图像模块需要这层桥接。

策略：SD1.5 本来就是「关键词 + 逗号」的用法，所以把中文描述切成词组、按内置手写词表
映射成英文关键词，稳定且够准；未命中的中文词会被记下来（界面可提示用户换词），
**原文一律原样保留在任务元数据里**，界面上用户看到的仍是他自己写的中文。

词表全部手写，不来自任何模型权重或第三方词典，许可干净（随项目同许可）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

# 中日韩统一表意文字 + 扩展 A + 假名 + 谚文：命中这些码位才需要桥接。
_CJK_SPAN = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]")
# 切段用的分隔符（中英混排时英文片段原样保留）。
_SPLIT = re.compile(r"[,，。.；;、！!？?：:\s（）()\[\]【】{}「」『』“”\"'`~·—…/\\|@#*+=<>%$^&]")

# 连接词/量词：直接丢掉，不算「未识别」，避免把噪声带进 CLIP。
# 注意：这里只放「纯语法」的单字，凡是可能属于词组的一律不放
# （比如「中」不放，否则「中国风」会被拆坏；匹配顺序是先查词表、再看连接词）。
_FILLER = frozenset("的了在上下里与及和或一只条个张位名有是很这那被把给向对于为所之等每要能着")

# 手写词表：中文 → CLIP 英文关键词。长词优先匹配（先试 4 字再试 3 字……）。
# 分四类：主体 / 风格媒介 / 光线色彩 / 构图画质，另加常用负向词。
_MAP: dict[str, str] = {}

def _add(mapping: dict[str, str]) -> None:
    _MAP.update(mapping)


_add(
    {
        # —— 人物与姿态 ——
        "人像": "portrait", "自拍": "selfie", "女生": "young woman", "男生": "young man",
        "女孩": "girl", "男孩": "boy", "女人": "woman", "男人": "man", "儿童": "child",
        "小孩": "kid", "婴儿": "baby", "老人": "elderly person", "老师": "teacher",
        "学生": "student", "医生": "doctor", "警察": "police officer", "舞者": "dancer",
        "运动员": "athlete", "士兵": "soldier", "厨师": "chef", "宇航员": "astronaut",
        "微笑": "smiling", "大笑": "laughing", "哭泣": "crying", "思考": "thinking pose",
        "站立": "standing", "坐着": "sitting", "奔跑": "running", "走路": "walking",
        "跳舞": "dancing", "睡觉": "sleeping", "阅读": "reading", "背影": "from behind",
        "侧脸": "side profile", "正面": "front view", "特写": "close-up shot",
        # —— 动物 ——
        "橘猫": "orange tabby cat", "猫咪": "cat", "猫": "cat", "狗": "dog",
        "金毛": "golden retriever", "柯基": "corgi", "熊猫": "giant panda", "老虎": "tiger",
        "狮子": "lion", "狼": "wolf", "狐狸": "fox", "兔子": "rabbit", "鸟": "bird",
        "鹰": "eagle", "鱼": "fish", "鲨鱼": "shark", "鲸鱼": "whale", "海豚": "dolphin",
        "马": "horse", "斑马": "zebra", "长颈鹿": "giraffe", "大象": "elephant",
        "牛": "cow", "羊": "sheep", "猪": "pig", "鸡": "chicken", "鸭子": "duck",
        "蝴蝶": "butterfly", "蜜蜂": "bee", "龙": "dragon", "独角兽": "unicorn",
        # —— 景物与场景 ——
        "窗台": "windowsill", "窗户": "window", "房间": "room", "室内": "indoor",
        "室外": "outdoor", "街道": "street", "城市": "city", "都市": "urban cityscape",
        "巷子": "alley", "公园": "park", "森林": "forest", "竹林": "bamboo forest",
        "山": "mountain", "雪山": "snow mountain", "湖": "lake", "河": "river",
        "海": "sea", "海边": "seaside", "沙滩": "beach", "岛": "island", "沙漠": "desert",
        "草原": "grassland", "农田": "farmland", "瀑布": "waterfall", "瀑布": "waterfall",
        "云": "clouds", "天空": "sky", "夜空": "night sky", "星星": "stars",
        "月亮": "moon", "太阳": "sun", "彩虹": "rainbow", "雪": "snow",
        "雨": "rain", "雾": "fog", "风": "wind", "花": "flower", "玫瑰": "rose",
        "樱花": "cherry blossoms", "荷花": "lotus", "向日葵": "sunflower",
        "树": "tree", "叶子": "leaves", "草": "grass", "叶子": "leaves",
        "建筑": "architecture", "房子": "house", "老屋": "old house", "城堡": "castle",
        "寺庙": "temple", "教堂": "church", "桥": "bridge", "塔": "tower",
        "公路": "road", "汽车": "car", "火车": "train", "飞机": "airplane",
        "自行车": "bicycle", "船": "boat", "机器人": "robot", "机甲": "mecha",
        "餐桌": "dining table", "咖啡": "coffee", "蛋糕": "cake", "水果": "fruit",
        "食物": "food", "拉面": "ramen", "书本": "books", "音乐": "music",
        "吉他": "guitar", "钢琴": "piano", "电脑": "computer", "手机": "smartphone",
        # —— 风格与媒介 ——
        "照片": "photograph", "摄影": "photography", "写实": "photorealistic",
        "真实": "realistic", "胶片": "film photography", "复古": "vintage",
        "油画": "oil painting", "水彩": "watercolor painting", "素描": "sketch",
        "国画": "traditional chinese ink painting", "水墨": "ink wash painting",
        "动漫": "anime style", "二次元": "anime style", "卡通": "cartoon style",
        "插画": "illustration", "3D": "3d render", "渲染": "3d render",
        "赛博朋克": "cyberpunk", "蒸汽朋克": "steampunk", "科幻": "sci-fi",
        "奇幻": "fantasy", "武侠": "wuxia", "仙侠": "xianxia fantasy",
        "恐怖": "horror", "悬疑": "suspense", "电影感": "cinematic",
        "电影": "cinematic film still", "海报": "poster design",
        "像素": "pixel art", "极简": "minimalist", "唯美": "aesthetic",
        "可爱": "cute", "帅气": "cool handsome", "优雅": "elegant",
        "中国风": "chinese traditional style", "古风": "ancient chinese style",
        "唐装": "tang dynasty costume", "汉服": "hanfu costume", "和服": "kimono",
        "婚纱": "wedding dress", "制服": "uniform", "盔甲": "armor",
        # —— 光线与色彩 ——
        "自然光": "natural lighting", "逆光": "backlit", "侧光": "side lighting",
        "柔光": "soft lighting", "强光": "harsh lighting", "暖光": "warm lighting",
        "冷光": "cool lighting", "黄昏": "dusk", "日落": "sunset", "日出": "sunrise",
        "夜景": "night scene", "灯光": "city lights", "烛光": "candle light",
        "霓虹": "neon lights", "光影": "dramatic light and shadow",
        "红色": "red", "橙色": "orange", "黄色": "yellow", "绿色": "green",
        "青色": "cyan", "蓝色": "blue", "紫色": "purple", "粉色": "pink",
        "棕色": "brown", "黑色": "black", "白色": "white", "灰色": "gray",
        "金色": "golden", "银色": "silver", "彩色": "colorful", "单色": "monochrome",
        "黑白": "black and white",
        # —— 构图与画质 ——
        "广角": "wide angle lens", "长焦": "telephoto lens", "微距": "macro shot",
        "俯拍": "top down view", "仰拍": "low angle shot", "全景": "panoramic view",
        "景深": "depth of field", "虚化": "blurred background", "背景": "background",
        "构图": "composition", "对称": "symmetry", "留白": "negative space",
        "高清": "high resolution", "超清": "ultra sharp", "细节": "highly detailed",
        "清晰": "sharp focus", "8K": "8k detail", "4K": "4k detail",
        "质感": "detailed texture", "纹理": "texture", "光泽": "glossy",
        "透明": "transparent", "金属": "metal", "木质": "wooden", "玻璃": "glass",
        "布料": "fabric", "皮革": "leather", "石材": "stone",
        # —— 常用负向词（用户写在负向框里的中文）——
        "模糊": "blurry", "低质": "low quality", "劣质": "low quality",
        "噪点": "noise", "变形": "deformed", "畸形": "deformed",
        "多余手指": "extra fingers", "六指": "extra fingers", "缺手": "missing hands",
        "水印": "watermark", "文字": "text", "logo": "logo", "签名": "signature",
        "难看": "ugly", "崩坏": "bad anatomy", "过曝": "overexposed",
        "欠曝": "underexposed", "灰暗": "dull colors",
        # —— 补丁词组（真机试提示词时发现的常见说法）——
        "夕阳": "setting sun", "晚霞": "evening glow", "丰富": "rich",
        "穿着": "wearing", "穿": "wearing", "戴": "wearing", "弹": "playing",
        "琵琶": "pipa (chinese lute)", "吉他": "guitar", "小提琴": "violin",
        "吹": "blowing", "手持": "holding", "拿着": "holding",
        "树下": "under a tree", "街道": "street", "小径": "path",
        "晨雾": "morning mist", "雨后": "after rain", "窗边": "by the window",
        "唯美": "aesthetic", "氛围": "atmosphere", "情绪": "moody",
    }
)


# 2026-11-08 补：真机提示词里最常出现、上一版没收录的时间/光线/人物词。
_add(
    {
        # —— 时间与天气 ——
        "雪夜": "snowy night", "雪景": "snow scenery", "夜景": "night scene",
        "夜晚": "night", "夜里": "night", "深夜": "late night", "白天": "daytime",
        "清晨": "morning light", "早晨": "morning light", "傍晚": "dusk",
        "黄昏": "golden hour dusk", "雨天": "rainy day", "雪天": "snowy day",
        "打雷": "thunderstorm", "极光": "aurora", "流星": "meteor",
        # —— 光线与色彩 ——
        "暖色": "warm lighting", "暖光": "warm lighting", "冷色": "cool tones",
        "逆光": "backlit", "侧光": "side lighting", "自然光": "natural lighting",
        "灯光": "artificial lighting", "烛光": "candlelight", "霓虹": "neon lights",
        "剪影": "silhouette", "柔和": "soft lighting", "明亮": "bright lighting",
        # —— 人物与服饰 ——
        "少女": "young woman", "少年": "young man", "女子": "woman", "男子": "man",
        "汉服": "hanfu costume", "和服": "kimono", "旗袍": "qipao dress",
        "婚纱": "wedding dress", "制服": "uniform", "眼镜": "glasses",
        # —— 画质与构图（常用中文说法）——
        "高清": "high resolution", "细节": "fine details", "质感": "rich texture",
        "构图": "composition", "对称": "symmetrical", "景深": "depth of field",
        "背景": "background", "前景": "foreground", "虚化": "blurred background",
        "电影感": "cinematic", "氛围": "atmospheric",
    }
)

_MAX_KEY = max(len(key) for key in _MAP)


@dataclass(frozen=True)
class BilingualPrompt:
    """桥接结果：给 CLIP 的英文关键词 + 用户原文 + 诊断信息。"""

    text: str
    original: str
    translated: bool = False
    mapped_terms: tuple[str, ...] = field(default=())
    unmapped: tuple[str, ...] = field(default=())

    def as_dict(self) -> dict[str, object]:
        return {
            "prompt_effective": self.text,
            "prompt_original": self.original,
            "prompt_translated": self.translated,
            "prompt_unmapped": list(self.unmapped),
        }


def has_cjk(text: str) -> bool:
    return bool(_CJK_SPAN.search(text or ""))


def _translate_run(run: str) -> tuple[list[str], list[str]]:
    """对一个纯中文片段做「最长优先」贪心匹配，返回（英文关键词, 未命中片段）。

    顺序很重要：先按词表试 4/3/2/1 字，命中就用；没命中才看是不是连接词（跳过）；
    还不是连接词就记进未命中片段（连续的字并成一段，方便界面提示用户换词）。
    这样「中国风」不会被单字「中」拆坏。
    """
    terms: list[str] = []
    leftovers: list[str] = []
    pending = ""
    index = 0
    while index < len(run):
        hit = ""
        for size in range(min(_MAX_KEY, len(run) - index), 0, -1):
            candidate = run[index : index + size]
            if candidate in _MAP:
                hit = candidate
                break
        if hit:
            terms.append(_MAP[hit])
            index += len(hit)
            if pending:
                leftovers.append(pending)
                pending = ""
            continue
        ch = run[index]
        if ch in _FILLER:
            if pending:
                leftovers.append(pending)
                pending = ""
        else:
            pending += ch
        index += 1
    if pending:
        leftovers.append(pending)
    return terms, leftovers


def to_clip_prompt(text: Optional[str]) -> BilingualPrompt:
    """把提示词整成 CLIP 看得懂的英文关键词（无中文时原样返回）。

    中英混排：英文片段原样保留，中文片段按词表映射；整句都没命中词表时
    translated=False 并回退原文（行为与桥接前完全一致，不会更差）。
    """
    original = (text or "").strip()
    if not original or not has_cjk(original):
        return BilingualPrompt(original, original)

    parts: list[str] = []
    unmapped: list[str] = []
    for piece in _SPLIT.split(original):
        if not piece:
            continue
        if not has_cjk(piece):
            parts.append(piece)
            continue
        terms, leftovers = _translate_run(piece)
        parts.extend(terms)
        unmapped.extend(leftovers)

    deduped: list[str] = []
    for term in parts:
        clean = term.strip()
        if clean and clean.lower() not in [t.lower() for t in deduped]:
            deduped.append(clean)
    if not deduped:
        return BilingualPrompt(original, original, unmapped=tuple(dict.fromkeys(unmapped)))
    return BilingualPrompt(
        ", ".join(deduped),
        original,
        translated=True,
        mapped_terms=tuple(dict.fromkeys(deduped)),
        unmapped=tuple(dict.fromkeys(unmapped)),
    )
