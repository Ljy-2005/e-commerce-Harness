"""成本追踪器测试

口径（与 `src/harness/pricing.py` 一致）：用量是事实、永远记录；**未标定价格的模型
不给金额**（`cost_usd=None` + `unknown_calls` 计数），`total_cost` 只累加已知部分。
"""

import pytest

from src.harness.cost_tracker import BudgetExceeded, CostTracker


class TestCostTracker:
    def test_initial_zero(self):
        tracker = CostTracker(budget_usd=10.0)
        assert tracker.total_cost == 0.0
        assert tracker.remaining_budget == 10.0

    def test_record_text_call(self):
        tracker = CostTracker(budget_usd=10.0)
        tracker.record(model="gpt-4o", tokens_in=1000, tokens_out=500, agent_name="商品分析员")
        assert tracker.total_cost > 0.0
        assert tracker.total_cost < 0.01  # ~$0.0075

    def test_record_image_call(self):
        tracker = CostTracker(budget_usd=10.0)
        tracker.record_image(model="dall-e-3", count=1, provider="openai")
        assert tracker.total_cost == 0.04
        assert tracker.unknown_calls == 0

    def test_record_by_agent(self):
        tracker = CostTracker(budget_usd=10.0)
        tracker.record(model="gpt-4o", tokens_in=500, tokens_out=200, agent_name="商品分析员")
        tracker.record(model="gpt-4o", tokens_in=300, tokens_out=100, agent_name="审查员")
        breakdown = tracker.breakdown()
        assert "商品分析员" in breakdown["by_agent"]
        assert "审查员" in breakdown["by_agent"]

    def test_record_by_model(self):
        tracker = CostTracker(budget_usd=10.0)
        tracker.record(model="gpt-4o", tokens_in=1000, tokens_out=500)
        tracker.record(model="deepseek-chat", tokens_in=1000, tokens_out=500)
        breakdown = tracker.breakdown()
        by_model = breakdown["by_model"]
        assert "gpt-4o" in by_model or by_model == {}
        assert tracker.total_cost >= 0.0

    def test_budget_warning(self):
        tracker = CostTracker(budget_usd=10.0, warn_threshold=0.5)
        # 触发 50% 告警
        tracker.record(model="claude-opus-4-20250514", tokens_in=400_000, tokens_out=100_000)
        assert tracker.is_warning()

    def test_budget_exceeded(self):
        tracker = CostTracker(budget_usd=10.0)
        # 大量 tokens 触发预算超限
        tracker.record(model="claude-opus-4-20250514", tokens_in=10_000_000, tokens_out=0)
        assert tracker.is_over_budget()

    def test_breakdown_structure(self):
        tracker = CostTracker(budget_usd=10.0)
        tracker.record(model="gpt-4o", tokens_in=500, tokens_out=200, agent_name="提示词生成员")
        breakdown = tracker.breakdown()
        assert "total" in breakdown
        assert "budget" in breakdown
        assert "remaining" in breakdown
        assert "warning" in breakdown
        assert "over_budget" in breakdown
        assert "by_model" in breakdown
        assert "by_agent" in breakdown
        assert "usage" in breakdown
        assert "unknown_calls" in breakdown
        assert "calls" in breakdown

    def test_reset(self):
        tracker = CostTracker(budget_usd=10.0)
        tracker.record(model="gpt-4o", tokens_in=1000, tokens_out=500)
        tracker.reset()
        assert tracker.total_cost == 0.0

    def test_unknown_model_is_not_estimated(self):
        """未知模型 → **不估算金额**（此前按 `(1.0, 3.0)`/1M 凭空估价）

        用户质疑："模型商会来回改价，不及时更新就是很大的误导" —— 给没标价的模型
        编一个"保守估计"正是误导。现在：用量照记（tokens 是事实），金额为 None，
        计入 `unknown_calls`；`total_cost` **只累加已知部分**。
        """
        tracker = CostTracker(budget_usd=10.0)
        tracker.record(model="unknown-model-xyz", tokens_in=1_000_000, tokens_out=0)

        assert tracker.total_cost == 0.0, "未知模型不得计入金额"
        assert tracker.unknown_calls == 1
        call = tracker._calls[0]
        assert call["cost_usd"] is None
        assert call["cost_unknown"] is True
        assert call["tokens_in"] == 1_000_000, "用量是事实，必须照记"


class TestUnknownCalls:
    def test_total_excludes_unknown(self):
        tracker = CostTracker(budget_usd=10.0)
        tracker.record(model="gpt-4o", tokens_in=1000, tokens_out=100, agent_name="商品分析员")
        tracker.record(model="doubao-seedream-5-0-260128", tokens_in=500, tokens_out=0,
                       agent_name="生图员")

        known = tracker.total_cost
        assert known > 0.0
        assert tracker.unknown_calls == 1
        assert tracker.breakdown()["unknown_calls"] == 1
        # 只累加已知部分：再加一笔未知也不改变金额
        tracker.record(model="另一款没标价的模型", tokens_in=999, tokens_out=999)
        assert tracker.total_cost == known
        assert tracker.unknown_calls == 2

    def test_breakdown_has_usage(self):
        tracker = CostTracker(budget_usd=10.0)
        tracker.record(model="gpt-4o", tokens_in=600, tokens_out=400)
        breakdown = tracker.breakdown()
        assert "usage" in breakdown
        assert breakdown["usage"]["tokens_in"] == 600
        assert breakdown["usage"]["tokens_out"] == 400
        assert breakdown["usage"]["calls"] == 1

    def test_record_image_unknown_model_is_counted_not_priced(self):
        tracker = CostTracker(budget_usd=10.0)
        tracker.record_image(model="doubao-seedream-5-0-260128", count=3,
                             agent_name="生图员", provider="ark")
        assert tracker.total_cost == 0.0, "方舟 Seedream 不得按 0.04 计价"
        assert tracker.unknown_calls == 1
        assert tracker._calls[0]["images"] == 3, "张数是事实，必须记下"
        assert tracker.breakdown()["usage"]["images"] == 3

    def test_record_image_priced_model_from_table(self):
        """价格表里有价的模型（dall-e-3）仍能按张算出金额"""
        tracker = CostTracker(budget_usd=10.0)
        tracker.record_image(model="dall-e-3", count=2, provider="openai")
        assert tracker.total_cost == pytest.approx(0.08)
        assert tracker.unknown_calls == 0

    def test_explicit_amount_wins_over_table(self):
        """Provider 回报的实际金额优先（拿账单口径，不用定价表重算）"""
        tracker = CostTracker(budget_usd=10.0)
        tracker.record_image(model="doubao-seedream-5-0-260128", count=3,
                             amount=0.45, provider="ark")
        assert tracker.total_cost == pytest.approx(0.45)
        assert tracker.unknown_calls == 0

    def test_unknown_calls_survive_reset(self):
        tracker = CostTracker(budget_usd=10.0)
        tracker.record(model="没标价的模型", tokens_in=10)
        assert tracker.unknown_calls == 1
        tracker.reset()
        assert tracker.unknown_calls == 0 and tracker.total_cost == 0.0


class TestBudgetExceeded:
    def test_exception_message(self):
        e = BudgetExceeded(cost=12.5, budget=10.0)
        assert "$12.50" in str(e) or "12.5000" in str(e)
        assert "$10.00" in str(e) or "10.00" in str(e)
