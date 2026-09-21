"""错误响应契约测试（test-plan P3 L3）

全端点错误响应统一为 `{"detail": str | [ValidationError]}` 形态。
抽查 ~26 个代表性错误路径（404/400/413/403/401/429/422/503），
防个别端点返回裸字符串/空体/HTML 等非常规形态。
"""

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from src.main import _session_manager, app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset_auth_failures():
    """清空 IP 失败字典（模块级状态，防 429 测试交叉污染）"""
    import src.api.auth as auth_mod
    auth_mod._AUTH_FAILURES.clear()
    yield
    auth_mod._AUTH_FAILURES.clear()


def _assert_error_shape(resp, status=None):
    """统一断言：状态码 + {"detail": str | list[dict(msg...)]}"""
    if status is not None:
        assert resp.status_code == status, f"{resp.status_code} != {status}: {resp.text[:300]}"
    assert resp.status_code >= 400
    body = resp.json()
    assert isinstance(body, dict), f"错误响应不是对象: {body!r}"
    assert "detail" in body, f"错误响应缺少 detail 字段: {body!r}"
    detail = body["detail"]
    assert isinstance(detail, (str, list)), f"detail 类型异常: {type(detail)!r}"
    if isinstance(detail, list):
        assert all(isinstance(item, dict) and "msg" in item for item in detail)


def _make_jpeg(size=(32, 32)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (1, 2, 3)).save(buf, format="JPEG")
    return buf.getvalue()


class Test404Contract:
    """不存在资源 → 404 + detail"""

    def test_session_resource_404s(self):
        for method, path in [
            ("GET", "/api/sessions/nonexistent1"),
            ("GET", "/api/sessions/nonexistent1/messages"),
            ("DELETE", "/api/sessions/nonexistent1"),
            ("POST", "/api/sessions/nonexistent1/interject"),
        ]:
            resp = getattr(client, method.lower())(path)
            _assert_error_shape(resp, 404)

    def test_decision_404_with_valid_action(self):
        resp = client.post("/api/sessions/nonexistent1/decision", data={"action": "approve"})
        _assert_error_shape(resp, 404)

    def test_ab_test_404(self):
        resp = client.post("/api/sessions/nonexistent1/ab-test", json={"variants": []})
        _assert_error_shape(resp, 404)

    def test_workflow_resource_404s(self):
        _assert_error_shape(client.get("/api/workflows/jobs/no-such-job"), 404)
        _assert_error_shape(
            client.post("/api/workflows/jobs/no-such-job/control", json={"action": "pause"}), 404
        )
        _assert_error_shape(client.get("/api/workflows/batches/no-such-batch"), 404)

    def test_instantiate_unknown_template_404(self):
        resp = client.post("/api/workflows/templates/no_such_template/instantiate", json={})
        _assert_error_shape(resp, 404)


class Test400Contract:
    """参数/状态非法 → 400 + detail"""

    def test_session_validation_400s(self):
        s = _session_manager.create(product_images=["fake_b64"], tenant_id="default")
        sid = s["session_id"]
        resp = client.post(f"/api/sessions/{sid}/decision", data={"action": "nonsense"})
        _assert_error_shape(resp, 400)
        resp = client.post(f"/api/sessions/{sid}/interject", json={"content": "   "})
        _assert_error_shape(resp, 400)
        resp = client.post(f"/api/sessions/{sid}/interject", data="bad")
        _assert_error_shape(resp, 400)
        resp = client.post(
            f"/api/sessions/{sid}/ab-test",
            json={"variants": [{"variant_id": f"v{i}"} for i in range(9)]},
        )
        _assert_error_shape(resp, 400)

    def test_settings_validation_400s(self):
        resp = client.post("/api/settings/api-keys", data="bad")
        _assert_error_shape(resp, 400)
        resp = client.post("/api/settings/api-keys", json={"api_keys": "oops"})
        _assert_error_shape(resp, 400)
        resp = client.post("/api/settings/agents/商品分析员", json={"defaults": {"not_a_param": 1}})
        _assert_error_shape(resp, 400)
        resp = client.post("/api/settings/models", json={"capabilities": "x"})
        _assert_error_shape(resp, 400)

    def test_workflow_validation_400s(self):
        resp = client.post("/api/workflows/templates/import", json={"yaml": "name: [broken"})
        _assert_error_shape(resp, 400)
        resp = client.post("/api/workflows/batches", json={"items": "not-a-list"})
        _assert_error_shape(resp, 400)
        resp = client.post(
            "/api/workflows/batches",
            json={"items": [{}] * 101, "template_name": "scene_suite"},
        )
        _assert_error_shape(resp, 400)


class TestUploadAndTenantContract:
    def test_oversized_upload_413(self):
        resp = client.post(
            "/api/sessions",
            data={"platform": "taobao"},
            headers={"X-Tenant-ID": "default"},
            files=[("files", ("big.jpg", b"\x00" * (20 * 1024 * 1024 + 1), "image/jpeg"))],
        )
        _assert_error_shape(resp, 413)

    def test_invalid_image_400(self):
        resp = client.post(
            "/api/sessions",
            data={"platform": "taobao"},
            headers={"X-Tenant-ID": "default"},
            files=[("files", ("fake.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 64, "image/png"))],
        )
        _assert_error_shape(resp, 400)

    def test_unknown_tenant_403(self):
        resp = client.post(
            "/api/sessions",
            data={"platform": "taobao"},
            headers={"X-Tenant-ID": "no_such_tenant_xyz"},
            files=[("files", ("p.jpg", _make_jpeg(), "image/jpeg"))],
        )
        _assert_error_shape(resp, 403)


class TestAuthContract:
    def test_no_key_401(self, monkeypatch):
        monkeypatch.setenv("ECOMM_API_KEY", "secret-123")
        _assert_error_shape(client.get("/api/sessions"), 401)

    def test_wrong_key_401(self, monkeypatch):
        monkeypatch.setenv("ECOMM_API_KEY", "secret-123")
        _assert_error_shape(client.get("/api/sessions", headers={"X-API-Key": "wrong"}), 401)
        _assert_error_shape(
            client.get("/api/sessions", headers={"Authorization": "Bearer wrong"}), 401
        )

    def test_bruteforce_429(self, monkeypatch):
        monkeypatch.setenv("ECOMM_API_KEY", "secret-123")
        for _ in range(10):
            client.get("/api/sessions", headers={"X-API-Key": "wrong"})
        _assert_error_shape(client.get("/api/sessions", headers={"X-API-Key": "wrong"}), 429)

    def test_webhook_disabled_503(self, monkeypatch):
        """未配置 ECOMM_WEBHOOK_TOKEN → 回调端点 503（停用），形态统一"""
        monkeypatch.delenv("ECOMM_WEBHOOK_TOKEN", raising=False)
        resp = client.post(
            "/api/webhooks/workflows/some-job/decision",
            json={"action": "approve"},
            headers={"X-Webhook-Token": "x"},
        )
        _assert_error_shape(resp, 503)


class Test422Contract:
    def test_missing_required_field_422(self):
        """FastAPI 校验失败 → 422 + detail 列表（含 msg）"""
        resp = client.post("/api/sessions", data={"platform": "taobao"})
        _assert_error_shape(resp, 422)

    def test_valid_image_success_unchanged(self):
        """成功路径不受影响（回归护栏）"""
        resp = client.post(
            "/api/sessions",
            data={"platform": "taobao"},
            headers={"X-Tenant-ID": "default"},
            files=[("files", ("p.jpg", _make_jpeg(), "image/jpeg"))],
        )
        assert resp.status_code in (200, 201)
        assert resp.json().get("session_id")
