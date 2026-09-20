"""Anthropic Provider — Claude Vision + Text (Messages API)"""

import os
from src.core.config import resolve_base_url
from src.harness.pricing import estimate
from src.providers.base import BaseLLMProvider, provider_error
from src.providers.json_parse import parse_json_loose


class AnthropicLLMProvider(BaseLLMProvider):
    """Claude Vision + Text via Anthropic Messages API"""

    name = "anthropic"
    capabilities = ["vision", "text"]

    def __init__(self, api_key: str = "", base_url: str = "", name: str = "",
                 capabilities: list[str] | None = None, label: str = "",
                 max_tokens: int = 4096):
        # 参数注入优先（自定义 Anthropic 兼容服务商走这条路），否则回落官方环境变量与端点
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
        self.base_url = base_url or resolve_base_url("anthropic", "https://api.anthropic.com/v1")
        self.label = label or "Anthropic"
        if name:
            self.name = name
        if capabilities:
            self.capabilities = list(capabilities)
        self.max_tokens = max_tokens
        self._api_version = "2023-06-01"

    async def chat(self, messages: list[dict], model: str = "claude-sonnet-4-20250514", json_mode: bool = False) -> dict:
        import httpx

        # Convert OpenAI-format messages to Anthropic format
        system, formatted = self._convert_messages(messages)
        if json_mode:
            system = (system or "") + "\nYou MUST respond with valid JSON only. No other text."

        body = {
            "model": model,
            "max_tokens": self.max_tokens,
            "messages": formatted,
        }
        if system:
            body["system"] = system

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": self._api_version,
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{self.base_url}/messages",
                headers=headers,
                json=body,
            )
            if resp.status_code != 200:
                return provider_error(self.label, resp, self.base_url, self.api_key)

            data = resp.json()
            usage = data.get("usage", {})
            tokens = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
            envelope = {
                "tokens_used": tokens,
                "tokens_in": usage.get("input_tokens", 0),
                "tokens_out": usage.get("output_tokens", 0),
                "cost_usd": self._estimate_cost(model, usage),
            }

            # 与 OpenAI 兼容链路同一套守卫：空内容/被 max_tokens 截断一律报错，
            # 不再回落 {"text": ""} 让 Agent 静默空白（A32）
            blocks = data.get("content") or []
            text = ""
            if isinstance(blocks, list):
                for block in blocks:
                    if isinstance(block, dict) and isinstance(block.get("text"), str):
                        text += block["text"]
            stop_reason = str(data.get("stop_reason") or "")
            if not text.strip():
                hint = "（stop_reason=max_tokens，输出被预算截断）" if stop_reason == "max_tokens" else \
                       f"（stop_reason={stop_reason or '未知'}）"
                return {"error": f"{self.label} 返回空内容{hint}：max_tokens={self.max_tokens}",
                        **envelope}

            if json_mode:
                parsed = parse_json_loose(text)
                content = parsed if parsed is not None else {"raw": text}
            else:
                content = {"text": text}

            return {"content": content, **envelope}

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

    def _estimate_cost(self, model: str, usage: dict) -> float | None:
        """查价（USD/1M tokens，input/output 分开）；**查不到返回 `None`**

        此前写死 `prices.get(model, (3.0, 15.0))` —— 未知模型一律按 Sonnet 计价。
        签名保持 `(model, usage)`（`anthropic.chat` 直接调用）。
        """
        usage = usage if isinstance(usage, dict) else {}
        model = model or getattr(self, "_current_model", "") or "claude-sonnet-4-20250514"
        result = estimate(getattr(self, "route", "") or "anthropic", model, "vision",
                          {"tokens_in": usage.get("input_tokens", 0),
                           "tokens_out": usage.get("output_tokens", 0)})
        return result["amount"]
