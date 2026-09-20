"""阿里通义千问 Provider — Vision + Text (DashScope API)"""

import os
from src.core.config import resolve_base_url
from src.harness.pricing import estimate
from src.providers.base import BaseLLMProvider
from src.providers.compat import openai_compatible_chat


class QwenLLMProvider(BaseLLMProvider):
    """通义千问 Qwen-VL-Max / Qwen-Max via DashScope"""

    name = "qwen"
    capabilities = ["vision", "text"]

    def __init__(self, max_tokens: int = 4096):
        self.api_key = os.getenv("DASHSCOPE_API_KEY", "")
        self.max_tokens = max_tokens
        # 端点可覆盖：env DASHSCOPE_BASE_URL → config/providers.yaml → 官方端点
        self.base_url = resolve_base_url(
            "qwen", "https://dashscope.aliyuncs.com/compatible-mode/v1"
        )

    async def chat(self, messages: list[dict], model: str = "qwen-max", json_mode: bool = False) -> dict:
        body = {
            "model": model,
            "messages": messages,
            "max_tokens": self.max_tokens,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}

        return await openai_compatible_chat(
            base_url=self.base_url,
            api_key=self.api_key,
            body=body,
            label="Qwen",
            model=model,
            json_mode=json_mode,
            cost_fn=lambda m, tokens_in, tokens_out: self._estimate_cost(m, tokens_in + tokens_out),
        )

    async def chat_with_vision(self, messages: list[dict], model: str = "qwen-vl-max") -> dict:
        """Qwen-VL-Max 视觉理解"""
        # Convert messages to DashScope multi-modal format
        formatted = self._convert_for_vision(messages)
        body = {
            "model": model,
            "messages": formatted,
            "max_tokens": self.max_tokens,
        }
        return await openai_compatible_chat(
            base_url=self.base_url,
            api_key=self.api_key,
            body=body,
            label="Qwen VL",
            model=model,
            parse_json=True,
            cost_fn=lambda m, tokens_in, tokens_out: self._estimate_cost(m, tokens_in + tokens_out),
        )

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

    def _estimate_cost(self, model: str, tokens: int) -> float | None:
        """查价（USD/1M tokens，按 ¥1 ≈ $0.14 换算）；**查不到返回 `None`**

        此前写死 `prices.get(model, 5.60)` —— 任何未知/新模型都被按 qwen-max 计价。
        现在未标定就是未知（界面显示"未标定"），价格可在设置页改。
        签名保持 `(model, tokens)` 不变（`compat` 的 cost_fn 以 lambda 适配）。
        """
        model = model or getattr(self, "_current_model", "") or "qwen-max"
        result = estimate(getattr(self, "route", "") or "qwen", model, "text",
                          {"tokens_in": int(tokens or 0), "tokens_out": 0})
        return result["amount"]
