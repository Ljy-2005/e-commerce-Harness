"""资源治理回归测试（test-plan P3 L2）— _runtimes 终态清理与按需重建

防止回归：工作流引擎的 _runtimes 字典此前无限增长（每个 job 一个
运行期镜像）；决策 19 加入终态清理后，本测试锁死"完成/取消后必须
弹出、后续控制指令按需重建"的行为。
"""

import asyncio

import pytest

from src.workflow import templates
from src.workflow.models import JobStatus


class TestRuntimeCleanup:
    @pytest.mark.asyncio
    async def test_runtime_removed_after_completion(self, engine, store, job_inputs):
        job = templates.instantiate("scene_suite", job_inputs, mode="auto")
        await store.create_job(job)
        task = await engine.start(job)
        await asyncio.wait_for(task, timeout=60)
        assert job.job_id not in engine._runtimes

    @pytest.mark.asyncio
    async def test_runtime_removed_after_cancel(self, engine, store, job_inputs):
        job = templates.instantiate("scene_suite", job_inputs, mode="auto")
        await store.create_job(job)
        task = await engine.start(job)
        await engine.control(job.job_id, "cancel")
        await asyncio.wait_for(task, timeout=60)
        assert job.job_id not in engine._runtimes
        assert (await store.get_job(job.job_id)).status == JobStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_runtime_rebuilt_on_demand_after_terminal(self, engine, store, job_inputs):
        """终态后 retry_step/control 按需 setdefault 重建镜像（不残留旧状态）"""
        job = templates.instantiate("scene_suite", job_inputs, mode="auto")
        await store.create_job(job)
        task = await engine.start(job)
        await asyncio.wait_for(task, timeout=60)
        assert job.job_id not in engine._runtimes

        resp = await engine.control(job.job_id, "pause")
        assert resp["status"] == "pausing"
        assert job.job_id in engine._runtimes
        # 重建的镜像干净（无旧任务/无残留暂停标志之外的脏状态）
        rt = engine._runtimes[job.job_id]
        assert rt.task is None
        assert rt.step_outputs == {}
