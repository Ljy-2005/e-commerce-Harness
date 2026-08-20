"""新 Provider 基础测试（Mock 模式下验证接口契约）"""

import pytest

from src.providers import ProviderRegistry

_ALL_KEY_ENVS = (
    "ANTHROPIC_API_KEY", "SEEDREAM_API_KEY", "DASHSCOPE_API_KEY",
    "BFL_API_KEY", "FAL_KEY", "REPLICATE_API_KEY",
    "VOLCANO_ACCESS_KEY", "VOLCANO_SECRET_KEY",
    "OPENAI_API_KEY", "DEEPSEEK_API_KEY",
)


class TestProviderAutoDetection:
    """审计修复：此前 4 个探测测试零断言/恒真断言（_ProviderMeta("x", True, ...)），
    真正的 os.getenv 探测分支零覆盖；现用 monkeypatch + 真实 ProviderRegistry 断言"""

    @pytest.fixture(autouse=True)
    def _clear_keys(self, monkeypatch):
        for k in _ALL_KEY_ENVS:
            monkeypatch.delenv(k, raising=False)

    def test_anthropic_detected_with_key(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        reg = ProviderRegistry()
        assert reg._meta["anthropic"].available is True
        assert any(p["name"] == "anthropic" for p in reg.list_available())

    def test_qwen_detected_with_key(self, monkeypatch):
        monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
        reg = ProviderRegistry()
        assert reg._meta["qwen"].available is True
        assert any(p["name"] == "qwen" for p in reg.list_available())

    def test_seedream_detected_with_volc_keys(self, monkeypatch):
        monkeypatch.setenv("VOLCANO_ACCESS_KEY", "ak")
        monkeypatch.setenv("VOLCANO_SECRET_KEY", "sk")
        reg = ProviderRegistry()
        assert reg._meta["seedream"].available is True

    def test_seedream_not_detected_with_partial_volc(self, monkeypatch):
        monkeypatch.setenv("VOLCANO_ACCESS_KEY", "ak")  # 缺 SECRET_KEY
        reg = ProviderRegistry()
        assert "seedream" not in reg._meta or reg._meta["seedream"].available is False

    def test_flux_detected_with_bfl_key(self, monkeypatch):
        monkeypatch.setenv("BFL_API_KEY", "test-key")
        reg = ProviderRegistry()
        assert reg._meta["flux"].available is True

    def test_no_keys_only_mock(self):
        reg = ProviderRegistry()
        names = {p["name"] for p in reg.list_available()}
        assert names == {"mock"}  # 无任何 Key → 仅 Mock 可用


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
