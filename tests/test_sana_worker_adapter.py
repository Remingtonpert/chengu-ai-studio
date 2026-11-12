"""Sana 任务适配器（app.tasks.sana_worker）的离线单元测试。

这一层是「桌面进程 → worker 子进程 → Sana 引擎」的桥。真正的推理只在 .venv-image 里发生，
但协议、参数白名单、提示词规整、metadata 组装都不需要 torch：引擎类与「支持的模型表」用
monkeypatch 换成替身，桌面 .venv（没有 torch）里也能实跑，而且 _get_engine 的缓存与
shutdown 都会真的走一遍（不是 skip）。

协议对齐 app/tasks/process_worker.py 要求的 {health_check, validate_request, generate,
shutdown}，generate 返回 app.tasks.models.TaskResult（output_files + metadata），
不是自研结构——历史上这里写错过一次方法名，worker 起不来，所以单独钉住。
"""

from __future__ import annotations

import asyncio
import importlib
from pathlib import Path

import pytest

from app.tasks.models import TaskRequest, TaskResult
from app.tasks.sana_worker import SANA_DEFAULT_MODEL_ID, SanaTaskAdapter, create_adapter

ENGINE = "sana-multiling"


def _request(parameters=None, prompt="一只橘猫坐在屋顶上看日落", engine_id=ENGINE, **kwargs):
    return TaskRequest(
        task_type="image",
        engine_id=engine_id,
        model_id=kwargs.pop("model_id", engine_id),
        prompt=prompt,
        parameters=parameters or {},
        **kwargs,
    )


class _FakeEngine:
    """替身引擎：记下 generate_image 收到的 kwargs，并回放一份真实字段结构的结果。"""

    def __init__(self, model_id=None, cache_dir=None, output_dir=None):
        self.model_id = model_id
        self.cache_dir = cache_dir
        self.output_dir = output_dir
        self.calls: list[dict] = []
        self.shutdown_calls = 0

    async def generate_image(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "path": str(Path(self.output_dir or "output") / "fake_sana.png"),
            "status": "generated",
            "seed": kwargs.get("seed"),
            "mode": "txt2img",
            # 超单遍预算时 Sana 只有「底图 + Lanczos 放大」，没有重绘、没有分块。
            "upscaled": True,
            "tiles": 0,
            "base_size": "1376x768",
            "target_size": "1920x1088",
            "steps": 20,
            "guidance": 4.5,
            "offload": "model",
            "peak_vram_mb": 5123,
            "prompt_original": kwargs.get("prompt"),
            "prompt_effective": kwargs.get("prompt"),
            "prompt_translated": False,
            "prompt_unmapped": [],
        }

    async def shutdown(self):
        self.shutdown_calls += 1


def _adapter_with_fake_engine(monkeypatch, tmp_path=None, **kwargs):
    """构造适配器，把「引擎类 + 支持的模型表」整个换成替身。

    这样真正的 _get_engine（含引擎缓存与 shutdown）都会实跑，而 app.engine.image.sana
    里那份 import torch 不会被触发：桌面 .venv（没有 torch）里也能测 generate 这条链路。
    替身实例通过 adapter._engine 取回。
    """
    monkeypatch.setattr(
        "app.tasks.sana_worker._load_sana_engine_symbols",
        lambda: (_FakeEngine, {SANA_DEFAULT_MODEL_ID: {}}),
    )
    if tmp_path is not None:
        monkeypatch.setenv("CHENGUAI_OUTPUT_DIR", str(tmp_path))
    return SanaTaskAdapter(**kwargs)


# ------------------------------------------------------------------ 协议与装配
def test_adapter_protocol_attributes() -> None:
    """process_worker._serve 只认这四个方法，名字与 task_types 都不能偏。"""
    adapter = SanaTaskAdapter()
    assert adapter.engine_id == ENGINE
    assert adapter.task_types == frozenset({"image"})
    assert adapter.test_only is False
    for method in ("health_check", "validate_request", "generate", "shutdown"):
        assert callable(getattr(adapter, method))
    # 旧实现用的是 run()，与 worker 协议不符（engine generate must return TaskResult 那关过不去）。
    assert not hasattr(adapter, "run")


def test_create_adapter_reads_engine_id_from_env(monkeypatch) -> None:
    monkeypatch.setenv("CHENGUAI_SANA_ENGINE_ID", "sana-multiling")
    adapter = create_adapter()
    assert isinstance(adapter, SanaTaskAdapter)
    assert adapter.engine_id == "sana-multiling"


def test_create_adapter_defaults_to_sana(monkeypatch) -> None:
    """老配置里没有 CHENGUAI_SANA_ENGINE_ID 时也要落在文生图引擎上，不能起错板块。"""
    monkeypatch.delenv("CHENGUAI_SANA_ENGINE_ID", raising=False)
    assert create_adapter().engine_id == SANA_DEFAULT_MODEL_ID


def test_adapter_factory_path_is_importable() -> None:
    """注册表里的 'app.tasks.sana_worker:create_adapter' 必须真的能被 worker 导入。"""
    module = importlib.import_module("app.tasks.sana_worker")
    assert getattr(module, "create_adapter") is create_adapter


def test_snapshot_root_prefers_injected_cache_dir(monkeypatch, tmp_path) -> None:
    """注册时注入 CHENGUAI_SANA_MODEL_CACHE_DIR（配置项 image_model_dir 指哪就读哪）。"""
    monkeypatch.setenv("CHENGUAI_SANA_MODEL_CACHE_DIR", str(tmp_path / "sana-custom"))
    assert SanaTaskAdapter()._snapshot_root() == tmp_path / "sana-custom"


def test_snapshot_root_falls_back_to_models_dir(monkeypatch, tmp_path) -> None:
    """没注入时按 <models_dir>/<model_id> 拼，与 scripts/download_sana_weights.py 的落盘位置一致。"""
    monkeypatch.delenv("CHENGUAI_SANA_MODEL_CACHE_DIR", raising=False)
    monkeypatch.setattr(
        "app.tasks.sana_worker._load_sana_engine_symbols",
        lambda: (object, {SANA_DEFAULT_MODEL_ID: {}}),
    )
    adapter = SanaTaskAdapter(cache_dir=str(tmp_path))
    assert adapter._snapshot_root() == tmp_path / SANA_DEFAULT_MODEL_ID


# ------------------------------------------------------------------ 参数白名单
@pytest.mark.parametrize(
    "parameters,expect_in_message",
    [
        ({"init_image_path": "x.png"}, "图生图"),
        ({"init_image_path": "x.png"}, "wan-i2i"),
        ({"strength": 0.5}, "去噪强度"),
        ({"hires": True}, "分块高清重绘"),
        ({"hires": True}, "放大"),
        ({"image_path": "x.png"}, "不接受输入图片"),
    ],
)
def test_rejected_params_fail_loudly_not_silently(parameters, expect_in_message) -> None:
    """写了 Sana 没有的参数必须当场报错并指路（改用哪个板块），静默丢弃是最难查的一类问题。"""
    adapter = SanaTaskAdapter()
    with pytest.raises(ValueError) as caught:
        adapter.validate_request(_request(parameters=parameters))
    message = str(caught.value)
    assert expect_in_message in message


def test_unknown_param_is_rejected_with_names() -> None:
    adapter = SanaTaskAdapter()
    with pytest.raises(ValueError) as caught:
        adapter.validate_request(_request(parameters={"cfg_scale": 7}))
    assert "cfg_scale" in str(caught.value)


def test_empty_prompt_rejected() -> None:
    adapter = SanaTaskAdapter()
    with pytest.raises(ValueError):
        adapter.validate_request(_request(prompt="   "))


@pytest.mark.parametrize(
    "parameters",
    [
        {"width": 0},
        {"height": -32},
        {"width": "1024"},
        {"width": True},
        {"steps": 0},
        {"seed": "7"},
        {"negative_prompt": 3},
    ],
)
def test_bad_param_types_rejected(parameters) -> None:
    """类型错误当场拦住：坏值进 pipeline 只会浪费几分钟加载权重后才炸。"""
    adapter = SanaTaskAdapter()
    with pytest.raises(ValueError):
        adapter.validate_request(_request(parameters=parameters))


@pytest.mark.parametrize(
    "prompt",
    ["一只猫", "a cat on a 屋顶", ["一只", "cat"], b"a cat"],
)
def test_prompt_coercion_accepts_odd_upstream_shapes(prompt) -> None:
    """网页端/智能体/历史数据偶尔把提示词写成列表或字节串，worker 这一层先收口成 str。"""
    assert SanaTaskAdapter().validate_request(_request(prompt=prompt)) is None


def test_ranges_are_left_to_the_engine_not_duplicated_here() -> None:
    """越界数字放行给引擎钳位：宽高等安全线只有 plan.py 一处，worker 再写一套就会漂移。"""
    adapter = SanaTaskAdapter()
    assert adapter.validate_request(_request(parameters={"width": 99999, "steps": 999})) is None


# ------------------------------------------------------------------ generate
def test_generate_returns_task_result_with_full_metadata(monkeypatch, tmp_path) -> None:
    """generate 必须返回 TaskResult，metadata 里是界面与历史详情要读的那几项。"""
    adapter = _adapter_with_fake_engine(monkeypatch, tmp_path)
    result = asyncio.run(
        adapter.generate(
            _request(
                prompt="a 猫 sitting on a 屋顶 watching sunset",
                parameters={"width": 1920, "height": 1088, "steps": 20, "seed": 20261110},
            )
        )
    )
    assert isinstance(result, TaskResult)
    assert result.test_only is False
    assert len(result.output_files) == 1
    assert str(result.output_files[0]).endswith("fake_sana.png")
    assert Path(result.output_files[0]).is_absolute(), "产物路径必须落在注入的 output 目录里"
    meta = result.metadata
    for key in (
        "engine_id",
        "model_id",
        "status",
        "seed",
        "steps",
        "guidance",
        "mode",
        "upscaled",
        "tiles",
        "base_size",
        "target_size",
        "prompt_original",
        "prompt_effective",
        "prompt_translated",
        "prompt_unmapped",
        "peak_vram_mb",
        "offload",
    ):
        assert key in meta, key
    assert meta["engine_id"] == ENGINE
    assert meta["model_id"] == SANA_DEFAULT_MODEL_ID
    # 放大与重绘的区分必须留档：upscaled=True、tiles=0、没有重绘字段，
    # 界面才不会把「底图 + Lanczos」写成「高清重绘」。
    assert meta["upscaled"] is True
    assert meta["tiles"] == 0
    assert meta["mode"] == "txt2img"
    # 中英混合原样进模型：原文与实际提示词必须相等，且不声称翻译过。
    assert meta["prompt_original"] == meta["prompt_effective"]
    assert meta["prompt_translated"] is False
    assert meta["prompt_unmapped"] == []


def test_generate_passes_only_allowed_kwargs_to_engine(monkeypatch, tmp_path) -> None:
    """引擎签名里没有 init_image_path/hires 这些参数，worker 不能把它们塞进去。"""
    adapter = _adapter_with_fake_engine(monkeypatch, tmp_path)
    asyncio.run(adapter.generate(_request(parameters={"width": 1024, "height": 1024})))
    fake = adapter._engine
    assert sorted(fake.calls[0]) == ["height", "negative_prompt", "prompt", "seed", "steps", "width"]
    # 缺省宽高用 Sana 的甜点档 1024，而不是 Wan 时代的 832×480。
    assert (fake.calls[0]["width"], fake.calls[0]["height"]) == (1024, 1024)
    assert fake.calls[0]["steps"] is None  # steps 缺省交给引擎按模型表取 20


def test_engine_is_cached_across_requests(monkeypatch, tmp_path) -> None:
    """两次请求只加载一次引擎：Sana 权重 9GB，每次请求重建 pipeline 会慢到不可用。"""
    adapter = _adapter_with_fake_engine(monkeypatch, tmp_path)
    asyncio.run(adapter.generate(_request()))
    first = adapter._engine
    asyncio.run(adapter.generate(_request()))
    assert adapter._engine is first
    assert len(first.calls) == 2


def test_generate_coerces_list_prompt_into_plain_text(monkeypatch, tmp_path) -> None:
    adapter = _adapter_with_fake_engine(monkeypatch, tmp_path)
    asyncio.run(adapter.generate(_request(prompt=["一只", "橘猫"])))
    assert adapter._engine.calls[0]["prompt"] == "一只, 橘猫"


def test_generate_validates_before_touching_the_engine(monkeypatch) -> None:
    """空提示词必须在加载 9GB 权重之前就被拒绝：_get_engine 一旦被调用就直接失败。"""
    adapter = SanaTaskAdapter()

    def _boom():
        raise AssertionError("校验失败时不允许触碰引擎（会白加载 9GB 权重）")

    monkeypatch.setattr(adapter, "_get_engine", _boom)
    with pytest.raises(ValueError):
        asyncio.run(adapter.generate(_request(prompt="")))
    with pytest.raises(ValueError):
        asyncio.run(adapter.generate(_request(parameters={"init_image_path": "x.png"})))


def test_shutdown_releases_engine_once(monkeypatch, tmp_path) -> None:
    """shutdown 必须真的调用引擎的 shutdown 并清掉缓存，第二次再调不该报错。"""
    adapter = _adapter_with_fake_engine(monkeypatch, tmp_path)
    asyncio.run(adapter.generate(_request()))
    fake = adapter._engine
    assert fake.shutdown_calls == 0
    asyncio.run(adapter.shutdown())
    assert fake.shutdown_calls == 1
    assert adapter._engine is None
    asyncio.run(adapter.shutdown())  # 幂等：没有引擎时也必须是空操作
    assert fake.shutdown_calls == 1


# ------------------------------------------------------------------ 健康检查
def test_health_check_never_raises() -> None:
    """没有 torch / 没有 CUDA 的环境必须优雅报告，而不是把 worker 带崩。"""
    ok, message = asyncio.run(SanaTaskAdapter().health_check())
    assert isinstance(ok, bool)
    assert isinstance(message, str) and message
    if not ok:
        assert any(word in message for word in ("CUDA", "环境", "torch", "权重", "ModuleNotFound"))


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
