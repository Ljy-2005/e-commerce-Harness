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
        agent._mock_decision()          # analyst
        agent._mock_decision()          # category
        d3 = agent._mock_decision()
        assert d3["action"] == "done", f"Expected done after 2 steps in ab_test, got {d3}"


class TestCoordinatorArtifactVisibility:
    """A35：协调者必须看得到"产物真实状态"

    实测事故：`_build_user_prompt` 只给它最近 10 条消息（每条截断 200 字），
    artifacts 完全不进 prompt → 生图员写了 3 条空图（url/base64 全空）它却宣布
    "已确认生图员已完成 taobao 主图生成（variant_1）"，逼审查员对着不存在的图评分。
    """

    def _session(self, images):
        return {
            "session_id": "s1",
            "task": {"platform": "taobao", "product_info": "肝迅康", "category_hint": "保健品"},
            "messages": [],
            "artifacts": {
                "analysis": {"category": "保健食品", "confidence_score": 72},
                "prompts": {"main_image": {"prompt": "白底主图" * 30},
                            "scene_images": [{"prompt": "a"}, {"prompt": "b"}],
                            "model_variants": {"dalle": "x"}},
                "images": images,
                "review": {"overall_score": None, "verdict": "retry",
                           "needs_human_review": True,
                           "review_blocked_reason": "no_image_accessible"},
            },
        }

    def test_prompt_exposes_artifact_status(self):
        session = self._session([{"prompt_name": "variant_1", "image_url": "https://cdn/a.png"}])
        prompt = CoordinatorAgent()._build_user_prompt(session, "")

        assert "产物状态" in prompt
        assert "variant_1" in prompt or "1 张" in prompt
        assert "retry" in prompt

    def test_prompt_flags_unusable_images(self):
        """实测现场：3 条图记录但没有任何可用图像数据"""
        session = self._session([
            {"prompt_name": f"variant_{i}", "image_url": "", "base64_data": ""} for i in (1, 2, 3)
        ])
        prompt = CoordinatorAgent()._build_user_prompt(session, "")

        assert "可用 0 张" in prompt
        assert "禁止" in prompt and "完成" in prompt

    def test_system_prompt_keeps_memory_section(self):
        """记忆段此前是死代码：YAML 有 system 键时直接 return，永远拼不进去"""
        prompt = CoordinatorAgent()._build_system_prompt("AGENTS", {
            "category": "保健品", "best_score": 82.0, "recalled_count": 5,
            "common_features": ["水飞蓟"], "common_praises": ["自然"],
        })
        assert "历史成功经验" in prompt
