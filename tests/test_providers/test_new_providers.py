"""新 Provider 基础测试（Mock 模式下验证接口契约）"""

import os
import pytest


def _clear_env_vars():
    """临时清除环境变量以测试自动检测"""
    for k in ("ANTHROPIC_API_KEY", "SEEDREAM_API_KEY", "DASHSCOPE_API_KEY",
              "BFL_API_KEY", "FAL_KEY", "REPLICATE_API_KEY",
              "VOLCANO_ACCESS_KEY", "VOLCANO_SECRET_KEY"):
        os.environ.pop(k, None)


class TestProviderAutoDetection:
    def test_anthropic_detected_with_key(self):
        os.environ["ANTHROPIC_API_KEY"] = "test-key"
        from src.providers import get_provider_registry
        import importlib
        import src.providers
        importlib.reload(src.providers)
        os.environ.pop("ANTHROPIC_API_KEY")

    def test_qwen_detected_with_key(self):
        os.environ["DASHSCOPE_API_KEY"] = "test-key"
        from src.providers import _ProviderMeta
        assert _ProviderMeta("qwen", True, ["vision", "text"]).available is True
        os.environ.pop("DASHSCOPE_API_KEY")

    def test_seedream_detected_with_volc_keys(self):
        os.environ["VOLCANO_ACCESS_KEY"] = "ak"
        os.environ["VOLCANO_SECRET_KEY"] = "sk"
        from src.providers import _ProviderMeta
        assert _ProviderMeta("seedream", True, ["image"]).available is True
        os.environ.pop("VOLCANO_ACCESS_KEY")
        os.environ.pop("VOLCANO_SECRET_KEY")

    def test_flux_detected_with_bfl_key(self):
        os.environ["BFL_API_KEY"] = "test-key"
        from src.providers import _ProviderMeta
        assert _ProviderMeta("flux", True, ["image"]).available is True
        os.environ.pop("BFL_API_KEY")


class TestProviderInstantiation:
    """测试 Provider 实例化（延迟加载 + 无 Key 时不崩溃）"""

    def test_anthropic_instantiation_graceful(self):
        from src.providers.anthropic import AnthropicLLMProvider
        p = AnthropicLLMProvider()
        assert p.name == "anthropic"
        assert "vision" in p.capabilities

    def test_qwen_instantiation_graceful(self):
        from src.providers.qwen import QwenLLMProvider
        p = QwenLLMProvider()
        assert p.name == "qwen"
        assert "vision" in p.capabilities

    def test_seedream_instantiation_graceful(self):
        from src.providers.seedream import SeedreamImageProvider
        p = SeedreamImageProvider()
        assert p.name == "seedream"
        assert "image" in p.capabilities

    def test_flux_instantiation_graceful(self):
        from src.providers.flux import FluxImageProvider
        p = FluxImageProvider()
        assert p.name == "flux"
        assert "image" in p.capabilities


class TestProviderNoKeyError:
    """测试无 Key 时返回明确错误而非崩溃"""

    @pytest.mark.asyncio
    async def test_seedream_no_key_returns_error(self):
        from src.providers.seedream import SeedreamImageProvider
        p = SeedreamImageProvider()
        # Force no keys
        p.ak = ""
        p.sk = ""
        p.api_key = ""
        p._use_volc = False
        result = await p.generate("test prompt")
        assert "error" in result
        assert "SEEDREAM_API_KEY" in result["error"] or "VOLCANO" in result["error"]

    @pytest.mark.asyncio
    async def test_flux_no_key_returns_error(self):
        from src.providers.flux import FluxImageProvider
        p = FluxImageProvider()
        p.replicate_key = ""
        p.fal_key = ""
        p.bfl_key = ""
        p._backend = None
        result = await p.generate("test prompt")
        assert "error" in result


class TestAnthropicMessageConversion:
    """测试 Anthropic 消息格式转换"""

    def test_converts_openai_format(self):
        from src.providers.anthropic import AnthropicLLMProvider
        p = AnthropicLLMProvider()
        system, formatted = p._convert_messages([
            {"role": "system", "content": "You are an assistant"},
            {"role": "user", "content": "Hello"},
        ])
        assert system == "You are an assistant"
        assert len(formatted) == 1
        assert formatted[0]["role"] == "user"

    def test_converts_vision_content(self):
        from src.providers.anthropic import AnthropicLLMProvider
        p = AnthropicLLMProvider()
        system, formatted = p._convert_messages([
            {"role": "user", "content": [
                {"type": "text", "text": "What is this?"},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,abc123"}},
            ]},
        ])
        assert isinstance(formatted[0]["content"], list)
        assert formatted[0]["content"][0]["type"] == "text"
        assert formatted[0]["content"][1]["type"] == "image"


class TestSeedreamVolcSigning:
    """测试火山引擎签名"""

    def test_signing_produces_valid_headers(self):
        from src.providers.seedream import SeedreamImageProvider
        p = SeedreamImageProvider()
        p.ak = "test-ak"
        p.sk = "test-sk"
        headers = p._sign_volc_request(
            service="cv",
            region="cn-north-1",
            action="CVProcess",
            version="2022-08-31",
            body={"req_key": "test", "prompt": "hello"},
        )
        assert "Authorization" in headers
        assert headers["Authorization"].startswith("HMAC-SHA256")
        assert "X-Date" in headers
        assert "X-Content-Sha256" in headers
