"""execute_with_timeout 单元测试"""

import asyncio
import pytest
from src.harness.timeout import execute_with_timeout, TimeoutError


async def _noop():
    """极短的 async 操作"""
    pass


class TestExecuteWithTimeout:
    """超时控制测试"""

    @pytest.mark.asyncio
    async def test_completes_before_timeout(self):
        """在超时前完成正常返回"""

        async def fast():
            await asyncio.sleep(0.01)
            return "done"

        result = await execute_with_timeout("test_agent", fast(), timeout_ms=5000)
        assert result == "done"

    @pytest.mark.asyncio
    async def test_raises_on_timeout(self):
        """超时时抛出 TimeoutError"""

        async def slow():
            await asyncio.sleep(10)

        with pytest.raises(TimeoutError) as exc:
            await execute_with_timeout("slow_agent", slow(), timeout_ms=10)
        assert exc.value.agent_name == "slow_agent"
        assert exc.value.timeout_ms == 10

    @pytest.mark.asyncio
    async def test_timeout_error_contains_info(self):
        """TimeoutError 包含 agent 名称和超时时间"""

        async def slow():
            await asyncio.sleep(1)

        with pytest.raises(TimeoutError) as exc:
            await execute_with_timeout("保健品分析员", slow(), timeout_ms=1)
        assert "保健品分析员" in str(exc.value)
        assert "1" in str(exc.value)

    @pytest.mark.asyncio
    async def test_zero_timeout_validation_async(self):
        """在 async 上下文中验证 timeout_ms=0 抛 ValueError"""
        with pytest.raises(ValueError, match="正数"):
            await execute_with_timeout("agent", _noop(), timeout_ms=0)

    @pytest.mark.asyncio
    async def test_negative_timeout_validation_async(self):
        """在 async 上下文中验证 timeout_ms=-1 抛 ValueError"""
        with pytest.raises(ValueError, match="正数"):
            await execute_with_timeout("agent", _noop(), timeout_ms=-1)
