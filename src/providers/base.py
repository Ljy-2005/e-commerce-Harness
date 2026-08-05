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

    @abstractmethod
    async def chat_with_vision(
        self,
        messages: list[dict],
        model: str = "",
    ) -> dict:
        """发送视觉对话请求（图片+文本）"""
        ...


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
