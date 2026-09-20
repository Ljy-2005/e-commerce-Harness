"""每轮轻量 checkpoint（B3 第二点）

现状问题：checkpoint 只在**人工暂停 / 会话终态 / 上下文压缩前**落盘，长会话中途强停/崩溃会丢掉
最近整段群聊轨迹（实测强停后恢复只剩 HITL 那 6 轮）。直接改成"每轮全量落盘"又不可行——
checkpoint 里含 `task.product_images`（用户上传图的 base64，2.6MB 图 → 3.5MB JSON），
每轮写 3.5MB 是明显的写放大。

方案：**每轮写"轻量快照"**（剔掉上传图等大字段，只保留轨迹/产物/成本），
完整快照仍在人工暂停、终态、压缩前写入。
"""

import json

import pytest

from src.chat.engine import ChatEngine
from src.chat.session import SessionManager
from src.storage import checkpoint as cp_mod


@pytest.fixture
def cp_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(cp_mod, "_checkpoint_dir", lambda: tmp_path)
    return tmp_path


BIG_B64 = "A" * 20000


def _state(session_id="s1"):
    return {
        "session_id": session_id,
        "status": "running",
        "task": {"product_images": [BIG_B64], "reference_images": [BIG_B64],
                 "platform": "taobao", "category_hint": "保健品"},
        "messages": [{"id": "m1", "sender": "商品分析员", "content": {"category": "保健品"}}],
        "artifacts": {"images": [{"prompt_name": "variant_1", "saved_path": "a/b.jpg"}]},
        "cost_so_far": 0.0477,
        "error_history": [],
    }


class TestSlimCheckpoint:
    @pytest.mark.asyncio
    async def test_slim_drops_uploaded_images_but_keeps_track(self, cp_dir):
        await cp_mod.save_checkpoint("s1", _state(), slim=True)

        raw = (cp_dir / "s1.json").read_text(encoding="utf-8")
        state = json.loads(raw)

        assert "product_images" not in state["task"], "轻量快照不应带上上传图的 base64"
        assert "reference_images" not in state["task"]
        assert state["task"]["platform"] == "taobao", "其余任务字段必须保留"
        assert state["messages"] and state["artifacts"]["images"]
        assert state["cost_so_far"] == 0.0477
        assert BIG_B64 not in raw, "大字段确实没落盘"

    @pytest.mark.asyncio
    async def test_full_keeps_uploaded_images(self, cp_dir):
        await cp_mod.save_checkpoint("s1", _state())
        state = await cp_mod.load_checkpoint("s1")
        assert state["task"]["product_images"] == [BIG_B64], "完整快照仍要保存上传图"

    @pytest.mark.asyncio
    async def test_slim_is_much_smaller(self, cp_dir):
        await cp_mod.save_checkpoint("full", _state("full"))
        await cp_mod.save_checkpoint("slim", _state("slim"), slim=True)
        full_size = (cp_dir / "full.json").stat().st_size
        slim_size = (cp_dir / "slim.json").stat().st_size
        assert slim_size < full_size / 2, f"轻量快照应显著更小：{slim_size} vs {full_size}"


class _CountingSessions(SessionManager):
    def __init__(self):
        super().__init__()
        self.slim_saves = 0

    async def update(self, session_id, state, slim: bool = False):
        if slim:
            self.slim_saves += 1
        return await super().update(session_id, state, slim=slim)


class TestEngineSavesEveryTurn:
    @pytest.mark.asyncio
    async def test_each_agent_step_triggers_a_slim_save(self, tmp_path, monkeypatch):
        import src.core.config as config_mod
        from src.agents.registry import AgentRegistry
        from src.providers import get_provider_registry

        monkeypatch.setattr(config_mod, "data_root", lambda: tmp_path)
        (tmp_path / "checkpoints").mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(cp_mod, "_checkpoint_dir", lambda: tmp_path / "checkpoints")

        registry = AgentRegistry()
        await registry.load_from_config(get_provider_registry())
        sessions = _CountingSessions()
        engine = ChatEngine(registry=registry, session_manager=sessions)
        session = sessions.create(product_images=["x"], platform="taobao")

        await engine.run(session, start_index=5)   # 审查员 + 合规审查员 → 至少两次

        assert sessions.slim_saves >= 2, "每个 Agent 步骤后都应落一次轻量快照"
        files = list((tmp_path / "checkpoints").glob("*.json"))
        assert files, "轻量快照应真的写到了磁盘"
