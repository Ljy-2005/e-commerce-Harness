"""A/B 测试框架单元测试 — 数据模型 / 工厂 / 执行器"""

import pytest

from src.core.models import AgentMeta
from src.agents.registry import AgentRegistry
from src.agents.prompt_gen import PromptGeneratorAgent
from src.agents.reviewer import ReviewerAgent
from src.providers.mock import MockLLMProvider
from src.harness.ab_testing import (
    ABTestConfig,
    ABTestResult,
    ABTestRunner,
    ABVariant,
    VariantResult,
    make_model_variants,
    make_prompt_variants,
    make_temperature_variants,
)


# ── Fixtures ──


@pytest.fixture
def ab_registry():
    """注册 提示词生成员 + 审查员（Mock Provider）的注册中心"""
    registry = AgentRegistry()
    mock = MockLLMProvider()
    registry.register(
        PromptGeneratorAgent(provider=mock),
        AgentMeta(name="提示词生成员", description="生成提示词", requires=["text"]),
    )
    registry.register(
        ReviewerAgent(provider=mock),
        AgentMeta(name="审查员", description="质量审查", requires=["vision"]),
    )
    return registry


@pytest.fixture
def ab_session():
    """含分析产物的会话"""
    from src.chat.session import SessionManager

    mgr = SessionManager()
    session = mgr.create(
        product_images=["fake_b64"],
        product_info="测试保健品 - 维生素C片",
        platform="taobao",
        category_hint="保健品",
    )
    session["artifacts"]["analysis"] = {"category": "保健品", "confidence_score": 85}
    return session


# ── 数据模型 ──


class TestVariantResult:
    def test_avg_score_empty(self):
        vr = VariantResult("v1")
        assert vr.avg_score == 0.0

    def test_avg_score(self):
        vr = VariantResult(
            "v1",
            scores=[{"overall_score": 80}, {"overall_score": 90}],
        )
        assert vr.avg_score == 85.0

    def test_avg_score_rounds_to_one_decimal(self):
        vr = VariantResult(
            "v1",
            scores=[{"overall_score": 82}, {"overall_score": 83}, {"overall_score": 84}],
        )
        assert vr.avg_score == 83.0  # 83.0 而非 82.999...

    def test_verdicts(self):
        vr = VariantResult(
            "v1",
            scores=[{"verdict": "pass"}, {"verdict": "retry"}],
        )
        assert vr.verdicts == ["pass", "retry"]

    def test_majority_verdict(self):
        vr = VariantResult(
            "v1",
            scores=[
                {"verdict": "pass"},
                {"verdict": "pass"},
                {"verdict": "retry"},
            ],
        )
        assert vr.majority_verdict == "pass"

    def test_majority_verdict_empty_defaults_fail(self):
        assert VariantResult("v1").majority_verdict == "fail"

    def test_success(self):
        assert VariantResult("v1", scores=[{"overall_score": 90}]).success is True
        assert VariantResult("v1", error="boom").success is False
        assert VariantResult("v1").success is False  # 无评分不算成功


class TestABTestConfig:
    def test_defaults(self):
        cfg = ABTestConfig(agent_name="审查员", variants=[ABVariant("v1")])
        assert cfg.scoring_method == "multi_reviewer"
        assert cfg.review_count == 3
        assert cfg.review_agent == "审查员"
        assert cfg.min_score_threshold == 70.0
        assert cfg.parallel is True


class TestABTestResult:
    def _result(self, scores_map: dict[str, list[dict]], threshold: float = 70.0):
        config = ABTestConfig(
            agent_name="提示词生成员",
            variants=[ABVariant(vid) for vid in scores_map],
            min_score_threshold=threshold,
        )
        variants = [
            VariantResult(vid, scores=scores_map[vid])
            for vid in scores_map
        ]
        return ABTestResult(config=config, variants=variants)

    def test_ranking_sorted_desc(self):
        result = self._result({
            "low": [{"overall_score": 50}],
            "high": [{"overall_score": 90}],
            "mid": [{"overall_score": 70}],
        })
        ids = [v.variant_id for v in result.ranking]
        assert ids == ["high", "mid", "low"]

    def test_all_passed(self):
        assert self._result({
            "a": [{"overall_score": 80}],
            "b": [{"overall_score": 75}],
        }).all_passed is True
        assert self._result({
            "a": [{"overall_score": 80}],
            "b": [{"overall_score": 60}],
        }).all_passed is False


# ── 快捷工厂 ──


class TestFactories:
    def test_make_model_variants(self):
        cfg = make_model_variants(
            "提示词生成员",
            [("v1", "gpt-4o"), ("v2", "deepseek-chat")],
        )
        assert cfg.agent_name == "提示词生成员"
        assert len(cfg.variants) == 2
        assert cfg.variants[0].variant_id == "v1"
        assert cfg.variants[0].model_override == "gpt-4o"
        assert "gpt-4o" in cfg.variants[0].label
        assert cfg.review_count == 3

    def test_make_prompt_variants(self):
        cfg = make_prompt_variants(
            "提示词生成员",
            [("v_a", "极简风", "请使用极简风格")],
        )
        assert cfg.variants[0].variant_id == "v_a"
        assert cfg.variants[0].label == "极简风"
        assert cfg.variants[0].prompt_override == "请使用极简风格"

    def test_make_temperature_variants(self):
        cfg = make_temperature_variants(
            "提示词生成员",
            [("t1", 0.5), ("t2", 1.0)],
        )
        assert cfg.variants[0].temperature == 0.5
        assert cfg.variants[0].label == "T=0.5"
        assert cfg.variants[1].temperature == 1.0


# ── 执行器 ──


class TestABTestRunner:
    @pytest.mark.asyncio
    async def test_unknown_agent_returns_error_variant(self, ab_session):
        runner = ABTestRunner(AgentRegistry(), ab_session)
        config = ABTestConfig(agent_name="不存在的Agent", variants=[ABVariant("v1")])
        result = await runner.run(config)
        assert result.variants[0].error
        assert "未注册" in result.variants[0].error
        assert result.winner is None

    @pytest.mark.asyncio
    async def test_parallel_run_selects_winner(self, ab_registry, ab_session):
        config = ABTestConfig(
            agent_name="提示词生成员",
            variants=[ABVariant("v1", label="变体1"), ABVariant("v2", label="变体2")],
            review_count=1,
        )
        runner = ABTestRunner(ab_registry, ab_session)
        result = await runner.run(config)

        assert result.winner is not None
        assert result.winner.variant_id in ("v1", "v2")
        assert result.winner.avg_score >= config.min_score_threshold
        # 排名按分数降序
        scores = [v.avg_score for v in result.ranking]
        assert scores == sorted(scores, reverse=True)
        # 每个成功变体都有审查评分
        for vr in result.variants:
            assert vr.success
            assert len(vr.scores) == 1

    @pytest.mark.asyncio
    async def test_serial_run_same_result(self, ab_registry, ab_session):
        config = ABTestConfig(
            agent_name="提示词生成员",
            variants=[ABVariant("v1"), ABVariant("v2")],
            review_count=1,
            parallel=False,
        )
        runner = ABTestRunner(ab_registry, ab_session)
        result = await runner.run(config)
        assert result.winner is not None
        assert all(vr.success for vr in result.variants)

    @pytest.mark.asyncio
    async def test_no_winner_below_threshold(self, ab_registry, ab_session):
        config = ABTestConfig(
            agent_name="提示词生成员",
            variants=[ABVariant("v1"), ABVariant("v2")],
            review_count=1,
            min_score_threshold=100.0,  # Mock 审查 82 分 < 100
        )
        runner = ABTestRunner(ab_registry, ab_session)
        result = await runner.run(config)
        assert result.winner is None
        assert result.runner_up is None
        assert len(result.ranking) == 2  # 排名仍在

    @pytest.mark.asyncio
    async def test_runner_up_selected(self, ab_registry, ab_session):
        config = ABTestConfig(
            agent_name="提示词生成员",
            variants=[ABVariant("v1"), ABVariant("v2"), ABVariant("v3")],
            review_count=1,
        )
        runner = ABTestRunner(ab_registry, ab_session)
        result = await runner.run(config)
        assert result.winner is not None
        assert result.runner_up is not None
        assert result.runner_up.variant_id != result.winner.variant_id

    @pytest.mark.asyncio
    async def test_variant_failure_isolated(self, ab_session):
        """单个变体抛异常不影响其他变体，且不产生 winner"""

        class _BoomAgent:
            provider = None  # 与 BaseAgent 保持属性一致

            async def execute(self, task_brief, session):
                raise RuntimeError("boom failure")

        registry = AgentRegistry()
        registry.register(
            _BoomAgent(),
            AgentMeta(name="爆炸员", description="测试用", requires=["text"]),
        )
        config = ABTestConfig(
            agent_name="爆炸员",
            variants=[ABVariant("v1"), ABVariant("v2")],
            review_count=1,
        )
        result = await ABTestRunner(registry, ab_session).run(config)
        assert all(vr.error == "boom failure" for vr in result.variants)
        assert all(not vr.success for vr in result.variants)
        assert result.winner is None

    @pytest.mark.asyncio
    async def test_multi_reviewer_scores_averaged(self, ab_registry, ab_session):
        config = ABTestConfig(
            agent_name="提示词生成员",
            variants=[ABVariant("v1")],
            review_count=3,
        )
        result = await ABTestRunner(ab_registry, ab_session).run(config)
        vr = result.variants[0]
        assert len(vr.scores) == 3
        # Mock 审查员固定 82 分 → 平均 82
        assert vr.avg_score == 82.0
        assert result.winner is vr

    @pytest.mark.asyncio
    async def test_model_override_propagates_to_agent(self):
        """审计修复：model_override 必须真实传给 Agent（此前只拼进提示词文本）"""

        class _CaptureAgent:
            meta_name = "抓取员"
            provider = None
            captured = []

            async def execute(self, task_brief, session, model_override=None):
                _CaptureAgent.captured.append(model_override)
                return {"content": {}, "tokens_used": 1, "cost_usd": 0.0}

        _CaptureAgent.captured = []
        registry = AgentRegistry()
        registry.register(
            _CaptureAgent(),
            AgentMeta(name="抓取员", description="测试用", requires=["text"]),
        )
        config = ABTestConfig(
            agent_name="抓取员",
            variants=[
                ABVariant("v1", model_override="gpt-4o"),
                ABVariant("v2", model_override="deepseek-chat"),
            ],
            review_count=1,
        )
        await ABTestRunner(registry, {"tenant_id": "default"}).run(config)
        assert _CaptureAgent.captured == ["gpt-4o", "deepseek-chat"]

    @pytest.mark.asyncio
    async def test_base_agent_model_override_in_kwargs(self):
        """BaseAgent.execute(model_override=...) 经 _model_kwargs 传给 Provider"""
        from src.agents.base import BaseAgent

        class _P:
            name = "fake"
            capabilities = ["text"]

        seen = {}

        class _A(BaseAgent):
            async def _execute_impl(self, task_brief, session):
                seen["model"] = self._model_kwargs().get("model")
                return {"content": "ok", "tokens_used": 1}

        agent = _A(provider=_P())
        await agent.execute("t", {"tenant_id": "default"}, model_override="qwen-max")
        assert seen["model"] == "qwen-max"
        # 覆盖只对本次调用生效
        await agent.execute("t2", {"tenant_id": "default"})
        assert seen["model"] is None


# ── 未标定价格（None）不得让 A/B 崩溃 ──


class TestUnknownCost:
    """`cost_usd=None`（价格未标定）时 `sum(...)` 会 TypeError；当 0 又是"编钱"。

    用户质疑："每个模型的花费又会随着时间被各大模型商来回修改……不然会出现很大的误导"。
    """

    @pytest.mark.asyncio
    async def test_none_cost_does_not_crash_and_is_not_summed(self, ab_session):
        class _MixedAgent:
            meta_name = "混合员"
            provider = None

            async def execute(self, task_brief, session, model_override=None):
                if model_override == "没标价的模型":
                    return {"content": {}, "tokens_used": 10, "cost_usd": None}
                return {"content": {}, "tokens_used": 10, "cost_usd": 0.3}

        registry = AgentRegistry()
        registry.register(_MixedAgent(),
                          AgentMeta(name="混合员", description="测试用", requires=["text"]))
        config = ABTestConfig(
            agent_name="混合员",
            variants=[ABVariant("v-priced", model_override="dall-e-3"),
                      ABVariant("v-unpriced", model_override="没标价的模型")],
            review_count=0,
        )
        result = await ABTestRunner(registry, ab_session).run(config)

        by_id = {v.variant_id: v for v in result.variants}
        assert by_id["v-unpriced"].cost_usd is None
        assert by_id["v-unpriced"].cost_unknown is True
        assert by_id["v-priced"].cost_usd == pytest.approx(0.3)
        # 只累加已知部分 + 单独计数未标定
        assert result.total_cost_usd == pytest.approx(0.3)
        assert result.cost_unknown_calls == 1

    def test_dataclass_defaults(self):
        assert VariantResult("v1").cost_usd is None
        assert VariantResult("v1").cost_unknown is True
        assert ABTestResult(config=ABTestConfig(agent_name="a", variants=[])).total_cost_usd == 0.0
