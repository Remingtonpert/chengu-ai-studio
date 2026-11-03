"""FLUX 任务适配器（app.tasks.flux_worker）的离线单元测试。

这些测试不需要 torch：FluxTaskAdapter 只在 health_check/generate 内部导入 torch，
validate_request / create_adapter / 协议属性都可以离线验证。
生成路径本身由 tests/test_flux_image.py（需在 .venv-image 下运行）覆盖。
"""

import asyncio
from pathlib import Path

import pytest

from app.tasks.flux_worker import FluxTaskAdapter, create_adapter
from app.tasks.models import TaskRequest


def _request(parameters=None, prompt="一只可爱的猫", **kwargs):
    return TaskRequest(
        task_type="image",
        engine_id="flux-schnell",
        model_id="flux-schnell",
        prompt=prompt,
        parameters=parameters or {},
        **kwargs,
    )


def test_adapter_protocol_attributes():
    adapter = FluxTaskAdapter()
    assert adapter.engine_id == "flux-schnell"
    assert adapter.task_types == frozenset({"image"})
    assert adapter.test_only is False
    for method in ("health_check", "validate_request", "generate", "shutdown"):
        assert callable(getattr(adapter, method))


def test_create_adapter_reads_engine_id_from_env(monkeypatch):
    monkeypatch.setenv("CHENGUAI_FLUX_ENGINE_ID", "flux-schnell")
    adapter = create_adapter()
    assert isinstance(adapter, FluxTaskAdapter)
    assert adapter.engine_id == "flux-schnell"


def test_create_adapter_loadable_by_factory_path():
    """process_worker 的 --adapter-factory 'module:callable' 路径必须可导入。"""
    import importlib

    module = importlib.import_module("app.tasks.flux_worker")
    factory = getattr(module, "create_adapter")
    assert callable(factory)


def test_validate_request_rejects_empty_prompt():
    adapter = FluxTaskAdapter()
    with pytest.raises(ValueError, match="提示词不能为空"):
        adapter.validate_request(_request(prompt="   "))


def test_validate_request_rejects_unknown_params():
    adapter = FluxTaskAdapter()
    with pytest.raises(ValueError, match="不支持的参数"):
        adapter.validate_request(_request(parameters={"lora_scale": 0.5}))


def test_validate_request_rejects_bad_types():
    adapter = FluxTaskAdapter()
    with pytest.raises(ValueError, match="width"):
        adapter.validate_request(_request(parameters={"width": "512"}))
    with pytest.raises(ValueError, match="strength"):
        adapter.validate_request(_request(parameters={"strength": "high"}))


def test_validate_request_rejects_missing_init_image(tmp_path):
    adapter = FluxTaskAdapter()
    missing = tmp_path / "nope.png"
    with pytest.raises(ValueError, match="图生图输入文件不存在"):
        adapter.validate_request(_request(parameters={"init_image_path": str(missing)}))


def test_validate_request_accepts_valid_img2img_params(tmp_path):
    adapter = FluxTaskAdapter()
    source = tmp_path / "source.png"
    source.write_bytes(b"\x89PNG\r\n\x1a\n")
    adapter.validate_request(
        _request(
            parameters={
                "init_image_path": str(source),
                "strength": 0.55,
                "width": 768,
                "height": 1024,
                "steps": 4,
                "seed": 7,
            }
        )
    )


def test_health_check_reports_cuda_or_fails_gracefully(monkeypatch):
    async def scenario():
        adapter = FluxTaskAdapter()
        healthy, message = await adapter.health_check()
        assert isinstance(healthy, bool)
        assert isinstance(message, str) and message
        if not healthy:
            # 桌面 .venv（无 torch）或无 GPU 环境必须优雅失败而不是抛异常。
            assert ("CUDA" in message) or ("环境异常" in message)

    asyncio.run(scenario())
