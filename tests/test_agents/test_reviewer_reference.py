"""审查/合规必须**拿到用户上传的真实商品图**才能判"还原度"

实测事故：审查员只收到生成图，没有原图 —— "商品还原度"维度没有基准，只能靠常识猜
（那次猜中了被臆造的 `NUTRIVA®`，但不可靠）；合规审查同理。

这里钉住：送审图 = 图一（真实商品图）+ 生成图；并把本地体检的客观数值一起给审查员。
"""

import base64
import io

import pytest
from PIL import Image

from src.agents.compliance import ComplianceAgent
from src.agents.reviewer import ReviewerAgent


def _png_b64(size=(48, 48), color=(180, 40, 40)) -> str:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


class _FakeVision:
    name = "fake-vision"
    capabilities = ["vision", "text"]

    def __init__(self, content=None):
        self.content = content if content is not None else {"overall_score": 80, "verdict": "pass"}
        self.seen: list = []

    async def chat_with_vision(self, messages, **kwargs):
        self.seen = messages
        return {"content": self.content, "tokens_in": 10, "tokens_out": 5,
                "cost_usd": 0.001, "model_used": "fake"}


IDENTITY = {"brand": "DEFOEBUENA®", "product_name": "金裝強力肝迅康", "spec": "60's",
            "certifications": [], "confidence": 0.9, "source": "vision",
            "status": "confirmed", "missing": [], "visible_text": {"lines": []},
            "derived_from": [], "evidence": ""}


def _session(with_reference=True, with_quality=True):
    task = {"platform": "taobao", "product_images": [_png_b64()] if with_reference else []}
    artifacts = {
        "analysis": {"category": "保健品"},
        "product_identity": dict(IDENTITY),
        "images": [{"slot_id": "main_white", "base64_data": _png_b64(color=(20, 120, 200))},
                   {"slot_id": "main_scene", "base64_data": _png_b64(color=(30, 200, 120))}],
    }
    if with_quality:
        artifacts["quality_report"] = {
            "count": 2, "white_bg_ok": False, "watermark_free": True,
            "identity_lost": ["main_white"],
            "issues": ["main_white：背景不是纯白：边缘平均亮度 238 < 250"],
        }
    return {"session_id": "s1", "tenant_id": "default", "turn_count": 2,
            "task": task, "artifacts": artifacts}


def _text_and_images(messages):
    content = messages[1]["content"]
    text = content[0]["text"]
    images = [part for part in content if part.get("type") == "image_url"]
    return text, images


class TestReviewerSeesReference:
    async def test_reference_is_first_image(self):
        provider = _FakeVision()
        session = _session()
        await ReviewerAgent(provider=provider).execute("审查", session)
        text, images = _text_and_images(provider.seen)
        assert len(images) == 3, "应为 1 张原图 + 2 张生成图"
        assert "图一" in text and "真实商品图" in text
        assert "还原度基准" in text or "基准" in text

    async def test_identity_card_and_quality_numbers_attached(self):
        provider = _FakeVision()
        await ReviewerAgent(provider=provider).execute("审查", _session())
        text, _ = _text_and_images(provider.seen)
        assert "DEFOEBUENA®" in text                     # 身份卡（事实基准）
        assert "本地体检数值" in text                     # 客观锚点
        assert "身份相似度过低" in text or "identity" in text
        assert "逐项比对" in text

    async def test_without_reference_still_reviews_but_says_so(self):
        provider = _FakeVision()
        session = _session(with_reference=False)
        result = await ReviewerAgent(provider=provider).execute("审查", session)
        text, images = _text_and_images(provider.seen)
        assert len(images) == 2, "没有原图时只送生成图"
        assert "没有拿到上传的真实商品图" in text
        assert result["reference_compared"] is False

    async def test_no_generated_images_still_blocks(self):
        provider = _FakeVision()
        session = _session()
        session["artifacts"]["images"] = []
        result = await ReviewerAgent(provider=provider).execute("审查", session)
        assert "NO_IMAGE_ACCESSIBLE" in result["error"]
        assert provider.seen == [], "无图可审时不应烧 token"


class TestComplianceSeesReference:
    async def test_reference_included_with_note(self):
        provider = _FakeVision({"passed": True, "risk_level": "low"})
        await ComplianceAgent(provider=provider).execute("合规检查", _session())
        text, images = _text_and_images(provider.seen)
        assert len(images) == 3
        assert "图一" in text
        assert "不存在的品牌" in text
