"""DeepSeek Provider — 极低成本 Text/多模态 LLM（OpenAI 兼容 API）

V4 代（2026-08 起）：
- deepseek-v4-flash           文本（默认，非思考）
- deepseek-v4-pro             文本（更强）
- deepseek-v4-flash-vision-exp 多模态（理解图片，视觉能力）
注意：deepseek-chat / deepseek-reasoner 是 V3 时代旧别名，已弃用。
"""

import os
import json
from src.providers.base import BaseLLMProvider


class DeepSeekLLMProvider(BaseLLMProvider):
    """DeepSeek-V4 Text + Vision（OpenAI 兼容接口）"""

    name = "deepseek"
    capabilities = ["text", "vision"]

    def __init__(self):
        self.api_key = os.getenv("DEEPSEEK_API_KEY", "")
        self.base_url = "https://api.deepseek.com/v1"

    async def _complete(self, messages: list[dict], model: str, json_mode: bool = False, parse_json: bool = False) -> dict:
        """调用 DeepSeek /chat/completions（OpenAI 兼容），返回结构化结果或 error dict

        parse_json=True 时总是尝试解析 JSON 内容（视觉 Agent 常返回结构化 JSON）；否则遵循 json_mode。
        """
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
            usage = data.get("usage", {})
            tokens = usage.get("total_tokens", 0)

            if parse_json:
                try:
                    content = json.loads(content_str)
                except json.JSONDecodeError:
                    content = {"text": content_str}
            elif json_mode:
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
                "cost_usd": self._estimate_cost(tokens),
            }

    async def chat(self, messages: list[dict], model: str = "deepseek-v4-flash", json_mode: bool = False) -> dict:
        """文本对话"""
        return await self._complete(messages, model, json_mode=json_mode)

    async def chat_with_vision(self, messages: list[dict], model: str = "deepseek-v4-flash-vision-exp") -> dict:
        """视觉对话（OpenAI 兼容多模态消息格式，透传给 /chat/completions；总是尝试解析 JSON）"""
        return await self._complete(messages, model, parse_json=True)

    def _estimate_cost(self, tokens: int) -> float:
        # DeepSeek-V4 ~¥1/百万 token ≈ $0.14/百万 token（估算值，实际以官方定价为准）
        return round((tokens / 1_000_000) * 0.14, 6)
