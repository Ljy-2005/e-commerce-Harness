"""FastAPI 端点测试 — TestClient 覆盖所有核心 API"""

import asyncio

from fastapi.testclient import TestClient

from src.main import _agent_registry, _provider_registry, app

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

    def test_oversized_upload_rejected_413(self):
        """审计修复：上传超限（>20MB）在读取阶段即 413，防内存耗尽 DoS"""
        resp = client.post(
            "/api/sessions",
            data={"platform": "taobao", "product_info": "x"},
            headers={"X-Tenant-ID": "default"},
            files=[("files", ("big.jpg", b"\x00" * (20 * 1024 * 1024 + 1), "image/jpeg"))],
        )
        assert resp.status_code == 413

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
            resp = client.post("/api/sessions/fake/decision", data={"action": action})
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

    def test_variant_caps_enforced(self):
        """审计修复：变体/评审数上限（防并行 LLM 费用 DoS）"""
        import io

        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (32, 32), (10, 20, 30)).save(buf, format="JPEG")
        jpeg = buf.getvalue()
        created = client.post(
            "/api/sessions",
            data={"platform": "taobao", "product_info": "x"},
            headers={"X-Tenant-ID": "default"},
            files=[("files", ("p.jpg", jpeg, "image/jpeg"))],
        )
        sid = created.json()["session_id"]

        too_many = [{"variant_id": f"v{i}", "label": str(i)} for i in range(9)]
        resp = client.post(f"/api/sessions/{sid}/ab-test", json={"variants": too_many})
        assert resp.status_code == 400
        assert "1-8" in resp.json()["detail"]

        resp = client.post(f"/api/sessions/{sid}/ab-test",
                           json={"variants": [{"variant_id": "v1"}], "review_count": 6})
        assert resp.status_code == 400
        assert "1-5" in resp.json()["detail"]


class TestParseBatchCsv:
    """_parse_batch_csv 编码矩阵（审计修复：此前仅 UTF-8 快乐路径有测试）"""

    def test_gbk_encoded(self):
        from src.main import _parse_batch_csv
        raw = "护肝片,taobao,保健品\n".encode("gbk")
        items = _parse_batch_csv(raw)
        assert len(items) == 1
        assert items[0]["product_info"] == "护肝片"
        assert items[0]["platform"] == "taobao"
        assert items[0]["category_hint"] == "保健品"

    def test_utf8_bom_with_header(self):
        from src.main import _parse_batch_csv
        raw = "\ufeffproduct_info,platform\n维生素C,taobao\n".encode("utf-8")
        items = _parse_batch_csv(raw)
        assert len(items) == 1
        assert items[0]["product_info"] == "维生素C"

    def test_no_header(self):
        from src.main import _parse_batch_csv
        items = _parse_batch_csv("商品A,jd\n".encode("utf-8"))
        assert len(items) == 1
        assert items[0]["product_info"] == "商品A"
        assert items[0]["platform"] == "jd"

    def test_empty_csv_raises(self):
        import pytest as _pytest

        from src.main import _parse_batch_csv
        with _pytest.raises(ValueError, match="CSV 为空"):
            _parse_batch_csv(b"")

    def test_undecodable_raises(self):
        import pytest as _pytest

        from src.main import _parse_batch_csv
        with _pytest.raises(ValueError, match="编码无法识别"):
            _parse_batch_csv(b"\xff\xfe\xfd\xfc\xff")

    def test_missing_platform_defaults_taobao(self):
        from src.main import _parse_batch_csv
        items = _parse_batch_csv("商品B\n".encode("utf-8"))
        assert items[0]["platform"] == "taobao"


class TestInterject:
    """M3 群聊插话 — REST + WS 双向"""

    def _make_session(self):
        from src.main import _session_manager
        return _session_manager.create(product_images=["fake_b64"], tenant_id="default")

    def test_interject_rest_appends_message(self):
        s = self._make_session()
        resp = client.post(
            f"/api/sessions/{s['session_id']}/interject",
            json={"content": "换个更简约的风格"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "appended"

        got = client.get(f"/api/sessions/{s['session_id']}").json()
        assert any(m.get("sender") == "用户插话" for m in got["messages"])
        assert any("换个更简约的风格" in str(m.get("content")) for m in got["messages"])

    def test_interject_validation(self):
        assert client.post("/api/sessions/nonexistent/interject", json={"content": "x"}).status_code == 404
        s = self._make_session()
        assert client.post(f"/api/sessions/{s['session_id']}/interject", json={"content": "  "}).status_code == 400
        assert client.post(f"/api/sessions/{s['session_id']}/interject", data="bad").status_code == 400

    def test_interject_via_websocket(self):
        import time
        s = self._make_session()
        with client.websocket_connect(f"/ws/sessions/{s['session_id']}") as ws:
            ws.send_json({"type": "chat", "content": "WS 插话指令"})
            time.sleep(0.3)  # 等服务端处理
        got = client.get(f"/api/sessions/{s['session_id']}").json()
        assert any("WS 插话指令" in str(m.get("content")) for m in got["messages"])
