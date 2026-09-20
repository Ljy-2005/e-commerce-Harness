"""成本追踪器 — 按模型计价的实时成本统计

## 口径（与 `src/harness/pricing.py` 同一套规则）

- **用量是事实**：每次调用都记 tokens / 张数 / 模型 / 路由（`breakdown()["calls"]`、
  `["usage"]`），与价格表无关；
- **未标定价格的模型 → 金额是 `None`**，不是 0、也不是兜底常量。
  此前 `_estimate` 对未知模型按 `(1.0, 3.0)`/1M tokens 估价、`record_image` 回落
  `(0.04, 0.04)` —— 给不认识的模型（方舟 `doubao-seedream-*` 等）**凭空造钱**，
  用户质疑"模型商会来回改价，不及时更新就是很大的误导"，核实成立；
- `total_cost` / `by_model` / `by_agent` **只累加已知部分**，另有 `unknown_calls` 计数；
- 有价格的模型走 `pricing.resolve_price`（`config/pricing.yaml` 用户填写优先于内置参考价）。
"""

from collections import defaultdict

from src.harness.pricing import resolve_price

_UNKNOWN = object()   # "没给 amount"（区别于显式传入 None = 明确未知）


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
        self._unknown_calls = 0                # 未标定价格、未计入金额的调用次数

    def record(
        self,
        model: str,
        tokens_in: int = 0,
        tokens_out: int = 0,
        agent_name: str = "",
        provider: str = "",
        usage: dict | None = None,
        cost_unknown: bool = False,
    ):
        """记录一次 API 调用的成本（查不到价格 → `cost_usd=None`，计入 `unknown_calls`）"""
        price = None if cost_unknown else self._price(model, "text")
        if price is None:
            cost = None
        else:
            cost = round((tokens_in / 1_000_000) * price["in"]
                         + (tokens_out / 1_000_000) * price["out"], 6)
        self._apply(model, agent_name, cost)
        entry = {
            "model": model,
            "provider": provider,
            "agent": agent_name,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cost_usd": cost,
            "cost_unknown": cost is None,
        }
        if usage:
            entry["usage"] = dict(usage)
        self._calls.append(entry)

    def record_image(self, model: str, count: int = 1, agent_name: str = "",
                     amount: float | None = _UNKNOWN, provider: str = "",
                     usage: dict | None = None, cost_unknown: bool = False):
        """记录图像生成成本

        `amount`：Provider/Agent 回报的**实际总金额**（有则优先，避免拿定价表的占位价重算）。
        没给（默认哨兵）时走 `pricing.resolve_price`；查不到 → 记未知（`cost_usd=None`，
        `unknown_calls` +1），**不再回落 0.04**（那是 DALL·E 3 的常量，与方舟计费无关）。
        显式传 `amount=None`（或 `cost_unknown=True`）表示调用方已知金额未知，直接记未知。

        实测 3 张图只算了 1 张的钱（A44）：金额取 Provider 汇总值、`count` 如实计张数。
        `provider`：路由 id（`ark` / `openai` …），用于 `路由/模型` 精确命中价格表。
        """
        count = max(0, int(count or 0))
        if amount is _UNKNOWN and not cost_unknown:
            if count == 0:
                price = None
            else:
                price = self._price(model, "image", size=str((usage or {}).get("size") or ""))
            if price is not None:
                amount = round(price["in"] * count, 6)
            else:
                cost_unknown = True
        if cost_unknown:
            cost = None
        elif amount is not None and amount >= 0:
            cost = round(float(amount), 6)
        else:
            cost = None
        self._apply(model, agent_name, cost)
        entry = {
            "model": model,
            "provider": provider,
            "agent": agent_name,
            "images": count,
            "cost_usd": cost,
            "cost_unknown": cost is None,
        }
        self._calls.append(entry)

    def _apply(self, model: str, agent_name: str, cost: float | None) -> None:
        """把一笔金额计入汇总（未知只计数，绝不按 0 计入金额）"""
        if cost is None:
            self._unknown_calls += 1
            return
        self._total_cost += cost
        self._by_model[model] += cost
        if agent_name:
            self._by_agent[agent_name] += cost

    @staticmethod
    def _price(model: str, capability: str, size: str = "",
               route: str = "") -> dict | None:
        """定价：`route/model` 命中优先，其次任意路由下的同名模型；查不到 None"""
        price = resolve_price(route, model, capability, size=size)
        if price is None and route:
            price = resolve_price("", model, capability, size=size)
        return price

    @property
    def total_cost(self) -> float:
        """**只累加已知部分**（未标定的调用不计入金额，另有 `unknown_calls` 计数）"""
        return round(self._total_cost, 6)

    @property
    def unknown_calls(self) -> int:
        """价格未标定、因此没有计入金额的调用次数（用量仍然如实记录）"""
        return self._unknown_calls

    @property
    def remaining_budget(self) -> float:
        return round(self.budget_usd - self._total_cost, 6)

    def is_over_budget(self) -> bool:
        return self._total_cost >= self.budget_usd

    def is_warning(self) -> bool:
        return self._total_cost >= self.budget_usd * self.warn_threshold

    def breakdown(self) -> dict:
        """返回成本分解: by_model + by_agent + usage + unknown_calls + total"""
        return {
            "total": self.total_cost,
            "budget": self.budget_usd,
            "remaining": self.remaining_budget,
            "warning": self.is_warning(),
            "over_budget": self.is_over_budget(),
            "by_model": dict(self._by_model),
            "by_agent": dict(self._by_agent),
            "usage": self.usage(),
            "unknown_calls": self._unknown_calls,
            "calls": self._calls[-10:],  # 最近 10 次
        }

    def usage(self) -> dict:
        """**用量汇总（事实，与价格表无关）**：调用数/张数/tokens/未标定次数"""
        images = sum(int(call.get("images") or 0) for call in self._calls)
        tokens_in = sum(int(call.get("tokens_in") or 0) for call in self._calls)
        tokens_out = sum(int(call.get("tokens_out") or 0) for call in self._calls)
        return {
            "calls": len(self._calls),
            "images": images,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "tokens_used": tokens_in + tokens_out,
            "unknown_calls": self._unknown_calls,
        }

    def reset(self):
        self._total_cost = 0.0
        self._calls.clear()
        self._by_model.clear()
        self._by_agent.clear()
        self._unknown_calls = 0

    # ── 会话恢复（A39） ──

    @classmethod
    def from_session(cls, session: dict) -> "CostTracker":
        """取出/重建会话的成本追踪器

        checkpoint 是 JSON，**无法保存 CostTracker 实例**：此前 `json.dump(default=str)`
        把它写成 `"<CostTracker object at 0x…>"`，恢复后 `tracker.record(...)` 直接
        AttributeError（被 `_track_cost` 的 try 吞掉）→ 该会话从此不再记账。
        这里：实例可用则原样返回；否则按 `cost_so_far` 重建（历史金额不丢）。
        """
        existing = session.get("_cost_tracker") if isinstance(session, dict) else None
        if isinstance(existing, cls):
            return existing

        try:
            budget = float(session.get("cost_budget_usd") or 10.0)
        except (TypeError, ValueError):
            budget = 10.0
        tracker = cls(budget_usd=budget)
        try:
            tracker._total_cost = float(session.get("cost_so_far") or 0.0)
        except (TypeError, ValueError):
            tracker._total_cost = 0.0
        # 未标定次数同样恢复：否则恢复后的会话会"看起来全都有价格"
        try:
            tracker._unknown_calls = int(session.get("cost_unknown_calls") or 0)
        except (TypeError, ValueError):
            tracker._unknown_calls = 0
        if isinstance(session, dict):
            session["_cost_tracker"] = tracker
        return tracker


class BudgetExceeded(Exception):
    """预算超限异常"""
    def __init__(self, cost: float, budget: float):
        self.cost = cost
        self.budget = budget
        super().__init__(f"预算超限: ${cost:.4f} / ${budget:.2f}")
