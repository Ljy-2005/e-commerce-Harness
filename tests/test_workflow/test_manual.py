"""手动挡控制测试 — 逐步执行 / 暂停 / 继续 / 跳过"""

import asyncio

import pytest

from src.workflow import templates
from src.workflow.models import JobStatus, StepStatus


async def _wait_done_paused(store, job_id, expected_done, timeout=30):
    """等待：指定步骤集合已成功 且 job 回到 PAUSED（避免陈旧读）"""
    for _ in range(int(timeout / 0.05)):
        job = await store.get_job(job_id)
        steps = await store.get_steps(job_id)
        done = [s.node for s in steps if s.status == StepStatus.SUCCEEDED]
        if done == expected_done and job.status == JobStatus.PAUSED:
            return done
        await asyncio.sleep(0.05)
    raise TimeoutError(f"等待 {expected_done} 超时，当前 done={done} status={job.status.value}")


async def _wait_status(store, job_id, statuses, timeout=30):
    for _ in range(int(timeout / 0.05)):
        job = await store.get_job(job_id)
        if job.status in statuses:
            return job
        await asyncio.sleep(0.05)
    raise TimeoutError(f"未达到状态 {statuses}")


class TestManualMode:
    @pytest.mark.asyncio
    async def test_pauses_after_each_step(self, engine, store, job_inputs):
        job = templates.instantiate("scene_suite", job_inputs, mode="manual")
        await store.create_job(job)
        task = await engine.start(job)

        await _wait_done_paused(store, job.job_id, ["validate"])

        # run_next 逐步推进，每一步后又暂停
        for expected in (["validate", "analyze"], ["validate", "analyze", "prompt"]):
            await engine.control(job.job_id, "run_next")
            done = await _wait_done_paused(store, job.job_id, expected)
            assert done == expected

        # resume 切换为自动挡 → 跑完
        await engine.control(job.job_id, "resume")
        await asyncio.wait_for(task, timeout=60)
        job = await store.get_job(job.job_id)
        assert job.status == JobStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_run_next_requires_paused(self, engine, store, job_inputs):
        job = templates.instantiate("scene_suite", job_inputs, mode="auto")
        await store.create_job(job)
        task = await engine.start(job)
        await asyncio.wait_for(task, timeout=60)
        with pytest.raises(ValueError, match="未处于暂停状态"):
            await engine.control(job.job_id, "run_next")

    @pytest.mark.asyncio
    async def test_skip_step_in_paused(self, engine, store, job_inputs):
        job = templates.instantiate("scene_suite", job_inputs, mode="manual")
        await store.create_job(job)
        task = await engine.start(job)
        await _wait_done_paused(store, job.job_id, ["validate"])

        # 跳过下一个节点（analyze）→ 引擎自动前进一步（prompt）后暂停
        await engine.control(job.job_id, "skip_step", "analyze")
        await _wait_done_paused(store, job.job_id, ["validate", "prompt"])
        steps = await store.get_steps(job.job_id)
        analyze = next(s for s in steps if s.node == "analyze")
        assert analyze.status == StepStatus.SKIPPED

        await engine.control(job.job_id, "resume")
        await asyncio.wait_for(task, timeout=60)
        job = await store.get_job(job.job_id)
        assert job.status == JobStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_cancel(self, engine, store, job_inputs):
        job = templates.instantiate("free_chat", job_inputs, mode="manual")
        await store.create_job(job)
        task = await engine.start(job)
        await _wait_status(store, job.job_id, [JobStatus.PAUSED])

        await engine.control(job.job_id, "cancel")
        try:
            await asyncio.wait_for(task, timeout=30)
        except asyncio.CancelledError:
            pass
        job = await store.get_job(job.job_id)
        assert job.status in (JobStatus.CANCELLED, JobStatus.PAUSED)

    @pytest.mark.asyncio
    async def test_retry_step_while_paused_restarts(self, engine, store, job_inputs):
        """审计修复：手动挡暂停中 retry_step 会取消旧任务并真正重启（此前任务卡死）"""
        job = templates.instantiate("scene_suite", job_inputs, mode="manual")
        await store.create_job(job)
        await engine.start(job)
        await _wait_done_paused(store, job.job_id, ["validate"])

        # 引擎任务仍在等待推进信号（未 done）→ 触发 cancel→start 竞态路径
        await engine.retry_step(job.job_id, "validate")
        await _wait_done_paused(store, job.job_id, ["validate"])

        # 证明 validate 真的被重跑：step_retrying 事件已记录（attempt 语义为
        # 单次执行内的重试计数，重置后重跑从 1 重新累计，不能用 attempt>=2 断言）
        events = await store.get_events(job.job_id)
        assert any(ev["event"] == "step_retrying" for ev in events), "缺少 step_retrying 事件"
        steps = await store.get_steps(job.job_id)
        validate = next(s for s in steps if s.node == "validate")
        assert validate.status == StepStatus.SUCCEEDED

        await engine.control(job.job_id, "resume")
        await asyncio.wait_for(engine._runtimes[job.job_id].task, timeout=60)
        job = await store.get_job(job.job_id)
        assert job.status == JobStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_unknown_action_rejected(self, engine, store, job_inputs):
        job = templates.instantiate("scene_suite", job_inputs, mode="auto")
        await store.create_job(job)
        task = await engine.start(job)
        await asyncio.wait_for(task, timeout=60)
        with pytest.raises(ValueError, match="未知控制指令"):
            await engine.control(job.job_id, "fly")
