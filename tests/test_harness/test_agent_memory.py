"""Agent 记忆/学习测试

审计修复：AgentMemory 注入临时目录（storage_dir=tmp_path），
此前用默认 data/memory 目录，clear() 会清空真实记忆库。
"""

import pytest
from src.harness.agent_memory import AgentMemory


@pytest.fixture
def memory(tmp_path):
    return AgentMemory(storage_dir=str(tmp_path / "memory"))


class TestAgentMemory:
    @pytest.mark.asyncio
    async def test_remember_and_recall(self, memory):
        await memory.clear()

        await memory.remember(
            session_id="test-1", category="保健品",
            analysis={"category": "保健品", "features": ["高纯度", "蓝帽认证"], "ingredients": ["水飞蓟"]},
            prompts={"main_image": {"prompt": "保健品白底主图，柔和自然光"}},
            review={"overall_score": 88, "verdict": "pass", "top_praises": ["质感好"]},
        )

        entries = await memory.recall("保健品")
        assert len(entries) >= 1
        assert entries[0]["score"] == 88

    @pytest.mark.asyncio
    async def test_low_score_not_remembered(self, memory):
        await memory.clear("保健品")

        await memory.remember(
            session_id="test-low", category="保健品",
            analysis={"category": "保健品", "features": []},
            prompts={"main_image": {"prompt": "test"}},
            review={"overall_score": 50, "verdict": "retry"},
        )

        entries = await memory.recall("保健品")
        assert len(entries) == 0  # 低分不记录

    @pytest.mark.asyncio
    async def test_recall_similar(self, memory):
        await memory.clear()

        await memory.remember(
            session_id="a", category="保健品",
            analysis={"features": ["高纯度", "蓝帽"]},
            prompts={"main_image": {"prompt": "主图A"}},
            review={"overall_score": 90, "verdict": "pass"},
        )
        await memory.remember(
            session_id="b", category="保健品",
            analysis={"features": ["植物配方", "蓝帽"]},
            prompts={"main_image": {"prompt": "主图B"}},
            review={"overall_score": 85, "verdict": "pass"},
        )

        # 查询"蓝帽"相关 → 两条都有交集
        similar = await memory.recall_similar("保健品", features=["蓝帽"])
        assert len(similar) >= 1

    @pytest.mark.asyncio
    async def test_stats(self, memory):
        stats = await memory.stats()
        assert "total_entries" in stats
        assert "by_category" in stats
        assert "avg_score" in stats

    @pytest.mark.asyncio
    async def test_category_isolation(self, memory):
        await memory.clear()

        await memory.remember(
            session_id="c1", category="保健品",
            analysis={"category": "保健品", "features": ["a"]},
            prompts={"main_image": {"prompt": "test"}},
            review={"overall_score": 80, "verdict": "pass"},
        )
        await memory.remember(
            session_id="c2", category="化妆品",
            analysis={"category": "化妆品", "features": ["b"]},
            prompts={"main_image": {"prompt": "test"}},
            review={"overall_score": 85, "verdict": "pass"},
        )

        health = await memory.recall("保健品")
        cosmetic = await memory.recall("化妆品")
        assert len(health) >= 1
        assert len(cosmetic) >= 1
        # 品类隔离：保健品的 entries 不含化妆品
        for e in health:
            assert e["category"] == "保健品"
