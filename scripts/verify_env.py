"""辰鹄 AI 创作中心 - 环境验证脚本"""

import sys
from pathlib import Path


def check_pytorch():
    """检查 PyTorch 和 CUDA"""
    print("\n🔍 检查 PyTorch...")
    try:
        import torch
        print(f"   ✅ PyTorch {torch.__version__}")
        print(f"   - CUDA 可用：{torch.cuda.is_available()}")
        if torch.cuda.is_available():
            print(f"   - GPU: {torch.cuda.get_device_name(0)}")
            print(f"   - VRAM: {torch.cuda.get_device_properties(0).total_memory / 1e9:.2f} GB")
        return True
    except Exception as e:
        print(f"   ❌ PyTorch 安装失败：{e}")
        return False


def check_diffusers():
    """检查 diffusers"""
    print("\n🔍 检查 diffusers...")
    try:
        import diffusers
        from diffusers import FluxPipeline
        print(f"   ✅ diffusers {diffusers.__version__}")
        print(f"   - FluxPipeline 可用：{FluxPipeline is not None}")
        return True
    except Exception as e:
        print(f"   ❌ diffusers 安装失败：{e}")
        return False


def check_transformers():
    """检查 transformers"""
    print("\n🔍 检查 transformers...")
    try:
        import transformers
        from transformers import CLIPTextModel
        print(f"   ✅ transformers {transformers.__version__}")
        print(f"   - CLIPTextModel 可用：{CLIPTextModel is not None}")
        return True
    except Exception as e:
        print(f"   ❌ transformers 安装失败：{e}")
        return False


def check_huggingface_hub():
    """检查 huggingface-hub"""
    print("\n🔍 检查 huggingface-hub...")
    try:
        import huggingface_hub
        from huggingface_hub import hf_hub_download
        print(f"   ✅ huggingface-hub {huggingface_hub.__version__}")
        return True
    except Exception as e:
        print(f"   ❌ huggingface-hub 安装失败：{e}")
        return False


def check_pillow():
    """检查 Pillow"""
    print("\n🔍 检查 Pillow...")
    try:
        from PIL import Image
        print(f"   ✅ Pillow {Image.__version__ if hasattr(Image, '__version__') else '已安装'}")
        return True
    except Exception as e:
        print(f"   ❌ Pillow 安装失败：{e}")
        return False


def check_xformers():
    """检查 xformers (可选优化)"""
    print("\n🔍 检查 xformers...")
    try:
        import xformers
        print(f"   ✅ xformers {xformers.__version__}")
        return True
    except Exception as e:
        print(f"   ⚠️  xformers 未安装 (非必需)：{e}")
        return False


def check_pytest():
    """检查 pytest"""
    print("\n🔍 检查 pytest...")
    try:
        import pytest
        print(f"   ✅ pytest {pytest.__version__}")
        return True
    except Exception as e:
        print(f"   ⚠️  pytest 未安装 (仅测试必需)：{e}")
        return False


def main():
    """主函数"""
    print("=" * 60)
    print("辰鹄 AI 创作中心 - 环境验证")
    print("=" * 60)
    
    results = []
    
    # 运行所有检查
    results.append(("PyTorch", check_pytorch()))
    results.append(("diffusers", check_diffusers()))
    results.append(("transformers", check_transformers()))
    results.append(("huggingface-hub", check_huggingface_hub()))
    results.append(("Pillow", check_pillow()))
    results.append(("xformers", check_xformers()))
    results.append(("pytest", check_pytest()))
    
    # 汇总报告
    print("\n" + "=" * 60)
    print("环境验证结果")
    print("=" * 60)
    
    passed = sum(1 for _, status in results if status)
    total = len(results)
    
    for name, status in results:
        icon = "✅" if status else "❌"
        print(f"{icon} {name}")
    
    print(f"\n总检查：{total}, 通过：{passed}")
    
    if passed == total:
        print("\n🎉 环境验证通过！可以开始使用 AI 生成功能")
        return 0
    else:
        missing = [name for name, status in results if not status]
        print(f"\n⚠️  缺少以下组件：{', '.join(missing)}")
        print("\n建议安装命令:")
        print("pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118")
        print("pip install diffusers transformers safetensors accelerate xformers Pillow huggingface-hub pytest")
        return 1


if __name__ == "__main__":
    sys.exit(main())
