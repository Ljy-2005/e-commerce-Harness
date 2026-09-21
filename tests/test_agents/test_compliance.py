"""ComplianceAgent 单元测试"""

import pytest

from src.agents.compliance import ComplianceAgent


class TestComplianceAgent:
    """合规审查员 — Mock 数据 + 品类规则"""

    def test_mock_returns_valid_structure(self):
        """Mock 返回合规审查结构"""
        agent = ComplianceAgent()
        result = agent._mock_compliance()
        assert "passed" in result
        assert "risk_level" in result
        assert "violations" in result
        assert "warnings" in result

    def test_healthcare_rules_contain_blue_hat(self):
        """保健品规则包含蓝帽要求"""
        agent = ComplianceAgent()
        rules = agent._rules_for_category("保健品")
        assert "蓝帽" in rules
        assert "治疗" in rules

    def test_cosmetic_rules_contain_medical_ban(self):
        """化妆品规则包含医疗宣称禁止"""
        agent = ComplianceAgent()
        rules = agent._rules_for_category("化妆品")
        assert "医疗" in rules
        assert "药妆" in rules

    def test_unknown_category_returns_generic_rules(self):
        """未知品类返回通用规则"""
        agent = ComplianceAgent()
        rules = agent._rules_for_category("未知品类")
        assert "通用规则" in rules or "广告法" in rules

    @pytest.mark.asyncio
    async def test_execute_with_mock(self, mock_llm, session_with_analysis):
        """Mock 执行合规检查"""
        agent = ComplianceAgent(provider=mock_llm)
        result = await agent.execute("检查合规性", session_with_analysis)
        assert "error" not in result
        assert "passed" in result

    @pytest.mark.asyncio
    async def test_execute_without_provider(self, empty_session):
        """无 Provider 返回 Mock 合规"""
        agent = ComplianceAgent(provider=None)
        result = await agent.execute("合规检查", empty_session)
        assert "passed" in result
        assert "risk_level" in result
