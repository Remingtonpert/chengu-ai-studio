"""SD1.5 任务适配器（app.tasks.sd_worker）的离线单元测试。

这一层是「桌面进程 → worker 子进程 → 图像引擎」的桥，跑在 .venv-image 里，但它的
校验、环境变量装配、metadata 组装都不需要 torch：FluxTaskAdapter 时代那套思路照搬过来，
改名成 sd-realvis / sd-realvis-i2i，并且参数白名单多了一个 hires（是否强制走分块高清重绘）。
真正调 diffusers 的生成路径由 tests/test_sd15_engine.py 覆盖（需在 .venv-image 下运行）。

需要 torch 的只有两处：_model_id()（它要读 SD15_SUPPORTED_MODELS）和真实生成路径，
所以那类用例在函数内 importorskip，桌面 .venv 里是 skip，其他用例在两端都实跑。
"""

import asyncio
import importlib
from pathlib import Path

import pytest

from app.tasks.models import TaskRequest
from app.tasks.sd_worker import SdTaskAdapter, create_adapter

T2I_ENGINE = "sd-realvis"
I2I_ENGINE = "sd-realvis-i2i"


def _request(parameters=None, prompt="一只可爱的猫", engine_id=T2I_ENGINE, **kwargs):
    return TaskRequest(
        task_type="image",
        engine_id=engine_id,
        model_id=engine_id,
        prompt=prompt,
        parameters=parameters or {},
        **kwargs,
    )


class _FakeEngine:
    """替身引擎：把 generate_image 收到的 kwargs 全记下来，并回放一份真实字段结构的结果。"""

    def __init__(self, model_id: str = T2I_ENGINE, path: str = "output/fake.png"):
        self.model_id = model_id
        self.calls: list[dict] = []
        self.shutdown_calls = 0
        self.path = path

    async def generate_image(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "path": self.path,
            "pil": None,
            "status": "generated",
            "seed": kwargs.get("seed"),
            "mode": "img2img" if kwargs.get("init_image_path") else "txt2img",
            "hires": kwargs.get("hires") is not False,
            "tiles": 15,
            "base_size": "896x512",
            "target_size": f"{kwargs.get('width')}x{kwargs.get('height')}",
            "steps": 6,
            "guidance": 1.0,
            "offload": "model",
            "peak_vram_mb": 4213,
            "lcm": True,
            "prompt_original": kwargs.get("prompt"),
            "prompt_effective": "cute cat",
            "prompt_translated": "cute cat",
            "prompt_unmapped": [],
        }

    async def shutdown(self):
        self.shutdown_calls += 1


def _adapter_with_fake_engine(monkeypatch, **kwargs):
    """构造适配器并把引擎惰性构造替身掉，这样 generate 路径在桌面 .venv 里也能跑。"""
    adapter = SdTaskAdapter(**kwargs)
    fake = _FakeEngine(path=str(Path("output") / "fake.png"))
    monkeypatch.setattr(adapter, "_get_engine", lambda: fake)
    return adapter, fake


# ------------------------------------------------------------------ 协议与环境变量装配
def test_adapter_protocol_attributes():
    adapter = SdTaskAdapter()
    assert adapter.engine_id == T2I_ENGINE
    assert adapter.task_types == frozenset({"image"})
    assert adapter.test_only is False
    for method in ("health_check", "validate_request", "generate", "shutdown"):
        assert callable(getattr(adapter, method))


def test_create_adapter_reads_engine_id_from_env(monkeypatch):
    monkeypatch.setenv("CHENGUAI_SD_ENGINE_ID", I2I_ENGINE)
    adapter = create_adapter()
    assert isinstance(adapter, SdTaskAdapter)
    assert adapter.engine_id == I2I_ENGINE


def test_create_adapter_defaults_to_txt2i_engine(monkeypatch):
    """工厂不带 env 时必须落在文生图引擎上（老配置里没这个变量的场景不能起错板块）。"""
    monkeypatch.delenv("CHENGUAI_SD_ENGINE_ID", raising=False)
    assert create_adapter().engine_id == T2I_ENGINE


def test_create_adapter_loadable_by_factory_path():
    """process_worker 的 --adapter-factory 'app.tasks.sd_worker:create_adapter' 必须可导入。"""
    module = importlib.import_module("app.tasks.sd_worker")
    factory = getattr(module, "create_adapter")
    assert callable(factory)


def test_adapter_reads_cache_and_output_dirs_from_env(monkeypatch):
    """权重根目录与输出目录由桌面进程透传（ProcessEngineAdapter），适配器不许自己另拼一套。"""
    monkeypatch.setenv("CHENGUAI_MODEL_CACHE_DIR", r"D:\Models")
    monkeypatch.setenv("CHENGUAI_OUTPUT_DIR", r"D:\Output")
    adapter = SdTaskAdapter()
    assert adapter._cache_dir == r"D:\Models"
    assert adapter._output_dir == r"D:\Output"


def test_adapter_falls_back_to_relative_dirs_without_env(monkeypatch):
    monkeypatch.delenv("CHENGUAI_MODEL_CACHE_DIR", raising=False)
    monkeypatch.delenv("CHENGUAI_OUTPUT_DIR", raising=False)
    adapter = SdTaskAdapter()
    assert adapter._cache_dir == "./models"
    assert adapter._output_dir == "./output"


def test_adapter_image_role_env_is_normalised(monkeypatch):
    """板块角色读 env 后统一小写去空白：配置写成 I2i 也一样能拦住「文生图传垫图」。"""
    monkeypatch.setenv("CHENGUAI_SD_IMAGE_ROLE", " I2I ")
    assert SdTaskAdapter().image_role == "i2i"
    monkeypatch.setenv("CHENGUAI_SD_IMAGE_ROLE", "")
    assert SdTaskAdapter().image_role == ""  # 空＝不限制（自定义引擎/测试走这条）


def test_model_id_reuses_one_weight_copy_for_both_boards(monkeypatch):
    """引擎 id 带板块后缀（sd-realvis-i2i）时权重 id 仍是 sd-realvis：不该下载第二份权重。"""
    pytest.importorskip("torch", reason="_model_id() 要读 SD15_SUPPORTED_MODELS，属图像引擎环境")
    monkeypatch.delenv("CHENGUAI_SD_MODEL_ID", raising=False)
    assert SdTaskAdapter(engine_id=I2I_ENGINE)._model_id() == T2I_ENGINE
    assert SdTaskAdapter(engine_id=T2I_ENGINE)._model_id() == T2I_ENGINE


def test_model_id_env_override_and_fallback(monkeypatch):
    """CHENGUAI_SD_MODEL_ID 可显式指定权重；写了不认识的 id 一律回落 sd-realvis，不炸 worker。"""
    pytest.importorskip("torch", reason="_model_id() 要读 SD15_SUPPORTED_MODELS，属图像引擎环境")
    monkeypatch.setenv("CHENGUAI_SD_MODEL_ID", T2I_ENGINE)
    assert SdTaskAdapter(engine_id=I2I_ENGINE)._model_id() == T2I_ENGINE
    monkeypatch.setenv("CHENGUAI_SD_MODEL_ID", "sd-realvis-i2i")
    assert SdTaskAdapter(engine_id=I2I_ENGINE)._model_id() == T2I_ENGINE
    monkeypatch.setenv("CHENGUAI_SD_MODEL_ID", "flux-schnell")  # 旧引擎名残留：不能拿去加载
    assert SdTaskAdapter(engine_id=T2I_ENGINE)._model_id() == T2I_ENGINE


# -------------------------------------------------------------------- validate_request
def test_validate_request_rejects_empty_prompt():
    adapter = SdTaskAdapter()
    with pytest.raises(ValueError, match="提示词不能为空"):
        adapter.validate_request(_request(prompt="   "))


def test_validate_request_rejects_unknown_params():
    """白名单外一律拦下：FLUX 时代的 lora_scale 之类参数在 SD1.5 引擎上没有对应实现。"""
    adapter = SdTaskAdapter()
    with pytest.raises(ValueError, match="不支持的参数"):
        adapter.validate_request(_request(parameters={"lora_scale": 0.5}))


def test_validate_request_allows_the_documented_whitelist():
    """白名单里的参数（含新加的 hires）全部要能通过，否则界面会平白少一个开关。"""
    adapter = SdTaskAdapter()
    adapter.validate_request(
        _request(
            parameters={
                "width": 1024,
                "height": 576,
                "steps": 6,
                "seed": 7,
                "negative_prompt": "模糊, 多余手指",
                "strength": 0.6,
                "hires": False,
            }
        )
    )


def test_validate_request_rejects_bad_int_types():
    adapter = SdTaskAdapter()
    for key, value in (("width", "512"), ("height", None), ("steps", 6.0), ("seed", "auto")):
        with pytest.raises(ValueError, match=f"参数 {key} 必须是整数"):
            adapter.validate_request(_request(parameters={key: value}))


def test_validate_request_rejects_bad_strength():
    adapter = SdTaskAdapter()
    with pytest.raises(ValueError, match="参数 strength 必须是数字"):
        adapter.validate_request(_request(parameters={"strength": "high"}))


def test_validate_request_rejects_non_bool_hires():
    """hires 只认真/假：写成 "true"、1 会让「到底走不走重绘」在两端语义不一致。"""
    adapter = SdTaskAdapter()
    for value in ("true", "false", 1, 0, None):
        with pytest.raises(ValueError, match="参数 hires 必须是布尔值"):
            adapter.validate_request(_request(parameters={"hires": value}))
    for value in (True, False):
        adapter.validate_request(_request(parameters={"hires": value}))


def test_validate_request_i2i_board_requires_init_image():
    """「图生图」板块没有垫图必须在加载几个 GB 权重之前就拦住。"""
    adapter = SdTaskAdapter(engine_id=I2I_ENGINE, image_role="i2i")
    with pytest.raises(ValueError, match="图生图引擎需要垫图"):
        adapter.validate_request(_request(engine_id=I2I_ENGINE))
    with pytest.raises(ValueError, match="图生图引擎需要垫图"):
        adapter.validate_request(_request(engine_id=I2I_ENGINE, parameters={"init_image_path": "  "}))


def test_validate_request_t2i_board_rejects_init_image():
    """「文生图」板块塞了垫图同样拦下，并把用户指向正确的板块与引擎名。"""
    adapter = SdTaskAdapter(image_role="t2i")
    with pytest.raises(ValueError, match="文生图引擎不接受垫图") as caught:
        adapter.validate_request(_request(parameters={"init_image_path": "some.png"}))
    assert I2I_ENGINE in str(caught.value)


def test_validate_request_rejects_blank_init_path():
    adapter = SdTaskAdapter()
    with pytest.raises(ValueError, match="参数 init_image_path 必须是非空字符串"):
        adapter.validate_request(_request(parameters={"init_image_path": ""}))


def test_validate_request_rejects_missing_init_image(tmp_path):
    adapter = SdTaskAdapter()
    missing = tmp_path / "nope.png"
    with pytest.raises(ValueError, match="图生图输入文件不存在") as caught:
        adapter.validate_request(_request(parameters={"init_image_path": str(missing)}))
    assert str(missing) in str(caught.value)


def test_validate_request_accepts_valid_img2img_params(tmp_path):
    adapter = SdTaskAdapter(engine_id=I2I_ENGINE, image_role="i2i")
    source = tmp_path / "source.png"
    source.write_bytes(b"\x89PNG\r\n\x1a\n")
    adapter.validate_request(
        _request(
            engine_id=I2I_ENGINE,
            parameters={
                "init_image_path": str(source),
                "strength": 0.55,
                "width": 768,
                "height": 1024,
                "steps": 6,
                "seed": 7,
                "hires": True,
            },
        )
    )


def test_out_of_range_numbers_are_clamped_by_the_engine_not_the_worker():
    """worker 只校验类型、不校验范围：越界数字必须活到 validate_request，再由引擎侧收口。

    分工是刻意设计的（宽高的安全线只有 plan.py 一处，worker 再写一份就会漂移）：
    99999 这种误输入由 round_to_multiple 钳到 MAX_SIDE，超预算则由 PlanBudgetError 报错。
    """
    from app.engine.image.plan import MAX_SIDE, PlanBudgetError, resolve_output_plan, round_to_multiple

    adapter = SdTaskAdapter()
    adapter.validate_request(_request(parameters={"width": 99999, "height": 99999, "steps": 999}))
    assert round_to_multiple(99999) == MAX_SIDE
    plan = resolve_output_plan(99999, 99999)
    assert (plan.target_width, plan.target_height) == (MAX_SIDE, MAX_SIDE)
    with pytest.raises(PlanBudgetError, match="单遍直出会超显存预算"):
        resolve_output_plan(1920, 1080, hires=False)


# ------------------------------------------------------------------------- generate
def test_generate_forwards_params_and_returns_task_result(monkeypatch, tmp_path):
    out = tmp_path / "sd_txt2img_hires_1920x1080.png"
    adapter, fake = _adapter_with_fake_engine(monkeypatch)
    fake.path = str(out)
    request = _request(parameters={"width": 1920, "height": 1080, "steps": 6, "seed": 7, "hires": True})

    async def scenario():
        return await adapter.generate(request)

    result = asyncio.run(scenario())
    assert result.output_files == (str(out),)
    assert result.metadata["status"] == "generated"
    assert result.metadata["seed"] == 7
    assert result.metadata["model_id"] == T2I_ENGINE
    assert result.metadata["mode"] == "txt2img"
    assert result.metadata["hires"] is True
    assert result.metadata["tiles"] == 15
    assert result.metadata["base_size"] == "896x512"
    assert result.metadata["target_size"] == "1920x1080"
    assert result.metadata["peak_vram_mb"] == 4213
    assert result.metadata["offload"] == "model"
    # 中文提示词桥接的四个字段必须一路带到界面历史记录，缺一个就查不到实际喂给 CLIP 的词。
    for key in ("prompt_original", "prompt_effective", "prompt_translated", "prompt_unmapped"):
        assert key in result.metadata

    kwargs = fake.calls[0]
    assert kwargs["prompt"] == "一只可爱的猫"
    assert (kwargs["width"], kwargs["height"]) == (1920, 1080)
    assert kwargs["steps"] == 6 and kwargs["seed"] == 7 and kwargs["hires"] is True
    assert kwargs["init_image_path"] is None


def test_generate_uses_engine_defaults_when_params_missing(monkeypatch):
    """参数缺省时由适配器给一套与面板默认一致的兜底值（steps/hires 交给引擎决定）。"""
    adapter, fake = _adapter_with_fake_engine(monkeypatch)

    async def scenario():
        return await adapter.generate(_request())

    asyncio.run(scenario())
    kwargs = fake.calls[0]
    assert (kwargs["width"], kwargs["height"]) == (512, 512)
    assert kwargs["steps"] is None and kwargs["seed"] is None
    assert kwargs["negative_prompt"] is None and kwargs["hires"] is None
    assert kwargs["strength"] == 0.6
    assert kwargs["init_image_path"] is None


def test_generate_marks_img2img_mode_when_init_image_present(monkeypatch, tmp_path):
    source = tmp_path / "source.png"
    source.write_bytes(b"\x89PNG\r\n\x1a\n")
    adapter, fake = _adapter_with_fake_engine(monkeypatch, engine_id=I2I_ENGINE, image_role="i2i")

    async def scenario():
        return await adapter.generate(
            _request(engine_id=I2I_ENGINE, parameters={"init_image_path": str(source), "strength": 0.55})
        )

    result = asyncio.run(scenario())
    assert result.metadata["mode"] == "img2img"
    assert fake.calls[0]["init_image_path"] == str(source)
    assert fake.calls[0]["strength"] == 0.55


def test_generate_coerces_non_string_prompts_into_plain_text(monkeypatch):
    """上游 JSON 混进列表/字节时，进引擎前先规整成合法 str：CLIP 只吃干净字符串。"""
    adapter, fake = _adapter_with_fake_engine(monkeypatch)

    async def scenario():
        return await adapter.generate(
            _request(prompt=["一只", "猫"], parameters={"negative_prompt": ["模糊", "多余手指"]})
        )

    asyncio.run(scenario())
    kwargs = fake.calls[0]
    assert kwargs["prompt"] == "一只, 猫"
    assert kwargs["negative_prompt"] == "模糊, 多余手指"
    assert not isinstance(kwargs["negative_prompt"], (list, bytes))


def test_generate_surfaces_engine_errors(monkeypatch):
    """引擎抛的错误（缺权重、超预算）原样向上冒泡：worker 不吞、也不换成通用文案。"""
    pytest.importorskip("torch", reason="SD15EngineError 来自 app.engine.image.sd15，属图像引擎环境")
    from app.engine.image.sd15 import SD15EngineError

    adapter, fake = _adapter_with_fake_engine(monkeypatch)

    async def failing_generate(**kwargs):
        raise SD15EngineError("未找到模型 sd-realvis 的完整本地权重快照")

    monkeypatch.setattr(fake, "generate_image", failing_generate)

    async def scenario():
        return await adapter.generate(_request())

    with pytest.raises(SD15EngineError, match="完整本地权重快照"):
        asyncio.run(scenario())


def test_shutdown_releases_engine_once(monkeypatch):
    """释放后把引用清空：二次 shutdown 不该再碰已销毁的引擎（切板块时会连续调两次）。"""
    adapter, fake = _adapter_with_fake_engine(monkeypatch)
    # 帮助函数把 adapter 的 health_check/validate_request/generate/shutdown 全换成了记录调用的桩，
    # 而这里要测的是「真」shutdown 有没有释放引擎并清空引用：把真方法换回来，并假装引擎已懒加载起来。
    monkeypatch.setattr(
        adapter, "shutdown", SdTaskAdapter.shutdown.__get__(adapter, SdTaskAdapter), raising=False
    )
    adapter._engine = fake

    async def scenario():
        await adapter.shutdown()
        await adapter.shutdown()

    asyncio.run(scenario())
    assert fake.shutdown_calls == 1
    assert adapter._engine is None


def test_shutdown_without_engine_is_safe():
    """没起过引擎的 worker 直接 shutdown 不该抛：进程退出路径上这是常态。"""

    async def scenario():
        await SdTaskAdapter().shutdown()

    asyncio.run(scenario())


# ----------------------------------------------------------------------- health_check
def test_health_check_reports_cuda_or_fails_gracefully():
    async def scenario():
        adapter = SdTaskAdapter()
        healthy, message = await adapter.health_check()
        assert isinstance(healthy, bool)
        assert isinstance(message, str) and message
        if not healthy:
            # 桌面 .venv（无 torch）或无 GPU 环境必须优雅失败而不是抛异常。
            assert ("CUDA" in message) or ("环境异常" in message)

    asyncio.run(scenario())


def test_health_check_names_the_board_role_it_enforces():
    """健康文案要带上板块角色，运维看 worker 状态才知道这个进程只收文生图还是只收图生图。"""
    pytest.importorskip("torch", reason="健康检查走通到 CUDA 的分支需要图像引擎环境")

    async def scenario():
        adapter = SdTaskAdapter(engine_id=T2I_ENGINE, image_role="t2i")
        healthy, message = await adapter.health_check()
        if healthy:
            assert "文生图" in message
        else:
            assert "CUDA" in message or "环境异常" in message

    asyncio.run(scenario())
