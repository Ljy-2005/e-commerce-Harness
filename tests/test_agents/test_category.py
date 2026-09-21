"""CategorySpecialistAgent 单元测试"""

import pytest

from src.agents.category import CategorySpecialistAgent


class TestCategorySpecialistAgent:
    """品类专项分析员 — 按品类返回不同分析维度"""

    def test_healthcare_category_adds_compliance_angles(self):
        """保健品品类追加合规/送礼等营销角度"""
        agent = CategorySpecialistAgent()
        result = agent._mock_by_category("保健品")
        angles = result.get("marketing_angles", {}).get("marketing_angles", [])
        assert any("送礼" in a or "职场" in a or "家庭" in a for a in angles), f"Missing healthcare angles: {angles}"

    def test_cosmetic_category_adds_skin_angles(self):
        """化妆品品类追加肤质/成分等维度"""
        agent = CategorySpecialistAgent()
        result = agent._mock_by_category("护肤")
        angles = result.get("marketing_angles", {}).get("marketing_angles", [])
        assert any("成分" in a or "肤质" in a or "护肤" in a for a in angles), f"Missing cosmetic angles: {angles}"

    def test_food_category_adds_ingredient_angles(self):
        """食品品类追加食材溯源等维度"""
        agent = CategorySpecialistAgent()
        result = agent._mock_by_category("食品")
        angles = result.get("marketing_angles", {}).get("marketing_angles", [])
        assert any("食材" in a or "健康" in a or "家庭" in a for a in angles), f"Missing food angles: {angles}"

    def test_3c_category_adds_design_angles(self):
        """3C 品类追加设计/功能等维度"""
        agent = CategorySpecialistAgent()
        result = agent._mock_by_category("3C数码")
        angles = result.get("marketing_angles", {}).get("marketing_angles", [])
        assert any("功能" in a or "设计" in a or "场景" in a for a in angles)

    def test_unknown_category_returns_valid_data(self):
        """未知品类返回有效结构"""
        agent = CategorySpecialistAgent()
        result = agent._mock_by_category("未知品类")
        assert result.get("category")

    @pytest.mark.asyncio
    async def test_execute_with_mock(self, mock_llm, session_with_analysis):
        """Mock 执行不崩溃"""
        agent = CategorySpecialistAgent(provider=mock_llm)
        result = await agent.execute("深度分析保健品", session_with_analysis)
        assert "error" not in result
