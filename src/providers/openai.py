"""OpenAI Provider — GPT-4o Vision + Text + DALL-E 3"""

import os
import json
from src.providers.base import BaseLLMProvider, BaseImageProvider


class OpenAILLMProvider(BaseLLMProvider):
    """GPT-4o Vision + Text"""

    name = "openai"
    capabilities = ["vision", "text"]

    def __init__(self):
        self.api_key = os.getenv("OPENAI_API_KEY", "")
        self.base_url = "https://api.openai.com/v1"

    async def chat(self, messages: list[dict], model: str = "gpt-4o", json_mode: bool = False) -> dict:
        import httpx
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": model,
            "messages": messages,
            "temperature": 0.7,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
            # OpenAI 要求消息中必须包含 "json" 关键词
            has_json_hint = any(
                "json" in str(m.get("content", "")).lower()
                for m in messages
            )
            if not has_json_hint:
                messages = [
                    {"role": "system", "content": "You must respond with a valid JSON object."},
                    *messages,
                ]
                body["messages"] = messages

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=body,
            )
            if resp.status_code != 200:
                return {"error": f"OpenAI API error: {resp.status_code}"}

            data = resp.json()
            content_str = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})
            tokens = usage.get("total_tokens", 0)

            # Parse JSON if json_mode
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
                "tokens_in": usage.get("prompt_tokens", 0),
                "tokens_out": usage.get("completion_tokens", 0),
                "cost_usd": self._estimate_cost(
                    model,
                    usage.get("prompt_tokens", 0),
                    usage.get("completion_tokens", 0),
                ),
            }

    async def chat_with_vision(self, messages: list[dict], model: str = "gpt-4o") -> dict:
        import httpx
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": model,
            "messages": messages,
            "max_tokens": 4096,
        }

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=body,
            )
            if resp.status_code != 200:
                return {"error": f"OpenAI API error: {resp.status_code}"}

            data = resp.json()
            content_str = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})

            # Try parsing as JSON
            try:
                content = json.loads(content_str)
            except json.JSONDecodeError:
                content = {"text": content_str}

            return {
                "content": content,
                "tokens_used": usage.get("total_tokens", 0),
                "tokens_in": usage.get("prompt_tokens", 0),
                "tokens_out": usage.get("completion_tokens", 0),
                "cost_usd": self._estimate_cost(
                    model,
                    usage.get("prompt_tokens", 0),
                    usage.get("completion_tokens", 0),
                ),
            }

    def _estimate_cost(self, model: str, prompt_tokens: int = 0, completion_tokens: int = 0) -> float:
        """按 input/output 分别计费（USD/1M tokens）"""
        prices: dict[str, tuple[float, float]] = {
            "gpt-4o": (2.50, 10.00),
            "gpt-4o-mini": (0.15, 0.60),
            "gpt-4.1": (2.00, 8.00),
            "gpt-4.1-mini": (0.40, 1.60),
            "o3": (10.00, 40.00),
            "o4-mini": (1.10, 4.40),
        }
        in_price, out_price = prices.get(model, (2.50, 10.00))
        cost = (prompt_tokens / 1_000_000) * in_price + (completion_tokens / 1_000_000) * out_price
        return round(cost, 6)


class OpenAIImageProvider(BaseImageProvider):
    """DALL-E 3 Image Generation"""

    name = "openai"
    capabilities = ["image"]

    def __init__(self):
        self.api_key = os.getenv("OPENAI_API_KEY", "")
        self.base_url = "https://api.openai.com/v1"

    async def generate(
        self, prompt: str, negative_prompt: str = "", size: str = "1024x1024", model: str = "dall-e-3"
    ) -> dict:
        import httpx
        import warnings

        if negative_prompt:
            warnings.warn(
                f"DALL-E 不支持 negative_prompt 参数，传入值被忽略: '{negative_prompt[:100]}'"
            )
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": model,
            "prompt": prompt,
            "n": 1,
            "size": size,
            "quality": "standard",
        }

        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.post(
                f"{self.base_url}/images/generations",
                headers=headers,
                json=body,
            )
            if resp.status_code != 200:
                return {"error": f"DALL-E API error: {resp.status_code}"}

            data = resp.json()
            image_url = data["data"][0].get("url", "")
            revised_prompt = data["data"][0].get("revised_prompt", prompt)

            return {
                "image_url": image_url,
                "base64_data": "",  # DALL-E 返回 URL，不返回 base64
                "revised_prompt": revised_prompt,
                "model_used": model,
                "cost_usd": self._estimate_cost(size),
            }

    def _estimate_cost(self, size: str) -> float:
        prices = {"1024x1024": 0.04, "1024x1792": 0.08, "1792x1024": 0.08}
        return prices.get(size, 0.04)
