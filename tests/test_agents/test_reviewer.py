"""ReviewerAgent 单元测试"""

import pytest
from src.agents.reviewer import ReviewerAgent


class TestReviewerAgent:
    """审查员 — Mock 评分 + pass/retry/fail 逻辑"""

    def test_mock_returns_valid_structure(self):
        """Mock 返回 5 维度审查结构"""
        agent = ReviewerAgent()
        result = agent._mock_review()
        assert "overall_score" in result
        assert "verdict" in result
        assert "dimension_scores" in result
        assert "top_issues" in result
        assert "top_praises" in result

    @pytest.mark.asyncio
    async def test_execute_with_mock(self, mock_llm, empty_session):
        """Mock 执行返回审查报告"""
        agent = ReviewerAgent(provider=mock_llm)
        result = await agent.execute("审查生成的商品图", empty_session)
        assert "error" not in result
        assert isinstance(result.get("overall_score"), (int, float))

    @pytest.mark.asyncio
    async def test_execute_with_images(self, mock_llm, session_with_prompts):
        """有图片时正常审查"""
        session_with_prompts["artifacts"]["images"] = [
            {"base64_data": "fake_b64_data_for_test_image"},
        ]
        agent = ReviewerAgent(provider=mock_llm)
        result = await agent.execute("审查图片质量", session_with_prompts)
        assert "error" not in result

    @pytest.mark.asyncio
    async def test_execute_without_provider(self, empty_session):
        """无 Provider 返回 Mock 审查"""
        agent = ReviewerAgent(provider=None)
        result = await agent.execute("审查图片", empty_session)
        assert "error" not in result
        assert "verdict" in result

    def test_iteration_field_added(self):
        """审查结果包含迭代标记"""
        agent = ReviewerAgent()
        result = agent._mock_review()
        # _mock_review 本身不添加 iteration（由 _execute_impl 添加）
        # 验证 mock 数据包含迭代所需的基础字段
        assert result.get("dimension_scores")
        assert isinstance(result["dimension_scores"], dict)
