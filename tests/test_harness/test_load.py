"""L8 性能/负载基础测试（test-plan P5，@pytest.mark.slow）

- 默认不跑（pyproject addopts 排除 slow）；`pytest -m slow` 手动触发，
  CI 后端 job 用 `-m "not real"` 含本套件；
- 全部 Mock 确定性执行（fast limiter + checkpoint noop 隔离）；
- 门槛：并发完成率 100%、批量计数精确、无会话泄漏、限流可恢复。
"""

import asyncio

import pytest
from fastapi.testclient import TestClient

from src.main import app

pytestmark = pytest.mark.slow

client = TestClient(app)


@pytest.fixture(autouse=True)
def _fast_limiter(monkeypatch):
    """注入高额限流器（沿用 test_workflow/conftest 的隔离模式）"""
    import src.agents.base as agents_base
    from src.harness.rate_limiter import RateLimiter
    monkeypatch.setattr(agents_base, "_rate_limiter", RateLimiter(default_rpm=100_000))


@pytest.fixture(autouse=True)
def _no_checkpoint_writes(monkeypatch):
    """会话运行会写 data/checkpoints —— 测试期间 noop，防污染"""
    import src.storage.checkpoint as cp_mod

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(cp_mod, "save_checkpoint", _noop)
    monkeypatch.setattr(cp_mod, "delete_checkpoint", _noop)


@pytest.fixture(autouse=True)
def _reset_auth_failures():
    import src.api.auth as auth_mod
    auth_mod._AUTH_FAILURES.clear()
    yield
    auth_mod._AUTH_FAILURES.clear()


async def _make_engine():
    from src.agents.registry import AgentRegistry
    from src.chat.engine import ChatEngine
    from src.chat.session import SessionManager
    from src.providers import get_provider_registry

    registry = AgentRegistry()
    await registry.load_from_config(get_provider_registry())
    mgr = SessionManager()
    return registry, mgr, ChatEngine(registry=registry, session_manager=mgr)


@pytest.fixture
def workflow_store(tmp_path):
    """独立 SQLite 存储（test_harness 目录看不到 test_workflow/conftest 的 store）"""
    from src.workflow.job_store import JobStore
    return JobStore(db_path=tmp_path / "load_test.db")


@pytest.fixture
def workflow_engine(workflow_store):
    from src.agents.registry import AgentRegistry
    from src.providers import get_provider_registry
    from src.workflow.engine import WorkflowEngine

    registry = AgentRegistry()
    _run_async_sync(registry.load_from_config(get_provider_registry()))
    return WorkflowEngine(registry, workflow_store)


def _run_async_sync(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestConcurrentChat:
    @pytest.mark.asyncio
    async def test_10_concurrent_sessions_complete(self):
        """10 并发 Mock 群聊：完成率 100%，无共享状态串扰
        （回归防护：Coordinator 状态曾为实例级 → 并发会话互相覆盖
        _workflow_index 导致流程跳步、产出物缺失）"""
        _, mgr, engine = await _make_engine()
        sessions = [
            mgr.create(
                product_images=["fake_b64_data_12345678"],
                product_info=f"商品{i}",
                platform="taobao",
                tenant_id=f"t{i % 2}",
            )
            for i in range(10)
        ]
        results = await asyncio.gather(*[engine.run(s) for s in sessions])
        for r in results:
            assert r["status"] == "completed", r.get("error_history")
            assert "analysis" in r["artifacts"]
            assert "review" in r["artifacts"]
            assert "compliance" in r["artifacts"]
        # 无串扰：会话 id 全部唯一、各自产出独立
        ids = [r["session_id"] for r in results]
        assert len(set(ids)) == 10
        # 会话仍可按租户取回（未被并发互相覆盖）
        assert len(mgr.list_ids("t0")) == 5
        assert len(mgr.list_ids("t1")) == 5


class TestBatchLoad:
    @pytest.mark.asyncio
    async def test_batch_100_items_boundary(self, workflow_engine, workflow_store):
        """100 项批量（MAX_BATCH_ITEMS 边界）：计数精确、死信隔离"""
        from src.workflow.batch import BatchScheduler

        scheduler = BatchScheduler(workflow_engine, workflow_store)
        items = [
            {"product_images": ["fake_b64_data_12345678"], "platform": "taobao",
             "product_info": f"商品{i}"}
            for i in range(99)
        ]
        items.append({"platform": "taobao"})  # 缺 product_images → 必填缺失 → 死信
        batch = await scheduler.submit("scene_suite", items, tenant_id="default",
                                       max_concurrency=10)
        for _ in range(600):  # 60s 预算
            b = await workflow_store.get_batch(batch["batch_id"])
            if b["status"] in ("completed", "partial", "failed", "cancelled"):
                break
            await asyncio.sleep(0.1)
        assert b["status"] == "partial"      # 有死信 → partial
        assert b["done"] == 99
        assert b["failed"] == 1
        batch_items = await workflow_store.get_batch_items(batch["batch_id"])
        failed = [it for it in batch_items if it["status"] == "failed"]
        assert len(failed) == 1
        assert "必填输入" in failed[0]["error"]
        assert failed[0]["attempts"] == 3    # 1 初始 + 2 自动重试


class TestLongRunStability:
    @pytest.mark.asyncio
    async def test_3_sessions_full_turns_no_leak(self):
        """3 会话 × 15 轮上限连续跑：全部完成、删除后 _sessions 归零（无泄漏）"""
        _, mgr, engine = await _make_engine()
        sessions = [
            mgr.create(
                product_images=["fake_b64_data_12345678"],
                max_turns=15,
                tenant_id="default",
            )
            for _ in range(3)
        ]
        results = await asyncio.gather(*[engine.run(s) for s in sessions])
        assert all(r["status"] == "completed" for r in results)
        assert all(r["turn_count"] <= 15 for r in results)
        assert len(mgr.list_ids()) == 3
        # 删除后内存清零（TTL 之外的管理面清理路径）
        for s in sessions:
            await mgr.delete(s["session_id"])
        assert mgr.list_ids() == []


class TestRateLimitEffectiveness:
    def test_429_then_recover_after_window(self, monkeypatch):
        """高频错误请求触发 429；失败窗口过期后恢复（401 而非永久锁死）"""
        import src.api.auth as auth_mod

        monkeypatch.setenv("ECOMM_API_KEY", "secret-123")
        for _ in range(11):
            client.get("/api/sessions", headers={"X-API-Key": "wrong"})
        assert client.get(
            "/api/sessions", headers={"X-API-Key": "wrong"}
        ).status_code == 429

        # 回拨失败记录时间戳 → 模拟 60s 窗口过期（确定性，不等真实时间）
        for ip, stamps in list(auth_mod._AUTH_FAILURES.items()):
            auth_mod._AUTH_FAILURES[ip] = [t - 61 for t in stamps]
        resp = client.get("/api/sessions", headers={"X-API-Key": "wrong"})
        assert resp.status_code == 401  # 限流解除，回到普通鉴权失败
        # 正确 Key 立即可用
        assert client.get(
            "/api/sessions", headers={"X-API-Key": "secret-123"}
        ).status_code == 200
