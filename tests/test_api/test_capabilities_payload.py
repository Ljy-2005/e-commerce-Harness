"""第三轮审计 B3-26：能力→实际 Provider/模型 可见（生图静默回落 Mock 的警示依据）。

背景：只配了 DeepSeek 的用户以为在出真图，实际生图能力回落到 Mock 占位图，
而设置页/新建任务表单没有任何提示（`available` 只表示"某个 env 非空"）。
`/api/settings` 新增 `capabilities[]`：能力 → 实际 provider/model/is_mock。
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


class _FakeProvider:
    def __init__(self, name):
        self.name = name


class _ResolvingRegistry:
    """按能力返回固定解析结果（模拟"只配了 DeepSeek"的真实注册表行为）"""

    def __init__(self, mapping):
        self._mapping = mapping

    def resolve(self, requires, agent_name=""):
        cap = (requires or ["text"])[0]
        name, model = self._mapping.get(cap, ("mock", "mock"))
        return _FakeProvider(name), model

    def list_available(self):
        return [{"name": n, "capabilities": [c]} for c, (n, _m) in self._mapping.items()
                if n != "mock"]

    def get_llm(self, name, model_name=""):
        return None

    def get_image(self, name, model_name=""):
        return None


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
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


class TestCapabilitiesPayload:
    def test_reports_real_provider_and_mock_fallback(self, monkeypatch):
        """文本→DeepSeek（真实），图像→回落 Mock（用户最需要看到的落差）"""
        monkeypatch.setattr(main_mod, "_provider_registry", _ResolvingRegistry({
            "text": ("deepseek", "deepseek-v4-flash"),
            "vision": ("deepseek", "deepseek-v4-flash-vision-exp"),
            "image": ("mock", "mock"),
        }))

        data = client.get("/api/settings").json()

        caps = {c["capability"]: c for c in data["capabilities"]}
        assert set(caps) == {"text", "vision", "image"}
        assert caps["text"]["provider"] == "deepseek"
        assert caps["text"]["model"] == "deepseek-v4-flash"
        assert caps["text"]["is_mock"] is False
        assert caps["image"]["is_mock"] is True, "生图回落 Mock 必须被如实标记"
        assert caps["image"]["provider"] == "mock"

    def test_all_mock_in_mock_mode(self, monkeypatch):
        monkeypatch.setattr(main_mod, "_provider_registry", _ResolvingRegistry({}))

        caps = {c["capability"]: c for c in client.get("/api/settings").json()["capabilities"]}

        assert all(c["is_mock"] for c in caps.values())

    def test_resolve_failure_does_not_break_settings(self, monkeypatch):
        """解析异常不能让整个设置页 500（降级为 is_mock=True）"""
        class _Boom(_ResolvingRegistry):
            def resolve(self, requires, agent_name=""):
                raise RuntimeError("registry exploded")

        monkeypatch.setattr(main_mod, "_provider_registry", _Boom({}))

        resp = client.get("/api/settings")

        assert resp.status_code == 200
        assert all(c["is_mock"] for c in resp.json()["capabilities"])

    def test_tenant_subset_also_exposes_capabilities(self, monkeypatch):
        """租户脱敏子集也要带 capabilities（新建任务表单是租户在用的）"""
        monkeypatch.setenv("ECOMM_TENANT_KEYS", f"tenant_a:{TENANT_KEY}")
        auth_mod.reload_tenant_keys()
        monkeypatch.setattr(main_mod, "_provider_registry", _ResolvingRegistry({
            "text": ("deepseek", "deepseek-v4-flash"), "image": ("mock", "mock"),
        }))

        data = client.get("/api/settings", headers={"X-API-Key": TENANT_KEY}).json()

        assert data["redacted"] is True
        caps = {c["capability"]: c for c in data["capabilities"]}
        assert caps["text"]["provider"] == "deepseek"
        assert caps["image"]["is_mock"] is True
