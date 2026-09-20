"""审计日志的用量/耗时（A38）

实测事故：9 条审计记录里 tokens / cost_usd / duration_ms **全是 0**（唯一非 0 是超时
那条 66000ms）。原因：引擎从 Agent 返回的 content 里取用量，而用量实际被
`BaseAgent._content_or_error` 存进了实例的 `_last_usage` → 审计页对"花了多少钱、慢在哪"
零参考价值。另外生图成本完全不落账（Agent 没把 Provider 的 cost_usd 交回来）。
"""

import pytest

from src.agents.base import BaseAgent
from src.agents.registry import AgentRegistry
from src.agents.reviewer import ReviewerAgent
from src.chat.engine import ChatEngine
from src.chat.session import SessionManager
import src.chat.engine as engine_mod
from src.providers import get_provider_registry

# 1×1 PNG
PNG_B64 = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
           "z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


class _UsageProvider:
    """真实 Provider 的成功返回（带用量信封）"""

    name = "usageprov"
    capabilities = ["vision", "text"]

    async def chat_with_vision(self, messages, model=""):
        return {"content": {"overall_score": 88, "verdict": "pass",
                            "dimension_scores": {"texture": 88}},
                "tokens_used": 1234, "tokens_in": 1000, "tokens_out": 234, "cost_usd": 0.0021}

    async def chat(self, messages, model="", json_mode=False):
        return await self.chat_with_vision(messages, model)


class _UsageAgent(BaseAgent):
    meta_name = "用量探针"

    async def _execute_impl(self, task_brief, session):
        result = await self.provider.chat([{"role": "user", "content": task_brief}])
        return self._content_or_error(result, {})


def _session():
    return {"session_id": "s1", "tenant_id": "default", "messages": [], "artifacts": {},
            "task": {}, "cost_budget_usd": 10.0}


class TestAgentStats:
    @pytest.mark.asyncio
    async def test_stats_filled_on_success(self):
        agent = _UsageAgent(provider=_UsageProvider())
        agent.model_name = "deepseek-v4-flash"
        stats: dict = {}

        await agent.execute("hi", _session(), stats=stats)

        assert stats["tokens_used"] == 1234
        assert stats["cost_usd"] == pytest.approx(0.0021)
        assert stats["elapsed_ms"] > 0
        assert stats.get("error", "") == ""

    @pytest.mark.asyncio
    async def test_stats_filled_on_error(self):
        class _Err(BaseAgent):
            meta_name = "报错探针"

            async def _execute_impl(self, task_brief, session):
                raise RuntimeError("boom")

        stats: dict = {}
        result = await _Err(provider=None).execute("hi", _session(), stats=stats)

        assert "error" in result
        assert stats["elapsed_ms"] > 0
        assert "boom" in stats["error"]

    @pytest.mark.asyncio
    async def test_execute_without_stats_still_works(self):
        """向后兼容：不传 stats 的调用方（workflow / ab_testing）行为不变"""
        result = await _UsageAgent(provider=_UsageProvider()).execute("hi", _session())
        assert result == {"overall_score": 88, "verdict": "pass", "dimension_scores": {"texture": 88}}

    @pytest.mark.asyncio
    async def test_explicit_zero_cost_stays_zero(self):
        """Provider 明确回报 0.0（如 Mock / 不走网络）→ stats 里是 0.0 且 `cost_unknown=False`

        `None`（价格未标定）与 `0.0`（真的是 0）必须分得开：都写成 0 就是把未知说成确定。
        """

        class _ZeroCostProvider:
            name = "mock"
            capabilities = ["text"]

            async def chat(self, messages, model="", json_mode=False):
                return {"content": {"ok": True}, "tokens_used": 50, "cost_usd": 0.0}

        stats: dict = {}
        await _UsageAgent(provider=_ZeroCostProvider()).execute("hi", _session(), stats=stats)
        assert stats["cost_usd"] == 0.0
        assert stats["cost_unknown"] is False

    @pytest.mark.asyncio
    async def test_unknown_cost_is_not_zeroed(self):
        """Provider 回报 `cost_usd=None`（价格未标定）→ stats/tracker 都必须保持"未知"

        此前 `result.get("cost_usd") or usage.get("cost_usd", 0.0) or 0.0` 会把 None 变成
        0.0 —— 界面显示 $0.0000、预算永不告警，等于把"不知道"说成"没花钱"。
        """

        class _UnpricedProvider:
            name = "ark"
            route = "ark"
            capabilities = ["text"]

            async def chat(self, messages, model="", json_mode=False):
                return {"content": {"ok": True}, "tokens_used": 500, "cost_usd": None,
                        "model_used": "doubao-seed-2-1-pro-260628"}

        session = _session()
        stats: dict = {}
        await _UsageAgent(provider=_UnpricedProvider()).execute("hi", session, stats=stats)

        assert stats["cost_usd"] is None, "未知不得变成 0.0"
        assert stats["cost_unknown"] is True
        assert stats["tokens_used"] == 500, "用量是事实，照记"
        tracker = session["_cost_tracker"]
        assert tracker.total_cost == 0.0
        assert tracker.unknown_calls == 1
        assert session["cost_unknown_calls"] == 1


class _CapturingAudit:
    rows: list[dict] = []

    def __init__(self, *args, **kwargs):
        pass

    async def log(self, **kwargs):
        _CapturingAudit.rows.append(kwargs)


class TestEngineAuditRow:
    @pytest.mark.asyncio
    async def test_audit_row_carries_usage_and_latency(self, monkeypatch):
        _CapturingAudit.rows = []
        monkeypatch.setattr(engine_mod, "AuditLogger", _CapturingAudit)

        registry = AgentRegistry()
        await registry.load_from_config(get_provider_registry())
        registry._agents["审查员"] = ReviewerAgent(provider=_UsageProvider())
        engine = ChatEngine(registry=registry, session_manager=SessionManager())
        session = SessionManager().create(product_images=["x"], platform="taobao")
        session["artifacts"]["images"] = [{"prompt_name": "variant_1", "base64_data": PNG_B64}]

        await engine.run(session, start_index=5)

        rows = [r for r in _CapturingAudit.rows if r.get("agent_name") == "审查员"]
        assert rows, "审查员调用必须有审计记录"
        row = rows[0]
        assert row["tokens_used"] == 1234, "审计 token 此前恒 0"
        assert row["cost_usd"] == pytest.approx(0.0021)
        assert row["duration_ms"] > 0, "审计耗时此前恒 0"


class TestImageCostTracked:
    @pytest.mark.asyncio
    async def test_image_cost_lands_in_session(self):
        """生图成本此前完全不落账（Provider 的 cost_usd 被 Agent 丢掉）"""
        from src.agents.image_gen import ImageGeneratorAgent

        class _ImageProvider:
            name = "imgprov"
            capabilities = ["image"]
            default_size = "2048x2048"

            async def generate(self, prompt, negative_prompt="", size="", model=""):
                return {"image_url": "https://cdn/a.png", "base64_data": "",
                        "model_used": "doubao-seedream-5-0-260128", "cost_usd": 0.03}

        agent = ImageGeneratorAgent(provider=_ImageProvider())
        agent.model_name = "doubao-seedream-5-0-260128"
        session = _session()
        session["artifacts"]["prompts"] = {"main_image": {"prompt": "白底主图"}}

        await agent.execute("生成商品图", session)

        assert session["cost_so_far"] > 0, "生图必须计入会话成本"
        tracker = session["_cost_tracker"]
        assert tracker._calls, "应有一笔按张计费的记录"
        # 默认 3 张（capabilities.image.variants），金额取 Provider 汇总值
        assert tracker._calls[0].get("images") == 3
        assert session["cost_so_far"] == pytest.approx(0.09)


class TestImageCostCountsEveryImage:
    """A44（真实会话实测发现）：3 张图只按 1 张计费

    实测：生图员审计行 `cost_usd=0.12`（3×0.04，Agent 汇总正确），但会话 `cost_so_far`
    只涨了 0.04 —— `_track_cost` 调 `record_image(count=1)` 用定价表重算，
    既丢了张数也丢了 Provider 回报的金额（方舟 Seedream 不在定价表里）。

    本轮口径补充：Provider **没给金额**时（价格未标定）不得拿常量估，
    张数照记、金额记未知（`unknown_calls`），界面显示"未标定（N 张图）"。
    """

    @pytest.mark.asyncio
    async def test_three_images_count_as_three(self):
        from src.agents.image_gen import ImageGeneratorAgent

        class _ImageProvider:
            name = "imgprov3"
            capabilities = ["image"]
            default_size = "2048x2048"

            async def generate(self, prompt, negative_prompt="", size="", model=""):
                return {"image_url": "https://cdn/a.png", "base64_data": "",
                        "model_used": "doubao-seedream-5-0-260128", "cost_usd": 0.04}

        agent = ImageGeneratorAgent(provider=_ImageProvider())
        agent.model_name = "doubao-seedream-5-0-260128"
        session = _session()
        session["artifacts"]["prompts"] = {"main_image": {"prompt": "白底主图"}}

        await agent.execute("生成商品图", session)

        tracker = session["_cost_tracker"]
        assert tracker._calls[0]["images"] == 3, "张数必须如实计入"
        assert session["cost_so_far"] == pytest.approx(0.12), "会话成本必须等于 Provider 汇总值"

    @pytest.mark.asyncio
    async def test_uncalibrated_image_cost_is_not_invented(self):
        """Provider 没给金额（价格未标定）→ 会话金额不涨，只计 unknown_calls + 张数

        方舟 Seedream 是我们在用的真实路由，而它**没有**可核对的公开价目表：
        此前 `cost_tracker.record_image` 会拿 0.04 兜底给 3 张图造出 $0.12。
        """
        from src.agents.image_gen import ImageGeneratorAgent

        class _UncalibratedProvider:
            name = "ark"
            route = "ark"
            capabilities = ["image"]
            default_size = "2048x2048"

            async def generate(self, prompt, negative_prompt="", size="", model=""):
                return {"image_url": "https://cdn/a.png", "base64_data": "",
                        "model_used": "doubao-seedream-5-0-260128", "cost_usd": None}

        agent = ImageGeneratorAgent(provider=_UncalibratedProvider())
        agent.model_name = "doubao-seedream-5-0-260128"
        session = _session()
        session["artifacts"]["prompts"] = {"main_image": {"prompt": "白底主图"}}

        result = await agent.execute("生成商品图", session)

        assert result.get("cost_usd") is None, "未标定不得变成 0.0"
        assert result.get("cost_unknown") is True
        assert session["cost_so_far"] == 0.0, "不得凭空造金额（旧的 3×0.04=0.12 就是造的）"
        tracker = session["_cost_tracker"]
        assert tracker.unknown_calls == 1
        assert tracker._calls[0]["images"] == 3, "张数是事实，必须记下"
