"""拉取可用模型 + 模型 id 形态提示（用户实测：模型 id 靠猜）。

实测案例：用户把方舟模型写成 `Doubao-Seedream-5.0-lite` → 404
`InvalidEndpointOrModel.NotFound`。用其真实账号 `GET /api/v3/models` 校准后确认：
方舟 id 是「小写 + 短横线 + 日期后缀」，且该账号的生图模型只有
`doubao-seedream-5-0-260128` / `-5-0-pro-260628` / `-4-5-251128` / `-4-0-250828`。
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

_ARK_MODELS = {
    "data": [
        {"id": "doubao-seedream-5-0-260128", "name": "doubao-seedream-5-0", "version": "260128",
         "created": 500, "status": "",
         "modalities": {"input_modalities": ["text", "image"], "output_modalities": ["image"]}},
        {"id": "doubao-seedream-4-5-251128", "name": "doubao-seedream-4-5", "version": "251128",
         "created": 400, "status": "",
         "modalities": {"input_modalities": ["text", "image"], "output_modalities": ["image"]}},
        {"id": "doubao-seed-2-1-pro-260628", "name": "doubao-seed-2-1-pro", "version": "260628",
         "created": 900, "status": "",
         "modalities": {"input_modalities": ["text", "image", "video"], "output_modalities": ["text"]}},
        {"id": "doubao-seed-2-0-lite-260428", "name": "doubao-seed-2-0-lite", "version": "260428",
         "created": 800, "status": "",
         "modalities": {"input_modalities": ["text", "image"], "output_modalities": ["text"]}},
        {"id": "glm-5-3-flash-260828", "name": "glm-5-3-flash", "version": "260828",
         "created": 700, "status": "",
         "modalities": {"input_modalities": ["text"], "output_modalities": ["text"]}},
        {"id": "doubao-lite-128k-240428", "status": "Shutdown", "modalities": {}},
        {"id": "doubao-seedream-3-0-t2i-250415", "name": "doubao-seedream-3-0-t2i",
         "version": "250415", "created": 100, "status": "Shutdown",
         "modalities": {"input_modalities": ["text"], "output_modalities": ["image"]}},
        # 即将下线：不该进建议
        {"id": "doubao-seed-1-6-250615", "name": "doubao-seed-1-6", "version": "250615",
         "created": 600, "status": "Retiring",
         "modalities": {"input_modalities": ["text", "image"], "output_modalities": ["text"]}},
        # 专用/辅助模型：不该混进"文本"
        {"id": "doubao-seed-translation-250915", "name": "doubao-seed-translation",
         "version": "250915", "created": 550, "status": "",
         "modalities": {"input_modalities": ["text"], "output_modalities": ["text"]}},
        {"id": "doubao-smart-router-250928", "name": "doubao-smart-router", "version": "250928",
         "created": 560, "status": "",
         "modalities": {"input_modalities": ["text"], "output_modalities": ["text"]}},
        {"id": "doubao-seed-character-260628", "name": "doubao-seed-character", "version": "260628",
         "created": 570, "status": "",
         "modalities": {"input_modalities": ["text", "image"], "output_modalities": ["text"]}},
        {"id": "doubao-seed-2-0-code-preview-260215", "name": "doubao-seed-2-0-code-preview",
         "version": "260215", "created": 580, "status": "",
         "modalities": {"input_modalities": ["text", "image"], "output_modalities": ["text"]}},
        {"id": "doubao-embedding-text-240715", "name": "doubao-embedding", "version": "text-240715",
         "created": 50, "status": "", "task_type": ["TextEmbedding"],
         "modalities": {"input_modalities": ["text"], "output_modalities": ["text"]}},
    ]
}


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "CUSTOM_PROVIDERS_REL", str(tmp_path / "custom.yaml"))
    monkeypatch.setattr(config_mod, "PROVIDER_CONFIG_REL", str(tmp_path / "providers.yaml"))
    monkeypatch.setattr(config_mod, "TENANT_KEYS_REL", str(tmp_path / "tenant_keys.yaml"))
    monkeypatch.delenv("ECOMM_TENANT_KEYS", raising=False)
    monkeypatch.delenv("ECOMM_API_KEY", raising=False)
    monkeypatch.setenv("ARK_API_KEY", "ark-test")
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


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class _FakeAsyncClient:
    """替换 httpx.AsyncClient：拦截 GET /models"""

    calls: list = []
    response = _FakeResponse(200, _ARK_MODELS)

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, headers=None):
        _FakeAsyncClient.calls.append({"url": url, "headers": headers or {}})
        return _FakeAsyncClient.response

    async def post(self, *args, **kwargs):   # pragma: no cover - 本文件不用
        raise AssertionError("不应调用 post")


@pytest.fixture
def fake_httpx(monkeypatch):
    import httpx
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.response = _FakeResponse(200, _ARK_MODELS)
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    return _FakeAsyncClient


class TestPullModels:
    def test_lists_models_grouped_by_capability(self, fake_httpx):
        data = client.get("/api/settings/providers/ark/models").json()

        assert data["ok"] is True
        assert data["total"] == 13
        assert data["base_url"] == "https://ark.cn-beijing.volces.com/api/v3"
        assert "doubao-seedream-5-0-260128" in data["applicable"]["image"]
        assert "doubao-seed-2-0-lite-260428" in data["applicable"]["vision"]
        assert "glm-5-3-flash-260828" in data["applicable"]["text"]

    def test_carries_upstream_name_and_version(self, fake_httpx):
        """上游每条都带 name/version（实测 133/133），此前被我丢掉 → 界面只剩原始 id"""
        data = client.get("/api/settings/providers/ark/models").json()

        entry = next(m for m in data["models"] if m["id"] == "doubao-seedream-5-0-260128")
        assert entry["name"] == "doubao-seedream-5-0"
        assert entry["version"] == "260128"

    def test_auxiliary_and_legacy_models_are_not_recommended(self, fake_httpx):
        """用户反馈"名字有些问题"：角色扮演/翻译/路由/代码专用/即将下线 不该进建议"""
        data = client.get("/api/settings/providers/ark/models").json()
        text_ids = data["applicable"]["text"]
        vision_ids = data["applicable"]["vision"]

        for mid in ("doubao-seed-translation-250915", "doubao-smart-router-250928",
                    "doubao-seed-character-260628", "doubao-embedding-text-240715"):
            assert mid not in text_ids, f"{mid} 是专用模型，不该出现在文本建议里"
        assert "doubao-seed-2-0-code-preview-260215" not in vision_ids
        assert "doubao-seed-1-6-250615" not in vision_ids, "即将下线的型号不该被推荐"

        # 但全量列表里保留 + 说明原因，便于排查"为什么某些模型不在建议里"
        reasons = {m["id"]: m["skip_reason"] for m in data["models"]}
        assert reasons["doubao-seed-1-6-250615"] == "即将下线"
        assert "专用模型" in reasons["doubao-seed-translation-250915"]
        assert "代码专用" in reasons["doubao-seed-2-0-code-preview-260215"]
        assert reasons["doubao-seedream-5-0-260128"] == ""
        assert all(m["recommended"] == (m["skip_reason"] == "") for m in data["models"])

    def test_suggestions_sorted_newest_first(self, fake_httpx):
        """平台目录里历史型号很多 → 建议按 created 新→旧，当前代排最前"""
        data = client.get("/api/settings/providers/ark/models").json()

        assert data["applicable"]["text"][:2] == ["doubao-seed-2-1-pro-260628",
                                                  "doubao-seed-2-0-lite-260428"]
        assert data["applicable"]["image"][0] == "doubao-seedream-5-0-260128"

    def test_shutdown_models_excluded_from_suggestions(self, fake_httpx):
        """已下线模型不能出现在"点一下即填入"里（点了必然失败），但全量列表保留"""
        data = client.get("/api/settings/providers/ark/models").json()

        assert "doubao-seedream-3-0-t2i-250415" not in data["applicable"]["image"]
        assert any(m["id"] == "doubao-seedream-3-0-t2i-250415"
                   and m["status"] == "Shutdown" for m in data["models"])

    def test_uses_configured_key_without_echoing_it(self, fake_httpx):
        data = client.get("/api/settings/providers/ark/models").json()

        assert fake_httpx.calls[0]["url"].endswith("/models")
        assert fake_httpx.calls[0]["headers"]["Authorization"] == "Bearer ark-test"
        assert "ark-test" not in str(data), "响应不得回显密钥"

    def test_missing_credential_short_circuits(self, monkeypatch):
        monkeypatch.delenv("ARK_API_KEY", raising=False)

        data = client.get("/api/settings/providers/ark/models").json()

        assert data["ok"] is False and "未配置凭据" in data["detail"]
        assert data["models"] == []

    def test_non_openai_kind_rejected(self):
        """旧版签名/FLUX 路由没有 /models 接口 → 明确说明而不是瞎试"""
        resp = client.get("/api/settings/providers/flux/models")

        assert resp.status_code == 400
        assert "仅 OpenAI 兼容" in resp.json()["detail"]

    def test_unknown_route_404(self):
        assert client.get("/api/settings/providers/nope/models").status_code == 404

    def test_provider_without_models_endpoint_explained(self, fake_httpx):
        fake_httpx.response = _FakeResponse(404, None, "not found")

        data = client.get("/api/settings/providers/ark/models").json()

        assert data["ok"] is False
        assert "404" in data["detail"] and "手填" in data["detail"]

    def test_bad_payload_explained(self, fake_httpx):
        fake_httpx.response = _FakeResponse(200, {"unexpected": []})

        assert client.get("/api/settings/providers/ark/models").json()["ok"] is False

    def test_network_error_explained(self, monkeypatch):
        import httpx

        class _Boom(httpx.AsyncClient):
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def get(self, *a, **k):
                raise RuntimeError("connection reset")

        monkeypatch.setattr(httpx, "AsyncClient", _Boom)

        data = client.get("/api/settings/providers/ark/models").json()

        assert data["ok"] is False and "connection reset" in data["detail"]

    def test_requires_admin(self, monkeypatch):
        monkeypatch.setenv("ECOMM_TENANT_KEYS", f"tenant_a:{TENANT_KEY}")
        auth_mod.reload_tenant_keys()

        resp = client.get("/api/settings/providers/ark/models", headers={"X-API-Key": TENANT_KEY})

        assert resp.status_code == 403


class TestModelIdShapeHint:
    def test_uppercase_or_dotted_id_gets_shape_hint(self, monkeypatch):
        """用户实测：Doubao-Seedream-5.0-lite → 提示 id 形态不对 + 指引拉取"""
        class _NotFoundLLM:
            name = "ark"
            capabilities = ["text"]

            async def chat(self, messages, model="", json_mode=False):
                return {"error": '火山引擎方舟（Ark） API error: 404 — '
                                 '{"error":{"code":"InvalidEndpointOrModel.NotFound",'
                                 '"message":"The model or endpoint Doubao-Seedream-5.0-lite does not '
                                 'exist or you do not have access to it."}}'}

        monkeypatch.setattr(main_mod, "_provider_registry",
                            type("R", (), {"get_llm": lambda self, n, m="": _NotFoundLLM() if n == "ark" else None,
                                           "get_image": lambda self, n, m="": None,
                                           "list_available": lambda self: []})())

        detail = client.post("/api/settings/providers/ark/test",
                             json={"model": "Doubao-Seedream-5.0-lite"}).json()["detail"]

        assert "鉴权已通过" in detail
        assert "小写字母 + 短横线" in detail, "要点明 id 形态（大写/点号不是有效 id）"
        assert "拉取可用模型" in detail

    def test_model_not_open_gets_activation_guidance(self, monkeypatch):
        """实测原文：404 ModelNotOpen「Your account … has not activated the model …」"""
        class _NotOpenLLM:
            name = "ark"
            capabilities = ["text"]

            async def chat(self, messages, model="", json_mode=False):
                return {"error": '火山引擎方舟（Ark） API error: 404 — {"error":{"code":"ModelNotOpen",'
                                 '"message":"Your account 2122038908 has not activated the model '
                                 'doubao-seed-2-1-pro-260628. Please activate the model service in '
                                 'the Ark Console."}}'}

        monkeypatch.setattr(main_mod, "_provider_registry",
                            type("R", (), {"get_llm": lambda self, n, m="": _NotOpenLLM(),
                                           "get_image": lambda self, n, m="": None,
                                           "list_available": lambda self: []})())

        detail = client.post("/api/settings/providers/ark/test", json={}).json()["detail"]

        assert "鉴权已通过" in detail
        assert "开通" in detail and "方舟控制台" in detail
        assert "分别开通" in detail, "文本与生图要分别开通，这点必须写明"

    def test_ark_defaults_are_lowercase_dashed(self):
        """内置默认模型必须是方舟真实形态（用真实账号校准过的 id）"""
        ark = next(r for r in client.get("/api/settings").json()["provider_routes"]
                   if r["route"] == "ark")

        for mid in ark["official_models"]:
            assert mid == mid.lower() and "." not in mid, mid
        assert "doubao-seedream-5-0-260128" in ark["models_by_capability"]["image"]

    def test_auth_error_explains_key_shape(self, monkeypatch):
        """用户实测原文：401 AuthenticationError「The API key format is incorrect」"""
        class _BadKeyLLM:
            name = "ark"
            capabilities = ["text"]

            async def chat(self, messages, model="", json_mode=False):
                return {"error": '火山引擎方舟（Ark） API error: 401 @ https://ark... — '
                                 '{"error":{"code":"AuthenticationError","message":'
                                 '"The API key format is incorrect."}}'}

        monkeypatch.setattr(main_mod, "_provider_registry",
                            type("R", (), {"get_llm": lambda self, n, m="": _BadKeyLLM(),
                                           "get_image": lambda self, n, m="": None,
                                           "list_available": lambda self: []})())

        detail = client.post("/api/settings/providers/ark/test", json={}).json()["detail"]

        assert "鉴权失败" in detail, "401 要明确说是 Key 问题，不是模型问题"
        assert "ark-" in detail and "ep-" in detail, "要点明方舟 Key 形态与常见填错的东西"
        assert "复制完整" in detail

    def test_model_404_hint_does_not_claim_auth_failure(self, monkeypatch):
        """反向断言：404 模型错误不能被说成鉴权失败（两者提示必须互斥）"""
        class _NotFoundLLM:
            name = "ark"
            capabilities = ["text"]

            async def chat(self, messages, model="", json_mode=False):
                return {"error": '404 — {"code":"InvalidEndpointOrModel.NotFound",'
                                 '"message":"The model ... does not exist"}'}

        monkeypatch.setattr(main_mod, "_provider_registry",
                            type("R", (), {"get_llm": lambda self, n, m="": _NotFoundLLM(),
                                           "get_image": lambda self, n, m="": None,
                                           "list_available": lambda self: []})())

        detail = client.post("/api/settings/providers/ark/test", json={}).json()["detail"]

        assert "鉴权已通过" in detail
        assert "鉴权失败" not in detail

    def test_custom_model_list_is_capability_aware(self, monkeypatch, tmp_path):
        """用户自定义列表混着生图与文本模型时：测文本不能把生图模型发过去"""
        import json

        import src.core.config as config_mod

        providers = tmp_path / "providers.yaml"
        providers.write_text(json.dumps({"ark": {"models": [
            "doubao-seedream-5-0-260128",      # 生图（用户在 Ark 卡片里填的）
            "doubao-seed-2-1-pro-260628",      # 文本/视觉
        ]}}), encoding="utf-8")
        monkeypatch.setattr(config_mod, "PROVIDER_CONFIG_REL", str(providers))

        class _RecordingLLM:
            name = "ark"
            capabilities = ["text", "vision"]
            seen: list = []

            async def chat(self, messages, model="", json_mode=False):
                _RecordingLLM.seen.append(model)
                return {"content": {"ok": True}, "tokens_used": 1, "cost_usd": 0.0}

        monkeypatch.setattr(main_mod, "_provider_registry",
                            type("R", (), {"get_llm": lambda self, n, m="": _RecordingLLM(),
                                           "get_image": lambda self, n, m="": None,
                                           "list_available": lambda self: []})())

        data = client.post("/api/settings/providers/ark/test", json={}).json()

        assert data["model"] == "doubao-seed-2-1-pro-260628", \
            "文本能力必须挑自定义列表里的文本模型，而不是第一条生图模型"
        assert _RecordingLLM.seen == ["doubao-seed-2-1-pro-260628"]
