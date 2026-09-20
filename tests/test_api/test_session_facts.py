"""会话事实补充 —— 让"缺素材被拦下"的信息图能靠用户补信息恢复

背景：信息图文案只取已确认事实（身份卡/分析结论/包装文字/用户填写），缺依据一律 `blocked`。
但用户手上往往有这些信息（知道食用方法、想跟旧包装对比）——此前**真实会话没有入口**把它们喂进去
（只有离线渲染脚本支持），于是被拦下的槽位只能靠人工改代码重跑。
`POST /api/sessions/{id}/facts` 补上这个入口：写进 `task.product_facts`，生图员下次出图即取用。
"""

import base64
import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import src.api.auth as auth_mod
import src.api.main as main_mod
from src.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("ECOMM_OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.delenv("ECOMM_API_KEY", raising=False)
    auth_mod._AUTH_FAILURES.clear()
    yield
    auth_mod._AUTH_FAILURES.clear()


def _png_b64() -> str:
    buf = io.BytesIO()
    Image.new("RGB", (24, 24), (240, 240, 245)).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _session() -> str:
    return main_mod._session_manager.create([_png_b64()], product_info="测试",
                                            platform="pinduoduo")["session_id"]


class TestFactsEndpoint:
    def test_saves_and_reads_back(self):
        sid = _session()
        resp = client.post(f"/api/sessions/{sid}/facts",
                           json={"usage": ["每日 2 粒，飯後服用"], "ingredients": ["水飛薊提取物", "姜黃素"]})
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["product_facts"]["usage"] == ["每日 2 粒，飯後服用"]
        assert payload["product_facts"]["ingredients"] == ["水飛薊提取物", "姜黃素"]
        assert "compare" in payload["available"]

        # 会话详情里能读到（前端据此回显）
        detail = client.get(f"/api/sessions/{sid}").json()
        assert detail["task"]["product_facts"]["usage"] == ["每日 2 粒，飯後服用"]

    def test_accepts_single_string(self):
        sid = _session()
        resp = client.post(f"/api/sessions/{sid}/facts", json={"spec": "60's 一盒"})
        assert resp.status_code == 200
        assert resp.json()["product_facts"]["spec"] == ["60's 一盒"]

    def test_merges_previous_facts(self):
        sid = _session()
        client.post(f"/api/sessions/{sid}/facts", json={"usage": ["每日 2 粒"]})
        resp = client.post(f"/api/sessions/{sid}/facts", json={"compare": ["升級版 vs 普通版"]})
        facts = resp.json()["product_facts"]
        assert facts["usage"] == ["每日 2 粒"] and facts["compare"] == ["升級版 vs 普通版"]

    def test_announces_in_chat(self):
        sid = _session()
        client.post(f"/api/sessions/{sid}/facts", json={"usage": ["每日 2 粒"]})
        detail = client.get(f"/api/sessions/{sid}").json()
        messages = [m for m in detail["messages"] if m["content"].get("facts_updated")]
        assert messages and "已补充事实" in messages[-1]["content"]["message"]

    def test_facts_are_persisted_to_checkpoint(self):
        """补充的事实必须落盘：只在内存里的话，重启/崩溃后就丢了

        （实测踩坑：`SessionManager.update` 是协程，漏 await 时只在内存生效）
        """
        import json as _json
        from src.storage import checkpoint as ckpt
        sid = _session()
        client.post(f"/api/sessions/{sid}/facts", json={"usage": ["每日 2 粒，飯後服用"]})

        path = ckpt._checkpoint_path(sid) if hasattr(ckpt, "_checkpoint_path") else None
        if path is None:  # 兼容不同实现：按数据目录找同名文件
            from src.core.config import data_root
            path = data_root() / "checkpoints" / f"{sid}.json"
        assert path.exists(), f"未找到 checkpoint：{path}"
        saved = _json.loads(path.read_text(encoding="utf-8"))
        assert saved["task"]["product_facts"]["usage"] == ["每日 2 粒，飯後服用"]

    def test_unknown_session_404(self):
        assert client.post("/api/sessions/nope/facts", json={"usage": ["x"]}).status_code == 404

    @pytest.mark.parametrize("payload", [
        {"nope": ["x"]},                       # 未知事实类型
        {"usage": "x" * 200},                  # 条目过长
        {"usage": [f"条目{i}" for i in range(20)]},   # 条数超限
        {"usage": []},                         # 空
        {"usage": ["", "  "]},                 # 全是空白
        "不是对象",
        {},
    ])
    def test_invalid_payload_400(self, payload):
        sid = _session()
        assert client.post(f"/api/sessions/{sid}/facts", json=payload).status_code == 400


class TestFactsUnblockGeneration:
    """补充事实后，原本 blocked 的信息图应能出图（端到端）"""

    @pytest.mark.asyncio
    async def test_user_facts_unblock_blocked_slot(self):
        from src.agents.image_gen import ImageGeneratorAgent
        from src.providers.base import BaseImageProvider

        def _b64(size=(256, 256)) -> str:
            buf = io.BytesIO()
            Image.new("RGB", size, (250, 250, 252)).save(buf, format="PNG")
            return base64.b64encode(buf.getvalue()).decode()

        class _Fake(BaseImageProvider):
            name, capabilities, default_size = "fake", ["image"], "2048x2048"
            supports_reference, supported_options = True, ("watermark",)

            async def generate(self, prompt, negative_prompt="", size="1024x1024", model="",
                               *, reference_images=None, options=None):
                return {"base64_data": _b64(), "model_used": "fake", "cost_usd": 0.04,
                        "reference_count": len(reference_images or []), "ignored_params": []}

        slots = [{"slot_id": "main_usage", "prompt": "版式底图", "background": "#FFFFFF"}]
        base_session = {
            "session_id": "s1", "tenant_id": "default", "turn_count": 2,
            "task": {"platform": "pinduoduo", "product_images": [_b64((32, 32))]},
            "artifacts": {"prompts": {"set_plan": {"platform": "pinduoduo", "slots": slots}},
                          "product_identity": {"status": "confirmed", "source": "vision",
                                               "brand": "DEFOEBUENA®", "product_name": "金裝強力肝迅康",
                                               "spec": "60's", "certifications": []},
                          "analysis": {"features": ["標示「升級版」"]}},
        }

        # 没有事实 → 拦下
        blocked = await ImageGeneratorAgent(provider=_Fake()).execute("出图", base_session)
        assert blocked["images"][0]["text_status"] == "blocked"

        # 用户在会话里补了用法 → 能出图
        base_session["task"]["product_facts"] = {"usage": ["每日 2 粒，飯後以溫水送服"]}
        fixed = await ImageGeneratorAgent(provider=_Fake()).execute("出图", base_session)
        assert fixed["images"][0]["text_status"] == "composed", fixed["images"][0].get("text_reason")
        assert "每日 2 粒" in fixed["images"][0]["slot_copy"]["items"][0]
