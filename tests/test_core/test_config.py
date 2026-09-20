"""config 边界测试（test-plan P3 L1）

覆盖 models.yaml 缺文件/坏 YAML、未知 provider 兜底、agent_overrides 优先级，
以及 CORS / 密钥持久化 / Mock 检测的环境边界（全部经 _project_root 注入 tmp 隔离）。
"""

import os

import pytest
import yaml

from src.core import config as cfg_mod
from src.providers import ProviderRegistry


@pytest.fixture
def fake_root(tmp_path, monkeypatch):
    """把配置根目录重定向到 tmp（不触碰真实 config/）"""
    monkeypatch.setattr(cfg_mod, "_project_root", lambda: tmp_path)
    return tmp_path


@pytest.fixture(autouse=True)
def _restore_secret_env():
    """快照-还原密钥类环境变量与"env 供给"记录集合。

    必要性：① monkeypatch 对**同一变量**重复 setenv/delenv 时只保留最后一次记录，
    而 apply_runtime_secrets_to_env() 会直接写入 os.environ —— 该值不会被
    monkeypatch 还原；② 该函数还会向模块级 `_ENV_PROVIDED_SECRETS` **原地追加**
    （替换整个属性也拦不住）。两者都会泄漏到后续测试（实测导致 test_settings
    中被误判为"环境变量供给"而 403 / source 判定错误）。
    """
    keys = ["OPENAI_API_KEY", "DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY",
            "SEEDREAM_API_KEY", "VOLCANO_ACCESS_KEY", "VOLCANO_SECRET_KEY"]
    snapshot = {k: os.environ.get(k) for k in keys}
    marked_snapshot = set(cfg_mod._ENV_PROVIDED_SECRETS)
    yield
    for k, v in snapshot.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    cfg_mod._ENV_PROVIDED_SECRETS.clear()
    cfg_mod._ENV_PROVIDED_SECRETS.update(marked_snapshot)


class TestLoadBoundary:
    def test_missing_yaml_returns_empty(self, fake_root):
        assert cfg_mod.load_yaml("config/models.yaml") == {}
        assert cfg_mod.load_models_config() == {}

    def test_bad_yaml_raises(self, fake_root):
        (fake_root / "config").mkdir()
        (fake_root / "config" / "models.yaml").write_text("key: [unclosed", encoding="utf-8")
        with pytest.raises(yaml.YAMLError):
            cfg_mod.load_models_config()

    def test_list_agent_configs_missing_dir_empty(self, fake_root):
        assert cfg_mod.list_agent_configs() == []

    def test_list_agent_configs_skips_underscore(self, fake_root):
        agents = fake_root / "config" / "agents"
        agents.mkdir(parents=True)
        (agents / "a.yaml").write_text("name: a", encoding="utf-8")
        (agents / "_secret.yaml").write_text("name: s", encoding="utf-8")
        (agents / "b.txt").write_text("x", encoding="utf-8")
        assert cfg_mod.list_agent_configs() == ["a"]

    def test_load_yaml_non_dict_content(self, fake_root):
        """YAML 解析出非 dict（如列表）→ 调用方按 {} 处理不崩溃"""
        (fake_root / "config").mkdir()
        (fake_root / "config" / "secrets.yaml").write_text("- a\n- b", encoding="utf-8")
        assert cfg_mod.load_runtime_secrets() == {}


class TestCorsOrigins:
    def test_env_override_wins(self, monkeypatch):
        monkeypatch.setenv("ECOMM_CORS_ORIGINS", "http://a.example, http://b.example")
        assert cfg_mod.get_cors_origins() == ["http://a.example", "http://b.example"]

    def test_env_blank_falls_to_yaml(self, fake_root, monkeypatch):
        monkeypatch.delenv("ECOMM_CORS_ORIGINS", raising=False)
        (fake_root / "config").mkdir()
        (fake_root / "config" / "default.yaml").write_text(
            "app:\n  cors_origins: ['http://yaml.example']\n", encoding="utf-8"
        )
        assert cfg_mod.get_cors_origins() == ["http://yaml.example"]

    def test_env_blank_commas_falls_to_yaml(self, fake_root, monkeypatch):
        """env 只有空段 → 视为未配置，落到 YAML"""
        monkeypatch.setenv("ECOMM_CORS_ORIGINS", "  , ,")
        (fake_root / "config").mkdir()
        (fake_root / "config" / "default.yaml").write_text(
            "app:\n  cors_origins: ['http://yaml.example']\n", encoding="utf-8"
        )
        assert cfg_mod.get_cors_origins() == ["http://yaml.example"]

    def test_default_fallback(self, fake_root, monkeypatch):
        monkeypatch.delenv("ECOMM_CORS_ORIGINS", raising=False)
        assert cfg_mod.get_cors_origins() == cfg_mod.DEFAULT_CORS_ORIGINS


class TestRuntimeSecrets:
    def test_save_load_roundtrip_and_delete(self, fake_root):
        cfg_mod.save_runtime_secrets({"OPENAI_API_KEY": "sk-abc"})
        assert cfg_mod.load_runtime_secrets() == {"OPENAI_API_KEY": "sk-abc"}
        cfg_mod.save_runtime_secrets({"OPENAI_API_KEY": ""})  # 空值 = 删除
        assert cfg_mod.load_runtime_secrets() == {}

    def test_apply_respects_existing_env(self, fake_root, monkeypatch):
        """env 显式设置的值优先于持久化文件（防'改了不生效'陷阱）"""
        monkeypatch.setenv("DEEPSEEK_API_KEY", "env-value")
        cfg_mod.save_runtime_secrets({"DEEPSEEK_API_KEY": "file-value"})
        assert cfg_mod.apply_runtime_secrets_to_env() == 0  # 注入 0 个
        assert os.environ["DEEPSEEK_API_KEY"] == "env-value"
        monkeypatch.delenv("DEEPSEEK_API_KEY")

    def test_apply_injects_missing(self, fake_root, monkeypatch):
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        cfg_mod.save_runtime_secrets({"DEEPSEEK_API_KEY": "file-value"})
        assert cfg_mod.apply_runtime_secrets_to_env() == 1
        assert os.environ["DEEPSEEK_API_KEY"] == "file-value"
        monkeypatch.delenv("DEEPSEEK_API_KEY")


class TestSecretKeySource:
    """P7：密钥来源判定（设置页据此锁定 env 供给的密钥行）"""

    def test_unconfigured_returns_empty(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        assert cfg_mod.secret_key_source("OPENAI_API_KEY") == ""

    def test_env_provided_marked_env(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
        monkeypatch.setattr(cfg_mod, "_ENV_PROVIDED_SECRETS", {"OPENAI_API_KEY"})
        assert cfg_mod.secret_key_source("OPENAI_API_KEY") == "env"

    def test_file_value_marked_file(self, fake_root, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-file")
        monkeypatch.setattr(cfg_mod, "_ENV_PROVIDED_SECRETS", set())
        cfg_mod.save_runtime_secrets({"OPENAI_API_KEY": "sk-file"})
        assert cfg_mod.secret_key_source("OPENAI_API_KEY") == "file"
        # env 与文件值不一致（启动后外部注入）→ 视为 env 供给
        monkeypatch.setenv("OPENAI_API_KEY", "sk-other")
        assert cfg_mod.secret_key_source("OPENAI_API_KEY") == "env"

    def test_apply_records_env_provided(self, fake_root, monkeypatch):
        """apply 时记录"环境已显式提供"的变量名（文件值被忽略）"""
        monkeypatch.setattr(cfg_mod, "_ENV_PROVIDED_SECRETS", set())
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        cfg_mod.save_runtime_secrets({"ANTHROPIC_API_KEY": "sk-file", "OPENAI_API_KEY": "sk-file2"})
        applied = cfg_mod.apply_runtime_secrets_to_env()
        provided = cfg_mod.env_provided_secret_keys()
        assert "ANTHROPIC_API_KEY" in provided       # env 优先，文件值未覆盖
        assert "OPENAI_API_KEY" not in provided      # 由文件注入
        assert applied == 1
        assert os.environ["OPENAI_API_KEY"] == "sk-file2"


class TestMockDetection:
    def test_explicit_true(self, monkeypatch):
        monkeypatch.setenv("MOCK_MODE", "true")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
        assert cfg_mod.is_mock_mode() is True

    def test_explicit_false(self, monkeypatch):
        monkeypatch.setenv("MOCK_MODE", "0")
        assert cfg_mod.is_mock_mode() is False

    def test_auto_true_without_keys(self, monkeypatch):
        for k in ("OPENAI_API_KEY", "DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY",
                  "SEEDREAM_API_KEY", "VOLCANO_ACCESS_KEY", "DASHSCOPE_API_KEY",
                  "BFL_API_KEY", "FAL_KEY", "REPLICATE_API_KEY"):
            monkeypatch.delenv(k, raising=False)
        assert cfg_mod.is_mock_mode() is True

    def test_auto_false_with_any_key(self, monkeypatch):
        monkeypatch.delenv("MOCK_MODE", raising=False)
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-d")
        assert cfg_mod.is_mock_mode() is False
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        monkeypatch.setenv("BFL_API_KEY", "bf")
        assert cfg_mod.is_mock_mode() is False


class TestModelResolutionBoundary:
    """ProviderRegistry.resolve 的配置边界（P3：未知 provider / 覆盖优先级）"""

    def _registry_with_models(self, models: dict) -> ProviderRegistry:
        reg = ProviderRegistry()
        reg._models_config = models
        return reg

    def test_unknown_provider_falls_back_to_mock(self, monkeypatch):
        """默认/备选 provider 全部不可用 → mock 兜底，不抛异常"""
        for k in ("OPENAI_API_KEY", "DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY", "DASHSCOPE_API_KEY"):
            monkeypatch.delenv(k, raising=False)
        reg = self._registry_with_models({
            "capabilities": {
                "text": {
                    "default": "nonexistent_provider/xyz",
                    "alternatives": ["also_missing/abc"],
                    "fallback": ["mock"],
                }
            }
        })
        p, model = reg.resolve(["text"])
        assert p is not None
        assert p.name == "mock"
        assert model == ""  # fallback 裸 spec "mock" 不带模型名

    def test_agent_override_beats_default(self, monkeypatch):
        """agent_overrides 声明的 spec 优先于 capabilities.default"""
        for k in ("OPENAI_API_KEY", "DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY", "DASHSCOPE_API_KEY"):
            monkeypatch.delenv(k, raising=False)
        reg = self._registry_with_models({
            "capabilities": {"text": {"default": "mock", "fallback": ["mock"]}},
            "agent_overrides": {"测试Agent": {"text": "mock/override-model"}},
        })
        # 有覆盖 → 用覆盖的模型名
        p, model = reg.resolve(["text"], agent_name="测试Agent")
        assert p.name == "mock"
        assert model == "override-model"
        # 无覆盖 → 走 capabilities.default
        p, model = reg.resolve(["text"], agent_name="别的Agent")
        assert model == ""

    def test_override_unavailable_provider_ignored(self, monkeypatch):
        """覆盖指向不可用 provider 时跳过，继续按候选链兜底（不崩溃）"""
        for k in ("OPENAI_API_KEY", "DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY", "DASHSCOPE_API_KEY"):
            monkeypatch.delenv(k, raising=False)
        reg = self._registry_with_models({
            "capabilities": {"text": {"default": "mock"}},
            "agent_overrides": {"测试Agent": {"text": "unavailable_provider/x"}},
        })
        p, model = reg.resolve(["text"], agent_name="测试Agent")
        assert p.name == "mock"

    def test_unknown_capability_resolves_mock(self, monkeypatch):
        """未在 models.yaml 声明的能力 → 直接 mock 兜底"""
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        reg = self._registry_with_models({"capabilities": {}})
        p, model = reg.resolve(["audio"])
        assert p.name == "mock"
        assert model == "mock"


class TestImageOptions:
    """生图尺寸/张数解析（A30：方舟 400 事故后尺寸必须可配 + 必须校验）"""

    def test_size_from_models_config(self, monkeypatch):
        monkeypatch.setattr(cfg_mod, "load_models_config",
                            lambda: {"capabilities": {"image": {"size": "2048x2048", "variants": 2}}})
        # 契约扩展（生图质量策略）：除 size/variants 外还带 text_strategy/reference_mode/
        # watermark/quality/platforms 等（见 tests/test_core/test_image_options.py）
        options = cfg_mod.image_options()
        assert options["size"] == "2048x2048"
        assert options["variants"] == 2
        for key in ("text_strategy", "reference_mode", "max_references",
                    "watermark", "slot_candidates", "quality", "platforms"):
            assert key in options

    @pytest.mark.parametrize("bad", ["", None, "abc", "1024", "1024x", "-1x1024",
                                     "1024x1024; rm -rf /", "99999x99999", "10x10"])
    def test_invalid_size_rejected(self, monkeypatch, bad):
        monkeypatch.setattr(cfg_mod, "load_models_config",
                            lambda: {"capabilities": {"image": {"size": bad}}})
        assert cfg_mod.image_options()["size"] == ""
        assert cfg_mod.resolve_image_size("2048x2048") == "2048x2048"

    @pytest.mark.parametrize("bad,expected", [("abc", 3), (0, 1), (99, 6), (None, 3)])
    def test_variants_clamped(self, monkeypatch, bad, expected):
        monkeypatch.setattr(cfg_mod, "load_models_config",
                            lambda: {"capabilities": {"image": {"variants": bad}}})
        assert cfg_mod.image_options()["variants"] == expected

    def test_resolve_precedence(self, monkeypatch):
        """config > 路由默认 > 1024x1024"""
        monkeypatch.setattr(cfg_mod, "load_models_config",
                            lambda: {"capabilities": {"image": {"size": "4096x4096"}}})
        assert cfg_mod.resolve_image_size("2048x2048") == "4096x4096"
        monkeypatch.setattr(cfg_mod, "load_models_config", lambda: {})
        assert cfg_mod.resolve_image_size("2048x2048") == "2048x2048"
        assert cfg_mod.resolve_image_size("") == "1024x1024"
