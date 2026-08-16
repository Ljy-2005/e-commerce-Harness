"""Workflow 测试共享 fixtures"""

import asyncio

import pytest

from src.agents.registry import AgentRegistry
from src.providers import get_provider_registry
from src.workflow.engine import WorkflowEngine
from src.workflow.job_store import JobStore
from src.workflow import templates


@pytest.fixture(autouse=True)
def _fast_rate_limiter(monkeypatch):
    """注入高额限流器，隔离全局 60rpm 限流对批量/并发测试的节流影响
    （限流器本身在 tests/test_harness/test_rate_limiter.py 有专门测试）"""
    from src.harness.rate_limiter import RateLimiter
    import src.agents.base as agents_base
    monkeypatch.setattr(agents_base, "_rate_limiter", RateLimiter(default_rpm=100_000))


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture(scope="session")
def loaded_registry():
    """加载全部 Agent（Mock Provider）的注册中心"""
    registry = AgentRegistry()
    _run_async(registry.load_from_config(get_provider_registry()))
    return registry


@pytest.fixture
def store(tmp_path):
    """独立 SQLite 存储（测试隔离，不污染 data/workflow.db）"""
    return JobStore(db_path=tmp_path / "test_workflow.db")


@pytest.fixture
def engine(loaded_registry, store):
    return WorkflowEngine(loaded_registry, store)


@pytest.fixture
def job_inputs():
    return {
        "product_images": ["fake_b64_data_12345678"],
        "platform": "taobao",
        "category_hint": "保健品",
        "collaboration_mode": "serial",
    }


async def instantiate_and_run(engine, store, template_name, inputs, mode="auto", timeout=60):
    """实例化 → 启动 → 等待完成，返回 (job, steps)"""
    job = templates.instantiate(template_name, inputs, tenant_id="default", mode=mode)
    await store.create_job(job)
    task = await engine.start(job)
    await asyncio.wait_for(task, timeout=timeout)
    job = await store.get_job(job.job_id)
    steps = await store.get_steps(job.job_id)
    return job, steps
