"""PromptGeneratorAgent 单元测试"""

import pytest
from src.agents.prompt_gen import PromptGeneratorAgent


class TestPromptGeneratorAgent:
    """提示词生成员 — Mock 数据 + 结构验证"""

    def test_mock_returns_valid_structure(self):
        """Mock 返回完整提示词结构"""
        agent = PromptGeneratorAgent()
        result = agent._mock_prompts()
        assert "main_image" in result
        assert "scene_images" in result
        assert result.get("main_image", {}).get("prompt")

    @pytest.mark.asyncio
    async def test_execute_without_prompts(self, mock_llm, empty_session):
        """无 prompt 时返回 Mock 数据"""
        agent = PromptGeneratorAgent(provider=mock_llm)
        result = await agent.execute("生成淘宝风格提示词", empty_session)
        assert "error" not in result

    @pytest.mark.asyncio
    async def test_execute_with_analysis(self, mock_llm, session_with_analysis):
        """有分析数据时正常执行"""
        agent = PromptGeneratorAgent(provider=mock_llm)
        result = await agent.execute("生成提示词", session_with_analysis)
        assert "error" not in result
        # Mock 返回的提示词应该有内容
        main = result.get("main_image", {})
        if main:
            assert main.get("prompt") or main.get("platform")

    @pytest.mark.asyncio
    async def test_execute_without_provider(self, empty_session):
        """无 Provider 也返回 Mock 数据"""
        agent = PromptGeneratorAgent(provider=None)
        result = await agent.execute("生成提示词", empty_session)
        assert "error" not in result
