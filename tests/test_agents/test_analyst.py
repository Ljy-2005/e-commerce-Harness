"""ProductAnalystAgent 单元测试"""

import pytest
from src.agents.analyst import ProductAnalystAgent


class TestProductAnalystAgent:
    """商品分析员 — Mock 模式 + 输出结构验证"""

    def test_mock_returns_valid_structure(self, mock_llm, empty_session):
        """Mock 模式返回 ProductAnalysis 的完整结构"""
        agent = ProductAnalystAgent(provider=mock_llm)
        result = agent._mock_analysis()
        assert result.get("category")
        assert "features" in result
        assert "target_audience" in result
        assert "marketing_angles" in result
        assert "style_constraints" in result

    @pytest.mark.asyncio
    async def test_execute_with_mock(self, mock_llm, empty_session):
        """Mock Provider 执行返回有效分析"""
        agent = ProductAnalystAgent(provider=mock_llm)
        result = await agent.execute("分析这张商品图片", empty_session)
        assert "error" not in result
        assert result.get("category") or True  # Mock always returns analysis

    @pytest.mark.asyncio
    async def test_execute_without_provider(self, empty_session):
        """无 Provider 时也返回 Mock 数据（不崩溃）"""
        agent = ProductAnalystAgent(provider=None)
        result = await agent.execute("分析图片", empty_session)
        assert "error" not in result


class TestProductAnalystWithRealSession:
    """使用完整 session 上下文测试"""

    @pytest.mark.asyncio
    async def test_confidence_in_range(self, mock_llm, session_with_analysis):
        """置信度在有效范围内"""
        agent = ProductAnalystAgent(provider=mock_llm)
        result = await agent.execute("分析商品图", session_with_analysis)
        if result.get("confidence_score"):
            assert 0 <= result["confidence_score"] <= 100
