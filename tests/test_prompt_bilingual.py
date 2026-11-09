"""中文提示词桥接的回归测试（纯标准库、零显存、离线）。

这条链路关系到「中英文 prompt」铁律：SD1.5 的 CLIP 没有中日韩词表，
桥接挂了或词表退化，文生图/图生图就会退化成「有画面没主体」。
"""

from __future__ import annotations

from app.engine.prompt_bilingual import has_cjk, to_clip_prompt

ZH_RICH = "雪夜里的中国风汉服少女，逆光，景深，电影感，细节丰富"
ZH_CAT = "一只在窗台上的橘猫，自然光，照片质感，细节丰富"
EN_PLAIN = "a cinematic photo of an orange tabby cat on a windowsill"


def test_英文提示词原样透传():
    bridged = to_clip_prompt(EN_PLAIN)
    assert bridged.translated is False
    assert bridged.text == EN_PLAIN
    assert bridged.unmapped == ()
    assert bridged.original == EN_PLAIN


def test_中文常用描述词能桥接成英文标签():
    bridged = to_clip_prompt(ZH_CAT)
    assert bridged.translated is True
    for term in ("orange tabby cat", "windowsill", "natural lighting", "photograph"):
        assert term in bridged.text, bridged.text
    assert bridged.unmapped == ()


def test_补录的时间光线人物词全部命中():
    bridged = to_clip_prompt(ZH_RICH)
    for term in (
        "snowy night",
        "chinese traditional style",
        "hanfu costume",
        "young woman",
        "backlit",
        "depth of field",
        "cinematic",
    ):
        assert term in bridged.text, bridged.text
    assert bridged.unmapped == ()


def test_词组不被单字拆坏():
    """「中国风」必须整体命中，不能被「中/国」单字连接词拆碎。"""
    bridged = to_clip_prompt("中国风")
    assert bridged.text == "chinese traditional style"


def test_未收录的词如实上报而不是假装成功():
    bridged = to_clip_prompt("一只橘猫，非常抽象的概念")
    assert "orange tabby cat" in bridged.text
    assert any("非常抽象" in word for word in bridged.unmapped), bridged.unmapped


def test_原文永远保留且元数据键固定():
    bridged = to_clip_prompt(ZH_RICH)
    payload = bridged.as_dict()
    assert set(payload) == {"prompt_original", "prompt_effective", "prompt_translated", "prompt_unmapped"}
    assert payload["prompt_original"] == ZH_RICH
    assert payload["prompt_effective"] == bridged.text


def test_桥接结果是幂等的再跑一次就是英文直通():
    once = to_clip_prompt(ZH_CAT)
    twice = to_clip_prompt(once.text)
    assert twice.translated is False
    assert twice.text == once.text


def test_空输入不炸():
    bridged = to_clip_prompt("")
    assert bridged.text == ""
    assert bridged.translated is False


def test_has_cjk_判断正确():
    assert has_cjk("橘猫") is True
    assert has_cjk("orange cat") is False
