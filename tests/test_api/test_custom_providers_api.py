"""自定义服务商 API（用户反馈："我无法自己添加模型服务商，局限性太大了"）。

覆盖：预设列表（含"已存在"标记）、新增/覆盖、校验拒绝、动态凭据槽与可用密钥、
删除（内置不可删）、租户 Key 拒绝、注册表热重载后该服务商即可用。
"""

import pytest
from fastapi.testclient import TestClient

import src.api.auth as auth_mod
import src.api.main as main_mod
import src.core.config as config_mod
from src.core.tenant import TenantContext, get_tenant_registry
from src.main import app

client = TestClient(app)
TENANT_KEY = "tenant-a-key-0001-abcdef"


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "CUSTOM_PROVIDERS_REL", str(tmp_path / "custom_providers.yaml"))
    monkeypatch.setattr(config_mod, "PROVIDER_CONFIG_REL", str(tmp_path / "providers.yaml"))
    monkeypatch.setattr(config_mod, "TENANT_KEYS_REL", str(tmp_path / "tenant_keys.yaml"))
    monkeypatch.delenv("ECOMM_TENANT_KEYS", raising=False)
    monkeypatch.delenv("ECOMM_API_KEY", raising=False)
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


def _custom(**over):
    body = {
        "route": "zhipu", "label": "智谱 GLM", "kind": "openai",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "api_key_env": "ZHIPU_API_KEY",
        "capabilities": ["text", "vision"],
        "models": ["glm-4.6", "glm-4v-plus"],
        "credential_hint": "智谱开放平台 Key",
    }
    body.update(over)
    return body


class TestPresets:
    def test_presets_listed_with_availability(self):
        data = client.get("/api/settings/providers/presets").json()

        routes = {p["route"]: p for p in data["presets"]}
        assert {"zhipu", "moonshot", "siliconflow", "openrouter", "ollama"} <= set(routes)
        # ark 已是内置路由 → 预设里应标记为不可添加，避免重复
        assert routes["ark"]["available"] is False
        assert "已存在" in routes["ark"]["reason"]
        assert routes["zhipu"]["available"] is True
        assert "ark" in data["existing_routes"]

    def test_tenant_key_denied(self, monkeypatch):
        monkeypatch.setenv("ECOMM_TENANT_KEYS", f"tenant_a:{TENANT_KEY}")
        auth_mod.reload_tenant_keys()

        resp = client.get("/api/settings/providers/presets", headers={"X-API-Key": TENANT_KEY})

        assert resp.status_code == 403


class TestUpsertCustomProvider:
    def test_add_provider_appears_everywhere(self, monkeypatch):
        """新增后：路由列表 / 凭据槽 / 模型目录 / 可用密钥 全部立刻可见"""
        monkeypatch.setenv("ZHIPU_API_KEY", "zk-test")

        resp = client.post("/api/settings/providers/custom", json=_custom())

        assert resp.status_code == 200, resp.text
        data = resp.json()
        route = next(r for r in data["provider_routes"] if r["route"] == "zhipu")
        assert route["custom"] is True and route["kind"] == "openai"
        assert route["label"] == "智谱 GLM"
        assert route["effective_base_url"] == "https://open.bigmodel.cn/api/paas/v4"
        assert route["official_models"] == ["glm-4.6", "glm-4v-plus"]

        cred = next(k for k in data["api_keys"] if k["env"] == "ZHIPU_API_KEY")
        assert cred["provider"] == "zhipu" and cred["configured"] is True
        assert cred["available"] is True, "注册表重载后该服务商应可用"
        assert "glm-4.6" in data["model_catalog"]["zhipu"]
        assert "zhipu" in [p["name"] for p in data["providers"]]

    def test_update_is_upsert(self, monkeypatch):
        monkeypatch.setenv("ZHIPU_API_KEY", "zk-test")
        client.post("/api/settings/providers/custom", json=_custom())

        resp = client.post("/api/settings/providers/custom",
                           json=_custom(label="智谱 GLM（改名）", models=["glm-4.6"]))

        assert resp.status_code == 200
        routes = [r for r in resp.json()["provider_routes"] if r["route"] == "zhipu"]
        assert len(routes) == 1, "同 route 应覆盖而不是新增"
        assert routes[0]["label"] == "智谱 GLM（改名）"

    def test_key_can_be_saved_afterwards(self, monkeypatch):
        """关键闭环：新增后该凭据变量必须在"可保存密钥"白名单里（此前是静态白名单）"""
        monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
        client.post("/api/settings/providers/custom", json=_custom())

        resp = client.post("/api/settings/api-keys", json={"api_keys": {"ZHIPU_API_KEY": "zk-123456"}})

        assert resp.status_code == 200, resp.text
        cred = next(k for k in resp.json()["api_keys"] if k["env"] == "ZHIPU_API_KEY")
        assert cred["configured"] is True

    @pytest.mark.parametrize("bad,keyword", [
        ({"label": ""}, "label"),
        ({"kind": "volc_cv"}, "kind"),
        ({"route": "ark"}, "已存在"),
        ({"base_url": "not-a-url"}, "base_url"),
        ({"capabilities": ["audio"]}, "capabilities"),
        ({"api_key_env": "1bad"}, "api_key_env"),
    ])
    def test_invalid_specs_rejected(self, bad, keyword):
        resp = client.post("/api/settings/providers/custom", json=_custom(**bad))

        assert resp.status_code == 400
        assert keyword in resp.json()["detail"], resp.json()

    def test_bad_body_400(self):
        assert client.post("/api/settings/providers/custom", data="oops").status_code == 400
        assert client.post("/api/settings/providers/custom", json=[1]).status_code == 400

    def test_tenant_key_denied(self, monkeypatch):
        monkeypatch.setenv("ECOMM_TENANT_KEYS", f"tenant_a:{TENANT_KEY}")
        auth_mod.reload_tenant_keys()

        resp = client.post("/api/settings/providers/custom", json=_custom(),
                           headers={"X-API-Key": TENANT_KEY})

        assert resp.status_code == 403


class TestDeleteCustomProvider:
    def test_delete_removes_route_and_credentials(self, monkeypatch):
        monkeypatch.setenv("ZHIPU_API_KEY", "zk-test")
        client.post("/api/settings/providers/custom", json=_custom())

        resp = client.delete("/api/settings/providers/custom/zhipu")

        assert resp.status_code == 200
        data = resp.json()
        assert "zhipu" not in [r["route"] for r in data["provider_routes"]]
        assert "ZHIPU_API_KEY" not in [k["env"] for k in data["api_keys"]]
        assert config_mod.load_custom_providers() == []

    def test_delete_cleans_up_orphan_credential(self, monkeypatch, tmp_path):
        """删除服务商要连带清掉它的 Key（否则孤儿密钥留在 secrets.yaml 且再删不掉）"""
        monkeypatch.setattr(config_mod, "RUNTIME_SECRETS_REL", str(tmp_path / "secrets.yaml"))
        monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
        client.post("/api/settings/providers/custom", json=_custom())
        resp = client.post("/api/settings/api-keys", json={"api_keys": {"ZHIPU_API_KEY": "zk-123456"}})
        assert resp.status_code == 200, resp.text
        assert config_mod.load_runtime_secrets().get("ZHIPU_API_KEY") == "zk-123456"

        client.delete("/api/settings/providers/custom/zhipu")

        assert "ZHIPU_API_KEY" not in config_mod.load_runtime_secrets(), \
            "删除服务商后其凭据不应留在 secrets.yaml"
        import os
        assert not os.getenv("ZHIPU_API_KEY")

    def test_delete_keeps_credential_still_used_by_others(self, monkeypatch, tmp_path):
        """同名凭据仍被其他路由使用时不得清除（内置路由可能共用）"""
        monkeypatch.setattr(config_mod, "RUNTIME_SECRETS_REL", str(tmp_path / "secrets.yaml"))
        monkeypatch.setenv("OPENAI_API_KEY", "sk-shared")
        client.post("/api/settings/providers/custom",
                    json=_custom(route="myoa", api_key_env="OPENAI_API_KEY",
                                 base_url="https://proxy.example.com/v1"))

        client.delete("/api/settings/providers/custom/myoa")

        import os
        assert os.getenv("OPENAI_API_KEY") == "sk-shared", "内置路由还在用的凭据不能被误删"

    def test_delete_unknown_404(self):
        assert client.delete("/api/settings/providers/custom/nope").status_code == 404

    def test_builtin_route_cannot_be_deleted(self):
        assert client.delete("/api/settings/providers/custom/ark").status_code == 404
        assert client.delete("/api/settings/providers/custom/openai").status_code == 404

    def test_tenant_key_denied(self, monkeypatch):
        monkeypatch.setenv("ECOMM_TENANT_KEYS", f"tenant_a:{TENANT_KEY}")
        auth_mod.reload_tenant_keys()

        resp = client.delete("/api/settings/providers/custom/zhipu",
                             headers={"X-API-Key": TENANT_KEY})

        assert resp.status_code == 403


class TestArkRoute:
    def test_ark_exposed_in_settings(self):
        data = client.get("/api/settings").json()

        ark = next(r for r in data["provider_routes"] if r["route"] == "ark")
        assert ark["custom"] is False
        assert ark["kind"] == "openai"
        assert ark["default_base_url"] == "https://ark.cn-beijing.volces.com/api/v3"
        assert "image" in ark["capabilities"]
        assert any(m.startswith("doubao-seedream") for m in ark["official_models"])

    def test_legacy_seedream_relabelled(self):
        data = client.get("/api/settings").json()

        legacy = next(r for r in data["provider_routes"] if r["route"] == "seedream")
        assert "旧版" in legacy["label"], "旧 CV 通道要与方舟区分开"
        assert legacy["base_url_supported"] is False

    def test_ark_participates_in_capabilities(self, monkeypatch):
        """只配 ARK_API_KEY → 生图能力不再回落 Mock（这正是用户最初的痛点）"""
        monkeypatch.setenv("ARK_API_KEY", "ark-test")
        main_mod._provider_registry = __import__("src.providers", fromlist=["x"]).reset_provider_registry()

        caps = {c["capability"]: c for c in client.get("/api/settings").json()["capabilities"]}

        assert caps["image"]["is_mock"] is False, "方舟接入后生图有真实服务商可用"
        assert caps["image"]["provider"] == "ark"
