"""数据模型序列化/反序列化测试"""

import pytest
from src.core.models import (
    ProductAnalysis, ImagePrompts, GeneratedImage,
    ReviewReport, ComplianceReport, CoordinatorDecision,
    AgentMeta, Message, MarketingAngles,
)


class TestProductAnalysis:
    def test_default_values(self):
        a = ProductAnalysis()
        assert a.category == ""
        assert a.confidence_score == 0.0
        assert a.features == []

    def test_serialization(self):
        a = ProductAnalysis(
            category="保健品",
            ingredients=["水飞蓟"],
            confidence_score=85.0,
        )
        d = a.model_dump()
        assert d["category"] == "保健品"
        assert d["confidence_score"] == 85.0

    def test_marketing_angles_nested(self):
        ma = MarketingAngles(
            selling_points=["高纯度"],
            regulatory_red_lines=["不能宣称治疗"],
        )
        a = ProductAnalysis(category="保健品", marketing_angles=ma)
        d = a.model_dump()
        assert d["marketing_angles"]["selling_points"] == ["高纯度"]
        assert "不能宣称治疗" in d["marketing_angles"]["regulatory_red_lines"]


class TestImagePrompts:
    def test_default(self):
        p = ImagePrompts()
        assert p.main_image.prompt == ""

    def test_serialization(self):
        p = ImagePrompts()
        p.scene_images.append({"prompt": "test", "platform": "taobao"})
        d = p.model_dump()
        assert len(d["scene_images"]) == 1
        assert d["scene_images"][0]["prompt"] == "test"


class TestGeneratedImage:
    def test_default(self):
        img = GeneratedImage(prompt_name="variant_1")
        assert img.model_used == ""
        assert img.processing_status == "raw"


class TestReviewReport:
    def test_default_verdict(self):
        r = ReviewReport()
        assert r.verdict == "fail"

    def test_pass(self):
        r = ReviewReport(overall_score=82, verdict="pass")
        d = r.model_dump()
        assert d["overall_score"] == 82
        assert d["verdict"] == "pass"


class TestComplianceReport:
    def test_default(self):
        c = ComplianceReport()
        assert c.passed is False
        assert c.risk_level == "low"


class TestCoordinatorDecision:
    def test_invite(self):
        d = CoordinatorDecision(
            action="invite",
            agent_name="商品分析员",
            task_brief="分析图片",
        )
        assert d.action == "invite"
        assert d.agent_name == "商品分析员"

    def test_done(self):
        d = CoordinatorDecision(action="done", reasoning="任务完成")
        assert d.action == "done"


class TestAgentMeta:
    def test_params(self):
        meta = AgentMeta(
            name="测试Agent",
            description="测试用",
            requires=["text"],
            params=[{"key": "level", "label": "级别", "type": "select", "options": ["a", "b"], "default": "a"}],
        )
        assert len(meta.params) == 1
        assert meta.params[0]["key"] == "level"


class TestMessage:
    def test_create(self):
        m = Message(
            turn=1,
            role="agent",
            sender="商品分析员",
            action="respond",
            content={"result": "ok"},
        )
        d = m.model_dump()
        assert d["turn"] == 1
        assert d["sender"] == "商品分析员"
        assert d["content"]["result"] == "ok"
