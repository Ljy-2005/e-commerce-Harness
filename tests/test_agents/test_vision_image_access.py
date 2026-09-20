"""审查员 / 合规审查员 的图源解析（A31）

红测试基线：旧实现只读 `base64_data`，而真实 Provider 只回 URL →
`NO_IMAGE_ACCESSIBLE`。这里用 FakeLLM（非 Mock）记录实际发出的 messages。
"""

import base64

import pytest

from src.agents.compliance import ComplianceAgent
from src.agents.reviewer import ReviewerAgent

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


class FakeVisionProvider:
    """记录 messages 的视觉 Provider 替身（非 MockLLMProvider）"""

    name = "fakevision"
    capabilities = ["text", "vision"]

    def __init__(self, result=None):
        self.calls: list[list[dict]] = []
        self._result = result

    async def chat_with_vision(self, messages, model=""):
        self.calls.append(messages)
        if self._result is not None:
            return self._result
        return {"content": {"overall_score": 88, "dimension_scores": {"texture": 88},
                            "verdict": "pass"}, "tokens_used": 10, "cost_usd": 0.0}


def _image_messages(provider) -> list[dict]:
    messages = provider.calls[0]
    user = next(m for m in messages if m["role"] == "user")
    return [p for p in user["content"] if p.get("type") == "image_url"]


class TestReviewerImageAccess:
    @pytest.mark.asyncio
    async def test_url_only_image_reaches_vision_model(self, session_with_prompts):
        """真实 Provider 只回 URL —— 必须能送进模型（旧实现送不进去）"""
        session_with_prompts["artifacts"]["images"] = [
            {"prompt_name": "variant_1", "image_url": "https://cdn.example/a.jpeg"},
        ]
        provider = FakeVisionProvider()
        agent = ReviewerAgent(provider=provider)

        async def fake_download(url):
            return PNG

        import src.harness.vision_payload as vp
        original = vp._download
        vp._download = fake_download
        try:
            result = await agent.execute("审查图片", session_with_prompts)
        finally:
            vp._download = original

        assert "error" not in result
        assert len(_image_messages(provider)) == 1
        assert result["overall_score"] == 88

    @pytest.mark.asyncio
    async def test_base64_image_still_works(self, session_with_prompts):
        session_with_prompts["artifacts"]["images"] = [
            {"prompt_name": "variant_1", "base64_data": base64.b64encode(PNG).decode()},
        ]
        provider = FakeVisionProvider()
        result = await ReviewerAgent(provider=provider).execute("审查图片", session_with_prompts)

        assert "error" not in result
        assert _image_messages(provider)[0]["image_url"]["url"].startswith("data:image/png;base64,")

    @pytest.mark.asyncio
    async def test_no_image_fails_deterministically_without_llm_call(self, session_with_prompts):
        """无图可审：不调用模型、不烧 token、给出可执行原因"""
        session_with_prompts["artifacts"]["images"] = [{"prompt_name": "variant_1"}]
        provider = FakeVisionProvider()
        result = await ReviewerAgent(provider=provider).execute("审查图片", session_with_prompts)

        assert provider.calls == [], "无图时不得再发起 LLM 调用"
        assert result["error"].startswith("NO_IMAGE_ACCESSIBLE")
        assert result["needs_human_review"] is True
        assert result["verdict"] == "retry"

    @pytest.mark.asyncio
    async def test_empty_images_also_fails(self, session_with_prompts):
        """实测事故现场：images 有 3 条但 url/base64 全空"""
        session_with_prompts["artifacts"]["images"] = [
            {"prompt_name": f"variant_{i}", "image_url": "", "base64_data": ""} for i in (1, 2, 3)
        ]
        provider = FakeVisionProvider()
        result = await ReviewerAgent(provider=provider).execute("审查图片", session_with_prompts)

        assert result["error"].startswith("NO_IMAGE_ACCESSIBLE")
        assert provider.calls == []


class TestComplianceImageAccess:
    @pytest.mark.asyncio
    async def test_url_only_image_reaches_vision_model(self, session_with_analysis):
        session_with_analysis["artifacts"]["images"] = [
            {"prompt_name": "variant_1", "image_url": "https://cdn.example/a.jpeg"},
        ]
        provider = FakeVisionProvider(result={"content": {"passed": True, "risk_level": "low"},
                                              "tokens_used": 10, "cost_usd": 0.0})

        import src.harness.vision_payload as vp
        original = vp._download
        async def fake_download(url):
            return PNG
        vp._download = fake_download
        try:
            result = await ComplianceAgent(provider=provider).execute("合规检查", session_with_analysis)
        finally:
            vp._download = original

        assert "error" not in result and result["passed"] is True
        assert len(_image_messages(provider)) == 1

    @pytest.mark.asyncio
    async def test_no_image_reports_error(self, session_with_analysis):
        session_with_analysis["artifacts"]["images"] = []
        provider = FakeVisionProvider()
        result = await ComplianceAgent(provider=provider).execute("合规检查", session_with_analysis)

        assert result["error"].startswith("NO_IMAGE_ACCESSIBLE")
        assert result["passed"] is False
        assert provider.calls == []
