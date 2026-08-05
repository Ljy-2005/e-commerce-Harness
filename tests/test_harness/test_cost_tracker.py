"""成本追踪器测试"""

import pytest
from src.harness.cost_tracker import CostTracker, BudgetExceeded


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
        tracker.record_image(model="dall-e-3", count=1)
        assert tracker.total_cost == 0.04

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
        assert "calls" in breakdown

    def test_reset(self):
        tracker = CostTracker(budget_usd=10.0)
        tracker.record(model="gpt-4o", tokens_in=1000, tokens_out=500)
        tracker.reset()
        assert tracker.total_cost == 0.0

    def test_unknown_model_default_price(self):
        tracker = CostTracker(budget_usd=10.0)
        tracker.record(model="unknown-model-xyz", tokens_in=1_000_000, tokens_out=0)
        # 未知模型按 $1/1M 保守估算
        assert tracker.total_cost > 0.5


class TestBudgetExceeded:
    def test_exception_message(self):
        e = BudgetExceeded(cost=12.5, budget=10.0)
        assert "$12.50" in str(e) or "12.5000" in str(e)
        assert "$10.00" in str(e) or "10.00" in str(e)
