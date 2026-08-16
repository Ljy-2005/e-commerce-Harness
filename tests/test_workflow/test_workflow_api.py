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
    """替换 main.py 的全局 store/engine 为临时实例（测试隔离）"""
    store = JobStore(db_path=tmp_path / "workflow_api.db")
    engine = WorkflowEngine(_agent_registry, store)
    monkeypatch.setattr(main_mod, "_workflow_store", store)
    monkeypatch.setattr(main_mod, "_workflow_engine", engine)
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
        assert resp.status_code == 400

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
