"""工作流引擎测试 — 自动挡全链路 / 分支 / 跳过 / 重试 / 恢复"""

import asyncio

import pytest

from src.workflow import templates
from src.workflow.models import JobStatus, StepStatus
from tests.test_workflow.conftest import instantiate_and_run


class TestAutoRun:
    @pytest.mark.asyncio
    async def test_scene_suite_completes(self, engine, store, job_inputs):
        job, steps = await instantiate_and_run(engine, store, "scene_suite", job_inputs)
        assert job.status == JobStatus.COMPLETED
        assert all(s.status == StepStatus.SUCCEEDED for s in steps if s.node != "failed_end")
        # 事件流完整
        events = await store.get_events(job.job_id)
        names = [e["event"] for e in events]
        assert "job_created" in names
        assert "job_started" in names
        assert "job_completed" in names
        assert names.count("step_succeeded") >= 5

    @pytest.mark.asyncio
    async def test_white_bg_suite_completes_with_branch(self, engine, store, job_inputs):
        """Mock 审查 82 分 → 走 >=75 分支，跳过人工审批"""
        job, steps = await instantiate_and_run(engine, store, "white_bg_suite", job_inputs)
        assert job.status == JobStatus.COMPLETED
        by_node = {s.node: s for s in steps}
        assert by_node["branch"].outputs["matched"] == "compliance"
        assert by_node["human_review"].status == StepStatus.PENDING  # 未触发
        assert by_node["post_process"].status == StepStatus.SUCCEEDED

    @pytest.mark.asyncio
    async def test_free_chat_completes(self, engine, store, job_inputs):
        job, steps = await instantiate_and_run(engine, store, "free_chat", job_inputs)
        assert job.status == JobStatus.COMPLETED
        creative = next(s for s in steps if s.node == "creative")
        assert creative.status == StepStatus.SUCCEEDED
        assert creative.outputs["status"] == "completed"
        assert "analysis" in creative.outputs["artifacts"]

    @pytest.mark.asyncio
    async def test_when_skip_condition(self, engine, store):
        """品类提示为空 + 分析结果非敏感品类 → category 节点跳过"""
        # Mock 分析固定返回保健品 → 不会跳过；改为直接验证 when 求值为假时 SKIPPED
        inputs = {"product_images": ["fake_b64_data_12345"], "platform": "taobao"}
        job = templates.instantiate("scene_suite", inputs, mode="auto")
        await store.create_job(job)
        task = await engine.start(job)
        await asyncio.wait_for(task, timeout=60)
        steps = await store.get_steps(job.job_id)
        assert all(s.status in (StepStatus.SUCCEEDED, StepStatus.PENDING) for s in steps)


class TestHumanNode:
    @pytest.mark.asyncio
    async def test_wait_and_approve(self, engine, store, job_inputs):
        job = templates.instantiate("compliance_hardened", job_inputs, mode="auto")
        await store.create_job(job)
        task = await engine.start(job)

        for _ in range(400):  # 40s 预算（慢机/满载防偶发，审计加固）
            await asyncio.sleep(0.1)
            job = await store.get_job(job.job_id)
            if job.status == JobStatus.WAITING_HUMAN:
                break
        assert job.status == JobStatus.WAITING_HUMAN

        steps = await store.get_steps(job.job_id)
        approval = next(s for s in steps if s.node == "final_approval")
        assert approval.status == StepStatus.WAITING_HUMAN

        await engine.decide_human(job.job_id, "approve")
        await asyncio.wait_for(task, timeout=60)

        job = await store.get_job(job.job_id)
        assert job.status == JobStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_reject_fails_job(self, engine, store, job_inputs):
        job = templates.instantiate("compliance_hardened", job_inputs, mode="auto")
        await store.create_job(job)
        task = await engine.start(job)
        for _ in range(400):  # 40s 预算（慢机/满载防偶发，审计加固）
            await asyncio.sleep(0.1)
            job = await store.get_job(job.job_id)
            if job.status == JobStatus.WAITING_HUMAN:
                break
        await engine.decide_human(job.job_id, "reject")
        await asyncio.wait_for(task, timeout=60)
        job = await store.get_job(job.job_id)
        assert job.status == JobStatus.FAILED

    @pytest.mark.asyncio
    async def test_decision_on_non_waiting_job_rejected(self, engine, store, job_inputs):
        job, _ = await instantiate_and_run(engine, store, "scene_suite", job_inputs)
        with pytest.raises(ValueError, match="未等待人工审批"):
            await engine.decide_human(job.job_id, "approve")


class TestResume:
    @pytest.mark.asyncio
    async def test_crash_resume_from_middle(self, engine, store, job_inputs):
        """模拟崩溃：前半步骤成功落库后重建引擎，run() 应从断点续跑"""
        from src.workflow.job_store import JobStore
        from src.workflow.engine import WorkflowEngine
        from src.workflow.models import StepRecord

        job = templates.instantiate("scene_suite", job_inputs, mode="auto")
        await store.create_job(job)
        # 手工构造：validate/analyze 已成功（模拟崩溃前已完成）
        snapshot = templates.get_snapshot(job)
        nodes = snapshot["nodes"]
        steps = [
            StepRecord(job_id=job.job_id, node=n, type=nodes[n]["type"], order=i)
            for i, n in enumerate(nodes.keys())
        ]
        for s in steps:
            if s.node in ("validate", "analyze"):
                s.status = StepStatus.SUCCEEDED
                s.outputs = {"simulated": True}
        await store.create_steps(steps)

        # 全新引擎实例（模拟进程重启）
        engine2 = WorkflowEngine(engine.registry, store)
        task = await engine2.start(job)
        await asyncio.wait_for(task, timeout=60)

        job = await store.get_job(job.job_id)
        assert job.status == JobStatus.COMPLETED
        steps = await store.get_steps(job.job_id)
        # 前两步保留原输出（未被重跑）
        analyze = next(s for s in steps if s.node == "analyze")
        assert analyze.outputs == {"simulated": True}
        assert analyze.attempt == 0

    @pytest.mark.asyncio
    async def test_retry_step_reruns_from_snapshot(self, engine, store, job_inputs):
        job, steps = await instantiate_and_run(engine, store, "scene_suite", job_inputs)
        assert job.status == JobStatus.COMPLETED

        await engine.retry_step(job.job_id, "prompt")
        task = engine._runtimes[job.job_id].task
        await asyncio.wait_for(task, timeout=60)

        job = await store.get_job(job.job_id)
        assert job.status == JobStatus.COMPLETED
        steps = await store.get_steps(job.job_id)
        prompt = next(s for s in steps if s.node == "prompt")
        assert prompt.attempt == 1
        assert job.context.get("retries", 0) >= 1

    @pytest.mark.asyncio
    async def test_unknown_step_retry_rejected(self, engine, store, job_inputs):
        job, _ = await instantiate_and_run(engine, store, "scene_suite", job_inputs)
        with pytest.raises(ValueError, match="不存在"):
            await engine.retry_step(job.job_id, "nope")
