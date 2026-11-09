"""辰鹄 AI 创作中心 - 环境验证脚本"""

import sys
from pathlib import Path

# Windows 默认控制台代码页是 GBK，打不出中文和 emoji：不兜底的话脚本会在 print 时
# 直接抛 UnicodeEncodeError 崩掉，用户只看到「跑不起来」。errors="replace" 保证永不因此崩。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # 被重定向、已关闭或过老的流：忽略，不影响主流程
        pass


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
        from diffusers import StableDiffusionPipeline
        print(f"   ✅ diffusers {diffusers.__version__}")
        print(f"   - StableDiffusionPipeline 可用：{StableDiffusionPipeline is not None}")
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
    """检查 xformers —— 可选项，本项目实际不用它，所以不计入「缺组件」。

    图像与视频引擎走的是 diffusers 的 SDPA 注意力；2026-11-08 的 SD1.5（文生图/图生图/
    分块 1080p）与 Wan2.1（文生视频）真机实测都没装 xformers，峰值显存照样压在 2GB 出头。
    把它算成必需项会把用户带去装一个用不上的大包，还让自检假报错。
    """
    print("\n🔍 检查 xformers（可选优化，本项目不依赖）...")
    try:
        import xformers

        print(f"   ✅ xformers {xformers.__version__}（可选项，装不装都不影响功能）")
        return True
    except Exception as e:
        print(f"   ➖ 未安装（正常：diffusers 走 SDPA，不需要 xformers）：{e}")
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
    
    # 汇总报告：必需项与可选项分开统计，可选项缺失不改退出码
    print("\n" + "=" * 60)
    print("环境验证结果")
    print("=" * 60)

    optional_names = {"xformers", "pytest"}
    required = [(name, ok) for name, ok in results if name not in optional_names]
    optional = [(name, ok) for name, ok in results if name in optional_names]

    passed = sum(1 for _, ok in required if ok)
    total = len(required)

    for name, ok in required:
        print(f"{'✅' if ok else '❌'} {name}（必需）")
    for name, ok in optional:
        print(f"{'✅' if ok else '➖'} {name}（可选，缺了不影响功能）")

    print(f"\n必需项：{total}，通过：{passed}")

    if passed == total:
        print("\n🎉 环境验证通过！可以开始使用 AI 生成功能")
        print("提示：这个自检查的是「引擎 venv」的依赖，请用对应的解释器跑它：")
        print("  图片引擎 -> .\\.venv-image\\Scripts\\python.exe scripts\\verify_env.py")
        print("  视频引擎 -> .\\.venv-video\\Scripts\\python.exe scripts\\verify_env.py")
        print("  音乐引擎 -> .\\.venv-music\\Scripts\\python.exe scripts\\verify_env.py")
        print("  桌面端 .venv 只跑界面（PySide6），不需要 torch")
        return 0

    missing = [name for name, ok in required if not ok]
    print(f"\n⚠️  缺少以下必需组件：{', '.join(missing)}")
    print("\n本项目实际使用的安装命令（CUDA 13.0 轮子，按引擎分别装）:")
    print("# 图片引擎（SD1.5 + RealVis V5.1 + LCM-LoRA）")
    print(r'  .\.venv-image\Scripts\python.exe -m pip install torch==2.14.1+cu130 --index-url https://download.pytorch.org/whl/cu130')
    print(r'  .\.venv-image\Scripts\python.exe -m pip install "diffusers==0.41.0" "transformers==5.19.0" accelerate peft safetensors Pillow huggingface-hub')
    print("# 视频引擎（Wan2.1 1.3B T2V / VACE）")
    print(r'  .\.venv-video\Scripts\python.exe -m pip install torch==2.14.1+cu130 torchvision==0.29.1+cu130 torchaudio==2.11.0+cu130 --index-url https://download.pytorch.org/whl/cu130')
    print(r'  .\.venv-video\Scripts\python.exe -m pip install "diffusers==0.41.0" "transformers==5.19.0" accelerate safetensors Pillow huggingface-hub imageio-ffmpeg')
    print("# xformers 不用装：图像/视频引擎走 diffusers 的 SDPA，真机实测没它也能出图出视频")
    return 1


if __name__ == "__main__":
    sys.exit(main())
