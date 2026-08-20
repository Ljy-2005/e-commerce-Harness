"""鉴权矩阵测试（审计修复：401/429/WS/管理面此前零覆盖）

覆盖：配置 ECOMM_API_KEY 后的 401/错 Key/200、暴力破解 429、
WS ?api_key= 鉴权（4001/4004）、管理面非本机 403。
"""

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import src.api.auth as auth_mod
import src.main as main_mod
from src.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset_auth_failures():
    """每个测试前清空 IP 失败字典（模块级状态）"""
    auth_mod._AUTH_FAILURES.clear()
    yield
    auth_mod._AUTH_FAILURES.clear()


class TestAuthMatrix:
    def test_no_key_401(self, monkeypatch):
        monkeypatch.setenv("ECOMM_API_KEY", "secret-123")
        resp = client.get("/api/sessions")
        assert resp.status_code == 401

    def test_wrong_key_401(self, monkeypatch):
        monkeypatch.setenv("ECOMM_API_KEY", "secret-123")
        resp = client.get("/api/sessions", headers={"X-API-Key": "wrong"})
        assert resp.status_code == 401
        # Bearer 方式同样
        resp = client.get("/api/sessions", headers={"Authorization": "Bearer wrong"})
        assert resp.status_code == 401

    def test_valid_key_200(self, monkeypatch):
        monkeypatch.setenv("ECOMM_API_KEY", "secret-123")
        resp = client.get("/api/sessions", headers={"X-API-Key": "secret-123"})
        assert resp.status_code == 200
        resp = client.get("/api/sessions", headers={"Authorization": "Bearer secret-123"})
        assert resp.status_code == 200

    def test_health_public_with_key(self, monkeypatch):
        monkeypatch.setenv("ECOMM_API_KEY", "secret-123")
        assert client.get("/health").status_code == 200
        assert client.get("/docs").status_code == 200

    def test_bruteforce_429(self, monkeypatch):
        """同一 IP 连续失败 > 10 次 → 429"""
        monkeypatch.setenv("ECOMM_API_KEY", "secret-123")
        for _ in range(10):
            resp = client.get("/api/sessions", headers={"X-API-Key": "wrong"})
            assert resp.status_code == 401
        resp = client.get("/api/sessions", headers={"X-API-Key": "wrong"})
        assert resp.status_code == 429

    def test_no_key_dev_mode_open(self):
        """未配置 ECOMM_API_KEY → 开发模式全放行"""
        import os
        if os.getenv("ECOMM_API_KEY"):
            pytest.skip("环境已配置 ECOMM_API_KEY")
        assert client.get("/api/sessions").status_code == 200


class TestWebSocketAuth:
    def test_ws_without_key_closed_4001(self, monkeypatch):
        monkeypatch.setenv("ECOMM_API_KEY", "secret-123")
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect("/ws/sessions/nonexistent123"):
                pass
        assert exc.value.code == 4001

    def test_ws_with_wrong_key_closed_4001(self, monkeypatch):
        monkeypatch.setenv("ECOMM_API_KEY", "secret-123")
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect("/ws/sessions/nonexistent123?api_key=wrong"):
                pass
        assert exc.value.code == 4001

    def test_ws_with_valid_key_reaches_session_check(self, monkeypatch):
        """鉴权通过后走到会话存在性检查（4004），证明 api_key 生效"""
        monkeypatch.setenv("ECOMM_API_KEY", "secret-123")
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect("/ws/sessions/nonexistent123?api_key=secret-123"):
                pass
        assert exc.value.code == 4004


class TestAdminGuard:
    def _fake_request(self, host):
        from starlette.requests import Request
        scope = {
            "type": "http", "method": "GET", "path": "/api/admin/status",
            "headers": [], "query_string": b"", "scheme": "http",
            "server": ("test", 80), "root_path": "", "client": (host, 1234),
        }
        return Request(scope)

    def test_remote_blocked_without_key(self, monkeypatch):
        """未配置 Key 时管理面仅允许本机：远程 403"""
        monkeypatch.delenv("ECOMM_API_KEY", raising=False)
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            main_mod._require_admin_access(self._fake_request("8.8.8.8"))
        assert exc.value.status_code == 403

    def test_local_allowed_without_key(self, monkeypatch):
        monkeypatch.delenv("ECOMM_API_KEY", raising=False)
        for host in ("127.0.0.1", "::1", "localhost", "testclient"):
            main_mod._require_admin_access(self._fake_request(host))  # 不抛异常

    def test_remote_allowed_with_key(self, monkeypatch):
        """配置 Key 后由 AuthMiddleware 全局保护，管理面守卫放行"""
        monkeypatch.setenv("ECOMM_API_KEY", "secret-123")
        main_mod._require_admin_access(self._fake_request("8.8.8.8"))  # 不抛异常
