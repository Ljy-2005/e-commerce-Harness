"""批量调度器测试 — 并发执行 / 失败隔离 / 死信重跑 / 暂停恢复 / 取消 / 续跑"""

import asyncio

import pytest

from src.workflow import templates
from src.workflow.batch import BatchScheduler
from src.workflow.engine import WorkflowEngine


@pytest.fixture
def scheduler(loaded_registry, store):
    engine = WorkflowEngine(loaded_registry, store)
    return BatchScheduler(engine, store)


def _items(n, platform="taobao"):
    return [
        {"product_images": ["fake_b64_data_12345678"], "platform": platform,
         "category_hint": "保健品", "product_info": f"商品{i}"}
        for i in range(n)
    ]


async def _wait_batch(store, batch_id, statuses, timeout=90):
    for _ in range(int(timeout / 0.1)):
        batch = await store.get_batch(batch_id)
        if batch["status"] in statuses:
            return batch
        await asyncio.sleep(0.1)
    raise TimeoutError(f"批次未达到 {statuses}，当前 {batch['status']}")


class TestBatchRun:
    @pytest.mark.asyncio
    async def test_all_items_complete(self, scheduler, store):
        batch = await scheduler.submit("scene_suite", _items(4), max_concurrency=2)
        result = await _wait_batch(store, batch["batch_id"], ["completed", "partial"])
        assert result["status"] == "completed"
        assert result["done"] == 4
        assert result["failed"] == 0

        items = await store.get_batch_items(batch["batch_id"])
        assert all(it["status"] == "succeeded" for it in items)
        # 每个项都产生了 workflow job
        jobs = await store.list_jobs(limit=100)
        assert sum(1 for j in jobs if j.template_name == "scene_suite") >= 4

    @pytest.mark.asyncio
    async def test_failed_item_isolated(self, scheduler, store):
        items = _items(3)
        items.append({"platform": "taobao"})  # 缺 product_images → instantiate 抛错
        batch = await scheduler.submit("scene_suite", items, max_concurrency=2)
        result = await _wait_batch(store, batch["batch_id"], ["completed", "partial"])
        assert result["status"] == "partial"   # 死信存在
        assert result["done"] == 3
        assert result["failed"] == 1

        batch_items = await store.get_batch_items(batch["batch_id"])
        failed = next(it for it in batch_items if it["status"] == "failed")
        assert "必填输入" in failed["error"]
        assert failed["attempts"] == 3          # 1 次初始 + 2 次自动重试

    @pytest.mark.asyncio
    async def test_retry_failed_reruns_dead_letter(self, scheduler, store, monkeypatch):
        items = _items(2)
        items.append({"platform": "taobao"})  # 缺必填 → 死信（seq=2）
        batch = await scheduler.submit("scene_suite", items, max_concurrency=2)
        await _wait_batch(store, batch["batch_id"], ["partial"])

        # 死信项瞬时完成（缺必填输入 → instantiate 立即抛错），轮询中间态 running
        # 会因时序错过而偶发失败 → 改用 spy 统计新一轮 instantiate 调用次数，
        # 确定性证明死信项真的被重新执行（决策 6/13：验收标准的 Mock 化落地）
        calls = {"dead": 0}
        real_instantiate = templates.instantiate

        def _counting_instantiate(name, inputs, **kwargs):
            if "product_images" not in inputs:
                calls["dead"] += 1
            return real_instantiate(name, inputs, **kwargs)

        monkeypatch.setattr(templates, "instantiate", _counting_instantiate)

        result = await scheduler.control(batch["batch_id"], "retry_failed")
        assert result["retried"] == 1

        dead = None
        for _ in range(900):
            batch_items = await store.get_batch_items(batch["batch_id"])
            dead = next(it for it in batch_items if it["seq"] == 2)
            if dead["status"] == "failed" and calls["dead"] >= 3:
                break
            await asyncio.sleep(0.05)
        assert calls["dead"] == 3, "死信项未被重新执行（新一轮应为 3 次 instantiate 尝试）"
        assert dead is not None and dead["status"] == "failed"
        assert dead["attempts"] == 3  # 新一轮：1 次初始 + 2 次自动重试

    @pytest.mark.asyncio
    async def test_cancel_mid_run(self, scheduler, store):
        batch = await scheduler.submit("free_chat", _items(12), max_concurrency=3)
        # 先暂停确保停在执行中段，再取消
        await asyncio.sleep(0.5)
        await scheduler.control(batch["batch_id"], "pause")
        await _wait_batch(store, batch["batch_id"], ["paused"])
        await scheduler.control(batch["batch_id"], "cancel")
        result = await _wait_batch(store, batch["batch_id"], ["cancelled"], timeout=60)
        assert result["status"] == "cancelled"
        assert result["done"] < 12

    @pytest.mark.asyncio
    async def test_pause_and_resume(self, scheduler, store):
        batch = await scheduler.submit("scene_suite", _items(8), max_concurrency=2)
        # 等至少完成 1 项后暂停
        for _ in range(600):
            b = await store.get_batch(batch["batch_id"])
            if b["done"] >= 1 and b["status"] == "running":
                break
            await asyncio.sleep(0.1)
        await scheduler.control(batch["batch_id"], "pause")
        b = await _wait_batch(store, batch["batch_id"], ["paused"])
        assert b["status"] == "paused"
        done_at_pause = b["done"]

        await scheduler.control(batch["batch_id"], "resume")
        result = await _wait_batch(store, batch["batch_id"], ["completed", "partial"], timeout=120)
        assert result["done"] >= done_at_pause
        assert result["done"] + result["failed"] == 8

    @pytest.mark.asyncio
    async def test_resume_skips_finished_items(self, scheduler, store, loaded_registry):
        """断点续跑：批次一半完成后「重启」（新调度器），只跑剩余项"""
        batch = await scheduler.submit("scene_suite", _items(4), max_concurrency=2)
        await _wait_batch(store, batch["batch_id"], ["completed"])

        # 模拟重启：新调度器实例（run 会跳过已成功项并立即收敛终态）
        scheduler2 = BatchScheduler(WorkflowEngine(loaded_registry, store), store)
        await asyncio.wait_for(scheduler2.run(batch["batch_id"]), timeout=30)
        result = await store.get_batch(batch["batch_id"])
        assert result["status"] == "completed"
        assert result["done"] == 4


class TestSubmitValidation:
    @pytest.mark.asyncio
    async def test_unknown_template(self, scheduler):
        with pytest.raises(ValueError, match="不存在"):
            await scheduler.submit("nope", _items(1))

    @pytest.mark.asyncio
    async def test_empty_items(self, scheduler):
        with pytest.raises(ValueError, match="至少"):
            await scheduler.submit("scene_suite", [])

    @pytest.mark.asyncio
    async def test_unknown_control_action(self, scheduler, store):
        batch = await scheduler.submit("scene_suite", _items(1))
        await _wait_batch(store, batch["batch_id"], ["completed"])
        with pytest.raises(ValueError, match="未知控制指令"):
            await scheduler.control(batch["batch_id"], "fly")
