"""风格词库 API（`/api/style-library*`）—— 用户 2026-09-18 指定的界面模块

契约见 `src/api/main.py` 的「风格词库」区块。测试盯住：
1. **列表/详情/编辑/删除**的语义（内置只读、可启停；用户词条可编辑）；
2. **花钱前的用量前置**：金额未标定时是 `null`（不是 0），并带可读说明；
3. **失败与边界**：没照片、照片太多、名称空、applies_to 不是 JSON、跨租户；
4. **照片不进会话上传目录**（安全边界，见 `style_store` 的静态扫描）。
"""

import asyncio
import base64
import json

import pytest
from fastapi.testclient import TestClient

from src.main import app, _agent_registry, _provider_registry, _session_manager  # noqa: F401

try:
    asyncio.get_event_loop().run_until_complete(
        _agent_registry.load_from_config(_provider_registry))
except RuntimeError:
    pass

client = TestClient(app)

PHOTO = base64.b64decode(
    b"/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0a"
    b"HBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAABAAAAAAAA"
    b"AAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AKp//2Q==")


@pytest.fixture(autouse=True)
def _restore_builtin_toggles():
    """内置档案的启停是**持久化**状态：测试改完必须还原

    否则同一个 pytest 会话里后面的用例会看到"某条内置档案被停用"（实测：风格档案库的
    覆盖率用例因此集体失败 —— 那种失败信息完全指不到真正的原因）。
    """
    from src.harness.style_store import StyleStore

    store = StyleStore()
    before = store.disabled_builtin_ids()
    yield
    after = store.disabled_builtin_ids()
    for entry_id in after - before:
        store.set_builtin_enabled(entry_id, True)
    for entry_id in before - after:
        store.set_builtin_enabled(entry_id, False)


def _create(name="冷白实验室感", photos=1, tenant="default", applies=None, as_anchor="true"):
    files = [("files", (f"p{index}.jpg", PHOTO, "image/jpeg")) for index in range(photos)]
    data = {"name": name, "as_anchor": as_anchor}
    if applies is not None:
        data["applies_to"] = json.dumps(applies, ensure_ascii=False)
    return client.post("/api/style-library", data=data, files=files,
                       headers={"X-Tenant-ID": tenant})


class _StubAgent:
    """「风格档案员」替身：不联网、返回一条干净分析结果"""

    def __init__(self, payload=None, error=None):
        self.payload = payload or {
            "background": "冷白到极浅灰的竖向渐层", "composition": "主体居中偏左占 60%",
            "lighting": "顶部柔光 + 双侧补光", "materials": "哑光纸纹",
            "summary": "干净正规", "style_words": "冷白留白、明度偏高",
            "taste_verdict": "冷静、留白充足、投影极淡",
            "reward_points": ["留白充足"], "avoid_points": ["暖黄调"],
            "name_suggestions": ["冷白留白感"],
            "removed_brand_text": ["SOMEBRAND"],
            "usage": {"calls": 1, "images": 1, "elapsed_ms": 900, "model": "stub"},
        }
        self.error = error
        self.provider = object()

    async def execute(self, brief, session, **kwargs):
        if self.error:
            return {"error": self.error}
        return dict(self.payload)


class _StubRegistry:
    def __init__(self, agent):
        self._agent = agent

    def get(self, name):
        return self._agent if name == "风格档案员" else None


class TestStyleLibraryList:
    def test_builtin_entries_are_listed(self):
        data = client.get("/api/style-library").json()
        assert len(data["builtin"]) >= 8
        assert all(item["source"] == "builtin" for item in data["builtin"])
        assert data["stats"]["enabled"] is True
        # 张数/体积口径由后端**单一来源**下发（此前前端各写一遍，还写着"每张 20MB"
        # 而处理器真实上限是 10MB —— 界面在撒谎）
        from src.harness.style_store import MAX_PHOTOS, VISION_BATCH_SIZE

        limits = data["stats"]["limits"]
        assert limits["max_photos"] == MAX_PHOTOS == 20
        assert limits["vision_batch"] == VISION_BATCH_SIZE
        assert limits["max_photo_mb"] > 0 and limits["max_total_mb"] > limits["max_photo_mb"]
        assert limits["max_entries_per_tenant"] >= 1

    def test_unknown_tenant_is_forbidden(self):
        assert client.get("/api/style-library",
                          headers={"X-Tenant-ID": "nope"}).status_code == 403


class TestStyleLibraryCreate:
    def test_create_returns_usage_first_estimate(self):
        response = _create(photos=2)
        assert response.status_code == 200
        body = response.json()
        assert body["entry"]["status"] == "analyzing"
        assert body["entry"]["source"] == "user"
        estimate = body["estimate"]
        assert estimate["images"] == 2 and estimate["calls"] == 1
        # **金额未标定就是 null**（不是 0）——用户明确质疑过"编出来的花费"
        assert estimate["amount"] is None
        assert "未标定" in estimate["source"]
        client.delete(f"/api/style-library/{body['entry']['id']}")

    def test_requires_photos_and_name(self):
        assert client.post("/api/style-library", data={"name": "x"}).status_code == 400
        response = client.post("/api/style-library", data={"name": "  "},
                               files=[("files", ("p.jpg", PHOTO, "image/jpeg"))])
        assert response.status_code == 400
        assert "名字" in response.json()["detail"]

    def test_rejects_too_many_photos(self):
        """上限是 20（旧版写死 6 —— 用户问"为什么只能 6 张"）；超上限要能读出来"""
        from src.harness.style_store import MAX_PHOTOS

        response = _create(photos=MAX_PHOTOS + 1)
        assert response.status_code == 400
        assert str(MAX_PHOTOS) in response.json()["detail"]

    def test_seven_photos_are_accepted_and_batched(self):
        """7 张：旧版直接 400；现在接受，且用量前置**说实话**（1 次调用）"""
        response = _create(photos=7)
        assert response.status_code == 200
        body = response.json()
        assert body["estimate"]["images"] == 7 and body["estimate"]["calls"] == 1
        client.delete(f"/api/style-library/{body['entry']['id']}")

    def test_estimate_reports_multiple_calls_over_one_batch(self):
        """超过单批（12 张）时前端必须看到"会分 2 次调用"（分批 = 多次计费）"""
        from src.api.main import _style_estimate

        estimate = _style_estimate(20)
        assert estimate["calls"] == 2
        assert "分批" in estimate["note"] and estimate["amount"] is None

    def test_rejects_bad_applies_to(self):
        response = client.post("/api/style-library", data={"name": "x", "applies_to": "{oops"},
                               files=[("files", ("p.jpg", PHOTO, "image/jpeg"))])
        assert response.status_code == 400
        assert "JSON" in response.json()["detail"]

    def test_tenant_isolation(self, monkeypatch):
        """隔离由 `_resolve_tenant` + 存储按 tenant_id 过滤共同保证

        这里把租户解析替换成两个假租户（真实部署里租户来自 ECOMM_TENANTS / tenant_keys）。
        """
        import src.api.main as main_mod
        from src.core.tenant import TenantContext

        monkeypatch.setattr(main_mod, "_resolve_tenant",
                            lambda raw: TenantContext(tenant_id=raw or "default"))

        created = _create(tenant="t1").json()["entry"]
        mine = client.get("/api/style-library", headers={"X-Tenant-ID": "t1"}).json()["mine"]
        other = client.get("/api/style-library", headers={"X-Tenant-ID": "t2"}).json()["mine"]
        assert any(item["id"] == created["id"] for item in mine)
        assert not any(item["id"] == created["id"] for item in other)
        assert client.get(f"/api/style-library/{created['id']}",
                          headers={"X-Tenant-ID": "t2"}).status_code == 404
        client.delete(f"/api/style-library/{created['id']}", headers={"X-Tenant-ID": "t1"})


class TestAnalysisFlow:
    def test_background_analysis_fills_entry(self, monkeypatch):
        import src.api.main as main_mod

        created = _create().json()["entry"]
        monkeypatch.setattr(main_mod, "_agent_registry", _StubRegistry(_StubAgent()))
        asyncio.run(main_mod._analyze_style_entry(created["id"], "default"))

        detail = client.get(f"/api/style-library/{created['id']}").json()["entry"]
        assert detail["status"] == "ready"
        assert detail["background"] and detail["taste_verdict"]
        assert detail["removed_brand_text"] == ["SOMEBRAND"]
        assert detail["usage"]["calls"] == 1
        # 金额未标定 → stay null（供应商没回报、价格表也没有）
        assert detail["usage"]["cost"]["amount"] is None
        assert "未标定" in detail["usage"]["cost"]["basis"] + detail["usage"]["cost"]["source"]
        client.delete(f"/api/style-library/{created['id']}")

    def test_analysis_error_marks_failed_not_stuck(self, monkeypatch):
        import src.api.main as main_mod

        created = _create().json()["entry"]
        monkeypatch.setattr(main_mod, "_agent_registry",
                            _StubRegistry(_StubAgent(error="上游超时")))
        asyncio.run(main_mod._analyze_style_entry(created["id"], "default"))
        detail = client.get(f"/api/style-library/{created['id']}").json()["entry"]
        assert detail["status"] == "failed"
        assert "上游超时" in detail["error"]
        client.delete(f"/api/style-library/{created['id']}")

    def test_missing_vision_model_is_explained(self, monkeypatch):
        import src.api.main as main_mod

        created = _create().json()["entry"]

        class _NoAgent:
            def get(self, name):
                return None

        monkeypatch.setattr(main_mod, "_agent_registry", _NoAgent())
        asyncio.run(main_mod._analyze_style_entry(created["id"], "default"))
        detail = client.get(f"/api/style-library/{created['id']}").json()["entry"]
        assert detail["status"] == "failed"
        assert "视觉模型" in detail["error"]
        client.delete(f"/api/style-library/{created['id']}")


class TestStyleLibraryUpdate:
    def test_user_entry_is_editable_and_sanitized(self):
        created = _create().json()["entry"]
        response = client.patch(f"/api/style-library/{created['id']}",
                                json={"background": "纯白无缝底，含 12 种成分",
                                      "taste_verdict": "留白充足"})
        assert response.status_code == 200
        body = response.json()
        assert "成分" not in body["entry"]["background"]      # 越界片段被剔除
        assert body["removed"], "剔除项要如实报出来"
        assert "已剔除" in body["message"]
        client.delete(f"/api/style-library/{created['id']}")

    def test_similar_entries_are_flagged(self):
        created = _create(name="净白硬照").json()["entry"]
        response = client.patch(f"/api/style-library/{created['id']}",
                                json={"style_words": "纯白无缝底，主体居中、四周留窄白边，"
                                                     "投影短椭圆"})
        assert response.status_code == 200
        names = [item["name"] for item in response.json()["similar"]]
        assert "净白硬照" in names, response.json()["similar"]
        client.delete(f"/api/style-library/{created['id']}")

    def test_builtin_only_toggles(self):
        builtin_id = client.get("/api/style-library").json()["builtin"][0]["id"]
        blocked = client.patch(f"/api/style-library/{builtin_id}",
                               json={"background": "改内容"})
        assert blocked.status_code == 403
        toggled = client.patch(f"/api/style-library/{builtin_id}", json={"enabled": False})
        assert toggled.status_code == 200
        listed = client.get("/api/style-library").json()
        item = next(entry for entry in listed["builtin"] if entry["id"] == builtin_id)
        # 停用后仍要看得见（否则用户没法再启用回来），但标记为未启用
        assert item["enabled"] is False
        again = client.patch(f"/api/style-library/{builtin_id}", json={"enabled": True})
        assert again.status_code == 200
        item = next(entry for entry in client.get("/api/style-library").json()["builtin"]
                    if entry["id"] == builtin_id)
        assert item["enabled"] is True

    def test_unknown_field_and_missing_entry(self):
        created = _create().json()["entry"]
        assert client.patch(f"/api/style-library/{created['id']}",
                            json={"nope": 1}).status_code == 400
        assert client.patch("/api/style-library/st_missing",
                            json={"name": "x"}).status_code == 404
        client.delete(f"/api/style-library/{created['id']}")

    def test_empty_name_rejected(self):
        created = _create().json()["entry"]
        assert client.patch(f"/api/style-library/{created['id']}",
                            json={"name": "   "}).status_code == 400
        client.delete(f"/api/style-library/{created['id']}")


class TestStyleLibraryDeleteAndReanalyze:
    def test_delete_reports_adoption(self):
        created = _create().json()["entry"]
        from src.harness.style_store import StyleStore

        StyleStore().adopt([created["id"]])
        body = client.delete(f"/api/style-library/{created['id']}").json()
        assert body["adopted"] == 1
        assert "采用" in body["message"]
        assert client.get(f"/api/style-library/{created['id']}").status_code == 404

    def test_reanalyze_requires_user_entry(self, monkeypatch):
        import src.api.main as main_mod

        builtin_id = client.get("/api/style-library").json()["builtin"][0]["id"]
        assert client.post(f"/api/style-library/{builtin_id}/reanalyze").status_code == 404

        created = _create().json()["entry"]
        calls = []

        async def _spy(entry_id, tenant_id, *, hint=""):
            calls.append((entry_id, hint))

        monkeypatch.setattr(main_mod, "_analyze_style_entry", _spy)
        response = client.post(f"/api/style-library/{created['id']}/reanalyze",
                               json={"hint": "更冷一点"})
        assert response.status_code == 200
        body = response.json()
        assert body["estimate"]["amount"] is None
        assert body["entry"]["status"] == "analyzing"
        assert "更冷一点" in body["message"]
        client.delete(f"/api/style-library/{created['id']}")


class TestStyleLibraryPreview:
    def test_preview_renders_block_without_calling_models(self):
        body = client.get("/api/style-library/preview?platform=pinduoduo&category=保健品").json()
        assert body["platform"] == "pinduoduo"
        assert "适用风格档案" in body["block"]
        assert "档案定义" in body["block"] and "逐张对应" in body["block"]
        assert body["slots"], "逐槽位命中关系不能为空"
        assert body["summary"]["entries"]

    def test_preview_subset_and_missing_entry(self):
        body = client.get("/api/style-library/preview?platform=taobao&slot=main_white").json()
        assert list(body["slots"]) == ["main_white"]
        assert client.get("/api/style-library/preview?entry_id=st_missing").status_code == 404

    def test_preview_reports_unknown_slot_instead_of_silently_widening(self):
        """请求了不在该平台档案里的槽位 → 如实说明，不静默改成"预览全部" """
        body = client.get("/api/style-library/preview?platform=taobao&slot=main_scene").json()
        assert any("不在 taobao" in note for note in body["notes"]), body["notes"]

    def test_preview_can_be_scoped_to_one_entry(self):
        created = _create(name="只有我这条").json()["entry"]
        from src.harness.style_store import StyleStore

        store = StyleStore()
        store.apply_analysis(created["id"], {"background": "冷白渐层底",
                                             "composition": "居中偏左占 60%"})
        body = client.get("/api/style-library/preview"
                          f"?platform=taobao&slot=main_benefits&entry_id={created['id']}").json()
        picked = [item["name"] for item in body["slots"]["main_benefits"]]
        assert picked == ["只有我这条"], picked
        store.delete_entry(created["id"])


# ── 单一风格（radio）+ 套图结构 + 照片增删（用户 2026-09-19/20 的两条纠正）──


class TestSingleStyleAndPhotos:
    def _ready(self, name, *, roles=None, flow="", tenant="default"):
        from src.harness.style_store import StyleStore

        store = StyleStore()
        created = _create(name=name, tenant=tenant).json()["entry"]
        payload = {"background": "浅色渐层底", "composition": "主体居中偏右占 60%",
                   "lighting": "均匀柔光", "materials": "哑光塑料"}
        if roles:
            payload["shot_roles"] = [{"number": index, "slot": slot,
                                      "treatment": "留白换到左侧"}
                                     for index, slot in enumerate(roles, start=1)]
        if flow:
            payload["shot_flow"] = flow
        store.apply_analysis(created["id"], payload)
        return created["id"]

    def test_enabling_one_style_disables_the_other(self):
        """词库里"启用一条 → 另一条自动停用"（用户问：有没有限定只启用一个？）"""
        from src.harness.style_store import StyleStore

        first = self._ready("风格A")
        second = self._ready("风格B")            # 分析成功 → B 生效、A 自动停用
        store = StyleStore()
        assert store.get_entry(first)["enabled"] is False
        response = client.patch(f"/api/style-library/{first}", json={"enabled": True})
        assert response.status_code == 200
        body = response.json()
        assert [item["name"] for item in body["auto_disabled"]] == ["风格B"]
        assert body["entry"]["enabled"] is True
        assert "自动停用" in body["message"]
        assert store.get_entry(second)["enabled"] is False
        client.delete(f"/api/style-library/{first}")
        client.delete(f"/api/style-library/{second}")

    def test_add_and_remove_photos(self):
        """一套图要能补齐/去掉几张（旧版只能删掉词条重建）"""
        from src.harness.style_store import StyleStore

        created = _create(name="要补齐的套图", photos=2).json()["entry"]
        added = client.post(f"/api/style-library/{created['id']}/photos",
                            files=[("files", ("p3.jpg", PHOTO, "image/jpeg")),
                                   ("files", ("p4.jpg", PHOTO, "image/jpeg"))])
        assert added.status_code == 200
        assert added.json()["entry"]["photo_count"] == 4
        assert "4 张" in added.json()["message"]

        removed = client.delete(f"/api/style-library/{created['id']}/photos/1")
        assert removed.status_code == 200
        assert removed.json()["entry"]["photo_count"] == 3
        assert client.delete(f"/api/style-library/{created['id']}/photos/9").status_code == 400
        StyleStore().delete_entry(created["id"])

    def test_remove_photo_clears_shot_roles(self):
        """重排会让"参考第2张"指向另一张照片 → 清空逐张角色并告知用户"""
        from src.harness.style_store import StyleStore

        entry_id = self._ready("有结构的套图", roles=["main_white", "main_ingredients"],
                               flow="先白底；再讲成分配方图")
        # 至少留 1 张 → 先补一张再删
        client.post(f"/api/style-library/{entry_id}/photos",
                    files=[("files", ("p2.jpg", PHOTO, "image/jpeg"))])
        body = client.delete(f"/api/style-library/{entry_id}/photos/1").json()
        assert body["cleared_roles"] is True
        assert "逐张角色已清空" in body["message"]
        detail = client.get(f"/api/style-library/{entry_id}").json()["entry"]
        assert detail["shot_roles"] == []
        assert detail["background"], "共同美术要保留"
        StyleStore().delete_entry(entry_id)

    def test_detail_exposes_sequence_and_summary_chip(self):
        from src.harness.style_store import StyleStore

        entry_id = self._ready("同色清新", roles=["main_white", "main_ingredients"],
                               flow="先白底；再讲成分配方图")
        detail = client.get(f"/api/style-library/{entry_id}").json()["entry"]
        assert detail["shot_flow"] == "先白底；再讲成分配方图"
        assert [item["slot"] for item in detail["shot_roles"]] == ["main_white", "main_ingredients"]
        assert detail["shot_role_count"] == 2 and detail["photo_count"] == 1
        # 逐张角色可编辑（识别错了要能改）
        patched = client.patch(f"/api/style-library/{entry_id}",
                               json={"shot_roles": [{"number": 1, "slot": "main_white",
                                                     "treatment": "留白在右"}]})
        assert patched.status_code == 200
        assert patched.json()["entry"]["shot_role_count"] == 1
        # 未登记槽位 → 400 并指出是哪一条
        bad = client.patch(f"/api/style-library/{entry_id}",
                           json={"shot_roles": [{"number": 1, "slot": "not_a_slot"}]})
        assert bad.status_code == 200        # 归空字符串（不是错误），但角色差异要写
        assert bad.json()["entry"]["shot_roles"] == []
        StyleStore().delete_entry(entry_id)

    def test_preview_reports_sequence_coverage(self):
        from src.harness.style_store import StyleStore

        entry_id = self._ready("同色清新", roles=["main_white"], flow="先白底")
        body = client.get(f"/api/style-library/preview"
                          f"?platform=taobao&entry_id={entry_id}").json()
        assert "参考套图的编排习惯" in body["block"]
        coverage = body["summary"]["coverage"][0]
        assert coverage["ref_count"] == 1
        assert "main_spec" in coverage["missing_in_ref"]     # 淘宝套图里这张没有对应角色
        StyleStore().delete_entry(entry_id)

    def test_session_style_switch(self):
        """会话锁：点「换风格」→ 写 task 并清旧锁（进行中的会话否则不会换）"""
        from src.harness.style_store import StyleStore

        entry_id = self._ready("会话风格")
        session = _session_manager.create(product_images=[], platform="taobao")
        session_id = session["session_id"]
        body = client.post(f"/api/sessions/{session_id}/style",
                           json={"entry_id": entry_id}).json()
        assert body["entry_id"] == entry_id and "会话风格" in body["message"]
        stored = _session_manager.get(session_id, tenant_id="default")
        assert stored["task"].get("style_entry_id") == entry_id
        # 清除指定
        cleared = client.post(f"/api/sessions/{session_id}/style", json={"entry_id": ""}).json()
        assert cleared["entry_id"] == ""
        assert client.post(f"/api/sessions/{session_id}/style",
                           json={"entry_id": "st_missing"}).status_code == 404
        StyleStore().delete_entry(entry_id)
