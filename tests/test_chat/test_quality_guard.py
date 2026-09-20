"""审查/合规「连续失败 N 次即停」（用户反馈，B1）

真实会话暴露的风险：合规判定 `passed=false` 后，协调者会**反复重新生成图片**（每轮约 ¥1），
而会话预算只告警不拦截、`max_turns=15` 又太远 → 需要一条明确的止损线。

阈值：`config/default.yaml → chat.max_consecutive_review_failures`（0 = 关闭，默认 2）。
计数规则：审查员 `verdict ∈ {retry, fail}` 或报错、合规审查员 `passed=false` 或报错 → +1；
任一通过 → 清零；人工介入（approve/retry）→ 清零（人已接管）。
"""

import pytest

from src.agents.registry import AgentRegistry
from src.chat.engine import ChatEngine
from src.chat.session import SessionManager
from src.core.state import RunStatus
from src.providers import get_provider_registry


class _StubAgent:
    """按序返回预置结果的 Agent 替身"""

    provider = None

    def __init__(self, results):
        self._results = list(results)

    async def execute(self, task_brief, session, **kwargs):
        result = self._results.pop(0) if self._results else self._results_default()
        return dict(result)

    def _results_default(self):
        return {"overall_score": 88, "verdict": "pass", "needs_human_review": False}

    def _get_model(self):
        return "stub"


async def _engine(reviewer=None, compliance=None):
    registry = AgentRegistry()
    await registry.load_from_config(get_provider_registry())
    if reviewer is not None:
        registry._agents["审查员"] = reviewer
    if compliance is not None:
        registry._agents["合规审查员"] = compliance
    engine = ChatEngine(registry=registry, session_manager=SessionManager())
    session = SessionManager().create(product_images=["x"], platform="taobao")
    return engine, session


def _guard_session():
    return {"session_id": "s-q", "tenant_id": "default", "messages": [], "artifacts": {},
            "task": {}, "cost_budget_usd": 10.0}


def _halt_message(session) -> dict:
    for msg in session.get("messages", []):
        content = msg.get("content")
        if isinstance(content, dict) and content.get("aborted") == "quality_guard":
            return content
    return {}


class TestQualityGuardUnit:
    def _engine(self):
        return ChatEngine(registry=None, session_manager=SessionManager())

    @pytest.fixture(autouse=True)
    def _explicit_limit(self, monkeypatch):
        """显式固定阈值：同会话里其它测试会写 config/chat.yaml，不能依赖环境默认值"""
        import src.chat.engine as engine_mod
        monkeypatch.setattr(engine_mod, "chat_settings",
                            lambda: {"max_consecutive_review_failures": 2,
                                     "max_turns": 15, "session_ttl_hours": 24.0})

    def test_counts_failures_and_halts_at_limit(self):
        engine = self._engine()
        session = _guard_session()

        assert engine._quality_guard(session, "合规审查员", {"passed": False}) == ""
        reason = engine._quality_guard(session, "合规审查员", {"passed": False})

        assert "连续 2 次未通过" in reason and "自动停止" in reason
        assert session["_quality_failures"] == 2

    def test_pass_resets_counter(self):
        engine = self._engine()
        session = _guard_session()

        engine._quality_guard(session, "审查员", {"verdict": "retry", "overall_score": 60})
        engine._quality_guard(session, "审查员", {"verdict": "pass", "overall_score": 88})
        assert session["_quality_failures"] == 0

        assert engine._quality_guard(session, "审查员", {"verdict": "fail"}) == "", "清零后不应累计"

    @pytest.mark.parametrize("limit", [0])
    def test_limit_zero_disables_guard(self, monkeypatch, limit):
        import src.chat.engine as engine_mod
        monkeypatch.setattr(engine_mod, "chat_settings",
                            lambda: {"max_consecutive_review_failures": limit,
                                     "max_turns": 15, "session_ttl_hours": 24.0})
        engine = self._engine()
        session = _guard_session()

        for _ in range(5):
            assert engine._quality_guard(session, "合规审查员", {"passed": False}) == ""
        assert session.get("_quality_failures", 0) == 0, "关闭时完全不计数"

    def test_other_agents_are_ignored(self):
        engine = self._engine()
        session = _guard_session()
        assert engine._quality_guard(session, "生图员", {"images": []}) == ""
        assert "_quality_failures" not in session

    def test_review_error_counts_as_failure(self):
        engine = self._engine()
        session = _guard_session()
        session["_quality_failures"] = 1

        reason = engine._quality_guard(session, "审查员", {"error": "NO_IMAGE_ACCESSIBLE"})

        assert "连续 2 次" in reason and "NO_IMAGE_ACCESSIBLE" in reason


class TestQualityGuardInEngine:
    @pytest.mark.asyncio
    async def test_two_consecutive_retry_reviews_halt_session(self, monkeypatch):
        """两次 retry 审查（每次都会重新生图）→ 第二次到达阈值，会话停止"""
        import src.chat.engine as engine_mod
        monkeypatch.setattr(engine_mod, "chat_settings",
                            lambda: {"max_consecutive_review_failures": 2,
                                     "max_turns": 15, "session_ttl_hours": 24.0})
        reviewer = _StubAgent([
            {"overall_score": 60, "verdict": "retry", "needs_human_review": False,
             "dimension_scores": {"texture": 60}, "top_issues": ["构图偏左"]},
            {"overall_score": 58, "verdict": "retry", "needs_human_review": False,
             "dimension_scores": {"texture": 58}, "top_issues": ["质感不足"]},
        ])
        engine, session = await _engine(reviewer=reviewer)

        out = await engine.run(session, start_index=5)

        assert out["status"] == RunStatus.FAILED.value, "达到阈值必须停止，而不是继续重生成"
        assert any(e["kind"] == "abort" and "连续" in e["error"] for e in out["error_history"])
        assert "自动停止" in _halt_message(out).get("message", "")

    @pytest.mark.asyncio
    async def test_single_retry_does_not_halt(self, monkeypatch):
        """一次 retry 是正常重试，不能误杀"""
        import src.chat.engine as engine_mod
        monkeypatch.setattr(engine_mod, "chat_settings",
                            lambda: {"max_consecutive_review_failures": 2,
                                     "max_turns": 15, "session_ttl_hours": 24.0})
        reviewer = _StubAgent([
            {"overall_score": 60, "verdict": "retry", "needs_human_review": False,
             "dimension_scores": {"texture": 60}, "top_issues": ["构图偏左"]},
        ])
        engine, session = await _engine(reviewer=reviewer)

        out = await engine.run(session, start_index=5)

        assert out["status"] != RunStatus.FAILED.value or not _halt_message(out)
        assert out.get("_quality_failures", 0) <= 1

    @pytest.mark.asyncio
    async def test_human_retry_resets_counter(self, monkeypatch):
        """人工介入后计数清零（人已接管，不该被上一次的累计倒数掐断）"""
        engine, session = await _engine()
        session["status"] = "waiting_human"
        session["artifacts"]["review"] = {"verdict": "retry", "overall_score": 60}
        session["_quality_failures"] = 2

        await engine.resume_after_hitl(session, "retry")

        assert session.get("_quality_failures", 0) == 0
