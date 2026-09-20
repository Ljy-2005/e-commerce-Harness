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


class TestRouteInjectionForPricing:
    """注册表必须把**路由 id** 注入 Provider —— 定价按「路由/模型」查

    没有它，同一模型在不同服务商（官方 / 中转 / 方舟）会被张冠李戴地按同一个价算，
    而且查不到时会去查"任意路由"条目。
    """

    @pytest.fixture(autouse=True)
    def _clear_keys(self, monkeypatch):
        for k in _ALL_KEY_ENVS + ("ARK_API_KEY", "ARK_BASE_URL"):
            monkeypatch.delenv(k, raising=False)

    def test_ark_image_provider_gets_route(self, monkeypatch):
        monkeypatch.setenv("ARK_API_KEY", "ark-test")
        reg = ProviderRegistry()
        provider = reg.get_image("ark")
        assert provider is not None
        assert provider.route == "ark"
        # 未标定 → 不编价（方舟 Seedream 没有可核对的公开价目表）
        assert provider._estimate_cost("doubao-seedream-5-0-260128", "2048x2048") is None

    def test_openai_image_provider_gets_route(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        reg = ProviderRegistry()
        provider = reg.get_image("openai")
        assert provider.route == "openai"
        assert provider._estimate_cost("dall-e-3", "1024x1024") == 0.04

    def test_llm_provider_gets_route(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-test")
        reg = ProviderRegistry()
        assert reg.get_llm("openai").route == "openai"
        # 内置专用实现（deepseek/qwen/anthropic）同样要注入
        assert reg.get_llm("deepseek").route == "deepseek"


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


class TestRouteParamInjection:
    """路由参数注入（A30/A32）：尺寸与输出预算必须从路由表/配置走到 Provider 实例"""

    @pytest.fixture(autouse=True)
    def _clear_keys(self, monkeypatch):
        for k in _ALL_KEY_ENVS:
            monkeypatch.delenv(k, raising=False)

    def test_ark_image_provider_gets_route_default_size(self, monkeypatch):
        monkeypatch.setenv("ARK_API_KEY", "ark-test")
        provider = ProviderRegistry().get_image("ark")
        assert provider is not None
        assert provider.default_size == "2048x2048", "方舟 Seedream 5.0 低于 3,686,400 像素直接 400"

    def test_dall_e_keeps_1024(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        provider = ProviderRegistry().get_image("openai")
        assert provider.default_size == "1024x1024"

    def test_models_yaml_size_beats_route_default(self, monkeypatch):
        monkeypatch.setenv("ARK_API_KEY", "ark-test")
        import src.core.config as cfg
        original = cfg.load_models_config
        monkeypatch.setattr(cfg, "load_models_config",
                            lambda: {"capabilities": {"image": {"size": "4096x4096"}}})
        provider = ProviderRegistry().get_image("ark")
        # 路由默认仍来自路由表，实际生效值由 resolve_image_size 决定（config 优先）
        from src.core.config import resolve_image_size
        assert resolve_image_size(provider.default_size) == "4096x4096"
        monkeypatch.setattr(cfg, "load_models_config", original)

    def test_deepseek_max_tokens_from_route_table(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds")
        provider = ProviderRegistry().get_llm("deepseek")
        assert provider.max_tokens == 16384, "推理模型 4096 会被思考 token 吃光"

    def test_providers_yaml_overrides_max_tokens(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds")
        import src.core.config as cfg
        monkeypatch.setattr(cfg, "PROVIDER_CONFIG_REL", str(tmp_path / "providers.yaml"))
        (tmp_path / "providers.yaml").write_text(
            "deepseek:\n  max_tokens: 8192\n", encoding="utf-8")
        provider = ProviderRegistry().get_llm("deepseek")
        assert provider.max_tokens == 8192

    def test_invalid_max_tokens_override_ignored(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds")
        import src.core.config as cfg
        monkeypatch.setattr(cfg, "PROVIDER_CONFIG_REL", str(tmp_path / "providers.yaml"))
        (tmp_path / "providers.yaml").write_text(
            "deepseek:\n  max_tokens: -5\n", encoding="utf-8")
        assert ProviderRegistry().get_llm("deepseek").max_tokens == 16384


class _RecordingLogger:
    """记录日志调用（项目 logger 不向 root 传播，caplog 抓不到）"""

    def __init__(self):
        self.warnings: list[str] = []

    def warning(self, message, *args, **kwargs):
        self.warnings.append(str(message) % args if args else str(message))


class TestOverrideMismatchWarning:
    """A41：覆盖键与 requires 不匹配时留日志（用户"配了没反应"的最常见原因）"""

    def _registry(self, monkeypatch, overrides):
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        reg = ProviderRegistry()
        reg._models_config = {"capabilities": {"text": {"default": "mock"}},
                              "agent_overrides": overrides}
        return reg

    def _patch_logger(self, monkeypatch):
        import src.core.logging_config as logging_config
        recorder = _RecordingLogger()
        monkeypatch.setattr(logging_config, "get_logger", lambda name: recorder)
        return recorder

    def test_mismatched_capability_logs_warning(self, monkeypatch):
        import src.providers as providers_mod
        providers_mod._WARNED_OVERRIDE_MISMATCH.clear()
        recorder = self._patch_logger(monkeypatch)
        reg = self._registry(monkeypatch, {"审查员": {"text": "deepseek/deepseek-v4-flash"}})

        reg.resolve(["vision"], agent_name="审查员")

        assert any("覆盖不生效" in w for w in recorder.warnings), recorder.warnings

    def test_warning_is_only_logged_once(self, monkeypatch):
        import src.providers as providers_mod
        providers_mod._WARNED_OVERRIDE_MISMATCH.clear()
        recorder = self._patch_logger(monkeypatch)
        reg = self._registry(monkeypatch, {"审查员": {"text": "deepseek/deepseek-v4-flash"}})

        reg.resolve(["vision"], agent_name="审查员")
        reg.resolve(["vision"], agent_name="审查员")

        assert len(recorder.warnings) == 1, "同一组合不应每次 resolve 都刷屏"

    def test_matching_capability_does_not_warn(self, monkeypatch):
        import src.providers as providers_mod
        providers_mod._WARNED_OVERRIDE_MISMATCH.clear()
        recorder = self._patch_logger(monkeypatch)
        reg = self._registry(monkeypatch, {"审查员": {"vision": "mock/x"}})

        reg.resolve(["vision"], agent_name="审查员")

        assert recorder.warnings == []
