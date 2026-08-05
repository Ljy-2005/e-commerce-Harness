"""ImageGeneratorAgent 单元测试"""

import pytest
from src.agents.image_gen import ImageGeneratorAgent


class TestImageGeneratorAgent:
    """生图员 — Mock 模式 + 输出结构"""

    def test_mock_returns_svg_placeholder(self):
        """Mock 模式返回 SVG placeholder"""
        agent = ImageGeneratorAgent()
        result = agent._mock_image(1, "test prompt")
        assert result.get("image_url", "").startswith("data:image/svg+xml")
        assert result.get("prompt_name", "").startswith("variant_")
        assert result.get("base64_data")

    @pytest.mark.asyncio
    async def test_execute_without_prompts(self, mock_image, session_with_analysis):
        """无提示词时用 fallback prompt"""
        agent = ImageGeneratorAgent(provider=mock_image)
        result = await agent.execute("生成商品图", session_with_analysis)
        assert "error" not in result
        assert result.get("images")
        assert len(result["images"]) == 3

    @pytest.mark.asyncio
    async def test_execute_with_prompts(self, mock_image, session_with_prompts):
        """有提示词时正常生成"""
        agent = ImageGeneratorAgent(provider=mock_image)
        result = await agent.execute("生成商品图", session_with_prompts)
        assert len(result.get("images", [])) == 3

    @pytest.mark.asyncio
    async def test_execute_without_provider(self, empty_session):
        """无 Provider 返回 Mock SVG"""
        agent = ImageGeneratorAgent(provider=None)
        result = await agent.execute("生成图片", empty_session)
        assert len(result.get("images", [])) == 3

    @pytest.mark.asyncio
    async def test_fallback_prompt_when_empty(self, mock_image, empty_session):
        """完全没有提示词时使用英文 fallback"""
        agent = ImageGeneratorAgent(provider=mock_image)
        result = await agent.execute("生成图片", empty_session)
        assert len(result.get("images", [])) == 3
