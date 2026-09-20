"""会话级槽位子集（`POST /api/sessions` 的 `slots` 字段）

用途（用户 2026-09-18 的成本敏感场景）：**最小付费冒烟**只跑 2 张（≈$0.08）再决定要不要
跑整套 10 张（≈$0.40）；也可用于"只重出某几张"。子集优先于 `config/image.yaml` 的平台
槽位覆盖，且必须一路生效到出图、体检与套图覆盖度。
"""

import asyncio
import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from src.main import app, _agent_registry, _provider_registry

try:
    asyncio.get_event_loop().run_until_complete(
        _agent_registry.load_from_config(_provider_registry)
    )
except RuntimeError:
    pass

client = TestClient(app)


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (800, 800), (255, 255, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


class TestSlotSubsetParsing:
    def _create(self, slots: str):
        return client.post(
            "/api/sessions",
            files={"files": ("p.png", _png(), "image/png")},
            data={"product_info": "子集测试", "platform": "pinduoduo", "mode": "serial",
                  "slots": slots},
        )

    def test_subset_is_stored_on_task(self):
        resp = self._create("main_white,main_selling_point")
        assert resp.status_code in (200, 201), resp.text
        session_id = resp.json()["session_id"]
        try:
            detail = client.get(f"/api/sessions/{session_id}").json()
            assert detail["task"]["slot_override"] == ["main_white", "main_selling_point"]
        finally:
            client.delete(f"/api/sessions/{session_id}")

    def test_blank_subset_means_full_platform_set(self):
        resp = self._create("")
        assert resp.status_code in (200, 201), resp.text
        session_id = resp.json()["session_id"]
        try:
            detail = client.get(f"/api/sessions/{session_id}").json()
            assert not detail["task"].get("slot_override")
        finally:
            client.delete(f"/api/sessions/{session_id}")

    def test_invalid_slot_id_is_400(self):
        resp = self._create("main_white, bad slot!")
        assert resp.status_code == 400
        assert "槽位" in resp.json()["detail"]

    def test_too_many_slots_is_400(self):
        resp = self._create(",".join(f"slot_{i}" for i in range(13)))
        assert resp.status_code == 400


class TestSlotSubsetAffectsImageGen:
    @pytest.mark.asyncio
    async def test_image_gen_only_builds_subset(self):
        from src.agents.image_gen import ImageGeneratorAgent
        from tests.test_agents.test_image_gen_slots import SET_PLAN, _FakeImage

        session = {
            "session_id": "s-subset", "tenant_id": "default", "turn_count": 1,
            "task": {"platform": "taobao", "product_images": [], "slot_override": ["main_white"]},
            "artifacts": {"prompts": SET_PLAN},
        }
        result = await ImageGeneratorAgent(provider=_FakeImage()).execute("生成", session)
        assert [img["slot_id"] for img in result["images"]] == ["main_white"]
        assert result["images"][0]["prompt_number"] == 1
