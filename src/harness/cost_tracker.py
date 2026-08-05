"""成本追踪器 — 按模型计价的实时成本统计"""

import os
from collections import defaultdict


# ── 模型定价表（USD / 1M tokens）──

_PRICES = {
    # OpenAI
    "gpt-4o": (2.50, 10.0),          # input, output
    "gpt-4o-mini": (0.15, 0.60),
    # Image generation (per-image price stored in first tuple element)
    "dall-e-3": (0.04, 0.04),
    "dall-e-2": (0.02, 0.02),
    # Anthropic
    "claude-sonnet-4-20250514": (3.0, 15.0),
    "claude-opus-4-20250514": (15.0, 75.0),
    "claude-haiku-3-5-20241022": (0.80, 4.0),
    # DeepSeek (~¥1/1M tokens ≈ $0.14, flat pricing)
    "deepseek-chat": (0.14, 0.14),
    # Qwen — $5.60/1M tokens (¥40/1M * 0.14)
    "qwen-max": (5.60, 5.60),
    "qwen-vl-max": (5.60, 5.60),
    "qwen-plus": (0.28, 0.28),
    "qwen-vl-plus": (0.42, 0.42),
    # Seedream
    "seedream-5.0": (0.0, 0.0),       # 免费额度
    # FLUX
    "flux.1-dev": (0.05, 0.05),       # per image
    "flux-pro-1.1": (0.05, 0.05),
}


class CostTracker:
    """会话级成本追踪器

    每个 session 一个实例。记录每次 Agent/Provider 调用的成本，
    支持预算上限和告警阈值。

    使用:
        tracker = CostTracker(budget_usd=10.0, warn_threshold=0.8)
        tracker.record(model="gpt-4o", tokens_in=500, tokens_out=200)
        if tracker.is_over_budget():
            raise BudgetExceeded(...)
    """

    def __init__(self, budget_usd: float = 10.0, warn_threshold: float = 0.8):
        self.budget_usd = budget_usd
        self.warn_threshold = warn_threshold
        self._total_cost = 0.0
        self._calls: list[dict] = []           # 调用明细
        self._by_model: dict[str, float] = defaultdict(float)
        self._by_agent: dict[str, float] = defaultdict(float)

    def record(
        self,
        model: str,
        tokens_in: int = 0,
        tokens_out: int = 0,
        agent_name: str = "",
        provider: str = "",
    ):
        """记录一次 API 调用的成本"""
        cost = self._estimate(model, tokens_in, tokens_out)
        self._total_cost += cost
        self._by_model[model] += cost
        if agent_name:
            self._by_agent[agent_name] += cost
        self._calls.append({
            "model": model,
            "provider": provider,
            "agent": agent_name,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cost_usd": cost,
        })

    def record_image(self, model: str, count: int = 1, agent_name: str = ""):
        """记录图像生成成本"""
        prices = _PRICES.get(model, (0.04, 0.04))
        cost = prices[0] * count  # per-image price
        self._total_cost += cost
        self._by_model[model] += cost
        if agent_name:
            self._by_agent[agent_name] += cost
        self._calls.append({
            "model": model,
            "agent": agent_name,
            "images": count,
            "cost_usd": cost,
        })

    def _estimate(self, model: str, tokens_in: int, tokens_out: int) -> float:
        """根据模型定价估算成本"""
        prices = _PRICES.get(model, (1.0, 3.0))  # 未知模型默认保守估计
        if len(prices) >= 2:
            in_price, out_price = prices[0], prices[1]
        else:
            in_price = out_price = prices[0]
        return round((tokens_in / 1_000_000) * in_price + (tokens_out / 1_000_000) * out_price, 6)

    @property
    def total_cost(self) -> float:
        return round(self._total_cost, 6)

    @property
    def remaining_budget(self) -> float:
        return round(self.budget_usd - self._total_cost, 6)

    def is_over_budget(self) -> bool:
        return self._total_cost >= self.budget_usd

    def is_warning(self) -> bool:
        return self._total_cost >= self.budget_usd * self.warn_threshold

    def breakdown(self) -> dict:
        """返回成本分解: by_model + by_agent + total"""
        return {
            "total": self.total_cost,
            "budget": self.budget_usd,
            "remaining": self.remaining_budget,
            "warning": self.is_warning(),
            "over_budget": self.is_over_budget(),
            "by_model": dict(self._by_model),
            "by_agent": dict(self._by_agent),
            "calls": self._calls[-10:],  # 最近 10 次
        }

    def reset(self):
        self._total_cost = 0.0
        self._calls.clear()
        self._by_model.clear()
        self._by_agent.clear()


class BudgetExceeded(Exception):
    """预算超限异常"""
    def __init__(self, cost: float, budget: float):
        self.cost = cost
        self.budget = budget
        super().__init__(f"预算超限: ${cost:.4f} / ${budget:.2f}")
