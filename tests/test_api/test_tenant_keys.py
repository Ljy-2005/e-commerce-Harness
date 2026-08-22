"""租户独立 API Key 测试（C2 方案①：每租户一把钥匙，2026-08-22）

覆盖：租户 Key 鉴权与租户绑定（X-Tenant-ID 声明被密钥覆盖）、跨租户读取隔离、
全局 admin Key 兼容、无 Key/错 Key 401、管理端点权限（租户 Key 403）、
租户 Key CRUD 端点（创建/轮换/删除/校验/持久化）、WS 租户 Key、设置载荷脱敏。
"""

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import src.api.auth as auth_mod
import src.main as main_mod
import src.core.config as config_mod
from src.core.tenant import TenantContext, get_tenant_registry
from src.main import app

client = TestClient(app)

TENANT_A_KEY = "tenant-a-key-0001-abcdef"
TENANT_B_KEY = "tenant-b-key-0002-abcdef"
ADMIN_KEY = "admin-secret-999"


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """隔离租户 Key 存储与鉴权状态（不污染真实 config/tenant_keys.yaml）"""
    monkeypatch.setattr(config_mod, "TENANT_KEYS_REL", str(tmp_path / "tenant_keys.yaml"))
    monkeypatch.delenv("ECOMM_TENANT_KEYS", raising=False)
    monkeypatch.delenv("ECOMM_API_KEY", raising=False)
    auth_mod.reload_tenant_keys()
    auth_mod._AUTH_FAILURES.clear()

    reg = get_tenant_registry()
    saved_tenants = dict(reg._tenants)
    reg._tenants["tenant_a"] = TenantContext(tenant_id="tenant_a", name="Tenant A", tier="pro")
    reg._tenants["tenant_b"] = TenantContext(tenant_id="tenant_b", name="Tenant B", tier="pro")

    saved_sessions = dict(main_mod._session_manager._sessions)
    yield

    # teardown：缓存置 None——下一测试惰性重载时 monkeypatch 已恢复真实环境，
    # 测试期间注入的租户 Key 不会泄漏为全局鉴权状态（否则后续模块全 401）
    auth_mod._tenant_keys_cache = None
    auth_mod._AUTH_FAILURES.clear()
    reg._tenants.clear()
    reg._tenants.update(saved_tenants)
    main_mod._session_manager._sessions.clear()
    main_mod._session_manager._sessions.update(saved_sessions)


def _set_keys(monkeypatch, tenant_keys: dict[str, str], admin: str = ""):
    """注入租户 Key（环境变量引导路径）+ 可选全局 admin Key"""
    if admin:
        monkeypatch.setenv("ECOMM_API_KEY", admin)
    if tenant_keys:
        env = ",".join(f"{k}:{v}" for k, v in tenant_keys.items())
        monkeypatch.setenv("ECOMM_TENANT_KEYS", env)
    auth_mod.reload_tenant_keys()


def _make_session(tenant_id: str) -> str:
    """直接在内存会话管理器注入一个会话，返回 session_id"""
    state = main_mod._session_manager.create([], product_info="测试商品", tenant_id=tenant_id)
    return state["session_id"]


class TestTenantKeyAuth:
    def test_tenant_key_binds_identity_overrides_header(self, monkeypatch):
        """租户 Key 即身份：X-Tenant-ID 声明被覆盖，无法冒充其他租户"""
        _set_keys(monkeypatch, {"tenant_a": TENANT_A_KEY, "tenant_b": TENANT_B_KEY})
        sid = _make_session("tenant_a")

        # A 的 Key + 声明 B 租户头 → 仍以 A 身份访问（读得到 A 的会话）
        resp = client.get(f"/api/sessions/{sid}",
                          headers={"X-API-Key": TENANT_A_KEY, "X-Tenant-ID": "tenant_b"})
        assert resp.status_code == 200

        # B 的 Key + 声明 A 租户头 → 仍以 B 身份访问（读不到 A 的会话）
        resp = client.get(f"/api/sessions/{sid}",
                          headers={"X-API-Key": TENANT_B_KEY, "X-Tenant-ID": "tenant_a"})
        assert resp.status_code == 404

    def test_tenant_key_list_scoped_to_bound_tenant(self, monkeypatch):
        """会话列表只返回密钥绑定租户的数据"""
        _set_keys(monkeypatch, {"tenant_a": TENANT_A_KEY, "tenant_b": TENANT_B_KEY})
        _make_session("tenant_a")
        _make_session("tenant_b")

        resp = client.get("/api/sessions", headers={"X-API-Key": TENANT_A_KEY})
        assert resp.status_code == 200
        ids = {s["session_id"] for s in resp.json().get("sessions", [])}
        sessions_a = {sid for sid, s in main_mod._session_manager._sessions.items()
                      if s.get("tenant_id") == "tenant_a"}
        assert ids == sessions_a and ids

    def test_admin_key_respects_header(self, monkeypatch):
        """全局 admin Key 保持原语义：租户仍由 X-Tenant-ID 声明"""
        _set_keys(monkeypatch, {"tenant_a": TENANT_A_KEY}, admin=ADMIN_KEY)
        sid = _make_session("tenant_a")

        resp = client.get(f"/api/sessions/{sid}",
                          headers={"X-API-Key": ADMIN_KEY, "X-Tenant-ID": "tenant_a"})
        assert resp.status_code == 200
        resp = client.get(f"/api/sessions/{sid}",
                          headers={"X-API-Key": ADMIN_KEY, "X-Tenant-ID": "tenant_b"})
        assert resp.status_code == 404

    def test_invalid_tenant_key_401(self, monkeypatch):
        _set_keys(monkeypatch, {"tenant_a": TENANT_A_KEY})
        resp = client.get("/api/sessions", headers={"X-API-Key": "wrong-key-123456"})
        assert resp.status_code == 401

    def test_missing_key_401_when_tenant_keys_configured(self, monkeypatch):
        """配置租户 Key 即启用鉴权：无 Key 请求 401（即使没有全局 Key）"""
        _set_keys(monkeypatch, {"tenant_a": TENANT_A_KEY})
        resp = client.get("/api/sessions")
        assert resp.status_code == 401

    def test_dev_mode_open_when_nothing_configured(self, monkeypatch):
        """未配置任何 Key → 开发模式全放行（向后兼容）"""
        monkeypatch.delenv("ECOMM_TENANT_KEYS", raising=False)
        monkeypatch.delenv("ECOMM_API_KEY", raising=False)
        auth_mod.reload_tenant_keys()
        assert client.get("/api/sessions").status_code == 200


class TestTenantKeyAdminBoundary:
    def test_tenant_key_denied_admin_endpoints(self, monkeypatch):
        """租户 Key 无权访问管理端点（admin/status 与 settings）"""
        _set_keys(monkeypatch, {"tenant_a": TENANT_A_KEY}, admin=ADMIN_KEY)

        resp = client.get("/api/admin/status", headers={"X-API-Key": TENANT_A_KEY})
        assert resp.status_code == 403
        resp = client.post("/api/settings/tenant-keys",
                           headers={"X-API-Key": TENANT_A_KEY},
                           json={"tenant_id": "tenant_a", "api_key": "x" * 16})
        assert resp.status_code == 403

        # 全局 Key 放行
        resp = client.get("/api/admin/status", headers={"X-API-Key": ADMIN_KEY})
        assert resp.status_code == 200


class TestTenantKeyManagement:
    def test_create_rotate_delete_tenant_key(self, monkeypatch, tmp_path):
        """管理端点 CRUD：创建 → 轮换（旧 Key 立即失效）→ 删除 → 持久化"""
        _set_keys(monkeypatch, {}, admin=ADMIN_KEY)

        # 创建
        resp = client.post("/api/settings/tenant-keys",
                           headers={"X-API-Key": ADMIN_KEY},
                           json={"tenant_id": "tenant_a", "api_key": TENANT_A_KEY})
        assert resp.status_code == 200 and resp.json()["configured"] is True
        assert config_mod.load_tenant_keys_file() == {"tenant_a": TENANT_A_KEY}
        assert client.get("/api/sessions", headers={"X-API-Key": TENANT_A_KEY}).status_code == 200

        # 轮换：新 Key 生效，旧 Key 失效
        new_key = "tenant-a-key-rotated-999"
        resp = client.post("/api/settings/tenant-keys",
                           headers={"X-API-Key": ADMIN_KEY},
                           json={"tenant_id": "tenant_a", "api_key": new_key})
        assert resp.status_code == 200
        assert client.get("/api/sessions", headers={"X-API-Key": new_key}).status_code == 200
        assert client.get("/api/sessions", headers={"X-API-Key": TENANT_A_KEY}).status_code == 401

        # 删除
        resp = client.post("/api/settings/tenant-keys",
                           headers={"X-API-Key": ADMIN_KEY},
                           json={"tenant_id": "tenant_a", "api_key": ""})
        assert resp.status_code == 200 and resp.json()["configured"] is False
        assert config_mod.load_tenant_keys_file() == {}
        assert client.get("/api/sessions", headers={"X-API-Key": new_key}).status_code == 401

    def test_env_provisioned_key_not_manageable(self, monkeypatch):
        """env 供给的租户 Key 禁止经设置页修改（env 优先级最高，避免改了不生效）"""
        _set_keys(monkeypatch, {"tenant_a": TENANT_A_KEY}, admin=ADMIN_KEY)
        resp = client.post("/api/settings/tenant-keys",
                           headers={"X-API-Key": ADMIN_KEY},
                           json={"tenant_id": "tenant_a", "api_key": "rotated-key-12345678"})
        assert resp.status_code == 403
        # env Key 保持生效（未被设置页修改）
        assert client.get("/api/sessions", headers={"X-API-Key": TENANT_A_KEY}).status_code == 200

    def test_unknown_tenant_403(self, monkeypatch):
        _set_keys(monkeypatch, {}, admin=ADMIN_KEY)
        resp = client.post("/api/settings/tenant-keys",
                           headers={"X-API-Key": ADMIN_KEY},
                           json={"tenant_id": "ghost", "api_key": "k" * 16})
        assert resp.status_code == 403

    def test_short_key_400(self, monkeypatch):
        _set_keys(monkeypatch, {}, admin=ADMIN_KEY)
        resp = client.post("/api/settings/tenant-keys",
                           headers={"X-API-Key": ADMIN_KEY},
                           json={"tenant_id": "tenant_a", "api_key": "short"})
        assert resp.status_code == 400

    def test_settings_payload_masks_keys(self, monkeypatch):
        """设置载荷只返回 configured 布尔与来源，绝不包含密钥内容"""
        _set_keys(monkeypatch, {"tenant_a": TENANT_A_KEY}, admin=ADMIN_KEY)
        resp = client.get("/api/settings", headers={"X-API-Key": ADMIN_KEY})
        assert resp.status_code == 200
        entries = resp.json()["tenant_keys"]
        a = next(e for e in entries if e["tenant_id"] == "tenant_a")
        assert a["configured"] is True
        assert a["source"] == "env"  # 环境变量引导的来源标注
        assert "api_key" not in a and "key" not in a
        assert TENANT_A_KEY not in resp.text

    def test_file_managed_key_source(self, monkeypatch):
        """设置页创建的 Key 来源标注为 file"""
        _set_keys(monkeypatch, {}, admin=ADMIN_KEY)
        client.post("/api/settings/tenant-keys",
                    headers={"X-API-Key": ADMIN_KEY},
                    json={"tenant_id": "tenant_a", "api_key": TENANT_A_KEY})
        entries = client.get("/api/settings", headers={"X-API-Key": ADMIN_KEY}).json()["tenant_keys"]
        a = next(e for e in entries if e["tenant_id"] == "tenant_a")
        assert a["source"] == "file" and a["configured"] is True


class TestTenantKeyWebSocket:
    def test_ws_tenant_key_authenticates_and_binds(self, monkeypatch):
        """WS 携带租户 Key：绑定租户身份（无需 tenant 参数），ping/pong 证明连接建立"""
        _set_keys(monkeypatch, {"tenant_a": TENANT_A_KEY})
        sid = _make_session("tenant_a")

        with client.websocket_connect(f"/ws/sessions/{sid}?api_key={TENANT_A_KEY}") as ws:
            ws.send_text("ping")
            assert ws.receive_text() == "pong"

        # 同一会话，B 的 Key 无法访问（4004，租户不匹配）
        _set_keys(monkeypatch, {"tenant_a": TENANT_A_KEY, "tenant_b": TENANT_B_KEY})
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(f"/ws/sessions/{sid}?api_key={TENANT_B_KEY}"):
                pass
        assert exc.value.code == 4004

    def test_ws_missing_or_wrong_key_4001(self, monkeypatch):
        _set_keys(monkeypatch, {"tenant_a": TENANT_A_KEY})
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect("/ws/sessions/nonexistent123"):
                pass
        assert exc.value.code == 4001
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect("/ws/sessions/nonexistent123?api_key=wrong"):
                pass
        assert exc.value.code == 4001

    def test_ws_admin_key_still_works(self, monkeypatch):
        _set_keys(monkeypatch, {"tenant_a": TENANT_A_KEY}, admin=ADMIN_KEY)
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect("/ws/sessions/nonexistent123?api_key=" + ADMIN_KEY):
                pass
        assert exc.value.code == 4004  # 鉴权通过后走到会话存在性检查
