"""DeepSeek Provider — 极低成本 Text LLM（OpenAI 兼容 API）"""

import os
import json
from src.providers.base import BaseLLMProvider


class DeepSeekLLMProvider(BaseLLMProvider):
    """DeepSeek-V3 Text-only（OpenAI 兼容接口）"""

    name = "deepseek"
    capabilities = ["text"]

    def __init__(self):
        self.api_key = os.getenv("DEEPSEEK_API_KEY", "")
        self.base_url = "https://api.deepseek.com/v1"

    async def chat(self, messages: list[dict], model: str = "deepseek-chat", json_mode: bool = False) -> dict:
        import httpx
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": model,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 4096,
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
                return {"error": f"DeepSeek API error: {resp.status_code}"}

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
                "cost_usd": self._estimate_cost(tokens),
            }

    def _estimate_cost(self, tokens: int) -> float:
        # DeepSeek-V3: ~¥1/百万 token ≈ $0.14/百万 token
        return round((tokens / 1_000_000) * 0.14, 6)
