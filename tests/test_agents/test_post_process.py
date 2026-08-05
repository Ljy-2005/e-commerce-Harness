"""PostProcessAgent 单元测试"""

import pytest
from src.agents.post_process import PostProcessAgent


class TestPostProcessAgent:
    """图像后处理员 — rembg 检测 + 优雅降级"""

    def test_rembg_available_check(self):
        """检测 rembg 是否可用（仅验证无异常）"""
        agent = PostProcessAgent()
        available = agent._check_rembg()
        assert isinstance(available, bool)

    @pytest.mark.asyncio
    async def test_execute_no_images(self, empty_session):
        """无图片时返回空处理"""
        agent = PostProcessAgent()
        result = await agent.execute("处理图片", empty_session)
        assert result.get("processed") == 0

    @pytest.mark.asyncio
    async def test_execute_with_mock_images(self, session_with_prompts):
        """有图片时处理（无 rembg 应标记 skipped）"""
        session_with_prompts["artifacts"]["images"] = [
            {"base64_data": "aGVsbG8gd29ybGQ="},  # base64("hello world")
        ]
        agent = PostProcessAgent()
        result = await agent.execute("后处理图片", session_with_prompts)
        assert result.get("processed_count") == 1
        assert result.get("rembg_available") is not None

    @pytest.mark.asyncio
    async def test_execute_invalid_base64(self, session_with_prompts):
        """无效 base64 不应崩溃"""
        session_with_prompts["artifacts"]["images"] = [
            {"base64_data": "!!!not valid base64!!!"},
        ]
        agent = PostProcessAgent()
        result = await agent.execute("处理", session_with_prompts)
        assert result.get("processed_count") == 1
