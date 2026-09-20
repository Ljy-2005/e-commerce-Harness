"""ImageGeneratorAgent 单元测试"""

import pytest

from src.agents.image_gen import ImageGeneratorAgent


class FakeImageProvider:
    """真实（非 Mock）生图 Provider 替身

    **不能继承 MockImageProvider** —— `image_gen` 用 `isinstance(provider, MockImageProvider)`
    判定是否走占位图分支，继承会被误判成 Mock 路径，真实调用逻辑就测不到了。
    """

    capabilities = ["image"]

    def __init__(self, name="fakeimg", results=None, default_size=None):
        self.name = name
        if default_size is not None:
            self.default_size = default_size
        self.calls: list[dict] = []
        self._results = list(results or [])

    async def generate(self, prompt, negative_prompt="", size="1024x1024", model=""):
        self.calls.append({"prompt": prompt, "size": size, "model": model})
        if self._results:
            return self._results.pop(0)
        return {"image_url": "https://cdn.example/x.png", "base64_data": "",
                "model_used": model or "fake-model", "cost_usd": 0.04}


class TestImageGeneratorAgent:
    """生图员 — Mock 模式 + 输出结构"""

    def test_mock_returns_svg_placeholder(self):
        """Mock 模式返回 SVG placeholder"""
        agent = ImageGeneratorAgent()
        job = {"name": "variant_1", "slot_id": "", "role": "主图", "prompt": "test prompt"}
        result = agent._mock_image(job, "test prompt", "blur", [])
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


class TestRealProviderPath:
    """真实 Provider 分支（A30：失败必须上报、尺寸必须按路由解析）

    背景（实测事故）：方舟 Seedream 5.0 要求 ≥ 3,686,400 像素，而 image_gen 写死
    `1024x1024` → 上游 400；Agent 又从不检查 `result["error"]` → 记 3 条空图、
    审计 status=ok、协调者据此认为"已生成"，整条链空转。
    """

    @pytest.mark.asyncio
    async def test_provider_error_fails_fast_without_fake_images(self, session_with_prompts):
        provider = FakeImageProvider(name="fakefail", results=[
            {"error": "火山引擎方舟（Ark） API error: 400 — image size must be at least 3686400 pixels"}
        ])
        agent = ImageGeneratorAgent(provider=provider)
        result = await agent.execute("生成商品图", session_with_prompts)

        assert "error" in result, "Provider 报错必须原样上报，不得伪装成成功"
        assert "images" not in result, "失败时不得写入空图记录（会污染 artifacts/协调者判断）"
        assert len(provider.calls) == 1, "首张失败即停，不再继续烧后两张的钱"

    @pytest.mark.asyncio
    async def test_empty_image_payload_is_failure(self, session_with_prompts):
        """200 但既无 url 也无 base64 → 视为失败（此前静默产出空记录）"""
        provider = FakeImageProvider(name="fakeempty", results=[
            {"image_url": "", "base64_data": "", "model_used": "doubao-x", "cost_usd": 0.04}
        ])
        agent = ImageGeneratorAgent(provider=provider)
        result = await agent.execute("生成商品图", session_with_prompts)

        assert "error" in result
        assert "images" not in result

    @pytest.mark.asyncio
    async def test_default_size_from_route_spec(self, session_with_prompts):
        """尺寸用 Provider 的路由默认值（方舟 2048x2048），不再写死 1024x1024"""
        provider = FakeImageProvider(name="fakesize", default_size="2048x2048")
        agent = ImageGeneratorAgent(provider=provider)
        result = await agent.execute("生成商品图", session_with_prompts)

        assert [c["size"] for c in provider.calls] == ["2048x2048"] * 3
        assert {img["generation_params"]["size"] for img in result["images"]} == {"2048x2048"}

    @pytest.mark.asyncio
    async def test_size_falls_back_when_provider_has_no_default(self, session_with_prompts):
        class _Bare:
            name = "fakebare"
            capabilities = ["image"]
            calls: list = []

            async def generate(self, prompt, negative_prompt="", size="", model=""):
                self.calls.append(size)
                return {"image_url": "https://cdn/x.png", "model_used": "m", "cost_usd": 0.0}

        provider = _Bare()
        agent = ImageGeneratorAgent(provider=provider)
        await agent.execute("生成商品图", session_with_prompts)
        assert set(provider.calls) == {"1024x1024"}

    @pytest.mark.asyncio
    async def test_models_yaml_overrides_size_and_variants(self, session_with_prompts, monkeypatch):
        """用户可在 config/models.yaml 覆盖尺寸与张数（config > 路由默认）"""
        import src.core.config as config_mod
        monkeypatch.setattr(config_mod, "load_models_config", lambda: {
            "capabilities": {"image": {"size": "4096x4096", "variants": 1}}
        })
        provider = FakeImageProvider(name="fakecfg", default_size="2048x2048")
        agent = ImageGeneratorAgent(provider=provider)
        result = await agent.execute("生成商品图", session_with_prompts)

        assert len(provider.calls) == 1
        assert provider.calls[0]["size"] == "4096x4096"
        assert len(result["images"]) == 1

    @pytest.mark.asyncio
    async def test_invalid_size_config_falls_back(self, session_with_prompts, monkeypatch):
        """非法尺寸（含注入字符/负数）不得下发给上游"""
        import src.core.config as config_mod
        monkeypatch.setattr(config_mod, "load_models_config", lambda: {
            "capabilities": {"image": {"size": "1024x1024; rm -rf /", "variants": "abc"}}
        })
        provider = FakeImageProvider(name="fakebad", default_size="2048x2048")
        agent = ImageGeneratorAgent(provider=provider)
        result = await agent.execute("生成商品图", session_with_prompts)

        assert provider.calls[0]["size"] == "2048x2048"
        assert len(result["images"]) == 3   # variants 非法 → 回落 3

    @pytest.mark.asyncio
    async def test_cost_and_model_aggregated_for_tracking(self, session_with_prompts):
        """成本/模型名必须回传，否则 _track_cost 拿不到 → 生图成本恒 0（A38）"""
        provider = FakeImageProvider(name="fakecost", results=[
            {"image_url": "https://cdn/1.png", "base64_data": "", "model_used": "doubao-seedream-5-0-260128", "cost_usd": 0.02},
            {"image_url": "https://cdn/2.png", "base64_data": "", "model_used": "doubao-seedream-5-0-260128", "cost_usd": 0.02},
            {"image_url": "https://cdn/3.png", "base64_data": "", "model_used": "doubao-seedream-5-0-260128", "cost_usd": 0.02},
        ])
        agent = ImageGeneratorAgent(provider=provider)
        result = await agent.execute("生成商品图", session_with_prompts)

        assert result["cost_usd"] == pytest.approx(0.06)
        assert result["model_used"] == "doubao-seedream-5-0-260128"
        assert all(img["image_url"] for img in result["images"])
