"""成本记账与产物保护测试（第二轮审计修复）

回归背景（均为实测复现）：
1. 成本链路整体失效 —— Agent 只把 `content` 交回上层，Provider 的 tokens/cost 被丢弃，
   `_track_cost` 读到 0 → `session.cost_so_far` 恒 0，租户预算检查、审计 token、批量报表全部失效；
2. 失败结果整键覆盖已成功产物 —— Agent 报错时 `_update_artifacts` 把 artifacts["images"]
   换成 `{"error": ...}`，静默销毁产出（会话仍报 completed）。
"""

import pytest

from src.agents.base import BaseAgent
from src.chat.engine import ChatEngine
from src.chat.session import SessionManager
from src.harness.rate_limiter import RateLimiter
import src.agents.base as agents_base


@pytest.fixture(autouse=True)
def _fast_limiter(monkeypatch):
    monkeypatch.setattr(agents_base, "_rate_limiter", RateLimiter(default_rpm=100_000))


class _UsageProvider:
    """模拟真实 Provider 的成功返回（含用量信封）"""

    name = "deepseek"

    async def chat(self, messages, model="", json_mode=False):
        return {"content": {"ok": True}, "tokens_used": 1000,
                "tokens_in": 600, "tokens_out": 400, "cost_usd": 0.00014}


class _UsageAgent(BaseAgent):
    meta_name = "用量探针"

    async def _execute_impl(self, task_brief, session):
        result = await self.provider.chat([{"role": "user", "content": task_brief}])
        return self._content_or_error(result, {"fallback": True})


def _usage_agent():
    """按生产方式注入模型名（注册表在 _create_agent 里就是这样写的）"""
    agent = _UsageAgent(provider=_UsageProvider())
    agent.model_name = "deepseek-v4-flash"
    return agent


def _session():
    return {"session_id": "s1", "tenant_id": "t0", "messages": [], "artifacts": {},
            "task": {}, "cost_budget_usd": 10.0}


class TestCostTracking:
    @pytest.mark.asyncio
    async def test_successful_call_records_tokens_and_cost(self):
        """成功调用必须记账：cost_so_far > 0、tracker 有调用记录、按真实模型名计价"""
        session = _session()
        result = await _usage_agent().execute("hi", session)
        assert result == {"ok": True}                       # 业务载荷不被用量字段污染
        assert session["cost_so_far"] > 0
        tracker = session["_cost_tracker"]
        assert len(tracker._calls) == 1
        call = tracker._calls[0]
        assert call["model"] == "deepseek-v4-flash"         # 真实模型名（而非路由名 deepseek）
        assert call["tokens_in"] + call["tokens_out"] == 1000
        # v4-flash 定价 0.14/M → 1000 token ≈ 0.00014（若回落默认价 1.0/3.0 会明显偏大）
        assert 0.0001 <= session["cost_so_far"] <= 0.002

    @pytest.mark.asyncio
    async def test_failed_call_records_nothing(self):
        class _ErrProvider:
            name = "deepseek"

            async def chat(self, messages, model="", json_mode=False):
                return {"error": "DeepSeek API error: 401"}

        session = _session()
        result = await _UsageAgent(provider=_ErrProvider()).execute("hi", session)
        assert "error" in result
        assert session.get("cost_so_far", 0.0) == 0.0


class TestArtifactProtection:
    def _engine(self):
        mgr = SessionManager()
        return ChatEngine(registry=None, session_manager=mgr), mgr

    @pytest.mark.asyncio
    async def test_error_result_does_not_overwrite_images(self):
        engine, mgr = self._engine()
        session = mgr.create(product_images=["fake"], tenant_id="t0")
        session["artifacts"]["images"] = [{"prompt_name": "main", "base64_data": "AAA"}]
        await engine._update_artifacts(session, "图像后处理员", {"error": "rembg crashed"})
        assert session["artifacts"]["images"] == [{"prompt_name": "main", "base64_data": "AAA"}]

    @pytest.mark.asyncio
    async def test_success_result_still_updates(self):
        engine, mgr = self._engine()
        session = mgr.create(product_images=["fake"], tenant_id="t0")
        await engine._update_artifacts(session, "生图员", {"images": [{"prompt_name": "v1"}]})
        assert session["artifacts"]["images"] == [{"prompt_name": "v1"}]

    @pytest.mark.asyncio
    async def test_error_result_does_not_overwrite_review(self):
        engine, mgr = self._engine()
        session = mgr.create(product_images=["fake"], tenant_id="t0")
        session["artifacts"]["review"] = {"overall_score": 82, "verdict": "pass"}
        await engine._update_artifacts(session, "审查员", {"error": "vision failed"})
        assert session["artifacts"]["review"]["verdict"] == "pass"


class TestAnalysisArtifactHygiene:
    """A40：失败/空的专家输出不得污染 artifacts，陈旧告警不得残留

    实测现场：品类专项分析员两轮 `{"text": ""}` 被 merge 进 analysis →
    `analysis` 多了个毫无意义的 `text: ""`；第一轮的 `_low_confidence_warning: true`
    在第二轮分析正常（置信度 72）后仍然留着，界面一直显示低置信度告警。
    """

    def _engine(self):
        mgr = SessionManager()
        return ChatEngine(registry=None, session_manager=mgr), mgr

    @pytest.mark.asyncio
    async def test_empty_specialist_output_does_not_inject_blank_fields(self):
        engine, mgr = self._engine()
        session = mgr.create(product_images=["fake"], tenant_id="t0")
        session["artifacts"]["analysis"] = {"category": "保健食品", "confidence_score": 72}

        await engine._update_artifacts(session, "品类专项分析员",
                                       {"text": "", "_output_issues": ["缺少必要字段: marketing_angles"]})

        analysis = session["artifacts"]["analysis"]
        assert analysis["category"] == "保健食品"
        assert "text" not in analysis, "空字符串不应写进分析产物"
        assert analysis.get("_output_issues"), "本轮的校验问题要留痕"

    @pytest.mark.asyncio
    async def test_substantive_specialist_output_still_merges(self):
        engine, mgr = self._engine()
        session = mgr.create(product_images=["fake"], tenant_id="t0")
        session["artifacts"]["analysis"] = {"category": "保健食品"}

        await engine._update_artifacts(session, "品类专项分析员",
                                       {"marketing_angles": {"selling_points": ["德国背书"]}})

        assert session["artifacts"]["analysis"]["marketing_angles"]["selling_points"] == ["德国背书"]

    @pytest.mark.asyncio
    async def test_stale_low_confidence_warning_is_cleared(self):
        engine, mgr = self._engine()
        session = mgr.create(product_images=["fake"], tenant_id="t0")
        session["artifacts"]["analysis"] = {"category": "保健品", "_low_confidence_warning": True}

        await engine._update_artifacts(session, "商品分析员",
                                       {"category": "保健食品", "confidence_score": 72})

        assert not session["artifacts"]["analysis"].get("_low_confidence_warning"), \
            "新一轮分析正常后必须清掉陈旧的低置信度告警"

    @pytest.mark.asyncio
    async def test_stale_output_issues_are_replaced(self):
        engine, mgr = self._engine()
        session = mgr.create(product_images=["fake"], tenant_id="t0")
        session["artifacts"]["analysis"] = {"category": "保健品",
                                            "_output_issues": ["缺少必要字段: features"]}

        await engine._update_artifacts(session, "商品分析员", {"category": "保健食品"})

        assert not session["artifacts"]["analysis"].get("_output_issues")
