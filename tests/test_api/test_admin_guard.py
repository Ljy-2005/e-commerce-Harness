"""第三轮审计 B0-1/B0-2：管理面守卫缺口回归测试。

B0-1：`GET /api/settings` 无管理面守卫 —— 持租户 Key（或 dev 模式下的任意远程 IP）
      即可读全量设置，其中 `provider_routes[].effective_base_url` 可能内嵌凭据
      （`https://user:SECRET@internal-gw/v1`），另有全租户清单与 Agent 系统提示词。
B0-2：`POST /api/workflows/templates/import` 无守卫 —— 任意租户 Key 可创建模板，
      并可 `force=true` 覆盖**全局**模板。

约定：租户身份返回脱敏子集（`redacted: true`，保留 Agent 列表供 Agents 页渲染，
但去掉 prompt / 端点 / 密钥 / 租户清单）；admin 与本机 dev 仍是全量。
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import src.api.auth as auth_mod
import src.core.config as config_mod
from src.core.tenant import TenantContext, get_tenant_registry
from src.main import app

client = TestClient(app)

TENANT_KEY = "tenant-a-key-0001-abcdef"
ADMIN_KEY = "admin-secret-999"
SECRET = "SUPERSECRET"

# 私有网关端点内嵌凭据：绝不能被非 admin 读到
PROVIDERS_YAML = f"""
openai:
  base_url: "https://ops:{SECRET}@internal-gw.example.com/v1"
  models: ["coding-plan-model-x"]
"""


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """隔离租户 Key 持久化与鉴权缓存（不污染真实 config/tenant_keys.yaml）"""
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


def _set_keys(monkeypatch, tenant_keys: dict[str, str], admin: str = ""):
    if admin:
        monkeypatch.setenv("ECOMM_API_KEY", admin)
    if tenant_keys:
        monkeypatch.setenv("ECOMM_TENANT_KEYS",
                            ",".join(f"{k}:{v}" for k, v in tenant_keys.items()))
    auth_mod.reload_tenant_keys()


def _use_fake_providers(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "providers.yaml"
    path.write_text(PROVIDERS_YAML, encoding="utf-8")
    monkeypatch.setattr(config_mod, "PROVIDER_CONFIG_REL", str(path))
    return path


class TestSettingsAdminGuard:
    """B0-1：GET /api/settings 的管理面守卫"""

    def test_tenant_key_gets_redacted_subset(self, tmp_path, monkeypatch):
        _use_fake_providers(tmp_path, monkeypatch)
        _set_keys(monkeypatch, {"tenant_a": TENANT_KEY})

        resp = client.get("/api/settings", headers={"X-API-Key": TENANT_KEY})

        assert resp.status_code == 200
        assert SECRET not in resp.text, "租户响应泄漏了内嵌凭据的私有端点"
        data = resp.json()
        assert data.get("redacted") is True
        assert data["provider_routes"] == [], "租户不应拿到 Provider 端点配置"
        assert data["tenant_keys"] == [], "租户不应拿到全租户清单"
        assert data["api_keys"] == [], "租户不应拿到平台密钥状态"
        assert data["agents"], "Agent 列表需保留（Agents 页据此渲染）"
        assert all("prompt" not in a for a in data["agents"]), "租户不应拿到系统提示词"

    def test_tenant_key_cannot_read_custom_model_ids(self, tmp_path, monkeypatch):
        """自定义模型 id 属运营配置，脱敏子集里不应出现"""
        _use_fake_providers(tmp_path, monkeypatch)
        _set_keys(monkeypatch, {"tenant_a": TENANT_KEY})

        data = client.get("/api/settings", headers={"X-API-Key": TENANT_KEY}).json()

        assert "coding-plan-model-x" not in str(data.get("model_catalog", {}))

    def test_admin_key_still_gets_full_payload(self, tmp_path, monkeypatch):
        _use_fake_providers(tmp_path, monkeypatch)
        _set_keys(monkeypatch, {"tenant_a": TENANT_KEY}, admin=ADMIN_KEY)

        resp = client.get("/api/settings", headers={"X-API-Key": ADMIN_KEY})

        assert resp.status_code == 200
        data = resp.json()
        assert not data.get("redacted")
        assert data["provider_routes"], "admin 仍需完整端点配置"
        assert data["api_keys"] and data["tenant_keys"]
        assert any(a.get("prompt") for a in data["agents"])

    def test_dev_mode_remote_client_denied_local_allowed(self):
        """dev 模式（未配任何 Key）：远程 IP 403，本机仍可读"""
        remote = TestClient(app, client=("10.11.12.13", 45678))

        assert remote.get("/api/settings").status_code == 403
        assert client.get("/api/settings").status_code == 200


class TestTemplateImportGuard:
    """B0-2：模板导入的管理面守卫（可覆盖全局模板，必须 admin）"""

    def _global_template(self) -> Path:
        return Path(config_mod._project_root()) / "config" / "workflows" / "scene_suite.yaml"

    def test_tenant_key_cannot_import(self, monkeypatch):
        _set_keys(monkeypatch, {"tenant_a": TENANT_KEY})
        target = self._global_template()
        before = target.read_bytes()

        resp = client.post(
            "/api/workflows/templates/import",
            json={"yaml": "name: [broken", "template_name": "scene_suite", "force": True},
            headers={"X-API-Key": TENANT_KEY},
        )

        assert resp.status_code == 403
        assert target.read_bytes() == before, "租户请求不得改动全局模板文件"

    def test_admin_import_passes_guard(self, monkeypatch):
        """同一份非法 YAML：admin 能走到校验（400），证明守卫只拦非 admin"""
        _set_keys(monkeypatch, {"tenant_a": TENANT_KEY}, admin=ADMIN_KEY)

        resp = client.post(
            "/api/workflows/templates/import",
            json={"yaml": "name: [broken"},
            headers={"X-API-Key": ADMIN_KEY},
        )

        assert resp.status_code == 400
        assert "YAML" in resp.json()["detail"]
