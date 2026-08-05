"""超时控制 — asyncio.wait_for 包装"""

import asyncio
from typing import Coroutine, Any


class TimeoutError(Exception):
    """操作超时"""
    def __init__(self, agent_name: str, timeout_ms: int):
        self.agent_name = agent_name
        self.timeout_ms = timeout_ms
        super().__init__(f"{agent_name} 超时 ({timeout_ms}ms)")


async def execute_with_timeout(agent_name: str, coro: Coroutine[Any, Any, Any], timeout_ms: int):
    """在指定时间内执行协程，超时抛 TimeoutError"""
    if timeout_ms <= 0:
        raise ValueError(f"timeout_ms 必须为正数，当前值: {timeout_ms}")
    try:
        return await asyncio.wait_for(coro, timeout=timeout_ms / 1000)
    except asyncio.TimeoutError:
        raise TimeoutError(agent_name, timeout_ms)
