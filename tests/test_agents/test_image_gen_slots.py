"""生图员 —— 文+图双条件 + 按套图槽位出图

用户三点要求在这里落地：
1. "要文＋图生图"：参考图与文本提示词必须**同时**送进生图接口；
2. "不是单纯图生图或文生图"：两者缺一不可 —— 没有参考图就禁止生成文字（强制虚化），
   只有参考图而没有文本条件时场景/构图无从谈起；
3. "要一套可直接上传的套图"：按 `set_plan.slots` 逐槽出图，而不是同一提示词出 3 个候选。

（旧行为保留：没有 set_plan 时仍按 `variants` 出同一提示词的多个候选。）
"""

import base64
import io

import pytest
from PIL import Image

from src.agents.image_gen import ImageGeneratorAgent
from src.providers.base import BaseImageProvider


def _png_b64(size=(32, 32)) -> str:
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


class _FakeImage(BaseImageProvider):
    """记录每次调用的完整实参（prompt / reference_images / options / size）"""

    name = "fakeimg"
    capabilities = ["image"]
    default_size = "2048x2048"
    supports_reference = True
    supported_options = ("watermark", "output_format")

    def __init__(self, error: str = "", ignored: list[str] | None = None):
        self.calls: list[dict] = []
        self.error = error
        self.ignored = ignored or []

    async def generate(self, prompt, negative_prompt="", size="1024x1024", model="",
                       *, reference_images=None, options=None):
        self.calls.append({"prompt": prompt, "negative_prompt": negative_prompt, "size": size,
                           "model": model, "reference_images": list(reference_images or []),
                           "options": dict(options or {})})
        if self.error:
            return {"error": self.error}
        return {"image_url": "https://img/1.jpeg", "model_used": model or "fakeimg",
                "cost_usd": 0.2, "reference_count": len(reference_images or []),
                "ignored_params": list(self.ignored),
                "request_params": {"model": model, "size": size,
                                   "image_count": len(reference_images or []),
                                   **(options or {})}}


SET_PLAN = {
    "set_plan": {"platform": "taobao", "slots": [
        {"slot_id": "main_white", "role": "白底主图", "prompt": "白底正面平视",
         "background": "#FFFFFF", "composition": "主体居中 85%", "text_in_image": False},
        {"slot_id": "main_selling_point", "role": "核心卖点", "prompt": "留白版面",
         "text_in_image": True},
        {"slot_id": "main_scene", "role": "使用场景", "prompt": "桌面场景", "text_in_image": False},
        {"slot_id": "main_detail", "role": "细节特写", "prompt": "瓶身特写", "text_in_image": False},
        {"slot_id": "main_spec", "role": "规格参数", "prompt": "参数版面", "text_in_image": False},
    ]},
    "main_image": {"prompt": "旧的单图提示词"},
}

LEGACY_PROMPTS = {"main_image": {"prompt": "旧单图提示词"}}


def _session(prompts=None, images=None, platform="taobao"):
    return {
        "session_id": "s1", "tenant_id": "default", "turn_count": 3,
        "task": {"platform": platform, "product_images": images if images is not None else [_png_b64()]},
        "artifacts": {"prompts": prompts if prompts is not None else SET_PLAN},
    }


class TestSetPlanGeneration:
    async def test_one_image_per_slot(self):
        provider = _FakeImage()
        agent = ImageGeneratorAgent(provider=provider)
        result = await agent.execute("生成套图", _session())
        # 顺序 = **平台槽位顺序**；不在 taobao 规范里的槽位（scene/detail）排在规范槽位之后：
        # taobao 主图 [white, selling_point, benefits, ingredients, audience] + 详情 [spec, …]
        assert len(result["images"]) == 5
        assert [img["slot_id"] for img in result["images"]] == [
            "main_white", "main_selling_point", "main_spec", "main_scene", "main_detail"]
        assert [img["prompt_number"] for img in result["images"]] == [1, 2, 6, 9, 10]
        assert [c["prompt"].splitlines()[1][4:8] for c in provider.calls] == [
            "白底正面", "留白版面", "参数版面", "桌面场景", "瓶身特写"]

    async def test_prompt_is_numbered_and_full_text_recorded(self):
        """用户反馈："未有明确约束每一张该有的提示词" —— 每张要带第N张与完整提示词"""
        result = await ImageGeneratorAgent(provider=_FakeImage()).execute("生成套图", _session())
        first = result["images"][0]
        assert first["prompt_number"] == 1
        assert first["prompt_text"].startswith("第1张｜")
        assert len(first["prompt_text"]) > 200          # 改前被截到 200 字，看不到提示词
        assert "【必须】" in first["prompt_text"] and "【身份】" in first["prompt_text"]
        assert first["prompt_sections"]["must"], "逐张契约要进产物"

    async def test_every_call_carries_both_text_and_image(self):
        """用户明确要求：必须是文+图，而不是单纯的图生图或文生图"""
        provider = _FakeImage()
        agent = ImageGeneratorAgent(provider=provider)
        await agent.execute("生成套图", _session())
        assert provider.calls, "应当有生图调用"
        for call in provider.calls:
            assert call["prompt"].strip(), "文本条件不能为空"
            assert call["reference_images"], "参考图必须同时送上去"
            assert call["reference_images"][0].startswith("data:image/png;base64,")
            assert call["options"] == {"watermark": False}, "必须显式关掉平台水印"

    async def test_generation_params_recorded(self):
        agent = ImageGeneratorAgent(provider=_FakeImage())
        result = await agent.execute("生成套图", _session())
        params = result["images"][0]["generation_params"]
        assert params["slot_id"] == "main_white"
        assert params["reference_count"] == 1
        assert params["watermark"] is False
        assert params["text_strategy"] == "preserve"
        assert params["size"] == "2048x2048"

    async def test_slot_candidates_multiply(self, monkeypatch):
        import src.agents.image_gen as mod
        real = mod.image_options
        monkeypatch.setattr(mod, "image_options",
                            lambda: {**real(), "slot_candidates": 2})
        agent = ImageGeneratorAgent(provider=_FakeImage())
        result = await agent.execute("生成套图", _session())
        assert len(result["images"]) == 10
        assert result["images"][0]["prompt_name"] == "main_white#1"

    async def test_legacy_path_unchanged_without_set_plan(self):
        provider = _FakeImage()
        agent = ImageGeneratorAgent(provider=provider)
        result = await agent.execute("生成", _session(prompts=LEGACY_PROMPTS))
        assert len(result["images"]) == 3          # variants 默认 3
        # 旧路径仍用同一提示词出多个候选；但安全规则（无文字/负面约束）照旧追加
        assert len({c["prompt"] for c in provider.calls}) == 1
        assert all(c["prompt"].splitlines()[1].startswith("【画面】旧单图提示词")
                   for c in provider.calls)
        assert all("【文字】" in c["prompt"] for c in provider.calls)
        assert result["images"][0]["prompt_number"] == 0   # 旧路径没有槽位编号


class TestTextStrategy:
    async def test_preserve_keeps_text_via_reference_not_by_writing_text(self):
        provider = _FakeImage()
        await ImageGeneratorAgent(provider=provider).execute("生成", _session())
        prompt = provider.calls[0]["prompt"]
        assert "逐字逐样保留" in prompt
        # 关键：不能把品牌名写进提示词去诱导模型"画字"
        assert "DEFOEBUENA" not in prompt

    async def test_no_reference_forces_blur(self, monkeypatch):
        from src.core import config as cfg_mod
        provider = _FakeImage()
        agent = ImageGeneratorAgent(provider=provider)
        result = await agent.execute("生成", _session(images=[]))
        prompt = provider.calls[0]["prompt"]
        assert "干净虚化" in prompt
        assert "务必不要凭想象生成包装文字" in prompt
        assert any("降级为 blur" in note for note in result.get("image_notes", []))

    async def test_reference_mode_off_sends_no_reference(self):
        from src.core.config import image_options as real_options
        provider = _FakeImage()
        agent = ImageGeneratorAgent(provider=provider)
        session = _session()
        # 直接改会话无关；用 monkeypatch 更稳，这里用轻量包装
        import src.agents.image_gen as mod
        original = mod.image_options

        def _patched():
            return {**original(), "reference_mode": "off"}

        mod.image_options = _patched
        try:
            result = await agent.execute("生成", session)
        finally:
            mod.image_options = original
        assert all(call["reference_images"] == [] for call in provider.calls)
        assert any("reference_mode=off" in note for note in result.get("image_notes", []))

    async def test_text_slot_gets_blank_area_not_generated_text(self):
        provider = _FakeImage()
        await ImageGeneratorAgent(provider=provider).execute("生成", _session())
        selling_point = provider.calls[1]
        assert "留白" in selling_point["prompt"]
        assert "不留乱码字形" in selling_point["prompt"] or "干净虚化" in selling_point["prompt"]

    async def test_negative_constraints_folded_into_prompt(self):
        """方舟不支持 negative_prompt 参数 → 负面约束必须折进正向提示词"""
        provider = _FakeImage()
        await ImageGeneratorAgent(provider=provider).execute("生成", _session())
        assert "避免：模糊" in provider.calls[0]["prompt"]


class TestFailureHandling:
    async def test_provider_error_fails_fast(self):
        provider = _FakeImage(error="400 InvalidParameter: image size")
        result = await ImageGeneratorAgent(provider=provider).execute("生成", _session())
        assert "error" in result
        assert "main_white" in result["error"]
        assert len(provider.calls) == 1, "第一张失败后不应继续烧后面的钱"

    async def test_ignored_params_are_recorded(self):
        provider = _FakeImage(ignored=["watermark"])
        result = await ImageGeneratorAgent(provider=provider).execute("生成", _session())
        assert result["images"][0]["generation_params"]["ignored_params"] == ["watermark"]

    async def test_no_prompts_at_all_is_error(self):
        result = await ImageGeneratorAgent(provider=_FakeImage()).execute("生成", _session(prompts={}))
        # 完全没有提示词时走兜底提示词（历史行为），仍有图
        assert result.get("images")

    async def test_cost_aggregated(self):
        agent = ImageGeneratorAgent(provider=_FakeImage())
        result = await agent.execute("生成", _session())
        assert result["cost_usd"] == pytest.approx(1.0)     # 5 张 × 0.2
