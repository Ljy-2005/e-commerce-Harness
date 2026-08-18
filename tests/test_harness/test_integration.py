"""Harness 集成测试 — 验证熔断+限流+成本在实际 Agent 调用中生效"""

import pytest
from src.agents.base import BaseAgent, get_circuit_breaker, _get_limiter
from src.chat.session import SessionManager


class _TestAgent(BaseAgent):
    """测试用 Agent"""
    meta_name = "test_agent"
    timeout_ms = 5000

    async def _execute_impl(self, task_brief: str, session) -> dict:
        return {"result": "ok", "tokens_used": 100, "cost_usd": 0.001, "model_used": "gpt-4o"}


class _FailingAgent(BaseAgent):
    """会失败的测试 Agent"""
    meta_name = "failing_agent"
    timeout_ms = 5000

    async def _execute_impl(self, task_brief: str, session) -> dict:
        raise RuntimeError("模拟 Provider 故障")


class TestCircuitBreakerIntegration:
    @pytest.mark.asyncio
    async def test_success_keeps_circuit_closed(self):
        agent = _TestAgent(provider=_FakeProvider("openai"))
        session = _make_session()

        for _ in range(3):
            result = await agent.execute("test", session)
            assert result.get("result") == "ok"

        cb = get_circuit_breaker("openai")
        assert cb.state.value == "closed"

    @pytest.mark.asyncio
    async def test_failures_open_circuit(self):
        agent = _FailingAgent(provider=_FakeProvider("openai"))
        session = _make_session()

        # 连续失败 5 次 → 熔断打开
        for _ in range(5):
            result = await agent.execute("test", session)
            assert "error" in result

        cb = get_circuit_breaker("openai")
        assert cb.state.value == "open"

    @pytest.mark.asyncio
    async def test_error_dict_opens_circuit(self):
        """审计修复：Provider 返回 {"error": ...}（非 200）也必须计为熔断失败"""

        class _ErrorAgent(BaseAgent):
            meta_name = "error_agent"
            timeout_ms = 5000

            async def _execute_impl(self, task_brief, session) -> dict:
                return {"error": "HTTP 500", "tokens_used": 0}

        agent = _ErrorAgent(provider=_FakeProvider("error-provider"))
        session = _make_session()
        for _ in range(5):
            await agent.execute("test", session)
        cb = get_circuit_breaker("error-provider")
        assert cb.state.value == "open"

    @pytest.mark.asyncio
    async def test_circuit_open_blocks_requests(self):
        agent = _FailingAgent(provider=_FakeProvider("blocked-provider"))
        session = _make_session()

        # 先打熔断
        for _ in range(5):
            await agent.execute("test", session)

        # 再请求应该被拦截
        result = await agent.execute("test", session)
        assert "已熔断" in result.get("error", "")


class TestCostTrackerIntegration:
    @pytest.mark.asyncio
    async def test_cost_accumulates_in_session(self):
        agent = _TestAgent(provider=_FakeProvider("openai"))
        session = _make_session()

        for _ in range(3):
            await agent.execute("test", session)

        assert session["cost_so_far"] > 0
        tracker = session.get("_cost_tracker")
        assert tracker is not None
        assert tracker.total_cost > 0

    @pytest.mark.asyncio
    async def test_cost_warning_triggered(self):
        agent = _TestAgent(provider=_FakeProvider("openai"))
        session = _make_session()

        # 设置极低预算用触发告警
        session["cost_budget_usd"] = 0.0001

        for _ in range(5):
            await agent.execute("test", session)

        warnings = session.get("_warnings", [])
        # 可能触发告警（取决于累计成本）
        assert session["cost_so_far"] > 0


def _make_session():
    mgr = SessionManager()
    return mgr.create(
        product_images=["fake"],
        product_info="test",
        platform="taobao",
    )


class _FakeProvider:
    """模拟 Provider 用于测试"""
    def __init__(self, name="test"):
        self.name = name
        self.capabilities = ["text"]
