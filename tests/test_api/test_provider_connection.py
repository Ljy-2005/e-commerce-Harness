"""第三轮审计 B3-24：Provider「测试连接」端点回归测试。

背景：`available` 只表示"环境变量非空"，配好 Key/端点/模型后无法验证真的可用——
第三方 coding plan / 中转场景下（端点与模型 id 都是自填的）用户只能等真正跑图时才发现错。

约定：`POST /api/settings/providers/{route}/test`（仅 admin）
- 文本/视觉路由 → 发起 1 次最小 chat 调用，回显状态码/上游原文/耗时/实际模型；
- 图像路由默认**跳过**（真实生成有费用与等待），body `allow_image=true` 才真跑；
- Mock 路由不发起真实调用（`skipped=true, is_mock=true`）；
- 连接失败仍返回 200 + `ok=false` + 可读 detail（UI 直接展示）。
"""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

import src.api.auth as auth_mod
import src.api.main as main_mod
import src.core.config as config_mod
from src.core.tenant import TenantContext, get_tenant_registry
from src.main import app

client = TestClient(app)
TENANT_KEY = "tenant-a-key-0001-abcdef"
ADMIN_KEY = "admin-secret-999"


class _FakeLLM:
    name = "openai"
    capabilities = ["vision", "text"]

    def __init__(self, result=None, delay=0.0):
        self.result = result if result is not None else {
            "content": {"ok": True}, "tokens_used": 5, "cost_usd": 0.0,
        }
        self.delay = delay
        self.calls: list[dict] = []

    async def chat(self, messages, model="", json_mode=False):
        self.calls.append({"messages": messages, "model": model})
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.result


class _FakeImage:
    name = "seedream"
    capabilities = ["image"]

    def __init__(self, result=None):
        self.result = result if result is not None else {
            "image_url": "https://cdn.example/x.png", "base64_data": "AAA", "cost_usd": 0.1,
        }
        self.calls: list[dict] = []

    async def generate(self, prompt, negative_prompt="", size="1024x1024", model=""):
        self.calls.append({"prompt": prompt, "size": size, "model": model})
        return self.result


class _FakeRegistry:
    def __init__(self, llm=None, image=None):
        self._llm = llm or {}
        self._image = image or {}

    def get_llm(self, name, model_name=""):
        return self._llm.get(name)

    def get_image(self, name, model_name=""):
        return self._image.get(name)

    def list_available(self):
        return []


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "TENANT_KEYS_REL", str(tmp_path / "tenant_keys.yaml"))
    monkeypatch.delenv("ECOMM_TENANT_KEYS", raising=False)
    monkeypatch.delenv("ECOMM_API_KEY", raising=False)
    monkeypatch.setattr(config_mod, "PROVIDER_CONFIG_REL", str(tmp_path / "providers.yaml"))
    # 测试连接现在会先做凭据预检（没配 Key 就没必要发请求）→ 这些用例测的是
    # "凭据就位后的调用路径"，故预置各路由的凭据变量
    for env in ("OPENAI_API_KEY", "SEEDREAM_API_KEY", "VOLCANO_ACCESS_KEY",
                "VOLCANO_SECRET_KEY", "ARK_API_KEY"):
        monkeypatch.setenv(env, "test-credential")
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


def _use_registry(monkeypatch, llm=None, image=None):
    registry = _FakeRegistry(llm=llm, image=image)
    monkeypatch.setattr(main_mod, "_provider_registry", registry)
    return registry


def _write_providers(tmp_path, route: str, models: list[str], base_url: str = ""):
    cfg = {route: {"models": models}}
    if base_url:
        cfg[route]["base_url"] = base_url
    (tmp_path / "providers.yaml").write_text(json.dumps(cfg), encoding="utf-8")


class TestRouteResolution:
    def test_unknown_route_404(self):
        assert client.post("/api/settings/providers/nope/test").status_code == 404

    def test_malformed_body_400(self):
        assert client.post("/api/settings/providers/openai/test",
                           json={"model": 123}).status_code == 400
        assert client.post("/api/settings/providers/openai/test",
                           data="not json").status_code == 400

    def test_tenant_key_denied(self, monkeypatch):
        monkeypatch.setenv("ECOMM_TENANT_KEYS", f"tenant_a:{TENANT_KEY}")
        auth_mod.reload_tenant_keys()

        resp = client.post("/api/settings/providers/openai/test",
                           headers={"X-API-Key": TENANT_KEY})

        assert resp.status_code == 403


class TestLlmRoute:
    def test_success_reports_model_latency_and_endpoint(self, tmp_path, monkeypatch):
        _write_providers(tmp_path, "openai", ["coding-plan-x"], "https://gw.example/v1")
        fake = _FakeLLM()
        _use_registry(monkeypatch, llm={"openai": fake})

        resp = client.post("/api/settings/providers/openai/test")

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True and data["skipped"] is False
        assert data["model"] == "coding-plan-x", "应优先用该路由的自定义模型"
        assert data["latency_ms"] >= 0
        assert "gw.example" in data["base_url"]
        assert fake.calls and fake.calls[0]["model"] == "coding-plan-x"
        assert fake.calls[0]["messages"], "必须真的发一次最小调用"

    def test_body_model_overrides_catalog(self, tmp_path, monkeypatch):
        _write_providers(tmp_path, "openai", ["coding-plan-x"])
        fake = _FakeLLM()
        _use_registry(monkeypatch, llm={"openai": fake})

        data = client.post("/api/settings/providers/openai/test",
                           json={"model": "my-own-model"}).json()

        assert data["model"] == "my-own-model"
        assert fake.calls[0]["model"] == "my-own-model"

    def test_falls_back_to_capability_mapping(self, monkeypatch):
        """无自定义模型时用「模型映射」里属于该路由的模型"""
        monkeypatch.setattr(main_mod, "load_models_config", lambda: {
            "capabilities": {"text": {"default": "openai/gpt-4o", "alternatives": []}},
        })
        fake = _FakeLLM()
        _use_registry(monkeypatch, llm={"openai": fake})

        data = client.post("/api/settings/providers/openai/test").json()

        assert data["model"] == "gpt-4o"

    def test_upstream_error_is_reported_verbatim(self, monkeypatch):
        fake = _FakeLLM(result={"error": "OpenAI API error: 401 @ https://gw.example/v1 — invalid api key"})
        _use_registry(monkeypatch, llm={"openai": fake})

        data = client.post("/api/settings/providers/openai/test").json()

        assert data["ok"] is False
        assert "401" in data["detail"] and "invalid api key" in data["detail"]

    def test_timeout_is_reported(self, monkeypatch):
        monkeypatch.setattr(main_mod, "PROVIDER_TEST_TIMEOUT_S", 0.05)
        fake = _FakeLLM(delay=0.5)
        _use_registry(monkeypatch, llm={"openai": fake})

        data = client.post("/api/settings/providers/openai/test").json()

        assert data["ok"] is False
        assert "超时" in data["detail"] or "timeout" in data["detail"].lower()

    def test_missing_provider_instance(self, monkeypatch):
        _use_registry(monkeypatch, llm={})   # 凭据缺失 → 取不到实例

        data = client.post("/api/settings/providers/openai/test").json()

        assert data["ok"] is False
        assert "未就绪" in data["detail"] or "凭据" in data["detail"]

    def test_mock_provider_is_marked_skipped(self, monkeypatch):
        from src.providers.mock import MockLLMProvider
        _use_registry(monkeypatch, llm={"openai": MockLLMProvider()})

        data = client.post("/api/settings/providers/openai/test").json()

        assert data["is_mock"] is True
        assert data["skipped"] is True and data["ok"] is False
        assert "Mock" in data["reason"]


class TestImageRoute:
    def test_image_route_skipped_by_default(self, monkeypatch):
        fake = _FakeImage()
        _use_registry(monkeypatch, image={"seedream": fake})

        data = client.post("/api/settings/providers/seedream/test").json()

        assert data["ok"] is False and data["skipped"] is True
        assert "费用" in data["reason"]
        assert fake.calls == [], "默认不得发起真实生图调用"

    def test_image_route_runs_when_allowed(self, monkeypatch):
        fake = _FakeImage()
        _use_registry(monkeypatch, image={"seedream": fake})

        data = client.post("/api/settings/providers/seedream/test",
                           json={"allow_image": True}).json()

        assert data["ok"] is True and data["skipped"] is False
        assert fake.calls, "允许后应真的调用一次"
        assert data["latency_ms"] >= 0

    def test_image_route_error_reported(self, monkeypatch):
        fake = _FakeImage(result={"error": "Seedream API error: 403 @ https://ark.example — signature mismatch"})
        _use_registry(monkeypatch, image={"seedream": fake})

        data = client.post("/api/settings/providers/seedream/test",
                           json={"allow_image": True}).json()

        assert data["ok"] is False
        assert "403" in data["detail"]

    def test_image_route_uses_route_default_size(self, monkeypatch):
        """实测事故：尺寸写死 1024x1024 → 方舟 Seedream 5.0 返回 400
        （要求 ≥ 3,686,400 像素），"测试连接"永远失败"""
        fake = _FakeImage()
        fake.default_size = "2048x2048"
        _use_registry(monkeypatch, image={"seedream": fake})

        client.post("/api/settings/providers/seedream/test", json={"allow_image": True})

        assert fake.calls[0]["size"] == "2048x2048"

    def test_image_route_has_its_own_timeout(self, monkeypatch):
        """生图实测约 30s：20s 的文本超时会把成功判成超时"""
        monkeypatch.setattr(main_mod, "PROVIDER_TEST_TIMEOUT_S", 0.05)
        fake = _FakeImage()
        _use_registry(monkeypatch, image={"seedream": fake})

        data = client.post("/api/settings/providers/seedream/test",
                           json={"allow_image": True}).json()

        assert data["ok"] is True, data["detail"]
