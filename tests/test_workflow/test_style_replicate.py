"""M3 一键风格复刻测试 — 模板全链路 + replicate_style 融合注入"""

import asyncio

import pytest

from src.agents.registry import AgentRegistry
from src.agents.prompt_gen import PromptGeneratorAgent
from src.core.models import AgentMeta
from src.providers.mock import MockLLMProvider
from src.workflow import templates
from src.workflow.models import JobStatus, StepStatus
from tests.test_workflow.conftest import instantiate_and_run


class _SpyPromptAgent(PromptGeneratorAgent):
    """记录任务描述的提示词生成员（验证风格要素确实注入了任务）"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.received_tasks = []

    async def _execute_impl(self, task_brief, session):
        self.received_tasks.append(task_brief)
        return self._mock_prompts()


@pytest.fixture
def spy_registry(loaded_registry):
    """复制注册中心并替换提示词生成员为 spy"""
    registry = AgentRegistry()
    for meta in loaded_registry.list_all():
        if meta.name == "提示词生成员":
            spy = _SpyPromptAgent(provider=MockLLMProvider())
            spy.model_name = "mock"
            registry.register(spy, meta)
        else:
            registry.register(loaded_registry.get(meta.name), meta)
    return registry


class TestStyleReplicateTemplate:
    @pytest.mark.asyncio
    async def test_full_run_completes(self, engine, store):
        job, steps = await instantiate_and_run(engine, store, "style_replicate", {
            "reference_images": ["fake_ref_b64_1234"],
            "product_images": ["fake_b64_data_12345678"],
            "platform": "taobao",
        })
        assert job.status == JobStatus.COMPLETED
        by_node = {s.node: s for s in steps}
        # 风格拆解节点产出完整要素
        breakdown = by_node["analyze_ref"].outputs
        assert breakdown.get("style_prompt_text")
        assert breakdown.get("style_tags")
        # 分支走通过路径
        assert by_node["branch"].outputs["matched"] == "success_end"

    @pytest.mark.asyncio
    async def test_required_inputs(self, engine, store):
        with pytest.raises(ValueError, match="参考风格图"):
            templates.instantiate("style_replicate", {"product_images": ["x"]})


class TestReplicateStyle:
    @pytest.mark.asyncio
    async def test_replicate_injects_style_into_prompt(self, store, spy_registry, job_inputs):
        """replicate_style 后提示词重跑，任务里包含风格拆解文本（风格要素匹配）"""
        from src.workflow.engine import WorkflowEngine

        engine = WorkflowEngine(spy_registry, store)
        job = templates.instantiate("scene_suite", job_inputs, mode="auto")
        await store.create_job(job)
        await asyncio.wait_for(engine.run(job), timeout=60)

        breakdown = await engine.replicate_style(job.job_id, ["fake_ref_b64_5678"])
        assert breakdown.get("style_prompt_text")
        # 等重跑完成
        task = engine._runtimes[job.job_id].task
        await asyncio.wait_for(task, timeout=60)

        job = await store.get_job(job.job_id)
        assert job.status == JobStatus.COMPLETED
        assert "_style_breakdown" in job.context
        assert job.context["retries"] >= 1  # 从 prompt 重跑计数

        spy = spy_registry.get("提示词生成员")
        assert spy.received_tasks, "提示词生成员未被重新执行"
        assert any("[风格复刻]" in t and "风格拆解要素" in t for t in spy.received_tasks)
        assert any("草木绿" in t for t in spy.received_tasks)  # 风格要素文本已融合

    @pytest.mark.asyncio
    async def test_replicate_missing_agent_or_job(self, engine, store, job_inputs):
        with pytest.raises(ValueError, match="不存在"):
            await engine.replicate_style("nope", ["x"])
        # 未注册风格拆解员的注册中心
        from src.agents.registry import AgentRegistry
        from src.workflow.engine import WorkflowEngine
        empty_registry = AgentRegistry()
        engine2 = WorkflowEngine(empty_registry, store)
        job = templates.instantiate("scene_suite", job_inputs, mode="auto")
        await store.create_job(job)
        with pytest.raises(ValueError, match="风格拆解员未注册"):
            await engine2.replicate_style(job.job_id, ["x"])
