"""生图员：信息类槽位 = 模型出无字底图 + **本地排版**文字层

用户原话："为什么生成的全是白背景＋商品的图……我要一套可以实际使用的图片，
例如『纯商品图+成分图+商品面向人群图片+商品效果列举图』"。

这里钉住三件事：
1. 纯摄影槽位（纯商品/场景/特写）行为不变；
2. 信息类槽位先出底图、再本地排版合成（合成前那张底图**不含任何文字**）；
3. 文案缺事实依据（如成分表不可见）→ 标记 `text_status=blocked` + 原因，
   **绝不用模型编造的成分/功效**，且该槽位不计入"成套"。
"""

import base64
import io

import pytest
from PIL import Image

from src.agents.image_gen import ImageGeneratorAgent
from src.providers.base import BaseImageProvider


def _png_b64(size=(256, 256), color=(240, 240, 245)) -> str:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


class _FakeImage(BaseImageProvider):
    name, capabilities = "fakeimg", ["image"]
    default_size = "2048x2048"
    supports_reference, supported_options = True, ("watermark",)

    def __init__(self):
        self.calls: list[dict] = []

    async def generate(self, prompt, negative_prompt="", size="1024x1024", model="",
                       *, reference_images=None, options=None):
        self.calls.append({"prompt": prompt, "size": size})
        return {"base64_data": _png_b64(), "image_url": "",
                "model_used": "fake", "cost_usd": 0.04,
                "reference_count": len(reference_images or []), "ignored_params": [],
                "request_params": {"size": size}}

    async def chat(self, *a, **k):  # pragma: no cover
        raise AssertionError


IDENTITY = {"status": "confirmed", "source": "vision", "brand": "DEFOEBUENA®",
            "product_name": "金裝強力肝迅康", "spec": "60's",
            "certifications": ["德國GMP優質產品"], "confidence": 0.93}
ANALYSIS = {
    "features": ["標示「升級版」", "正面以肝臟解剖圖為核心視覺"],
    "ingredients": ["本次圖片僅見正面，未見成分表 → 不可見/待確認（嚴禁臆造）"],
    "target_audience": {"age": "25–55 歲", "lifestyle": "熬夜加班、應酬飲酒"},
    "marketing_angles": {"selling_points": ["德國來源標示", "德國GMP優質產品標示"]},
}

SLOTS = [
    {"slot_id": "main_white", "prompt": "白底商品照", "background": "#FFFFFF"},
    {"slot_id": "main_selling_point", "prompt": "卖点版面底图，左上留白", "background": "#FFFFFF"},
    {"slot_id": "main_benefits", "prompt": "功效版面底图", "background": "#FFFFFF"},
    {"slot_id": "main_ingredients", "prompt": "成分配方版面底图", "background": "#FFFFFF"},
    {"slot_id": "main_audience", "prompt": "人群版面底图", "background": "#FFFFFF"},
]


def _session():
    return {"session_id": "s1", "tenant_id": "default", "turn_count": 2,
            "task": {"platform": "taobao", "product_images": [_png_b64((32, 32))]},
            "artifacts": {"prompts": {"set_plan": {"platform": "taobao", "slots": SLOTS}},
                          "product_identity": dict(IDENTITY), "analysis": dict(ANALYSIS)}}


class TestInfoSlotComposition:
    async def test_photo_slot_is_untouched(self):
        agent = ImageGeneratorAgent(provider=_FakeImage())
        result = await agent.execute("出图", _session())
        white = next(img for img in result["images"] if img["slot_id"] == "main_white")
        assert "text_status" not in white
        assert white["processing_status"] == "raw"

    async def test_info_slot_is_composed_locally_with_text(self):
        agent = ImageGeneratorAgent(provider=_FakeImage())
        result = await agent.execute("出图", _session())
        selling = next(img for img in result["images"] if img["slot_id"] == "main_selling_point")
        assert selling["text_status"] == "composed"
        assert selling["processing_status"] == "info_composed"
        assert selling["compose"]["ok"] is True
        assert selling["compose"]["items"] >= 1
        assert selling["slot_copy"]["items"], "应带上实际绘制的文案，便于人工核对"
        # 交付物是**本地合成的图**（底图 URL 另存，供设计师重排）
        assert selling["base64_data"] != _png_b64()
        assert selling["generation_params"]["kind"] == "info"

    async def test_info_without_facts_is_blocked_not_faked(self):
        """成分表不可见 → 成分图必须 blocked（绝不让模型编造成分）"""
        agent = ImageGeneratorAgent(provider=_FakeImage())
        result = await agent.execute("出图", _session())
        ingredients = next(img for img in result["images"] if img["slot_id"] == "main_ingredients")
        assert ingredients["text_status"] == "blocked"
        assert ingredients["text_reason"]
        assert "成分" in ingredients["text_reason"] or "背面" in ingredients["text_reason"]
        assert ingredients["slot_copy"]["items"] == []

    async def test_prompt_tells_model_to_leave_room_not_draw_text(self):
        provider = _FakeImage()
        agent = ImageGeneratorAgent(provider=provider)
        await agent.execute("出图", _session())
        for call in provider.calls:
            assert "不要生成任何可辨认的文字" in call["prompt"] or \
                   "不要生成文字" in call["prompt"] or "干净虚化" in call["prompt"] or \
                   "逐字逐样保留" in call["prompt"]

    async def test_coverage_marks_blocked_slot_as_not_done(self):
        from src.harness.set_plan import normalize_set_plan, set_plan_coverage

        agent = ImageGeneratorAgent(provider=_FakeImage())
        result = await agent.execute("出图", _session())
        plan = normalize_set_plan(_session()["artifacts"]["prompts"], platform="taobao")
        coverage = set_plan_coverage(plan, result["images"])
        assert "main_ingredients" in coverage["blocked_slots"]
        assert "main_ingredients" not in coverage["done_slots"]
        assert coverage["complete"] is False

    async def test_blocked_reason_is_actionable(self):
        agent = ImageGeneratorAgent(provider=_FakeImage())
        result = await agent.execute("出图", _session())
        blocked = [img for img in result["images"] if img.get("text_status") == "blocked"]
        assert blocked
        for img in blocked:
            assert "请" in img["text_reason"] or "需要" in img["text_reason"]
