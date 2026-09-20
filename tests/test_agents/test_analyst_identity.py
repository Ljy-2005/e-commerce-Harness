"""商品分析员 —— 必须识别品牌/商品名，并且不得把 mock 数据当成事实

用户反馈："产品分析员根本没有识别到我喂的图是什么品牌，商品名是什么都没强调或者提醒"。
实测该 Agent 的提示词 schema 里 10 个字段全是品类/成分/卖点，没有品牌也没有商品名。

同时钉住两条防污染规则：
1. 上传图的 MIME 必须按魔数嗅探（实测上传的是 PNG，而代码写死 `data:image/jpeg`）；
2. Mock Provider（或 Provider 正常但没给 content）产出的分析**必须标记来源**，
   不能让它冒充"本次商品的真实分析"（用户担心的"套成别的牌子"）。
"""

import base64
import io

import pytest
from PIL import Image

from src.agents.analyst import ProductAnalystAgent
from src.providers.mock import MOCK_ANALYSIS, MockLLMProvider


def _png_base64(size=(48, 48), color=(200, 30, 30)) -> str:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


class _FakeVision:
    """记录收到的 messages，返回解析后的 JSON（模拟真实 Provider 的 parse_completion）"""

    name = "fake-vision"
    capabilities = ["vision", "text"]

    def __init__(self, payload: str = "", error: str = ""):
        from src.providers.json_parse import parse_json_loose
        self.parsed = parse_json_loose(payload) if payload else None
        self.raw = payload
        self.error = error
        self.seen: list = []

    async def chat_with_vision(self, messages, **kwargs):
        self.seen = messages
        if self.error:
            return {"error": self.error}
        if isinstance(self.parsed, dict):
            content = self.parsed
        else:
            content = {"text": self.raw} if self.raw else None   # 空内容 = Provider 没给业务内容
        return {"content": content, "tokens_in": 10, "tokens_out": 5,
                "cost_usd": 0.001, "model_used": "fake"}

    async def chat(self, messages, **kwargs):  # pragma: no cover - 不应被调用
        raise AssertionError("分析员应走 chat_with_vision")


@pytest.fixture
def session_factory():
    def make(b64: str):
        return {"session_id": "s1", "tenant_id": "default", "turn_count": 1,
                "task": {"product_images": [b64], "platform": "taobao"},
                "artifacts": {}}
    return make


VISION_JSON = """{
  "category": "保健品",
  "product_identity": {
    "brand": "DEFOEBUENA®", "product_name": "金裝強力肝迅康", "spec": "60's",
    "certifications": ["德國 GMP 優質產品"], "confidence": 0.93,
    "evidence": "正面金色燙印條"
  },
  "visible_text": {"lines": [{"text": "金裝強力肝迅康", "location": "正面", "legible": true}],
                   "language": "zh-Hant", "has_illlegible": false}
}"""


class TestAnalystIdentity:
    async def test_real_analysis_carries_confirmed_identity(self, session_factory):
        agent = ProductAnalystAgent(provider=_FakeVision(VISION_JSON))
        result = await agent.execute("分析商品", session_factory(_png_base64()))
        identity = result["product_identity"]
        assert identity["brand"] == "DEFOEBUENA®"
        assert identity["status"] == "confirmed"
        assert identity["source"] == "vision"
        assert result["identity_status"] == "confirmed"
        assert "is_mock" not in result

    async def test_analysis_without_identity_is_uncertain(self, session_factory):
        agent = ProductAnalystAgent(provider=_FakeVision('{"category": "保健品"}'))
        result = await agent.execute("分析商品", session_factory(_png_base64()))
        assert result["identity_status"] == "uncertain"
        assert "品牌" in result["product_identity"]["missing"]

    async def test_png_upload_is_sent_with_sniffed_mime(self, session_factory):
        provider = _FakeVision(VISION_JSON)
        agent = ProductAnalystAgent(provider=provider)
        await agent.execute("分析商品", session_factory(_png_base64()))
        user_message = provider.seen[1]          # messages = [system, user]
        parts = user_message["content"]
        urls = [p["image_url"]["url"] for p in parts if p.get("type") == "image_url"]
        assert urls and all(url.startswith("data:image/png") for url in urls), \
            "实测上传图是 PNG，写死 image/jpeg 会让视觉模型收到错误 MIME"

    async def test_provider_error_is_reported_not_replaced_by_mock(self, session_factory):
        agent = ProductAnalystAgent(provider=_FakeVision(error="429 限流"))
        result = await agent.execute("分析商品", session_factory(_png_base64()))
        assert "error" in result
        assert "product_identity" not in result

    async def test_mock_provider_marks_demo_data_and_never_confirms(self, session_factory):
        agent = ProductAnalystAgent(provider=MockLLMProvider())
        result = await agent.execute("分析商品", session_factory(_png_base64()))
        assert result.get("is_mock") is True
        assert result["identity_status"] == "uncertain"
        assert result["product_identity"]["source"] == "mock"

    async def test_provider_without_content_is_treated_as_demo_data(self, session_factory):
        """Provider 正常但没给 content：不能再"看起来成功"，必须显式标为演示数据"""
        agent = ProductAnalystAgent(provider=_FakeVision(""))
        result = await agent.execute("分析商品", session_factory(_png_base64()))
        assert result.get("is_mock") is True
        assert result["identity_status"] == "uncertain"


class TestMockAnalysisIsNeutral:
    """MOCK_ANALYSIS 不得写死具体商品事实（用户："这不会直接影响到我生产其他牌子的商品吗"）"""

    def test_mock_analysis_has_no_brand_or_ingredient_facts(self):
        text = str(MOCK_ANALYSIS)
        for forbidden in ("水飞蓟", "护肝", "蓝帽", "解酒", "五味子", "蒲公英"):
            assert forbidden not in text, f"MOCK_ANALYSIS 里仍有具体商品事实：{forbidden}"

    def test_mock_analysis_is_marked(self):
        assert MOCK_ANALYSIS.get("is_mock") is True
        assert MOCK_ANALYSIS.get("product_identity", {}).get("source") == "mock"

    def test_mock_analysis_keeps_schema_keys(self):
        """字段名保持稳定，避免大面积改测试与前端"""
        for key in ("category", "ingredients", "features", "target_audience",
                    "style_constraints", "special_constraints", "marketing_angles",
                    "confidence_score"):
            assert key in MOCK_ANALYSIS, f"缺少字段 {key}"
