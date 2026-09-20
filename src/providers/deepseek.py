"""DeepSeek Provider — 极低成本 Text/多模态 LLM（OpenAI 兼容 API）

V4 代（2026-08 起）：
- deepseek-v4-flash           文本（默认）
- deepseek-v4-pro             文本（更强）
- deepseek-v4-flash-vision-exp 多模态（理解图片，视觉能力）
注意：deepseek-chat / deepseek-reasoner 是 V3 时代旧别名，已弃用。

**V4 是推理模型**：思考 token 也计入 `max_tokens`。实测 `max_tokens=4096` 时
`reasoning_tokens=4097`、`content=""`、`finish_reason=length` —— 调用"成功"但输出为空，
Agent 静默空白（品类专项分析员连续两轮翻车）。响应解析统一交给
`src/providers/compat.py`：空/截断一律报错，并在截断时自动提升预算重试一次。
"""

import os
from src.core.config import resolve_base_url
from src.providers.base import BaseLLMProvider
from src.providers.compat import openai_compatible_chat


class DeepSeekLLMProvider(BaseLLMProvider):
    """DeepSeek-V4 Text + Vision（OpenAI 兼容接口）"""

    name = "deepseek"
    capabilities = ["text", "vision"]

    def __init__(self, max_tokens: int = 4096):
        self.api_key = os.getenv("DEEPSEEK_API_KEY", "")
        # 端点可覆盖：env DEEPSEEK_BASE_URL → config/providers.yaml → 官方端点
        self.base_url = resolve_base_url("deepseek", "https://api.deepseek.com/v1")
        # 单次输出预算（思考 + 正文）；由路由表/配置注入，截断时 compat 会自动升 4 倍
        self.max_tokens = max_tokens

    async def _complete(self, messages: list[dict], model: str, json_mode: bool = False,
                        parse_json: bool = False) -> dict:
        """调用 DeepSeek /chat/completions，返回结构化结果或 error dict

        parse_json=True 时总是尝试解析 JSON 内容（视觉 Agent 常返回结构化 JSON）；否则遵循 json_mode。
        """
        body = {
            "model": model,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": self.max_tokens,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}

        return await openai_compatible_chat(
            base_url=self.base_url,
            api_key=self.api_key,
            body=body,
            label="DeepSeek",
            model=model,
            json_mode=json_mode,
            parse_json=parse_json,
            cost_fn=lambda model, tokens_in, tokens_out: self._estimate_cost(
                tokens_in + tokens_out, model),
        )

    async def chat(self, messages: list[dict], model: str = "deepseek-v4-flash", json_mode: bool = False) -> dict:
        """文本对话"""
        return await self._complete(messages, model, json_mode=json_mode)

    async def chat_with_vision(self, messages: list[dict], model: str = "deepseek-v4-flash-vision-exp") -> dict:
        """视觉对话（OpenAI 兼容多模态消息格式，透传给 /chat/completions；总是尝试解析 JSON）"""
        return await self._complete(messages, model, parse_json=True)

    def _estimate_cost(self, tokens: int, model: str = "") -> float | None:
        """查价；**查不到返回 `None`**（不猜、不回落默认价）

        签名保持 `(tokens)` 兼容（`compat.openai_compatible_chat` 的 `cost_fn` 以
        `(model, tokens_in, tokens_out)` 调用 lambda；返回 None 也要能安全穿过）。
        """
        from src.harness.pricing import estimate

        model = model or getattr(self, "_current_model", "") or "deepseek-v4-flash"
        # DeepSeek 现行口径输入输出同价（模型商按 token 计费，此处 token 总量按输入价）
        result = estimate(getattr(self, "route", "") or "deepseek", model, "text",
                          {"tokens_in": int(tokens or 0), "tokens_out": 0})
        return result["amount"]
