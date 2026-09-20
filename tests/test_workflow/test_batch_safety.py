"""第三轮审计 B0-3/B0-4：批量调度器的并发双跑与「取消被推翻」回归测试。

B0-3：`batch.py` 用 `await engine.run(job)` 驱动（不登记 `runtime.task`），
      `retry_step` 的 cancel→start 保护因此失效 → 同一 job 被并发跑两遍
      （实测 `step_started` 12 次 / 应为 6）、步数与成本翻倍。
B0-4：批量项内的 job 被 cancel 后，调度器把 cancelled 当可重试失败 →
      新建 job 重跑全程且最终记 succeeded（实测 job 数 1→2）。

约定：run() 在飞时登记 runner；retry_step 仅对「本引擎自己 start 的任务」执行
cancel→重启，对批量调度器驱动的在飞 run 明确拒绝（400），避免并发双跑。
"""

import asyncio

import pytest

from src.workflow import templates
from src.workflow.batch import BatchScheduler
from src.workflow.engine import WorkflowEngine
from src.workflow.models import JobStatus


@pytest.fixture
def scheduler(loaded_registry, store):
    return BatchScheduler(WorkflowEngine(loaded_registry, store), store)


def _items(n):
    return [
        {"product_images": ["fake_b64_data_12345678"], "platform": "taobao",
         "category_hint": "保健品", "product_info": f"商品{i}"}
        for i in range(n)
    ]


async def _wait_batch(store, batch_id, statuses, timeout=60):
    for _ in range(int(timeout / 0.1)):
        batch = await store.get_batch(batch_id)
        if batch["status"] in statuses:
            return batch
        await asyncio.sleep(0.1)
    raise TimeoutError(f"批次未达到 {statuses}，当前 {batch['status']}")


def _gate_first_node(monkeypatch):
    """把首个节点卡在闸门上，返回 (entered, gate, seen) —— 让「运行中」可被确定性观测"""
    entered = asyncio.Event()
    gate = asyncio.Event()
    seen: list[str] = []
    real = WorkflowEngine._execute_node

    async def gated(self, job, node, cfg, step, scope, runtime, graph):
        seen.append(node)
        if not gate.is_set():
            entered.set()
            await gate.wait()
        return await real(self, job, node, cfg, step, scope, runtime, graph)

    monkeypatch.setattr(WorkflowEngine, "_execute_node", gated)
    return entered, gate, seen


async def _only_job(store, template_name="scene_suite"):
    jobs = await store.list_jobs(limit=100)
    return next(j for j in jobs if j.template_name == template_name)


class TestNoDuplicateRun:
    """B0-3：同一 job 不得并发跑两遍"""

    @pytest.mark.asyncio
    async def test_start_reuses_inflight_run(self, engine, store, job_inputs, monkeypatch):
        """run() 在飞时 start() 必须复用而非另起一个 run（批量调度器即这条路径）"""
        entered, gate, seen = _gate_first_node(monkeypatch)
        job = templates.instantiate("scene_suite", job_inputs, mode="auto")
        await store.create_job(job)

        task_a = asyncio.create_task(engine.run(job))
        await asyncio.wait_for(entered.wait(), timeout=20)

        task_b = await engine.start(job)
        assert task_b is task_a, "在飞 run 未被复用 → 同一 job 并发跑两遍"

        gate.set()
        result = await asyncio.wait_for(task_a, timeout=60)
        assert result.status == JobStatus.COMPLETED
        assert len(seen) == len(set(seen)), f"同一步骤被执行多次: {seen}"

    @pytest.mark.asyncio
    async def test_retry_step_rejected_while_batch_item_running(self, scheduler, store, monkeypatch):
        """批量执行中 retry_step 必须被拒绝（此前会并发起第二个 run，步骤翻倍）"""
        entered, gate, seen = _gate_first_node(monkeypatch)
        batch = await scheduler.submit("scene_suite", _items(1))
        await asyncio.wait_for(entered.wait(), timeout=30)
        job = await _only_job(store)
        first_node = seen[0]

        with pytest.raises(ValueError, match="执行中"):
            await scheduler.engine.retry_step(job.job_id, first_node)

        gate.set()
        await _wait_batch(store, batch["batch_id"], ["completed", "partial"], timeout=60)
        assert len(seen) == len(set(seen)), f"同一步骤被执行多次: {seen}"

    @pytest.mark.asyncio
    async def test_retry_step_still_works_after_batch_item_finished(self, scheduler, store):
        """回归：批量项跑完后（终态、无在飞 run）重跑步骤仍可用"""
        batch = await scheduler.submit("scene_suite", _items(1))
        await _wait_batch(store, batch["batch_id"], ["completed"])
        job = await _only_job(store)
        steps = await store.get_steps(job.job_id)

        await scheduler.engine.retry_step(job.job_id, steps[0].node)

        runtime = scheduler.engine._runtimes.get(job.job_id)
        if runtime and runtime.task:
            await asyncio.wait_for(asyncio.shield(runtime.task), timeout=60)
        assert (await store.get_job(job.job_id)).status == JobStatus.COMPLETED


class TestCancelledJobNotRetried:
    """B0-4：job 被取消后不得被批量调度器当失败重试"""

    @pytest.mark.asyncio
    async def test_cancel_is_not_overturned_by_batch(self, scheduler, store, monkeypatch):
        entered, gate, _seen = _gate_first_node(monkeypatch)
        batch = await scheduler.submit("scene_suite", _items(1))
        await asyncio.wait_for(entered.wait(), timeout=30)
        job = await _only_job(store)

        await scheduler.engine.control(job.job_id, "cancel")
        gate.set()

        result = await _wait_batch(store, batch["batch_id"], ["completed", "partial"], timeout=60)

        jobs = await store.list_jobs(limit=100)
        same = [j for j in jobs if j.template_name == "scene_suite"]
        assert len(same) == 1, f"取消后新建了 job 重跑（job 数 {len(same)}）"
        assert (await store.get_job(job.job_id)).status == JobStatus.CANCELLED

        items = await store.get_batch_items(batch["batch_id"])
        assert items[0]["status"] == "failed", "取消项应成为死信，可经 retry_failed 重跑"
        assert "取消" in items[0]["error"]
        assert result["status"] == "partial"
        assert result["done"] == 0
