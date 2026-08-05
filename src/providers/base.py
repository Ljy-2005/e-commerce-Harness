"""Provider 抽象接口"""

from abc import ABC, abstractmethod
from typing import Literal


class BaseLLMProvider(ABC):
    """LLM Provider 抽象接口"""

    name: str = "base"
    capabilities: list[str] = []  # ["vision", "text"]

    @abstractmethod
    async def chat(
        self,
        messages: list[dict],
        model: str = "",
        json_mode: bool = False,
    ) -> dict:
        """发送文本对话请求，返回 {"content": dict, "tokens_used": int, "cost_usd": float}"""
        ...

    async def chat_with_vision(
        self,
        messages: list[dict],
        model: str = "",
    ) -> dict:
        """发送视觉对话请求。不支持 Vision 的 Provider 返回顶层错误。"""
        return {
            "error": f"{self.name} 不支持视觉能力",
            "content": {},
            "tokens_used": 0,
            "cost_usd": 0.0,
        }


class BaseImageProvider(ABC):
    """Image Provider 抽象接口"""

    name: str = "base"
    capabilities: list[str] = []  # ["image"]

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        negative_prompt: str = "",
        size: str = "1024x1024",
        model: str = "",
    ) -> dict:
        """生成图片，返回 {"image_url": str, "base64_data": str, "cost_usd": float}"""
        ...

    @staticmethod
    def _parse_size(size: str) -> tuple[int, int]:
        """解析尺寸字符串 "WxH" → (width, height)，所有 Image Provider 共用"""
        if "x" in size:
            w_str, h_str = size.lower().replace("x", " ").split()
            return int(w_str), int(h_str)
        return 1024, 1024
