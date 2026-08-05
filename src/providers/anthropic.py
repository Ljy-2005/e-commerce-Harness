"""Anthropic Provider — Claude Vision + Text (Messages API)"""

import os
import json
from src.providers.base import BaseLLMProvider


class AnthropicLLMProvider(BaseLLMProvider):
    """Claude Vision + Text via Anthropic Messages API"""

    name = "anthropic"
    capabilities = ["vision", "text"]

    def __init__(self):
        self.api_key = os.getenv("ANTHROPIC_API_KEY", "")
        self.base_url = "https://api.anthropic.com/v1"
        self._api_version = "2023-06-01"

    async def chat(self, messages: list[dict], model: str = "claude-sonnet-4-20250514", json_mode: bool = False) -> dict:
        import httpx

        # Convert OpenAI-format messages to Anthropic format
        system, formatted = self._convert_messages(messages)
        if json_mode:
            system = (system or "") + "\nYou MUST respond with valid JSON only. No other text."

        body = {
            "model": model,
            "max_tokens": 4096,
            "messages": formatted,
        }
        if system:
            body["system"] = system

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": self._api_version,
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.post(
                f"{self.base_url}/messages",
                headers=headers,
                json=body,
            )
            if resp.status_code != 200:
                return {"error": f"Anthropic API error: {resp.status_code}"}

            data = resp.json()
            text = data["content"][0]["text"]
            tokens = data.get("usage", {}).get("input_tokens", 0) + data.get("usage", {}).get("output_tokens", 0)

            if json_mode:
                try:
                    content = json.loads(text)
                except json.JSONDecodeError:
                    content = {"raw": text}
            else:
                content = {"text": text}

            return {
                "content": content,
                "tokens_used": tokens,
                "cost_usd": self._estimate_cost(model, data.get("usage", {})),
            }

    async def chat_with_vision(self, messages: list[dict], model: str = "claude-sonnet-4-20250514") -> dict:
        """Claude natively supports images in the Messages API"""
        return await self.chat(messages, model=model, json_mode=False)

    def _convert_messages(self, messages: list[dict]) -> tuple[str | None, list[dict]]:
        """Convert OpenAI-style messages to Anthropic format.

        Returns (system_prompt, formatted_messages)
        """
        system = None
        formatted = []

        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")

            if role == "system":
                if isinstance(content, str):
                    system = content
                elif isinstance(content, list):
                    system = " ".join(p["text"] for p in content if isinstance(p, dict) and p.get("type") == "text")
                else:
                    system = str(content)
                continue

            # Handle vision content (OpenAI format: list of {"type": "text"/"image_url", ...})
            if isinstance(content, list):
                anthropic_content = []
                for part in content:
                    if part.get("type") == "text":
                        anthropic_content.append({"type": "text", "text": part["text"]})
                    elif part.get("type") == "image_url":
                        url = part.get("image_url", {}).get("url", "")
                        # Extract base64 data from data:image/...;base64,...
                        if url.startswith("data:"):
                            media_type = url.split(";")[0].replace("data:", "")
                            b64 = url.split(",", 1)[1] if "," in url else url
                            anthropic_content.append({
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": media_type,
                                    "data": b64,
                                },
                            })
                formatted.append({"role": "user", "content": anthropic_content})
            else:
                formatted.append({"role": role, "content": str(content)})

        return system, formatted

    def _estimate_cost(self, model: str, usage: dict) -> float:
        """Claude pricing per 1M tokens (input/output)"""
        prices = {
            "claude-sonnet-4-20250514": (3.0, 15.0),
            "claude-opus-4-20250514": (15.0, 75.0),
            "claude-haiku-3-5": (0.8, 4.0),
        }
        in_price, out_price = prices.get(model, (3.0, 15.0))
        in_tokens = usage.get("input_tokens", 0)
        out_tokens = usage.get("output_tokens", 0)
        return round((in_tokens / 1_000_000) * in_price + (out_tokens / 1_000_000) * out_price, 6)
