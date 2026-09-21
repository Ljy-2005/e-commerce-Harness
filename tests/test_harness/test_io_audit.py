"""Input/Output Pipeline + 审计日志测试"""

import pytest

from src.harness.audit_logger import AuditLogger
from src.harness.input_pipeline import ImageValidator
from src.harness.output_pipeline import OutputPipeline, SchemaValidator


class _FakeImage:
    def __init__(self, media_type="image/jpeg", file_size=1000, base64_data="abc123def456"):
        self.media_type = media_type
        self.file_size = file_size
        self.base64_data = base64_data


class TestImageValidator:
    def test_valid_image_passes(self):
        v = ImageValidator()
        img = _FakeImage()
        result = v.validate(img)
        assert result.passed

    def test_invalid_format_fails(self):
        v = ImageValidator()
        img = _FakeImage(media_type="image/gif")
        result = v.validate(img)
        assert not result.passed
        assert any("格式" in e for e in result.errors)

    def test_oversized_fails(self):
        v = ImageValidator()
        img = _FakeImage(file_size=50 * 1024 * 1024)  # 50MB
        result = v.validate(img)
        assert not result.passed

    def test_empty_base64_fails(self):
        v = ImageValidator()
        img = _FakeImage(base64_data="")
        result = v.validate(img)
        assert not result.passed


class TestSchemaValidator:
    def test_valid_analyst_output(self):
        v = SchemaValidator()
        output = {
            "category": "保健品",
            "features": ["高纯度"],
            "target_audience": {"age": "25-50"},
            "confidence_score": 85.0,
        }
        result = v.validate("商品分析员", output)
        assert result.passed

    def test_missing_field(self):
        v = SchemaValidator()
        output = {"category": "保健品"}  # missing features, target_audience, confidence_score
        result = v.validate("商品分析员", output)
        assert not result.passed
        assert len(result.missing_fields) >= 2

    def test_non_dict_input(self):
        v = SchemaValidator()
        result = v.validate("商品分析员", "not a dict")
        assert not result.passed

    def test_valid_reviewer_output(self):
        v = SchemaValidator()
        output = {
            "overall_score": 82.0,
            "dimension_scores": {"texture": 80},
            "verdict": "pass",
        }
        result = v.validate("审查员", output)
        assert result.passed


class TestOutputPipeline:
    def test_full_pipeline_pass(self):
        p = OutputPipeline()
        output = {
            "overall_score": 82.0,
            "dimension_scores": {"texture": 80},
            "verdict": "pass",
        }
        result = p.validate("审查员", output)
        assert result.passed

    def test_business_rule_fails_bad_score(self):
        p = OutputPipeline()
        output = {
            "overall_score": 150,  # > 100
            "dimension_scores": {},
            "verdict": "pass",
        }
        result = p.validate("审查员", output)
        assert not result.passed

    def test_unregistered_agent_passes(self):
        p = OutputPipeline()
        result = p.validate("未知Agent", {"any": "data"})
        # 无 Schema 要求 → 直接通过
        assert result.passed


class TestAuditLogger:
    @pytest.fixture
    def logger(self, tmp_path):
        """审计修复：注入临时目录，不再污染真实 data/audit"""
        return AuditLogger(log_dir=str(tmp_path / "audit"))

    @pytest.mark.asyncio
    async def test_log_and_query(self, logger):
        await logger.log(
            session_id="test-123",
            agent_name="商品分析员",
            provider_name="openai",
            model="gpt-4o",
            action="execute",
            duration_ms=150.5,
            tokens_used=500,
            cost_usd=0.0025,
            status="ok",
        )
        entries = await logger.query(session_id="test-123")
        assert len(entries) >= 1
        assert entries[-1]["agent"] == "商品分析员"

    @pytest.mark.asyncio
    async def test_stats(self, logger):
        stats = await logger.stats()
        assert "total_calls" in stats
        assert "total_cost_usd" in stats
        assert "by_agent" in stats

    @pytest.mark.asyncio
    async def test_query_by_agent(self, logger):
        # May return 0 if no previous calls, that's fine
        entries = await logger.query(agent_name="审查员")
        assert isinstance(entries, list)

    @pytest.mark.asyncio
    async def test_log_error(self, logger):
        await logger.log(
            session_id="test-error",
            agent_name="生图员",
            provider_name="openai",
            model="dall-e-3",
            action="execute",
            duration_ms=2000,
            tokens_used=0,
            cost_usd=0.0,
            status="failed",
            error="Rate limit exceeded",
        )
        entries = await logger.query(session_id="test-error")
        assert len(entries) >= 1
        assert entries[-1]["error"] == "Rate limit exceeded"

    @pytest.mark.asyncio
    async def test_tenant_isolation(self, logger):
        """审计修复：审计条目按租户过滤"""
        for tid in ("tenant_a", "tenant_b"):
            await logger.log(
                session_id=f"s-{tid}", agent_name="审查员", provider_name="mock",
                model="mock", action="execute", duration_ms=1,
                tokens_used=1, cost_usd=0.0, status="ok", tenant_id=tid,
            )
        only_a = await logger.query(tenant_id="tenant_a")
        assert len(only_a) == 1
        assert only_a[0]["session_id"] == "s-tenant_a"
        # 无租户参数 → 不限制
        assert len(await logger.query()) >= 2

    @pytest.mark.asyncio
    async def test_null_cost_is_logged_not_crashed(self, logger):
        """价格未标定 → `cost_usd=None` 必须能落盘（null）并标 `cost_unknown`

        此前 `entry["cost_usd"] = round(cost_usd, 6)` 遇 None 直接 TypeError；
        引擎那层 try 会吞掉它 → **这次调用整条审计丢失**（用量/耗时也一起消失）。
        """
        await logger.log(
            session_id="s-unpriced",
            agent_name="生图员",
            provider_name="ark",
            model="doubao-seedream-5-0-260128",
            action="execute",
            duration_ms=30000,
            tokens_used=0,
            cost_usd=None,
            status="ok",
            cost_unknown=True,
        )
        entries = await logger.query(session_id="s-unpriced")
        assert len(entries) == 1
        entry = entries[0]
        assert entry["cost_usd"] is None, "未标定写 null，不能写 0"
        assert entry["cost_unknown"] is True
        assert entry["tokens"] == 0
        assert entry["duration_ms"] == 30000.0, "用量/耗时是事实，必须留下"

    @pytest.mark.asyncio
    async def test_stats_skips_null_cost(self, logger):
        """汇总跳过 null（不当 0），并给出 unknown_calls —— 否则"今日花费"会偏小"""
        await logger.log(session_id="s1", agent_name="A", provider_name="openai",
                         model="gpt-4o", action="execute", duration_ms=1,
                         tokens_used=10, cost_usd=0.25, status="ok")
        await logger.log(session_id="s2", agent_name="生图员", provider_name="ark",
                         model="doubao-seedream-5-0-260128", action="execute",
                         duration_ms=1, tokens_used=0, cost_usd=None, status="ok")
        stats = await logger.stats()
        assert stats["total_calls"] == 2
        assert stats["total_cost_usd"] == pytest.approx(0.25)
        assert stats["unknown_calls"] == 1
