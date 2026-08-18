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
from src.main import app, _agent_registry
from src.workflow.job_store import JobStore
from src.workflow.engine import WorkflowEngine
from src.workflow.batch import BatchScheduler


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
        resp = client.post("/api/workflows/jobs/whatever/decision", json={"action": "nuke"})
        assert resp.status_code == 400


class TestControl:
    def test_control_bad_body(self, client):
        resp = client.post("/api/workflows/jobs/whatever/control", json={})
        assert resp.status_code == 400

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
        assert client.post("/api/workflows/batches/x/control", json={}).status_code == 400
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
        # 无文件字段 → FastAPI 422；job 不存在且带文件 → 400
        resp = client.post(
            "/api/workflows/jobs/nonexistent123/replicate",
            files=[("files", ("ref.jpg", _valid_jpeg_bytes(), "image/jpeg"))],
        )
        assert resp.status_code == 400
