"""Workflow REST API 测试（TestClient + 独立临时 JobStore/Engine）

注意：TestClient 每个请求使用即弃的临时事件循环，端点上 create_task 的
后台任务在其内不会被执行（生产 uvicorn 循环常驻，无此问题）。
因此本文件用测试自己的事件循环驱动引擎（engine.run），HTTP 调用经
asyncio.to_thread 转发，控制类端点（control/decision）跨线程操作运行时标志。
"""

import asyncio
import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import src.main as main_mod
from src.main import _agent_registry, app
from src.workflow.batch import BatchScheduler
from src.workflow.engine import WorkflowEngine
from src.workflow.job_store import JobStore


def _valid_jpeg_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (48, 48), (200, 60, 40)).save(buf, format="JPEG")
    return buf.getvalue()


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def client(tmp_path, monkeypatch):
    """替换 main.py 的全局 store/engine/scheduler 为临时实例（测试隔离）"""
    store = JobStore(db_path=tmp_path / "workflow_api.db")
    engine = WorkflowEngine(_agent_registry, store)
    monkeypatch.setattr(main_mod, "_workflow_store", store)
    monkeypatch.setattr(main_mod, "_workflow_engine", engine)
    monkeypatch.setattr(main_mod, "_batch_scheduler", BatchScheduler(engine, store))
    _run_async(_agent_registry.load_from_config(main_mod._provider_registry))
    return TestClient(app)


async def _instantiate(client, template, mode="auto"):
    resp = await asyncio.to_thread(
        client.post,
        f"/api/workflows/templates/{template}/instantiate",
        data={"platform": "taobao", "category_hint": "保健品", "mode": mode},
        files=[("files", ("product.jpg", _valid_jpeg_bytes(), "image/jpeg"))],
    )
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["job_id"]


async def _get(client, path):
    resp = await asyncio.to_thread(client.get, path)
    return resp


async def _run_job_until_done(job_id, timeout=60):
    """在测试事件循环中驱动引擎直至终态"""
    engine = main_mod._workflow_engine
    store = main_mod._workflow_store
    job = await store.get_job(job_id)
    await asyncio.wait_for(engine.run(job), timeout=timeout)
    return await store.get_job(job_id)


class TestTemplatesEndpoint:
    def test_list_templates(self, client):
        resp = client.get("/api/workflows/templates")
        assert resp.status_code == 200
        templates = resp.json()["templates"]
        assert len(templates) >= 4
        for t in templates:
            assert "inputs" in t and "node_count" in t

    def test_instantiate_unknown_template(self, client):
        resp = client.post("/api/workflows/templates/nope/instantiate", data={})
        assert resp.status_code in (400, 404)

    def test_instantiate_missing_required_input(self, client):
        resp = client.post("/api/workflows/templates/white_bg_suite/instantiate", data={})
        assert resp.status_code == 400

    def test_export_path_traversal_blocked(self, client):
        """审计修复 CRITICAL：导出端点的路径穿越必须 404（此前可读 config/secrets.yaml）"""
        import urllib.parse
        for name in ("..\\..\\config\\secrets", "../../config/models", "..\\config\\default"):
            encoded = urllib.parse.quote(name, safe="")
            resp = client.get(f"/api/workflows/templates/{encoded}/export")
            assert resp.status_code == 404, f"穿越未被拦截: {name} → {resp.status_code}"
        # 正常模板导出仍可用
        assert client.get("/api/workflows/templates/scene_suite/export").status_code == 200


class TestTenantIsolation:
    """审计修复：未知租户 403 + 跨租户 IDOR 404"""

    def test_unknown_tenant_create_session_403(self, client):
        resp = client.post(
            "/api/sessions",
            data={"product_info": "x", "platform": "taobao"},
            files=[("files", ("p.jpg", _valid_jpeg_bytes(), "image/jpeg"))],
            headers={"X-Tenant-ID": "ghost_tenant"},
        )
        assert resp.status_code == 403

    def test_unknown_tenant_instantiate_403(self, client):
        resp = client.post(
            "/api/workflows/templates/scene_suite/instantiate",
            data={"platform": "taobao"},
            files=[("files", ("p.jpg", _valid_jpeg_bytes(), "image/jpeg"))],
            headers={"X-Tenant-ID": "ghost_tenant"},
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_cross_tenant_control_404(self, client):
        """A 租户的 job，B 租户控制 → 404（此前可跨租户 IDOR）"""
        job_id = await _instantiate(client, "scene_suite")
        job = await _run_job_until_done(job_id)
        assert job.status.value == "completed"
        resp = await asyncio.to_thread(
            client.post,
            f"/api/workflows/jobs/{job_id}/control",
            json={"action": "cancel"},
            headers={"X-Tenant-ID": "other_tenant"},
        )
        assert resp.status_code == 404

    def test_cross_tenant_batch_control_404(self, client):
        from datetime import datetime, timezone
        store = main_mod._workflow_store
        now = datetime.now(timezone.utc)
        _run_async(store.create_batch({
            "batch_id": "b-tenant-a", "template_name": "scene_suite", "tenant_id": "default",
            "status": "running", "mode": "auto", "max_concurrency": 2,
            "total": 1, "done": 0, "failed": 0,
            "created_at": now, "updated_at": now,
        }))
        resp = client.post(
            "/api/workflows/batches/b-tenant-a/control",
            json={"action": "cancel"},
            headers={"X-Tenant-ID": "other_tenant"},
        )
        assert resp.status_code == 404

    def test_batch_items_cap_enforced(self, client):
        """审计修复：单批次商品项上限"""
        items = [{"product_info": f"商品{i}", "platform": "taobao"} for i in range(101)]
        resp = client.post(
            "/api/workflows/batches",
            json={"template_name": "scene_suite", "items": items},
            headers={"X-Tenant-ID": "default"},
        )
        assert resp.status_code == 400
        assert "100" in resp.json()["detail"]


class TestJobsEndpoint:
    @pytest.mark.asyncio
    async def test_instantiate_and_run_to_completion(self, client):
        job_id = await _instantiate(client, "scene_suite")
        job = await _run_job_until_done(job_id)
        assert job.status.value == "completed"

        resp = await _get(client, f"/api/workflows/jobs/{job_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"
        assert len(data["steps"]) >= 5
        assert data["events"]
        for s in data["steps"]:
            assert s["type"] in ("tool", "agent", "condition", "human", "group_chat", "end")
            assert "elapsed_ms" in s

    def test_jobs_list(self, client):
        resp = client.get("/api/workflows/jobs", headers={"X-Tenant-ID": "default"})
        assert resp.status_code == 200
        assert "jobs" in resp.json()

    def test_tenant_isolation(self, client):
        resp = client.get("/api/workflows/jobs", headers={"X-Tenant-ID": "other_tenant"})
        assert resp.json()["total"] == 0

    def test_nonexistent_job_404(self, client):
        assert client.get("/api/workflows/jobs/nonexistent123").status_code == 404


class TestHumanDecision:
    @pytest.mark.asyncio
    async def test_full_hitl_flow(self, client):
        job_id = await _instantiate(client, "compliance_hardened")
        engine = main_mod._workflow_engine
        store = main_mod._workflow_store
        job = await store.get_job(job_id)
        task = asyncio.create_task(engine.run(job))

        # 等待进入人工审批
        for _ in range(200):
            await asyncio.sleep(0.1)
            j = await store.get_job(job_id)
            if j.status.value == "waiting_human":
                break
        assert j.status.value == "waiting_human"

        # 经 HTTP 提交决策（portal 循环里执行 decide_human，跨线程唤醒测试循环中的等待）
        resp = await asyncio.to_thread(
            client.post,
            f"/api/workflows/jobs/{job_id}/decision",
            json={"action": "approve"},
        )
        assert resp.status_code == 200
        await asyncio.wait_for(task, timeout=60)
        j = await store.get_job(job_id)
        assert j.status.value == "completed"

    def test_bad_decision_action(self, client):
        # 审计修复：job 不存在 → 404（此前穿透到引擎层报 400）
        resp = client.post("/api/workflows/jobs/whatever/decision", json={"action": "nuke"})
        assert resp.status_code == 404


class TestControl:
    def test_control_bad_body(self, client):
        # 审计修复：job 不存在 → 404（此前穿透到引擎层报 400）
        resp = client.post("/api/workflows/jobs/whatever/control", json={})
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_manual_mode_control_flow(self, client):
        job_id = await _instantiate(client, "scene_suite", mode="manual")
        engine = main_mod._workflow_engine
        store = main_mod._workflow_store
        job = await store.get_job(job_id)
        task = asyncio.create_task(engine.run(job))

        # 等第一步完成后暂停
        for _ in range(300):
            await asyncio.sleep(0.1)
            j = await store.get_job(job_id)
            if j.status.value == "paused":
                break
        assert j.status.value == "paused"

        # HTTP 控制：run_next → 再暂停
        resp = await asyncio.to_thread(
            client.post, f"/api/workflows/jobs/{job_id}/control", json={"action": "run_next"}
        )
        assert resp.status_code == 200
        for _ in range(300):
            await asyncio.sleep(0.1)
            j = await store.get_job(job_id)
            if j.status.value == "paused":
                break

        # HTTP 控制：resume → 完成
        resp = await asyncio.to_thread(
            client.post, f"/api/workflows/jobs/{job_id}/control", json={"action": "resume"}
        )
        assert resp.status_code == 200
        await asyncio.wait_for(task, timeout=60)
        j = await store.get_job(job_id)
        assert j.status.value == "completed"

    @pytest.mark.asyncio
    async def test_retry_step(self, client):
        job_id = await _instantiate(client, "scene_suite")
        await _run_job_until_done(job_id)
        resp = await asyncio.to_thread(
            client.post,
            f"/api/workflows/jobs/{job_id}/control",
            json={"action": "retry_step", "step_node": "prompt"},
        )
        assert resp.status_code == 200
        # retry_step 会在 portal 循环起新任务；由测试循环驱动完成
        job = await _run_job_until_done(job_id)
        assert job.status.value == "completed"


class TestBatchEndpoint:
    """POST/GET /api/workflows/batches — 批量任务（M2）"""

    @pytest.mark.asyncio
    async def test_create_batch_json_and_complete(self, client):
        resp = await asyncio.to_thread(
            client.post,
            "/api/workflows/batches",
            json={
                "template_name": "scene_suite",
                "max_concurrency": 2,
                "items": [
                    {"product_images": ["fake_b64_data_12345678"], "platform": "taobao",
                     "product_info": f"商品{i}"}
                    for i in range(3)
                ],
            },
        )
        assert resp.status_code == 200, resp.text
        bid = resp.json()["batch_id"]

        # TestClient portal 不推进后台任务 → 测试循环驱动调度器
        await asyncio.wait_for(main_mod._batch_scheduler.run(bid), timeout=90)

        d = (await _get(client, f"/api/workflows/batches/{bid}")).json()
        assert d["status"] == "completed"
        assert d["done"] == 3
        assert len(d["items"]) == 3
        assert all(it["status"] == "succeeded" for it in d["items"])

    @pytest.mark.asyncio
    async def test_create_batch_csv(self, client):
        csv_bytes = "product_info,platform,category_hint\n商品A,taobao,保健品\n商品B,amazon,\n".encode("utf-8")
        resp = await asyncio.to_thread(
            client.post,
            "/api/workflows/batches",
            data={"template_name": "scene_suite"},
            files=[("file", ("batch.csv", csv_bytes, "text/csv"))],
        )
        assert resp.status_code == 200, resp.text
        bid = resp.json()["batch_id"]
        items = (await _get(client, f"/api/workflows/batches/{bid}")).json()["items"]
        assert len(items) == 2
        assert items[1]["inputs"]["platform"] == "amazon"
        assert items[0]["inputs"]["category_hint"] == "保健品"
        assert items[0]["inputs"]["product_images"] == ["Y3N2"]  # CSV 占位图

    @pytest.mark.asyncio
    async def test_batch_dead_letter_and_retry(self, client):
        resp = await asyncio.to_thread(
            client.post,
            "/api/workflows/batches",
            json={
                "template_name": "scene_suite",
                "items": [
                    {"product_images": ["fake_b64_data_12345678"], "platform": "taobao"},
                    {"platform": "taobao"},   # 缺必填 product_images → 死信
                ],
            },
        )
        assert resp.status_code == 200
        bid = resp.json()["batch_id"]
        await asyncio.wait_for(main_mod._batch_scheduler.run(bid), timeout=90)

        d = (await _get(client, f"/api/workflows/batches/{bid}")).json()
        assert d["status"] == "partial"
        assert d["failed"] == 1

        # retry_failed 控制
        r = await asyncio.to_thread(
            client.post, f"/api/workflows/batches/{bid}/control", json={"action": "retry_failed"}
        )
        assert r.status_code == 200
        assert r.json()["retried"] == 1
        await asyncio.wait_for(main_mod._batch_scheduler.run(bid), timeout=90)

    def test_batch_list_and_bad_control(self, client):
        resp = client.get("/api/workflows/batches")
        assert resp.status_code == 200
        assert "batches" in resp.json()
        # 审计修复：批次不存在 → 404（此前穿透到调度器报 400）
        assert client.post("/api/workflows/batches/x/control", json={}).status_code == 404
        assert client.post("/api/workflows/batches", json={"template_name": "nope", "items": [{}]}).status_code == 400


class TestReplicateEndpoint:
    """M3 POST /api/workflows/jobs/{id}/replicate — 一键风格复刻"""

    @pytest.mark.asyncio
    async def test_instantiate_style_replicate_multi_image_inputs(self, client):
        """多图片输入模板：reference_images / product_images 按字段名分发"""
        resp = await asyncio.to_thread(
            client.post,
            "/api/workflows/templates/style_replicate/instantiate",
            data={"platform": "taobao"},
            files=[
                ("reference_images", ("ref.jpg", _valid_jpeg_bytes(), "image/jpeg")),
                ("product_images", ("product.jpg", _valid_jpeg_bytes(), "image/jpeg")),
            ],
        )
        assert resp.status_code in (200, 201), resp.text
        job_id = resp.json()["job_id"]

        job = await _run_job_until_done(job_id)
        assert job.status.value == "completed"
        # 两个图片输入都被正确填充
        assert len(job.inputs["reference_images"]) == 1
        assert len(job.inputs["product_images"]) == 1

    def test_instantiate_style_replicate_missing_reference(self, client):
        """缺参考图字段 → 400"""
        resp = client.post(
            "/api/workflows/templates/style_replicate/instantiate",
            data={"platform": "taobao"},
            files=[("product_images", ("product.jpg", _valid_jpeg_bytes(), "image/jpeg"))],
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_replicate_and_rerun(self, client):
        job_id = await _instantiate(client, "scene_suite")
        await _run_job_until_done(job_id)

        resp = await asyncio.to_thread(
            client.post,
            f"/api/workflows/jobs/{job_id}/replicate",
            files=[("files", ("ref.jpg", _valid_jpeg_bytes(), "image/jpeg"))],
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "replicating"
        assert body["style"]["style_tags"]  # 风格标签已拆解

        # 测试循环驱动重跑完成
        job = await _run_job_until_done(job_id)
        assert job.status.value == "completed"
        assert "_style_breakdown" in job.context
        assert job.context["retries"] >= 1

    def test_replicate_validation(self, client):
        assert client.post("/api/workflows/jobs/nope/replicate").status_code == 422  # 缺文件
        # 无文件字段 → FastAPI 422；job 不存在且带文件 → 404（审计修复：先租户/存在性校验）
        resp = client.post(
            "/api/workflows/jobs/nonexistent123/replicate",
            files=[("files", ("ref.jpg", _valid_jpeg_bytes(), "image/jpeg"))],
        )
        assert resp.status_code == 404


class TestM4Api:
    """M4 — 模板导入导出 + Webhook 入站回调"""

    def test_export_template(self, client):
        resp = client.get("/api/workflows/templates/scene_suite/export")
        assert resp.status_code == 200
        assert "name:" in resp.text
        assert "场景图套装" in resp.text

    def test_export_nonexistent_404(self, client):
        assert client.get("/api/workflows/templates/nope/export").status_code == 404

    def test_import_roundtrip_and_duplicate(self, client):
        from src.core.config import _project_root
        yaml_text = '''name: M4 导入测试模板
version: "1.0.0"
description: 导入导出测试
nodes:
  check:
    type: tool
    tool: validate_image
    inputs:
      images: ["x"]
  done:
    type: end
    status: completed
'''
        path = _project_root() / "config" / "workflows" / "m4_test_import.yaml"
        try:
            resp = client.post("/api/workflows/templates/import",
                               json={"yaml": yaml_text, "template_name": "m4_test_import"})
            assert resp.status_code == 200, resp.text
            assert resp.json()["imported"] == "m4_test_import"

            # 列表可见
            tpls = client.get("/api/workflows/templates").json()["templates"]
            assert any(t["template_name"] == "m4_test_import" for t in tpls)

            # 同名未 force → 400；force → 200
            assert client.post("/api/workflows/templates/import",
                               json={"yaml": yaml_text, "template_name": "m4_test_import"}).status_code == 400
            assert client.post("/api/workflows/templates/import",
                               json={"yaml": yaml_text, "template_name": "m4_test_import",
                                     "force": True}).status_code == 200
        finally:
            if path.exists():
                path.unlink()

    def test_import_validation(self, client):
        assert client.post("/api/workflows/templates/import",
                           json={"yaml": ":::bad", "template_name": "x1"}).status_code == 400
        assert client.post("/api/workflows/templates/import",
                           json={"yaml": "name: X\nnodes:\n  a:\n    type: agent\n    agent: 不存在\n",
                                 "template_name": "x2"}).status_code == 400
        assert client.post("/api/workflows/templates/import",
                           json={"yaml": "name: X\nnodes:\n  a:\n    type: tool\n    tool: nope\n",
                                 "template_name": "x3"}).status_code == 400
        assert client.post("/api/workflows/templates/import",
                           json={"yaml": "nodes: {}", "template_name": "x4"}).status_code == 400
        assert client.post("/api/workflows/templates/import",
                           json={"yaml": "name: X\nnodes: {}", "template_name": "bad/name"}).status_code == 400

    def test_webhook_token_gate(self, client, monkeypatch):
        monkeypatch.delenv("ECOMM_WEBHOOK_TOKEN", raising=False)
        r = client.post("/api/webhooks/workflows/x/decision", json={"action": "approve"})
        assert r.status_code == 503  # 未启用

        monkeypatch.setenv("ECOMM_WEBHOOK_TOKEN", "secret-token")
        r = client.post("/api/webhooks/workflows/x/decision", json={"action": "approve"})
        assert r.status_code == 401  # 缺 token 头
        r = client.post("/api/webhooks/workflows/x/decision", json={"action": "approve"},
                        headers={"X-Webhook-Token": "wrong"})
        assert r.status_code == 401
        r = client.post("/api/webhooks/workflows/x/decision", json={"action": "approve"},
                        headers={"X-Webhook-Token": "secret-token"})
        assert r.status_code == 400  # token 通过，但 job 不存在

    @pytest.mark.asyncio
    async def test_webhook_triggers_state_transition(self, client, monkeypatch):
        """M4 验收：外部回调触发工作流状态流转（人工审批 → 完成）"""
        monkeypatch.setenv("ECOMM_WEBHOOK_TOKEN", "secret-token")
        job_id = await _instantiate(client, "compliance_hardened")
        engine = main_mod._workflow_engine
        store = main_mod._workflow_store
        job = await store.get_job(job_id)
        task = asyncio.create_task(engine.run(job))

        for _ in range(200):
            await asyncio.sleep(0.1)
            j = await store.get_job(job_id)
            if j.status.value == "waiting_human":
                break
        assert j.status.value == "waiting_human"

        resp = await asyncio.to_thread(
            client.post,
            f"/api/webhooks/workflows/{job_id}/decision",
            json={"action": "approve"},
            headers={"X-Webhook-Token": "secret-token"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "accepted"
        await asyncio.wait_for(task, timeout=60)
        j = await store.get_job(job_id)
        assert j.status.value == "completed"


class TestBatchReportEndpoint:
    """M5 批量报表端点：GET /api/workflows/batches/report"""

    async def _seed(self, client):
        from datetime import datetime, timedelta, timezone

        from src.workflow.models import WorkflowJob
        store = main_mod._workflow_store
        now = datetime.now(timezone.utc)
        await store.create_batch({
            "batch_id": "rp1", "template_name": "scene_suite", "tenant_id": "default",
            "status": "partial", "mode": "auto", "max_concurrency": 2,
            "total": 3, "done": 2, "failed": 1,
            "created_at": now, "updated_at": now,
        })
        await store.create_batch_items([
            {"batch_id": "rp1", "seq": 0, "inputs": {}, "job_id": "rj1",
             "status": "succeeded", "updated_at": now},
            {"batch_id": "rp1", "seq": 1, "inputs": {}, "job_id": "rj2",
             "status": "succeeded", "updated_at": now},
            {"batch_id": "rp1", "seq": 2, "inputs": {}, "job_id": "",
             "status": "failed", "error": "必填输入缺失: product_images", "updated_at": now},
        ])
        for jid, secs in (("rj1", 2), ("rj2", 8)):
            await store.create_job(WorkflowJob(
                job_id=jid, template_name="scene_suite", tenant_id="default",
                status="completed", cost_so_far=0.1,
                created_at=now, updated_at=now + timedelta(seconds=secs),
            ))

    @pytest.mark.asyncio
    async def test_report_shape_and_content(self, client):
        await self._seed(client)
        resp = await asyncio.to_thread(
            client.get, "/api/workflows/batches/report",
            headers={"X-Tenant-ID": "default"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert set(data) >= {"overview", "batch_status", "by_template",
                             "duration_histogram", "failure_reasons"}
        ov = data["overview"]
        assert ov["batch_count"] == 1
        assert ov["item_count"] == 3
        assert ov["succeeded"] == 2 and ov["failed"] == 1
        assert ov["success_rate"] == pytest.approx(0.6667)
        assert data["by_template"][0]["template_name"] == "scene_suite"
        assert data["failure_reasons"][0]["reason"] == "必填输入缺失"

    def test_report_route_not_shadowed_by_batch_id(self, client):
        """路由顺序回归：/batches/report 不能被 /batches/{batch_id} 吞掉"""
        resp = client.get("/api/workflows/batches/report",
                          headers={"X-Tenant-ID": "default"})
        assert resp.status_code == 200
        assert "overview" in resp.json()

    def test_report_tenant_isolation(self, client):
        resp = client.get("/api/workflows/batches/report",
                          headers={"X-Tenant-ID": "ghost"})
        assert resp.status_code == 200
        assert resp.json()["overview"]["item_count"] == 0
