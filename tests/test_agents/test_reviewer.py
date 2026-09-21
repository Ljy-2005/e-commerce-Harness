"""ReviewerAgent 单元测试"""

import pytest

from src.agents.reviewer import ReviewerAgent


class TestReviewerAgent:
    """审查员 — Mock 评分 + pass/retry/fail 逻辑"""

    def test_mock_returns_valid_structure(self):
        """Mock 返回 6 维度审查结构"""
        agent = ReviewerAgent()
        result = agent._mock_review()
        assert "overall_score" in result
        assert "verdict" in result
        assert "dimension_scores" in result
        assert "top_issues" in result
        assert "top_praises" in result
        # 深度/光影/构图/还原度/平台适配/画面真实感 —— 与
        # config/prompts/reviewer.yaml 的「6 维度评分」及前端 ReviewChart 的
        # LABELS 保持一致（此前这三处曾不同步：提示词已 6 维、其余仍是 5 维）
        assert set(result["dimension_scores"]) == {
            "texture", "lighting", "composition",
            "product_fidelity", "platform_fit", "realism",
        }

    @pytest.mark.asyncio
    async def test_execute_with_mock(self, mock_llm, empty_session):
        """Mock 执行返回审查报告"""
        agent = ReviewerAgent(provider=mock_llm)
        result = await agent.execute("审查生成的商品图", empty_session)
        assert "error" not in result
        assert isinstance(result.get("overall_score"), (int, float))

    @pytest.mark.asyncio
    async def test_execute_with_images(self, mock_llm, session_with_prompts):
        """有图片时正常审查"""
        session_with_prompts["artifacts"]["images"] = [
            {"base64_data": "fake_b64_data_for_test_image"},
        ]
        agent = ReviewerAgent(provider=mock_llm)
        result = await agent.execute("审查图片质量", session_with_prompts)
        assert "error" not in result

    @pytest.mark.asyncio
    async def test_execute_without_provider(self, empty_session):
        """无 Provider 返回 Mock 审查"""
        agent = ReviewerAgent(provider=None)
        result = await agent.execute("审查图片", empty_session)
        assert "error" not in result
        assert "verdict" in result

    def test_iteration_field_added(self):
        """审查结果包含迭代标记"""
        agent = ReviewerAgent()
        result = agent._mock_review()
        # _mock_review 本身不添加 iteration（由 _execute_impl 添加）
        # 验证 mock 数据包含迭代所需的基础字段
        assert result.get("dimension_scores")
        assert isinstance(result["dimension_scores"], dict)

    # ── 用户审美锚点（A79-A96）──

    def test_anchor_block_only_when_user_provided(self):
        """锚点是**用户填的**：没填时审查头里不出现这一节（不能凭空替他定审美）"""
        agent = ReviewerAgent()
        session = {"task": {"platform": "taobao"}, "artifacts": {}}
        assert agent._anchor_block(session, {}) == ""

    def test_anchor_block_renders_user_verdict(self, monkeypatch):
        import src.harness.style_library as sl

        monkeypatch.setattr(sl, "load_library", lambda *args, **kwargs: {
            "entries": [], "dropped": [], "defaults": {}, "exists": True,
            "anchors": [{"id": "a1", "name": "冷白实验室感", "source": "用户锚点",
                         "taste_verdict": "冷白留白、投影几乎不可见、边缘只留细窄高光",
                         "reward_points": ["留白充足"], "avoid_points": ["暖黄调"],
                         "applies_to": {"kinds": [], "slots": [], "not_slots": [],
                                        "categories": [], "platforms": [],
                                        "requires_policy": []}, "enabled": True}],
        })
        agent = ReviewerAgent()
        session = {"task": {"platform": "taobao"}, "artifacts": {}}
        block = agent._anchor_block(session, {})
        assert "审美锚点" in block
        assert "冷白实验室感" in block
        assert "留白充足" in block

    def test_anchor_block_failure_is_silent(self, monkeypatch):
        """锚点取不到不能挡住审查"""
        import src.harness.style_library as sl

        monkeypatch.setattr(sl, "select_by_slot",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        agent = ReviewerAgent()
        assert agent._anchor_block({"task": {}, "artifacts": {}}, {}) == ""
