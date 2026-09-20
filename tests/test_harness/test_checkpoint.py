"""Checkpoint 持久化测试（test-plan P3 L2）

损坏/半写 checkpoint JSON → load 返回 None（视为"无 checkpoint"），
不抛异常；save/load/delete 往返正确。全部经 _checkpoint_dir 重定向到 tmp。
"""

import pytest

from src.storage import checkpoint as cp_mod


@pytest.fixture
def cp_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(cp_mod, "_checkpoint_dir", lambda: tmp_path)
    return tmp_path


@pytest.mark.asyncio
async def test_save_load_roundtrip(cp_dir):
    await cp_mod.save_checkpoint("s1", {"session_id": "s1", "status": "running", "messages": []})
    state = await cp_mod.load_checkpoint("s1")
    assert state["session_id"] == "s1"
    assert state["status"] == "running"


@pytest.mark.asyncio
async def test_missing_checkpoint_returns_none(cp_dir):
    assert await cp_mod.load_checkpoint("nope") is None


@pytest.mark.asyncio
async def test_corrupted_json_returns_none(cp_dir):
    """半写/损坏 JSON → None（启动恢复路径不再依赖外层 try/except）"""
    (cp_dir / "bad.json").write_text('{"session_id": "bad", "status": "run', encoding="utf-8")
    assert await cp_mod.load_checkpoint("bad") is None


@pytest.mark.asyncio
async def test_non_dict_json_returns_none(cp_dir):
    (cp_dir / "arr.json").write_text("[1, 2, 3]", encoding="utf-8")
    assert await cp_mod.load_checkpoint("arr") is None


@pytest.mark.asyncio
async def test_save_then_delete(cp_dir):
    await cp_mod.save_checkpoint("s1", {"session_id": "s1"})
    await cp_mod.delete_checkpoint("s1")
    assert await cp_mod.load_checkpoint("s1") is None
    # 删除不存在的文件也不抛
    await cp_mod.delete_checkpoint("never-existed")


class TestRuntimeObjectsNotPersisted:
    """A39：运行时对象不得落进 checkpoint

    实测：`_cost_tracker`（CostTracker 实例）经 `json.dump(default=str)` 写成
    `"<src.harness.cost_tracker.CostTracker object at 0x…>"`。从 checkpoint 恢复后
    `_track_cost` 里 `tracker.total_cost` 抛 AttributeError（被 try 吞掉）→
    该会话**从此不再记账**（成本/预算/审计全失效），文件里还留着内存地址。
    """

    @pytest.mark.asyncio
    async def test_cost_tracker_is_dropped_not_stringified(self, cp_dir):
        from src.harness.cost_tracker import CostTracker
        tracker = CostTracker(budget_usd=10.0)
        tracker.record(model="deepseek-v4-flash", tokens_in=100, tokens_out=100)

        await cp_mod.save_checkpoint("s1", {"session_id": "s1", "cost_so_far": 0.0001,
                                            "_cost_tracker": tracker})

        raw = (cp_dir / "s1.json").read_text(encoding="utf-8")
        assert "object at 0x" not in raw
        state = await cp_mod.load_checkpoint("s1")
        assert state["cost_so_far"] == 0.0001
        assert not isinstance(state.get("_cost_tracker"), str)

    @pytest.mark.asyncio
    async def test_unknown_unserializable_value_is_dropped_without_crashing(self, cp_dir):
        class Weird:
            __slots__ = ()

        await cp_mod.save_checkpoint("s2", {"session_id": "s2", "junk": Weird()})
        state = await cp_mod.load_checkpoint("s2")
        assert state["session_id"] == "s2"
        assert "junk" not in state

    @pytest.mark.asyncio
    async def test_normal_fields_survive(self, cp_dir):
        await cp_mod.save_checkpoint("s3", {
            "session_id": "s3", "status": "running", "messages": [{"id": "m1"}],
            "artifacts": {"images": [{"prompt_name": "v1"}]}, "cost_so_far": 0.5,
        })
        state = await cp_mod.load_checkpoint("s3")
        assert state["messages"] == [{"id": "m1"}]
        assert state["artifacts"]["images"][0]["prompt_name"] == "v1"


class TestCostTrackerRestore:
    """恢复后仍要能记账（A39 的配套：自愈）"""

    def test_from_session_rebuilds_when_value_is_garbage(self):
        from src.harness.cost_tracker import CostTracker
        session = {"cost_so_far": 0.25, "_cost_tracker": "<CostTracker object at 0x1>",
                   "cost_budget_usd": 5.0}

        tracker = CostTracker.from_session(session)

        assert isinstance(tracker, CostTracker)
        assert tracker.total_cost == 0.25, "历史成本不能丢"
        assert tracker.budget_usd == 5.0

    def test_from_session_keeps_existing_instance(self):
        from src.harness.cost_tracker import CostTracker
        existing = CostTracker(budget_usd=2.0)
        existing.record(model="deepseek-v4-flash", tokens_in=10, tokens_out=10)
        session = {"cost_so_far": existing.total_cost, "_cost_tracker": existing}

        assert CostTracker.from_session(session) is existing

    @pytest.mark.asyncio
    async def test_cost_still_recorded_after_restore(self):
        """恢复后的会话必须继续记账（此前静默失效）"""
        from src.agents.base import BaseAgent
        from src.harness.cost_tracker import CostTracker

        class _Provider:
            name = "deepseek"

            async def chat(self, messages, model="", json_mode=False):
                return {"content": {"ok": True}, "tokens_used": 1000,
                        "tokens_in": 600, "tokens_out": 400, "cost_usd": 0.00014}

        class _Agent(BaseAgent):
            meta_name = "恢复探针"

            async def _execute_impl(self, task_brief, session):
                result = await self.provider.chat([{"role": "user", "content": task_brief}])
                return self._content_or_error(result, {})

        agent = _Agent(provider=_Provider())
        agent.model_name = "deepseek-v4-flash"
        session = {"session_id": "s9", "tenant_id": "t0", "messages": [], "artifacts": {},
                   "task": {}, "cost_so_far": 0.25, "cost_budget_usd": 5.0,
                   "_cost_tracker": "<CostTracker object at 0x1>"}

        await agent.execute("hi", session)

        assert session["cost_so_far"] > 0.25
