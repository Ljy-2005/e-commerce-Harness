"""CoordinatorAgent 单元测试"""

import pytest
from src.agents.coordinator import CoordinatorAgent


class TestCoordinatorAgent:
    """中心决策者 — 决策序列 + 模式切换 + Mock 决策"""

    def test_serial_workflow_starts_with_analyst(self):
        """串行模式首步是邀请商品分析员"""
        agent = CoordinatorAgent()
        agent.set_mode("serial")
        decision = agent._mock_decision()
        assert decision["action"] == "invite"
        assert decision["agent_name"] == "商品分析员"

    def test_serial_workflow_ends_with_done(self):
        """串行模式遍历完所有步骤后返回 done"""
        agent = CoordinatorAgent()
        agent.set_mode("serial")
        for _ in range(8):  # 跳过所有 invite 步骤
            agent._mock_decision()
        decision = agent._mock_decision()
        assert decision["action"] == "done"

    def test_ab_generate_mode_has_two_image_gens(self):
        """A/B 生成模式有两轮生图（方案A + 方案B）"""
        agent = CoordinatorAgent()
        agent.set_mode("ab_generate")
        decisions = []
        for _ in range(9):
            decisions.append(agent._mock_decision())
        # 检查有两个生图员的 invite
        gen_invites = [d for d in decisions if d.get("agent_name") == "生图员" and d["action"] == "invite"]
        assert len(gen_invites) >= 2, f"Expected 2 image_gen invites, got {len(gen_invites)}"

    def test_vote_mode_has_three_reviewers(self):
        """投票模式有 3 个审查员"""
        agent = CoordinatorAgent()
        agent.set_mode("vote")
        decisions = []
        for _ in range(9):
            decisions.append(agent._mock_decision())
        reviewer_invites = [d for d in decisions if d.get("agent_name") == "审查员" and d["action"] == "invite"]
        assert len(reviewer_invites) >= 3, f"Expected 3 reviewer invites, got {len(reviewer_invites)}"

    def test_reset_restarts_workflow(self):
        """reset 后回到第一步"""
        agent = CoordinatorAgent()
        agent.set_mode("serial")
        agent._mock_decision()  # step 0
        agent._mock_decision()  # step 1
        agent.reset()
        decision = agent._mock_decision()
        assert decision["agent_name"] == "商品分析员"

    @pytest.mark.asyncio
    async def test_decide_async_without_provider(self, empty_session):
        """无 Provider 时 async decide 返回 Mock 决策"""
        agent = CoordinatorAgent()
        agent.set_mode("serial")
        decision = await agent.decide(empty_session)
        assert decision["action"] in ("invite", "done")

    @pytest.mark.asyncio
    async def test_decide_async_with_mock_provider(self, mock_llm, empty_session):
        """Mock Provider 的 async decide 也返回 Mock 决策"""
        agent = CoordinatorAgent(provider=mock_llm)
        agent.set_mode("serial")
        decision = await agent.decide(empty_session)
        assert decision["action"] in ("invite", "done")

    def test_ab_test_mode_short_circuit(self):
        """ab_test 模式只有 analyst + category，然后 done"""
        agent = CoordinatorAgent()
        agent.set_mode("ab_test")
        d1 = agent._mock_decision()
        d2 = agent._mock_decision()
        d3 = agent._mock_decision()
        assert d3["action"] == "done", f"Expected done after 2 steps in ab_test, got {d3}"
