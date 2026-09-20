"""平台档案 + 生图质量策略 API

用户要求：
- "我可能还会做拼多多的商品图，他们的风格也要写上" → `GET /api/platforms`（前端选择器与
  套图槽位的单一事实来源，新增平台只改 config/platforms.yaml）；
- 生图的文字策略/参考图模式/水印要能自己设置 → `POST /api/settings/image`（白名单校验，
  写入独立文件 config/image.yaml，**不改写 models.yaml** —— 那会丢掉文件里全部注释）；
- 能不能先试一张看看 → `POST /api/settings/image/probe`（真调一次生图 + 本地体检）。
"""

import pytest
from fastapi.testclient import TestClient

import src.api.auth as auth_mod
import src.api.main as main_mod
import src.core.config as config_mod
from src.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "IMAGE_CONFIG_REL", str(tmp_path / "image.yaml"))
    monkeypatch.setattr(config_mod, "CHAT_CONFIG_REL", str(tmp_path / "chat.yaml"))
    monkeypatch.delenv("ECOMM_API_KEY", raising=False)
    auth_mod._AUTH_FAILURES.clear()
    yield
    auth_mod._AUTH_FAILURES.clear()


class TestPlatformsEndpoint:
    def test_lists_pinduoduo_with_style(self):
        resp = client.get("/api/platforms")
        assert resp.status_code == 200
        items = {item["slug"]: item for item in resp.json()["platforms"]}
        assert "pinduoduo" in items
        assert items["pinduoduo"]["label"] == "拼多多"
        assert items["pinduoduo"]["text_policy"] == "none"
        assert items["pinduoduo"]["slots"][0] == "main_white"
        assert items["taobao"]["is_default"] is True

    def test_sorted_with_default_first(self):
        items = client.get("/api/platforms").json()["platforms"]
        assert items[0]["slug"] == "taobao"


class TestImageSettingsEndpoint:
    def test_settings_payload_includes_image_policy(self):
        payload = client.get("/api/settings").json()
        assert "image" in payload
        assert payload["image"]["text_strategy"] in ("preserve", "blur", "none")

    def test_save_and_read_back(self):
        resp = client.post("/api/settings/image", json={"text_strategy": "blur",
                                                        "watermark": True})
        assert resp.status_code == 200
        assert resp.json()["image"]["text_strategy"] == "blur"
        assert resp.json()["image"]["watermark"] is True

    def test_unknown_field_400(self):
        assert client.post("/api/settings/image", json={"nope": 1}).status_code == 400

    @pytest.mark.parametrize("payload", [
        {"text_strategy": "乱写"},
        {"reference_mode": "乱写"},
        {"max_references": 99},
        {"watermark": "也许"},
        {"size": "2048x2048; rm -rf /"},
        {"platforms": {"taobao": {"slots": "not-a-list"}}},
    ])
    def test_invalid_value_400(self, payload):
        assert client.post("/api/settings/image", json=payload).status_code == 400

    def test_empty_body_400(self):
        assert client.post("/api/settings/image", json={}).status_code == 400

    def test_platform_slot_override_persisted(self):
        resp = client.post("/api/settings/image",
                           json={"platforms": {"pinduoduo": {"slots": ["main_white"]}}})
        assert resp.status_code == 200
        assert resp.json()["image"]["platforms"]["pinduoduo"]["slots"] == ["main_white"]

    def test_does_not_rewrite_models_yaml(self):
        models = config_mod._project_root() / "config" / "models.yaml"
        before = models.read_bytes()
        client.post("/api/settings/image", json={"text_strategy": "none"})
        assert models.read_bytes() == before


class TestProbeEndpoint:
    def test_missing_credentials_400_with_guidance(self):
        """没有真实凭据时必须**先拦下来**（此前会带着空 Bearer 发真实请求，
        抛出 httpx `Illegal header value b'Bearer '` 这种看不懂的错）"""
        resp = client.post("/api/settings/image/probe", json={"route": "ark"})
        assert resp.status_code == 400
        assert "ARK_API_KEY" in resp.text or "凭据" in resp.text

    def test_unknown_route_404(self):
        resp = client.post("/api/settings/image/probe", json={"route": "nonexistent-route"})
        assert resp.status_code == 404
