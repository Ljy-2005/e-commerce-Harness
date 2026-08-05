"""Mock Provider 测试"""

import pytest
from src.providers.mock import MockLLMProvider, MockImageProvider


class TestMockLLMProvider:
    @pytest.mark.asyncio
    async def test_chat_returns_analysis_for_keywords(self):
        p = MockLLMProvider()
        result = await p.chat([
            {"role": "system", "content": "分析商品"},
        ])
        assert "content" in result
        content = result["content"]
        assert content.get("category") == "保健品"

    @pytest.mark.asyncio
    async def test_chat_with_vision_returns_analysis(self):
        p = MockLLMProvider()
        result = await p.chat_with_vision([
            {"role": "user", "content": [{"type": "text", "text": "分析这张保健品图片"}]},
        ])
        assert result["content"]["category"] == "保健品"

    @pytest.mark.asyncio
    async def test_chat_returns_review_for_keywords(self):
        p = MockLLMProvider()
        result = await p.chat([
            {"role": "system", "content": "审查评分"},
        ])
        assert result["content"]["verdict"] == "pass"

    @pytest.mark.asyncio
    async def test_chat_returns_compliance_for_keywords(self):
        p = MockLLMProvider()
        result = await p.chat([
            {"role": "system", "content": "合规检查"},
        ])
        assert result["content"]["passed"] is True

    @pytest.mark.asyncio
    async def test_mock_cost_is_zero(self):
        p = MockLLMProvider()
        result = await p.chat([{"role": "user", "content": "test"}])
        assert result["cost_usd"] == 0.0


class TestMockImageProvider:
    @pytest.mark.asyncio
    async def test_generate_returns_svg_placeholder(self):
        p = MockImageProvider()
        result = await p.generate("test prompt")
        assert "image_url" in result
        assert "data:image/svg+xml;base64," in result["image_url"]
        assert result["cost_usd"] == 0.0
