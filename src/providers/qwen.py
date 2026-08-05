"""阿里通义千问 Provider — Vision + Text (DashScope API)"""

import os
import json
from src.providers.base import BaseLLMProvider


class QwenLLMProvider(BaseLLMProvider):
    """通义千问 Qwen-VL-Max / Qwen-Max via DashScope"""

    name = "qwen"
    capabilities = ["vision", "text"]

    def __init__(self):
        self.api_key = os.getenv("DASHSCOPE_API_KEY", "")
        self.base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    async def chat(self, messages: list[dict], model: str = "qwen-max", json_mode: bool = False) -> dict:
        import httpx

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": model,
            "messages": messages,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=body,
            )
            if resp.status_code != 200:
                return {"error": f"Qwen API error: {resp.status_code}"}

            data = resp.json()
            content_str = data["choices"][0]["message"]["content"]
            tokens = data.get("usage", {}).get("total_tokens", 0)

            if json_mode:
                try:
                    content = json.loads(content_str)
                except json.JSONDecodeError:
                    content = {"raw": content_str}
            else:
                content = {"text": content_str}

            return {
                "content": content,
                "tokens_used": tokens,
                "cost_usd": self._estimate_cost(model, tokens),
            }

    async def chat_with_vision(self, messages: list[dict], model: str = "qwen-vl-max") -> dict:
        """Qwen-VL-Max 视觉理解"""
        import httpx

        # Convert messages to DashScope multi-modal format
        formatted = self._convert_for_vision(messages)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": model,
            "messages": formatted,
        }

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=body,
            )
            if resp.status_code != 200:
                return {"error": f"Qwen VL API error: {resp.status_code}"}

            data = resp.json()
            content_str = data["choices"][0]["message"]["content"]
            tokens = data.get("usage", {}).get("total_tokens", 0)

            try:
                content = json.loads(content_str)
            except json.JSONDecodeError:
                content = {"text": content_str}

            return {
                "content": content,
                "tokens_used": tokens,
                "cost_usd": self._estimate_cost(model, tokens),
            }

    def _convert_for_vision(self, messages: list[dict]) -> list[dict]:
        """将 OpenAI 视觉格式转为 Qwen 多模态格式"""
        formatted = []
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if role == "system":
                formatted.append({"role": "system", "content": [{"type": "text", "text": str(content)}]})
            elif isinstance(content, list):
                formatted.append({"role": "user", "content": content})
            else:
                formatted.append({"role": role, "content": content})
        return formatted

    def _estimate_cost(self, model: str, tokens: int) -> float:
        """Qwen 定价（USD/1M tokens），按 ¥1 ≈ $0.14 换算

        Qwen-Max: ¥40/1M tokens ≈ $5.60/1M tokens
        """
        prices = {
            "qwen-max": 5.60,       # $5.60/1M tokens
            "qwen-plus": 0.28,
            "qwen-vl-max": 5.60,
            "qwen-vl-plus": 0.42,
        }
        price_per_m = prices.get(model, 5.60)
        return round((tokens / 1_000_000) * price_per_m, 6)
