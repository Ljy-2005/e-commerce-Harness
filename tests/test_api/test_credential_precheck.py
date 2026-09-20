"""凭据预检与迁移（用户实测反馈）。

用户场景：把**火山方舟 API Key**（`ark-` 前缀）填进了旧卡片「火山视觉智能（旧版 AK/SK 签名）」
的 `VOLCANO_ACCESS_KEY` 槽 →
- ark 路由因缺 `ARK_API_KEY` 不可用；
- 旧路由只有半个 AK（缺 SecretKey）→ 按既有约定判为不可用 → 生图回落 Mock；
- 点「测试连接」只看到一句 "Seedream 需要 VOLCANO_ACCESS_KEY + VOLCANO_SECRET_KEY 或
  SEEDREAM_API_KEY"，没有任何"Key 该放哪儿"的指引。

本文件钉住修复后的行为：凭据缺失/不完整/放错槽位都要给出可执行说明 + 一键迁移。
"""

import pytest
from fastapi.testclient import TestClient

import src.api.auth as auth_mod
import src.api.main as main_mod
import src.core.config as config_mod
from src.core.tenant import TenantContext, get_tenant_registry
from src.main import app
from src.providers.mock import MockImageProvider, MockLLMProvider

client = TestClient(app)
TENANT_KEY = "tenant-a-key-0001-abcdef"

_CRED_ENVS = ("ARK_API_KEY", "VOLCANO_ACCESS_KEY", "VOLCANO_SECRET_KEY", "SEEDREAM_API_KEY",
              "OPENAI_API_KEY", "DEEPSEEK_API_KEY")


class _FakeLLM:
    name = "ark"
    capabilities = ["text", "vision"]

    def __init__(self):
        self.calls = []

    async def chat(self, messages, model="", json_mode=False):
        self.calls.append(model)
        return {"content": {"ok": True}, "tokens_used": 1, "cost_usd": 0.0}


class _FakeRegistry:
    def __init__(self, llm=None, image=None):
        self._llm, self._image = llm or {}, image or {}

    def get_llm(self, name, model_name=""):
        return self._llm.get(name)

    def get_image(self, name, model_name=""):
        return self._image.get(name)

    def list_available(self):
        return []


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "CUSTOM_PROVIDERS_REL", str(tmp_path / "custom.yaml"))
    monkeypatch.setattr(config_mod, "PROVIDER_CONFIG_REL", str(tmp_path / "providers.yaml"))
    monkeypatch.setattr(config_mod, "TENANT_KEYS_REL", str(tmp_path / "tenant_keys.yaml"))
    monkeypatch.setattr(config_mod, "RUNTIME_SECRETS_REL", str(tmp_path / "secrets.yaml"))
    monkeypatch.delenv("ECOMM_TENANT_KEYS", raising=False)
    monkeypatch.delenv("ECOMM_API_KEY", raising=False)
    for env in _CRED_ENVS:
        monkeypatch.delenv(env, raising=False)
    auth_mod.reload_tenant_keys()
    auth_mod._AUTH_FAILURES.clear()

    reg = get_tenant_registry()
    saved = dict(reg._tenants)
    reg._tenants["tenant_a"] = TenantContext(tenant_id="tenant_a", name="Tenant A", tier="pro")
    yield
    auth_mod._tenant_keys_cache = None
    auth_mod._AUTH_FAILURES.clear()
    reg._tenants.clear()
    reg._tenants.update(saved)


def _test(route: str, **body):
    return client.post(f"/api/settings/providers/{route}/test", json=body or None)


class TestCredentialPrecheck:
    def test_missing_credential_points_to_the_field(self):
        """没有凭据时不该把裸错误（httpx Illegal header value）丢给用户"""
        data = _test("ark").json()

        assert data["ok"] is False
        assert "未配置凭据" in data["detail"] and "ARK_API_KEY" in data["detail"]

    def test_partial_volcano_ak_sk_is_explained(self, monkeypatch):
        monkeypatch.setenv("VOLCANO_ACCESS_KEY", "real-ak-not-ark")
        _use_registry(monkeypatch, image={"seedream": MockImageProvider()})

        data = _test("seedream", allow_image=True).json()

        assert data["ok"] is False
        assert "凭据不完整" in data["detail"]
        assert "VOLCANO_SECRET_KEY" in data["detail"]

    def test_ark_key_in_legacy_slot_is_detected_with_migration_hint(self, monkeypatch):
        """核心场景：方舟 Key 被填进旧卡片 → 明确指出它属于哪个路由 + 一键迁移建议"""
        monkeypatch.setenv("VOLCANO_ACCESS_KEY", "ark-12345678-abcd-ef00-112233445566")
        _use_registry(monkeypatch, image={"seedream": MockImageProvider()})

        data = _test("seedream", allow_image=True).json()

        assert data["ok"] is False
        assert "火山方舟" in data["detail"] and "ARK_API_KEY" in data["detail"]
        assert data["suggested"] == {"route": "ark", "env": "ARK_API_KEY",
                                     "from_env": "VOLCANO_ACCESS_KEY"}

    def test_configured_credential_proceeds_to_real_call(self, monkeypatch):
        monkeypatch.setenv("ARK_API_KEY", "ark-test")
        fake = _FakeLLM()
        _use_registry(monkeypatch, llm={"ark": fake})

        data = _test("ark").json()

        assert data["ok"] is True and fake.calls, "凭据就位后应真的发起最小调用"

    def test_suggested_is_null_in_normal_result(self, monkeypatch):
        monkeypatch.setenv("ARK_API_KEY", "ark-test")
        _use_registry(monkeypatch, llm={"ark": _FakeLLM()})

        assert _test("ark").json()["suggested"] is None

    def test_legacy_route_marked_deprecated_with_hint(self):
        legacy = next(r for r in client.get("/api/settings").json()["provider_routes"]
                      if r["route"] == "seedream")

        assert legacy["deprecated"] is True
        assert "火山引擎方舟" in legacy["deprecated_hint"]

    def test_model_not_found_error_explains_auth_is_fine(self, monkeypatch):
        """实测场景：Ark Key 有效但模型未开通 → 404，用户会误以为 Key 错"""
        monkeypatch.setenv("ARK_API_KEY", "ark-test")

        class _NotFoundLLM:
            name = "ark"
            capabilities = ["text", "vision"]

            async def chat(self, messages, model="", json_mode=False):
                return {"error": '火山引擎方舟（Ark） API error: 404 @ https://ark... — '
                                 '{"error":{"code":"InvalidEndpointOrModel.NotFound",'
                                 '"message":"The model or endpoint doubao-seed-1-6-250815 does not '
                                 'exist or you do not have access to it."}}'}

        _use_registry(monkeypatch, llm={"ark": _NotFoundLLM()})

        data = _test("ark").json()

        assert data["ok"] is False
        assert "鉴权已通过" in data["detail"]
        assert "接入点" in data["detail"]

    def test_other_errors_are_not_decorated(self, monkeypatch):
        monkeypatch.setenv("ARK_API_KEY", "ark-test")

        class _UnauthorizedLLM:
            name = "ark"
            capabilities = ["text"]

            async def chat(self, messages, model="", json_mode=False):
                return {"error": "火山引擎方舟（Ark） API error: 401 @ https://ark... — invalid key"}

        _use_registry(monkeypatch, llm={"ark": _UnauthorizedLLM()})

        detail = _test("ark").json()["detail"]

        assert "401" in detail
        assert "鉴权已通过" not in detail, "401 是鉴权失败，不能误导成模型问题"

    def test_model_by_capability_used_when_no_mapping(self, monkeypatch):
        """端点没有默认模型（如方舟）时必须带上该能力的官方模型，否则 400 MissingParameter"""
        monkeypatch.setenv("ARK_API_KEY", "ark-test")
        fake = _FakeLLM()
        _use_registry(monkeypatch, llm={"ark": fake})

        data = _test("ark").json()

        assert data["model"], "必须解析出模型"
        assert fake.calls == [data["model"]]

    def test_payload_exposes_models_by_capability(self):
        ark = next(r for r in client.get("/api/settings").json()["provider_routes"]
                   if r["route"] == "ark")

        grouped = ark["models_by_capability"]
        assert any(m.startswith("doubao-seedream") for m in grouped["image"])
        assert all(not m.startswith("doubao-seedream") for m in grouped["text"]), \
            "生图模型不能被建议到文本能力上"


class TestMoveCredential:
    def test_moves_ark_key_out_of_legacy_slot(self, monkeypatch):
        monkeypatch.setenv("VOLCANO_ACCESS_KEY", "ark-secret-value")

        resp = client.post("/api/settings/providers/move-credential",
                           json={"from_env": "VOLCANO_ACCESS_KEY", "to_env": "ARK_API_KEY"})

        assert resp.status_code == 200, resp.text
        assert resp.json()["moved"] == {"from_env": "VOLCANO_ACCESS_KEY", "to_env": "ARK_API_KEY"}
        import os
        assert os.getenv("ARK_API_KEY") == "ark-secret-value"
        assert not os.getenv("VOLCANO_ACCESS_KEY")
        secrets = config_mod.load_runtime_secrets()
        assert secrets.get("ARK_API_KEY") == "ark-secret-value"
        assert "VOLCANO_ACCESS_KEY" not in secrets
        # 迁移后 ark 立刻可用
        cred = next(k for k in resp.json()["api_keys"] if k["env"] == "ARK_API_KEY")
        assert cred["configured"] is True and cred["available"] is True

    def test_rejects_unknown_env_names(self):
        resp = client.post("/api/settings/providers/move-credential",
                           json={"from_env": "PATH", "to_env": "ARK_API_KEY"})

        assert resp.status_code == 400

    def test_rejects_empty_source(self):
        resp = client.post("/api/settings/providers/move-credential",
                           json={"from_env": "VOLCANO_ACCESS_KEY", "to_env": "ARK_API_KEY"})

        assert resp.status_code == 400
        assert "没有已配置的密钥" in resp.json()["detail"]

    def test_requires_admin(self, monkeypatch):
        monkeypatch.setenv("ECOMM_TENANT_KEYS", f"tenant_a:{TENANT_KEY}")
        auth_mod.reload_tenant_keys()

        resp = client.post("/api/settings/providers/move-credential",
                           json={"from_env": "VOLCANO_ACCESS_KEY", "to_env": "ARK_API_KEY"},
                           headers={"X-API-Key": TENANT_KEY})

        assert resp.status_code == 403


def _use_registry(monkeypatch, llm=None, image=None):
    monkeypatch.setattr(main_mod, "_provider_registry", _FakeRegistry(llm=llm, image=image))
