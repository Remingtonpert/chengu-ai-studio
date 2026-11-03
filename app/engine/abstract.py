"""AI 生成引擎抽象接口"""

from abc import ABC, abstractmethod
from typing import List, Optional, Dict, Any, Union
import asyncio
import torch


class AIGenerationEngine(ABC):
    """AI 生成引擎基类"""
    
    def __init__(self, engine_name: str):
        self.engine_name = engine_name
    
    @abstractmethod
    async def generate(self, prompt: str, **kwargs) -> List[str]:
        """生成内容
        
        Args:
            prompt: 提示词
            **kwargs: 额外参数
            
        Returns:
            生成的文件路径列表
        """
        pass
    
    @property
    @abstractmethod
    def memory_usage(self) -> float:
        """显存占用 (GB)"""
        pass
    
    @property
    @abstractmethod
    def requires_cuda(self) -> bool:
        """是否需要 CUDA"""
        pass


class ImageGenerationEngine(AIGenerationEngine):
    """图片生成引擎抽象类"""
    
    @abstractmethod
    async def generate_image(
        self, 
        prompt: str,
        width: int = 512,
        height: int = 512,
        steps: int = 20,
        seed: Optional[int] = None,
        negative_prompt: Optional[str] = None,
    ) -> str:
        """生成图片
        
        Args:
            prompt: 提示词
            width, height: 图像尺寸
            steps: 采样步数
            seed: 随机种子
            
        Returns:
            图片文件路径 (或 PIL.Image)
        """
        pass
    
    @property
    @abstractmethod
    def default_steps(self) -> int:
        """默认采样步数"""
        pass


class VideoGenerationEngine(AIGenerationEngine):
    """视频生成引擎抽象类"""
    
    @abstractmethod
    async def generate_video(
        self,
        prompt: str,
        image_path: Optional[str] = None,
        num_frames: int = 25,
        width: int = 480,
        height: int = 640,
        **kwargs
    ) -> str:
        """生成视频
        
        Args:
            prompt: 提示词
            image_path: 首帧图片路径 (可选)
            num_frames: 帧数
            
        Returns:
            视频文件路径 (.mp4)
        """
        pass


class AudioGenerationEngine(AIGenerationEngine):
    """音频生成引擎抽象类"""
    
    @abstractmethod
    async def generate_audio(
        self,
        prompt: str,
        duration: float = 30.0,
        sample_rate: int = 44100,
        **kwargs
    ) -> str:
        """生成音频
        
        Args:
            prompt: 提示词
            duration: 时长 (秒)
            sample_rate: 采样率
            
        Returns:
            音频文件路径 (.wav/.mp3)
        """
        pass


# 占位实现 - 等待具体引擎实现

class NoOpImageEngine(ImageGenerationEngine):
    """无操作图片引擎占位符"""
    
    def __init__(self):
        super().__init__("noop-image")
    
    async def generate_image(self, *args, **kwargs) -> str:
        return "尚未实现"
    
    @property
    def memory_usage(self) -> float:
        return 0.0
    
    @property
    def default_steps(self) -> int:
        return 25


# 其他占位符引擎...