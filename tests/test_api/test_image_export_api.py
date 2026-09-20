"""图片导出 API（用户反馈：没法设置导出路径）。

覆盖：
- `GET /api/sessions/{id}/images/{index}/download` 单张下载（租户隔离）；
- `GET /api/sessions/{id}/export` 整会话 ZIP；
- `GET /api/settings` 暴露输出目录状态（生效路径/来源/是否可写/env 是否锁定）；
- `POST /api/settings/output` 修改输出根（仅 admin；env 锁定时 403；非法路径 400）。
"""

import base64
import io
import zipfile

import pytest
from fastapi.testclient import TestClient

import src.api.auth as auth_mod
import src.api.main as main_mod
import src.core.config as config_mod
from src.core.tenant import TenantContext, get_tenant_registry
from src.main import app
from src.storage import image_export as ex

client = TestClient(app)
TENANT_KEY = "tenant-a-key-0001-abcdef"
PNG = b"\x89PNG\r\n\x1a\n" + b"p" * 32


def _seed_images(session_id: str, tenant: str = "default", count: int = 2):
    import asyncio
    images = [{"base64_data": base64.b64encode(PNG).decode()} for _ in range(count)]
    return asyncio.run(ex.save_images(session_id, tenant, "taobao", "保健品", images))


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("ECOMM_OUTPUT_DIR", str(tmp_path / "output"))
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


def _session(tenant: str = "default") -> str:
    return main_mod._session_manager.create([], product_info="测试", tenant_id=tenant)["session_id"]


class TestDownloadImage:
    def test_download_single(self):
        sid = _session()
        _seed_images(sid, count=2)

        resp = client.get(f"/api/sessions/{sid}/images/1/download")

        assert resp.status_code == 200
        assert resp.content == PNG
        assert "attachment" in resp.headers["content-disposition"]
        assert resp.headers["content-type"] == "image/png"

    def test_missing_index_404(self):
        sid = _session()
        _seed_images(sid, count=1)

        assert client.get(f"/api/sessions/{sid}/images/9/download").status_code == 404

    def test_unknown_session_404(self):
        assert client.get("/api/sessions/nope/images/1/download").status_code == 404

    def test_cross_tenant_404(self):
        sid = _session(tenant="default")
        _seed_images(sid)

        resp = client.get(f"/api/sessions/{sid}/images/1/download",
                          headers={"X-Tenant-ID": "other"})

        assert resp.status_code == 404, "不得跨租户下载"

    def test_bad_index_400(self):
        sid = _session()
        assert client.get(f"/api/sessions/{sid}/images/abc/download").status_code == 400


class TestSessionZipExport:
    def test_export_zip(self):
        sid = _session()
        _seed_images(sid, count=3)

        resp = client.get(f"/api/sessions/{sid}/export")

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/zip"
        assert "attachment" in resp.headers["content-disposition"]
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            assert len(zf.namelist()) == 3
            assert zf.read(zf.namelist()[0]) == PNG

    def test_export_without_images_404(self):
        sid = _session()
        assert client.get(f"/api/sessions/{sid}/export").status_code == 404

    def test_export_cross_tenant_404(self):
        sid = _session(tenant="default")
        _seed_images(sid)

        resp = client.get(f"/api/sessions/{sid}/export", headers={"X-Tenant-ID": "other"})

        assert resp.status_code == 404


class TestSettingsOutput:
    def test_settings_exposes_status(self):
        data = client.get("/api/settings").json()

        out = data["output"]
        assert out["writable"] is True and out["error"] == ""
        assert out["source"] == "env" and out["env_locked"] is True
        assert out["effective_dir"].endswith("output")

    def test_save_output_dir(self, tmp_path, monkeypatch):
        monkeypatch.delenv("ECOMM_OUTPUT_DIR", raising=False)
        target = tmp_path / "my-exports"

        resp = client.post("/api/settings/output", json={"dir": str(target)})

        assert resp.status_code == 200
        out = resp.json()["output"]
        assert out["source"] == "file"
        assert out["effective_dir"] == str(target)
        assert out["writable"] is True
        assert config_mod.output_root() == target

    def test_clear_restores_default(self, monkeypatch):
        monkeypatch.delenv("ECOMM_OUTPUT_DIR", raising=False)
        client.post("/api/settings/output", json={"dir": ""})

        out = client.get("/api/settings").json()["output"]
        assert out["source"] == "default"
        assert out["effective_dir"].endswith("output")

    def test_env_locked_403(self):
        resp = client.post("/api/settings/output", json={"dir": "D:/whatever"})

        assert resp.status_code == 403
        assert "ECOMM_OUTPUT_DIR" in resp.json()["detail"]

    def test_invalid_dir_400(self, tmp_path, monkeypatch):
        monkeypatch.delenv("ECOMM_OUTPUT_DIR", raising=False)
        blocker = tmp_path / "blocker"
        blocker.write_text("x", encoding="utf-8")

        resp = client.post("/api/settings/output", json={"dir": str(blocker / "sub")})

        assert resp.status_code == 400
        assert "不可用" in resp.json()["detail"] or "无法" in resp.json()["detail"]

    def test_requires_admin(self, monkeypatch):
        monkeypatch.setenv("ECOMM_TENANT_KEYS", f"tenant_a:{TENANT_KEY}")
        auth_mod.reload_tenant_keys()
        monkeypatch.delenv("ECOMM_OUTPUT_DIR", raising=False)

        resp = client.post("/api/settings/output", json={"dir": "D:/x"},
                           headers={"X-API-Key": TENANT_KEY})

        assert resp.status_code == 403

    def test_bad_body_400(self, monkeypatch):
        monkeypatch.delenv("ECOMM_OUTPUT_DIR", raising=False)
        assert client.post("/api/settings/output", json={"dir": 123}).status_code == 400
        assert client.post("/api/settings/output", data="oops").status_code == 400
