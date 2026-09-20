"""超时预算与重试策略（A34）

实测事故：提示词生成员超时上限 15s，而 `with_retry` 默认重试 3 次（共 4 次尝试）
→ 用户白等 **66 秒**才看到失败（错误文案还说"超时 (15000ms)"，自相矛盾）。
另外 `config/agents/*.yaml` 的 `timeout_ms` / `retry` 是**死配置**：注册表只把它们
读进 AgentMeta（还会展示在 API 里），从没写回 Agent 实例，跑的一直是类属性。
"""

import asyncio

import httpx
import pytest

from src.agents.base import BaseAgent
from src.agents.registry import AgentRegistry
from src.harness.retry import RetryConfig
from src.providers import get_provider_registry


class _EchoAgent(BaseAgent):
    meta_name = "回声"

    async def _execute_impl(self, task_brief, session):
        return await self.provider.chat([{"role": "user", "content": task_brief}])


class _SlowProvider:
    name = "slowprov"
    capabilities = ["text"]

    def __init__(self):
        self.calls = 0

    async def chat(self, messages, model="", json_mode=False):
        self.calls += 1
        await asyncio.sleep(0.4)
        return {"content": {"text": "late"}, "tokens_used": 1}


class _TransportFailProvider:
    name = "transportprov"
    capabilities = ["text"]

    def __init__(self):
        self.calls = 0

    async def chat(self, messages, model="", json_mode=False):
        self.calls += 1
        raise httpx.ConnectError("connection refused")


class _ValueErrorProvider:
    name = "valueprov"
    capabilities = ["text"]

    def __init__(self):
        self.calls = 0

    async def chat(self, messages, model="", json_mode=False):
        self.calls += 1
        raise ValueError("boom")


def _session():
    return {"session_id": "s-t", "tenant_id": "default", "turn_count": 1,
            "task": {}, "artifacts": {}, "messages": []}


class TestTimeoutNotRetried:
    @pytest.mark.asyncio
    async def test_agent_timeout_fails_once(self):
        """超时不再被重试 3 次（旧行为：4 次尝试 × 15s = 66s 白等）"""
        provider = _SlowProvider()
        agent = _EchoAgent(provider=provider,
                           retry_config=RetryConfig(max_retries=3, base_delay_ms=1, jitter=False))
        agent.timeout_ms = 60

        result = await agent.execute("hi", _session())

        assert "error" in result and "超时" in result["error"]
        assert provider.calls == 1, f"超时不应重试，实际调用 {provider.calls} 次"

    @pytest.mark.asyncio
    async def test_transport_error_is_retried(self):
        """网络类抖动仍要重试（重试机制本身不能被砍掉）"""
        provider = _TransportFailProvider()
        agent = _EchoAgent(provider=provider,
                           retry_config=RetryConfig(max_retries=2, base_delay_ms=1, jitter=False))

        result = await agent.execute("hi", _session())

        assert "error" in result
        assert provider.calls == 3, f"传输错误应重试到上限，实际 {provider.calls} 次"

    @pytest.mark.asyncio
    async def test_programming_error_is_not_retried(self):
        """代码缺陷不该被重试掩盖（也无意义地烧钱）"""
        provider = _ValueErrorProvider()
        agent = _EchoAgent(provider=provider,
                           retry_config=RetryConfig(max_retries=3, base_delay_ms=1, jitter=False))

        result = await agent.execute("hi", _session())

        assert "error" in result
        assert provider.calls == 1


class TestYamlTimeoutsReachAgent:
    @pytest.mark.asyncio
    async def test_registry_applies_timeout_and_retry(self, monkeypatch):
        import src.agents.registry as reg_mod

        monkeypatch.setattr(reg_mod, "list_agent_configs", lambda: ["probe"])
        monkeypatch.setattr(reg_mod, "load_agent_config", lambda name: {
            "name": "商品分析员",
            "requires": ["text"],
            "timeout_ms": 12345,
            "retry": {"max_retries": 1, "backoff": "exponential"},
        })

        registry = AgentRegistry()
        await registry.load_from_config(get_provider_registry())

        agent = registry.get("商品分析员")
        assert agent is not None
        assert agent.timeout_ms == 12345, "config/agents/*.yaml 的 timeout_ms 必须是生效配置"
        assert agent.retry_config.max_retries == 1

    def test_shipped_timeouts_are_generous_enough(self):
        """实测：单次视觉调用 28s、推理调用 75s、方舟出图 30s/张 —— 旧预算明显不够"""
        from src.agents.coordinator import CoordinatorAgent
        from src.agents.image_gen import ImageGeneratorAgent
        from src.agents.prompt_gen import PromptGeneratorAgent
        from src.agents.reviewer import ReviewerAgent
        from src.core.config import load_agent_config

        assert PromptGeneratorAgent.timeout_ms >= 120_000
        assert ImageGeneratorAgent.timeout_ms >= 180_000
        assert ReviewerAgent.timeout_ms >= 120_000
        assert CoordinatorAgent.timeout_ms >= 60_000
        for name in ("prompt_generator", "image_generator", "reviewer", "coordinator"):
            cfg = load_agent_config(name)
            if cfg.get("timeout_ms"):
                assert cfg["timeout_ms"] >= 60_000, f"{name} 的 YAML 预算过小"
