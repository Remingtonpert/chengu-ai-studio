"""FLUX 图片生成引擎的单元测试"""

import pytest
import asyncio
from pathlib import Path


# FLUX integration tests require the separately provisioned image-engine environment.
pytest.importorskip("torch", reason="requires the separate image-engine environment")

# 导入待测试的类（当实现完成后）
from app.engine.image.flux import FluxImageEngine, FLUX_SUPPORTED_MODELS


class TestFluxSupportedModels:
    """测试支持的模型列表"""
    
    def test_flux_models_defined(self):
        """测试模型定义是否存在"""
        assert "flux-schnell" in FLUX_SUPPORTED_MODELS
        assert "flux-dev" in FLUX_SUPPORTED_MODELS
    
    def test_flux_schnell_config(self):
        """测试 flux-schnell 的配置"""
        config = FLUX_SUPPORTED_MODELS["flux-schnell"]
        
        assert config["hf_repo"] == "black-forest-labs/FLUX.1-schnell"
        assert "Apache-2.0" in config["license"]
        assert config["est_mem_gb"] == 6.0
        assert config["default_steps"] == 4


class TestFluxImageEngine:
    """测试 FluxImageEngine 类"""
    
    def test_engine_creation(self):
        """测试引擎创建"""
        engine = FluxImageEngine("flux-schnell")
        assert engine.engine_name == "FluxImageEngine"
        assert engine.model_id == "flux-schnell"
        assert engine.default_steps == 4
    
    def test_invalid_model_id(self):
        """测试无效的模型 ID"""
        with pytest.raises(ValueError, match="不支持的模型 ID"):
            FluxImageEngine("unknown-model")
    
    def test_generate_image_requires_gpu_or_weights(self):
        """未加载权重时调用生成：应抛出 FluxEngineError 而不是返回占位符"""
        from app.engine.image.flux import FluxEngineError

        async def scenario():
            engine = FluxImageEngine("flux-schnell", cache_dir="./models/nonexistent-test")
            # 无网络/无权重或无 GPU 时，load() 必须显式失败（绝不静默降级）
            with pytest.raises(FluxEngineError):
                await engine.generate_image("一只可爱的猫", width=512, height=512)

        asyncio.run(scenario())

    def test_default_steps_property(self):
        """测试 default_steps 属性"""
        engine = FluxImageEngine("flux-schnell")
        steps = engine.default_steps
        
        assert steps == 4

    def test_size_rounding_to_multiple_of_16(self):
        """FLUX 要求尺寸是 16 的倍数，生成前应自动取整"""
        from app.engine.image.flux import _round_to_multiple

        assert _round_to_multiple(512) == 512
        assert _round_to_multiple(500) == 496 or _round_to_multiple(500) == 512
        assert _round_to_multiple(500) % 16 == 0
        assert _round_to_multiple(7) >= 16

    def test_img2img_missing_input_raises(self):
        """图生图传入不存在的输入文件应报 FluxEngineError"""
        from app.engine.image.flux import FluxEngineError

        async def scenario():
            engine = FluxImageEngine("flux-schnell")
            with pytest.raises(FluxEngineError, match="输入文件不存在"):
                await engine.generate_image(
                    "猫", init_image_path=str(Path(__file__).parent / "不存在的图片.png")
                )

        asyncio.run(scenario())


if __name__ == "__main__":
    pytest.main([__file__, "-v"])