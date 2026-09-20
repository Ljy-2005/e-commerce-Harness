"""自定义服务商 + 火山引擎方舟建模（用户反馈）。

背景（用户原话）：
1. "我无法自己添加模型服务商，这样局限性太大了" —— 路由/凭据槽/模型目录/可用密钥
   此前全硬编码，用户一个都改不了；
2. "即梦不是一个模型提供商吧，它是存在于火山引擎里可调用的模型" —— 正确：即梦是
   字节的消费者产品，Seedream/Seedance 是模型，服务商是**火山引擎方舟（Ark）**
   （OpenAI 兼容 `https://ark.cn-beijing.volces.com/api/v3`）。原 seedream 路由打的
   是旧版视觉智能 CV 签名接口，现降级保留。

覆盖：内置路由表（ark 新增 / seedream 降级）、自定义服务商校验、配置读写、
注册表按路由表实例化（OpenAI / Anthropic 兼容 + 注入端点与密钥）。
"""

import pytest

import src.core.config as config_mod
from src.providers import ProviderRegistry, all_route_specs
from src.providers.routes import BUILTIN_BY_ROUTE, PROVIDER_PRESETS, validate_spec


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    """自定义服务商落到 tmp；清空可能影响可用性的环境变量"""
    monkeypatch.setattr(config_mod, "CUSTOM_PROVIDERS_REL", str(tmp_path / "custom_providers.yaml"))
    monkeypatch.setattr(config_mod, "PROVIDER_CONFIG_REL", str(tmp_path / "providers.yaml"))
    for env in ("OPENAI_API_KEY", "DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY", "DASHSCOPE_API_KEY",
                "ARK_API_KEY", "SEEDREAM_API_KEY", "VOLCANO_ACCESS_KEY", "VOLCANO_SECRET_KEY",
                "BFL_API_KEY", "FAL_KEY", "REPLICATE_API_KEY",
                "ZHIPU_API_KEY", "MOONSHOT_API_KEY", "SILICONFLOW_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    return tmp_path


def _spec(**over):
    base = {
        "route": "zhipu", "label": "智谱 GLM", "kind": "openai",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "api_key_env": "ZHIPU_API_KEY",
        "capabilities": ["text", "vision"], "models": ["glm-4.6", "glm-4v-plus"],
    }
    base.update(over)
    return base


class TestBuiltinRouteTable:
    def test_ark_exists_as_openai_compatible_provider(self):
        """即梦/Seedream 的真正服务商是火山方舟：单 Key + OpenAI 兼容端点"""
        spec = BUILTIN_BY_ROUTE["ark"]

        assert spec.kind == "openai"
        assert spec.default_base_url == "https://ark.cn-beijing.volces.com/api/v3"
        assert spec.key_envs == ("ARK_API_KEY",)
        assert set(spec.capabilities) == {"text", "vision", "image"}
        assert any(m.startswith("doubao-seedream") for m in spec.models), spec.models
        assert "方舟" in spec.label or "Ark" in spec.label

    def test_seedream_is_downgraded_to_legacy_route(self):
        spec = BUILTIN_BY_ROUTE["seedream"]

        assert spec.kind == "volc_cv"
        assert "旧版" in spec.label, "旧 CV 通道要标注清楚，避免与方舟混淆"
        assert spec.base_url_supported is False

    def test_all_builtins_have_label_and_credentials(self):
        for route, spec in BUILTIN_BY_ROUTE.items():
            assert spec.label and spec.key_envs, route
            assert spec.credentials, route

    def test_presets_are_valid_specs(self):
        for preset in PROVIDER_PRESETS:
            _, errors = validate_spec(preset, existing_routes=set(), allow_builtin_override=True)
            assert not errors, f"{preset['route']}: {errors}"

    def test_route_table_merges_custom(self, _isolated_config):
        config_mod.upsert_custom_provider(_spec())

        merged = all_route_specs()

        assert "zhipu" in merged and merged["zhipu"].custom is True
        assert "ark" in merged and merged["ark"].custom is False


class TestSpecValidation:
    def test_valid_spec_normalized(self):
        spec, errors = validate_spec(_spec(route="My_Vendor", label="  测试商  "),
                                     existing_routes=set())

        assert errors == []
        assert spec["route"] == "my_vendor"          # 统一小写
        assert spec["label"] == "测试商"

    @pytest.mark.parametrize("bad,keyword", [
        ({"route": "Ark"}, "已存在"),                 # 与内置重名
        ({"route": "a"}, "route"),
        ({"route": "有中文"}, "route"),
        ({"route": "has space"}, "route"),
        ({"kind": "volc_cv"}, "kind"),
        ({"capabilities": []}, "capabilities"),
        ({"capabilities": ["audio"]}, "capabilities"),
        ({"base_url": "ftp://x/v1"}, "base_url"),
        ({"base_url": "not-a-url"}, "base_url"),
        ({"api_key_env": "1BAD"}, "api_key_env"),
        ({"api_key_env": "has space"}, "api_key_env"),
        ({"label": ""}, "label"),
        ({"models": ["bad model"]}, "模型 id"),
    ])
    def test_invalid_specs_rejected(self, bad, keyword):
        _, errors = validate_spec(_spec(**bad), existing_routes=set())

        assert errors, f"{bad} 应被拒绝"
        assert any(keyword in e for e in errors), errors

    def test_api_key_env_is_uppercased(self):
        """环境变量名大小写统一（用户手填 zhipu_key → ZHIPU_KEY）"""
        spec, errors = validate_spec(_spec(api_key_env="zhipu_key"), existing_routes=set())

        assert errors == []
        assert spec["api_key_env"] == "ZHIPU_KEY"

    def test_duplicate_custom_route_rejected(self):
        _, errors = validate_spec(_spec(), existing_routes={"zhipu"})

        assert any("已存在" in e for e in errors)

    def test_more_than_50_models_rejected(self):
        _, errors = validate_spec(_spec(models=[f"m{i}" for i in range(51)]),
                                  existing_routes=set())

        assert any("50" in e for e in errors)


class TestConfigStorage:
    def test_upsert_remove_roundtrip(self, _isolated_config):
        config_mod.upsert_custom_provider(_spec())
        config_mod.upsert_custom_provider(_spec(route="moonshot", label="Kimi",
                                                api_key_env="MOONSHOT_API_KEY"))

        assert {p["route"] for p in config_mod.load_custom_providers()} == {"zhipu", "moonshot"}

        # 同 route 再写 = 覆盖
        config_mod.upsert_custom_provider(_spec(label="智谱 GLM（改名）"))
        providers = config_mod.load_custom_providers()
        assert len(providers) == 2
        assert next(p for p in providers if p["route"] == "zhipu")["label"] == "智谱 GLM（改名）"

        assert config_mod.remove_custom_provider("zhipu") is True
        assert config_mod.remove_custom_provider("zhipu") is False
        assert [p["route"] for p in config_mod.load_custom_providers()] == ["moonshot"]

    def test_upsert_rejects_invalid(self, _isolated_config):
        with pytest.raises(ValueError):
            config_mod.upsert_custom_provider(_spec(kind="volc_cv"))

    def test_corrupt_file_returns_empty(self, _isolated_config):
        config_mod.custom_providers_path().write_text("providers: [oops", encoding="utf-8")

        assert config_mod.load_custom_providers() == []


class TestRegistryUsesRouteTable:
    def test_custom_provider_available_and_instantiated(self, _isolated_config, monkeypatch):
        config_mod.upsert_custom_provider(_spec())
        monkeypatch.setenv("ZHIPU_API_KEY", "zk-secret")

        registry = ProviderRegistry()

        assert "zhipu" in [m["name"] for m in registry.list_available()]
        provider = registry.get_llm("zhipu")
        assert provider is not None
        assert provider.name == "zhipu"
        assert provider.api_key == "zk-secret"
        assert provider.base_url == "https://open.bigmodel.cn/api/paas/v4"
        assert provider.capabilities == ["text", "vision"]

    def test_custom_anthropic_kind(self, _isolated_config, monkeypatch):
        config_mod.upsert_custom_provider(_spec(route="claude-proxy", label="Claude 中转",
                                                kind="anthropic",
                                                base_url="https://proxy.example.com/v1",
                                                api_key_env="CLAUDE_PROXY_KEY"))
        monkeypatch.setenv("CLAUDE_PROXY_KEY", "cp-secret")

        provider = ProviderRegistry().get_llm("claude-proxy")

        assert provider is not None and provider.name == "claude-proxy"
        assert provider.base_url == "https://proxy.example.com/v1"

    def test_custom_image_route_drops_dalle_specific_params(self, _isolated_config, monkeypatch):
        config_mod.upsert_custom_provider(_spec(route="myimg", label="自建生图", kind="openai",
                                                capabilities=["image"],
                                                api_key_env="MYIMG_KEY"))
        monkeypatch.setenv("MYIMG_KEY", "k")

        provider = ProviderRegistry().get_image("myimg")

        assert provider is not None and provider.name == "myimg"
        assert "quality" in provider.drop_params, "DALL-E 专有参数不能发给兼容端点"
        assert provider.extra_body.get("response_format") == "url"

    def test_ark_uses_openai_compatible_impl(self, _isolated_config, monkeypatch):
        monkeypatch.setenv("ARK_API_KEY", "ark-secret")

        registry = ProviderRegistry()
        llm = registry.get_llm("ark")
        image = registry.get_image("ark")

        assert llm is not None and llm.name == "ark"
        assert llm.base_url == "https://ark.cn-beijing.volces.com/api/v3"
        assert image is not None and image.name == "ark"
        assert "quality" in image.drop_params

    def test_custom_route_participates_in_capability_resolution(self, _isolated_config, monkeypatch):
        """写进 models.yaml 的能力映射能解析到自定义服务商"""
        config_mod.upsert_custom_provider(_spec(route="myimg", label="自建生图",
                                                capabilities=["image"],
                                                api_key_env="MYIMG_KEY"))
        monkeypatch.setenv("MYIMG_KEY", "k")
        # 注册表在模块导入时绑定了 load_models_config，补丁要打在 src.providers 上
        import src.providers as providers_mod
        monkeypatch.setattr(providers_mod, "load_models_config", lambda: {
            "capabilities": {"image": {"default": "myimg/my-image-model"}},
        })

        registry = ProviderRegistry()
        provider, model = registry.resolve(["image"], "")

        assert provider is not None and provider.name == "myimg"
        assert model == "my-image-model"

    def test_unknown_route_returns_none(self, _isolated_config):
        registry = ProviderRegistry()

        assert registry.get_llm("nope") is None
        assert registry.get_image("nope") is None
