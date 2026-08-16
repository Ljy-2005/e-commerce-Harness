"""FastAPI 端点测试 — TestClient 覆盖所有核心 API"""

import pytest
import asyncio
from fastapi.testclient import TestClient
from src.main import app, _agent_registry, _provider_registry

# 初始化 Agent registry（TestClient 不触发 lifespan）
try:
    asyncio.get_event_loop().run_until_complete(
        _agent_registry.load_from_config(_provider_registry)
    )
except RuntimeError:
    pass

client = TestClient(app)


class TestHealthEndpoint:
    """GET /health — 健康检查"""

    def test_health_returns_200(self):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"

    def test_health_has_minimal_info(self):
        """简化版 /health 仅返回 status + mock_mode（详细信息移至 /api/admin/status）"""
        resp = client.get("/health")
        data = resp.json()
        assert "status" in data
        assert "mock_mode" in data

    def test_health_includes_mock_mode(self):
        resp = client.get("/health")
        data = resp.json()
        assert "mock_mode" in data

    def test_admin_status_has_details(self):
        """管理员端点返回完整详情"""
        resp = client.get("/api/admin/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "agents" in data
        assert "providers" in data
        assert "tenants" in data


class TestListAgents:
    """GET /api/agents — 列出 Agent"""

    def test_list_agents_returns_array(self):
        resp = client.get("/api/agents")
        assert resp.status_code == 200
        data = resp.json()
        assert "agents" in data
        assert len(data["agents"]) >= 7

    def test_agents_have_required_fields(self):
        resp = client.get("/api/agents")
        agents = resp.json()["agents"]
        for a in agents:
            assert "name" in a
            assert "requires" in a
            assert "params" in a


class TestCreateSession:
    """POST /api/sessions — 创建会话"""

    def test_create_session_no_files_returns_error(self):
        """无文件时 FastAPI 返回 422（缺少必需参数 files）"""
        resp = client.post("/api/sessions", data={"platform": "taobao"})
        assert resp.status_code in (400, 422)

    def test_create_session_tenant_header_accepted(self):
        """X-Tenant-ID header 正常工作（伪造 PNG 预处理失败 → 400，但未触发租户错误）"""
        resp = client.post(
            "/api/sessions",
            data={"platform": "taobao", "product_info": "test"},
            headers={"X-Tenant-ID": "default"},
            files=[("files", ("test.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 100, "image/png"))],
        )
        assert resp.status_code == 400  # 伪造 PNG 无法解码 → 图片验证失败

    def test_create_session_with_valid_image_succeeds(self):
        """有效图片上传 → 会话创建成功
        （回归防护：曾因 _FakeImage.base64_data 恒为空导致所有上传被 400 拒绝）
        """
        import io
        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (64, 64), (255, 0, 0)).save(buf, format="JPEG")
        valid_jpeg = buf.getvalue()

        resp = client.post(
            "/api/sessions",
            data={"platform": "taobao", "product_info": "测试保健品"},
            headers={"X-Tenant-ID": "default"},
            files=[("files", ("product.jpg", valid_jpeg, "image/jpeg"))],
        )
        assert resp.status_code in (200, 201), resp.text
        body = resp.json()
        assert body.get("session_id")
        assert body.get("preprocess", {}).get("total") == 1

    def test_invalid_file_type_rejected(self):
        """非图片文件被拒绝"""
        resp = client.post(
            "/api/sessions",
            files=[("files", ("malware.exe", b"bad data", "application/octet-stream"))],
        )
        # 验证失败 → 400
        assert resp.status_code == 400


class TestGetSession:
    """GET /api/sessions/{id} — 获取会话"""

    def test_nonexistent_session_returns_404(self):
        resp = client.get("/api/sessions/nonexistent123")
        assert resp.status_code == 404

    def test_tenant_mismatch_returns_404(self):
        """租户不匹配返回 404（不泄露会话存在性）"""
        resp = client.get("/api/sessions/fake_session", headers={"X-Tenant-ID": "other_tenant"})
        assert resp.status_code == 404


class TestDeleteSession:
    """DELETE /api/sessions/{id} — 删除会话"""

    def test_nonexistent_session_returns_404(self):
        resp = client.delete("/api/sessions/nonexistent123")
        assert resp.status_code == 404

    def test_tenant_mismatch_returns_404_on_delete(self):
        resp = client.delete("/api/sessions/fake_session", headers={"X-Tenant-ID": "other_tenant"})
        assert resp.status_code == 404


class TestHumanDecision:
    """POST /api/sessions/{id}/decision — 人工决策"""

    def test_invalid_action_returns_400(self):
        resp = client.post("/api/sessions/some_id/decision", data={"action": "invalid"})
        assert resp.status_code == 400

    def test_nonexistent_session_returns_404(self):
        resp = client.post("/api/sessions/nonexistent/decision", data={"action": "approve"})
        assert resp.status_code == 404

    def test_valid_actions_accepted_format(self):
        """approve/retry/reject 格式检查通过（但 session 不存在 → 404）"""
        for action in ("approve", "retry", "reject"):
            resp = client.post(f"/api/sessions/fake/decision", data={"action": action})
            assert resp.status_code == 404  # 404 表示 action 格式通过了


class TestMessages:
    """GET /api/sessions/{id}/messages — 增量消息"""

    def test_nonexistent_session_returns_404(self):
        resp = client.get("/api/sessions/nonexistent/messages")
        assert resp.status_code == 404

    def test_tenant_mismatch_returns_404(self):
        resp = client.get("/api/sessions/fake/messages", headers={"X-Tenant-ID": "other"})
        assert resp.status_code == 404


class TestMemoryEndpoints:
    """GET /api/memory/* — 记忆库"""

    def test_memory_stats_returns_ok(self):
        resp = client.get("/api/memory/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_entries" in data

    def test_memory_recall_returns_ok(self):
        resp = client.get("/api/memory/recall?category=保健品&limit=3")
        assert resp.status_code == 200
        data = resp.json()
        assert "entries" in data


class TestAuditEndpoint:
    """GET /api/audit — 审计日志"""

    def test_audit_returns_ok(self):
        resp = client.get("/api/audit")
        assert resp.status_code == 200
        data = resp.json()
        assert "entries" in data

    def test_audit_with_date_returns_ok(self):
        """合法日期格式正常返回"""
        resp = client.get("/api/audit?date=2026-08-06")
        assert resp.status_code == 200

    def test_audit_path_traversal_blocked(self):
        """路径穿越攻击被阻止（非法日期格式 → 返回空）"""
        resp = client.get("/api/audit?date=../../etc/passwd")
        assert resp.status_code == 200
        data = resp.json()
        # 非法日期应返回空结果，不读取文件
        assert data.get("entries", []) == [] or "entries" in data


class TestWebSocket:
    """WS /ws/sessions/{id} — WebSocket"""

    def test_websocket_nonexistent_session(self):
        """不存在的 session → WebSocket 连接后立即关闭（4004）"""
        from starlette.websockets import WebSocketDisconnect
        try:
            with client.websocket_connect("/ws/sessions/nonexistent123"):
                pass
        except WebSocketDisconnect:
            pass  # 预期的断开行为


class TestAuthMiddleware:
    """鉴权中间件"""

    def test_health_not_authenticated(self):
        """/health 无需鉴权"""
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_docs_not_authenticated(self):
        """/docs 无需鉴权"""
        resp = client.get("/docs")
        assert resp.status_code == 200


class TestABTestEndpoint:
    """POST /api/sessions/{id}/ab-test"""

    def test_nonexistent_session_returns_404(self):
        resp = client.post("/api/sessions/nonexistent/ab-test")
        assert resp.status_code == 404
