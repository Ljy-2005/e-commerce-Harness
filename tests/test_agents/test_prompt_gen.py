"""PromptGeneratorAgent 单元测试"""

import pytest
from src.agents.prompt_gen import PromptGeneratorAgent


class TestPromptGeneratorAgent:
    """提示词生成员 — Mock 数据 + 结构验证"""

    def test_mock_returns_valid_structure(self):
        """Mock 返回完整提示词结构"""
        agent = PromptGeneratorAgent()
        result = agent._mock_prompts()
        assert "main_image" in result
        assert "scene_images" in result
        assert result.get("main_image", {}).get("prompt")

    @pytest.mark.asyncio
    async def test_execute_without_prompts(self, mock_llm, empty_session):
        """无 prompt 时返回 Mock 数据"""
        agent = PromptGeneratorAgent(provider=mock_llm)
        result = await agent.execute("生成淘宝风格提示词", empty_session)
        assert "error" not in result

    @pytest.mark.asyncio
    async def test_execute_with_analysis(self, mock_llm, session_with_analysis):
        """有分析数据时正常执行"""
        agent = PromptGeneratorAgent(provider=mock_llm)
        result = await agent.execute("生成提示词", session_with_analysis)
        assert "error" not in result
        # Mock 返回的提示词应该有内容
        main = result.get("main_image", {})
        if main:
            assert main.get("prompt") or main.get("platform")

    @pytest.mark.asyncio
    async def test_execute_without_provider(self, empty_session):
        """无 Provider 也返回 Mock 数据"""
        agent = PromptGeneratorAgent(provider=None)
        result = await agent.execute("生成提示词", empty_session)
        assert "error" not in result


class TestStyleArchiveInjection:
    """逐槽位风格档案（A79-A96，用户指定的「风格词库」）

    真实事故预警（本轮自查 #9）：Mock 路径此前在拼 user_msg 前就 early-return，
    于是"用了哪条档案"在 Mock/测试/前端里**永远是空的** —— e2e 断言会全部落空。
    所以这里同时钉住 Mock 与真实两条路径。
    """

    @pytest.mark.asyncio
    async def test_mock_path_still_reports_style_refs(self, mock_llm, session_with_analysis):
        agent = PromptGeneratorAgent(provider=mock_llm)
        result = await agent.execute("生成提示词", session_with_analysis)
        refs = result.get("style_refs")
        assert refs and refs["enabled"] is True, result.get("style_refs")
        assert refs["entries"], "Mock 路径也要给出命中的档案"
        assert refs["slots"], "逐槽位命中关系不能为空"
        assert "采用风格档案" in result.get("message", "")

    @pytest.mark.asyncio
    async def test_real_path_brief_carries_archive_and_slot_mapping(self, session_with_analysis):
        class _Spy:
            name = "spy"
            capabilities = ["text"]

            def __init__(self):
                self.messages = []

            async def chat(self, messages=None, **kwargs):
                self.messages = messages or []
                return {"content": {"set_plan": {"platform": "taobao", "slots": [
                    {"number": 1, "slot_id": "main_white", "prompt": "纯白底，纸盒居中占 88%"}]}}}

        spy = _Spy()
        agent = PromptGeneratorAgent(provider=spy)
        result = await agent.execute("生成提示词", session_with_analysis)
        brief = spy.messages[-1]["content"]
        assert "适用风格档案" in brief
        assert "逐张对应" in brief and "第1张" in brief
        assert "不要照搬档案措辞" in brief
        assert result["style_refs"]["entries"]

    @pytest.mark.asyncio
    async def test_subset_only_reports_subset_slots(self, mock_llm, session_with_analysis):
        """`--slots` 最小付费冒烟：只报子集里的槽位（不能把整套都算进来）"""
        session_with_analysis["task"]["slot_override"] = ["main_white"]
        agent = PromptGeneratorAgent(provider=mock_llm)
        result = await agent.execute("生成提示词", session_with_analysis)
        assert list(result["style_refs"]["slots"]) == ["main_white"]

    @pytest.mark.asyncio
    async def test_library_failure_does_not_block(self, mock_llm, session_with_analysis,
                                                 monkeypatch):
        """档案库出问题**不能挡住出图**（增强，不是依赖）"""
        import src.harness.style_library as sl

        def _boom(*args, **kwargs):
            raise RuntimeError("档案库炸了")

        monkeypatch.setattr(sl, "select_by_slot", _boom)
        agent = PromptGeneratorAgent(provider=mock_llm)
        result = await agent.execute("生成提示词", session_with_analysis)
        assert "error" not in result
        assert result["style_refs"]["enabled"] is False
        assert any("风格档案库不可用" in note for note in result["style_refs"]["notes"])
