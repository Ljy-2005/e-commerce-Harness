"""Mock Provider 测试"""

import pytest

from src.providers.mock import MockImageProvider, MockLLMProvider


class TestMockLLMProvider:
    @pytest.mark.asyncio
    async def test_chat_returns_neutral_analysis_for_keywords(self):
        """演示数据必须"中性 + 显式标记"：不得写死具体商品事实（用户实测质疑"会不会串到别的牌子"）"""
        p = MockLLMProvider()
        result = await p.chat([
            {"role": "system", "content": "分析商品"},
        ])
        assert "content" in result
        content = result["content"]
        assert content.get("is_mock") is True
        assert content["product_identity"]["source"] == "mock"
        assert content["product_identity"]["brand"] == ""
        for forbidden in ("水飞蓟", "护肝", "蓝帽", "解酒"):
            assert forbidden not in str(content), f"演示数据里仍有具体商品事实：{forbidden}"

    @pytest.mark.asyncio
    async def test_chat_with_vision_returns_neutral_analysis(self):
        p = MockLLMProvider()
        result = await p.chat_with_vision([
            {"role": "user", "content": [{"type": "text", "text": "分析这张保健品图片"}]},
        ])
        content = result["content"]
        assert content["is_mock"] is True
        assert "未识别" in content["category"]

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
