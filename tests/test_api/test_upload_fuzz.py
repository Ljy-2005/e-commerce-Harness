"""L7 安全补缺（test-plan P5）— 上传模糊测试 + 头伪造变体

全部断言"不崩溃"（400/413/401/403 等防护性状态码），无 500。
"""

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from src.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset_auth_failures():
    import src.api.auth as auth_mod
    auth_mod._AUTH_FAILURES.clear()
    yield
    auth_mod._AUTH_FAILURES.clear()


def _upload(content: bytes, filename: str = "f.jpg", content_type: str = "image/jpeg",
            tenant: str = "default", extra_data: dict | None = None):
    data = {"platform": "taobao", **(extra_data or {})}
    return client.post(
        "/api/sessions",
        data=data,
        headers={"X-Tenant-ID": tenant},
        files=[("files", (filename, content, content_type))],
    )


def _jpeg_bytes(size=(32, 32)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(buf, format="JPEG")
    return buf.getvalue()


class TestUploadFuzz:
    """截断/炸弹/空文件/超长名/超大文件 → 防护性状态码，绝不 500"""

    def test_truncated_jpeg_400(self):
        resp = _upload(b"\xff\xd8\xff\xe0" + b"\x00" * 64)
        assert resp.status_code == 400, resp.text
        assert "detail" in resp.json()

    def test_truncated_png_400(self):
        resp = _upload(b"\x89PNG\r\n\x1a\n" + b"\x00" * 128, filename="f.png",
                       content_type="image/png")
        assert resp.status_code == 400, resp.text

    def test_decompression_bomb_header_400(self):
        """解压炸弹：PNG IHDR 声明 25000x25000 但无像素数据 → 头部尺寸校验拦截"""
        import struct
        import zlib

        def _chunk(ctype: bytes, data: bytes) -> bytes:
            return (struct.pack(">I", len(data)) + ctype + data
                    + struct.pack(">I", zlib.crc32(ctype + data) & 0xFFFFFFFF))

        ihdr = struct.pack(">IIBBBBB", 25000, 25000, 8, 2, 0, 0, 0)
        bomb = b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IEND", b"")
        resp = _upload(bomb, filename="bomb.png", content_type="image/png")
        assert resp.status_code == 400, resp.text
        assert "炸弹" in resp.json()["detail"]

    def test_empty_file_400(self):
        resp = _upload(b"", filename="empty.jpg")
        assert resp.status_code == 400, resp.text

    def test_rgba_odd_width_ok(self):
        """RGBA 奇数宽度 → 正常转 RGB 处理，不崩溃"""
        buf = io.BytesIO()
        Image.new("RGBA", (63, 32), (255, 0, 0, 128)).save(buf, format="PNG")
        resp = _upload(buf.getvalue(), filename="odd.png", content_type="image/png")
        assert resp.status_code in (200, 201), resp.text

    def test_oversized_file_400(self):
        """>10MB（预处理上限）→ 400 文件过大；不整体读入内存"""
        resp = _upload(_jpeg_bytes() + b"\x00" * (11 * 1024 * 1024))
        assert resp.status_code == 400, resp.text
        assert "过大" in resp.json()["detail"]

    def test_very_long_filename_ok(self):
        """超长文件名（300 字符）→ 不崩溃"""
        name = "x" * 300 + ".jpg"
        resp = _upload(_jpeg_bytes(), filename=name)
        assert resp.status_code in (200, 201), resp.text


class TestHeaderForgery:
    """头伪造变体：大小写/URL 编码/重复头/Key 前导空格/Bearer 畸形"""

    def test_tenant_header_case_insensitive(self):
        """HTTP 头大小写不敏感：小写 x-tenant-id 与 X-Tenant-ID 等价"""
        resp = client.get("/api/sessions", headers={"x-tenant-id": "default"})
        assert resp.status_code == 200

    def test_tenant_url_encoded_403(self):
        """URL 编码的租户头按字面处理（不解码不穿越）→ 未知租户 403"""
        resp = _upload(_jpeg_bytes(), tenant="default%2F..%2F..")
        assert resp.status_code == 403, resp.text

    def test_duplicate_tenant_header_first_wins(self):
        """重复租户头：取第一个，不崩溃"""
        resp = client.get(
            "/api/sessions",
            headers=[("X-Tenant-ID", "default"), ("X-Tenant-ID", "tenant_x")],
        )
        assert resp.status_code == 200

    def test_api_key_leading_space_401(self, monkeypatch):
        """X-API-Key 前导空格 → 不做 trim，按字面比较 → 401"""
        monkeypatch.setenv("ECOMM_API_KEY", "secret-123")
        resp = client.get("/api/sessions", headers={"X-API-Key": " secret-123"})
        assert resp.status_code == 401

    def test_malformed_bearer_401(self, monkeypatch):
        monkeypatch.setenv("ECOMM_API_KEY", "secret-123")
        # 无 token 的 Bearer
        assert client.get("/api/sessions", headers={"Authorization": "Bearer"}).status_code == 401
        # 非 Bearer 前缀
        assert client.get(
            "/api/sessions", headers={"Authorization": "BearerX secret-123"}
        ).status_code == 401
        # 普通文本头
        assert client.get(
            "/api/sessions", headers={"Authorization": "secret-123"}
        ).status_code == 401
