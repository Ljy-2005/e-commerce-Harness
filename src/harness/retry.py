"""智能重试 — 指数退避 + Jitter"""

import random
import asyncio
from typing import Callable, Awaitable, Optional


class RetryConfig:
    def __init__(
        self,
        max_retries: int = 3,
        base_delay_ms: int = 1000,
        max_delay_ms: int = 30000,
        backoff_multiplier: float = 2.0,
        jitter: bool = True,
    ):
        self.max_retries = max_retries
        self.base_delay_ms = base_delay_ms
        self.max_delay_ms = max_delay_ms
        self.backoff_multiplier = backoff_multiplier
        self.jitter = jitter

    def delay_for_attempt(self, attempt: int) -> float:
        """计算第 N 次重试的等待时间（秒）"""
        delay = self.base_delay_ms * (self.backoff_multiplier ** attempt) / 1000
        delay = min(delay, self.max_delay_ms / 1000)
        if self.jitter:
            delay = delay * (0.5 + random.random())
        return delay


async def with_retry(
    fn: Callable[[], Awaitable],
    config: Optional[RetryConfig] = None,
    retryable_errors: tuple = (Exception,),
) -> any:
    """用指数退避 + Jitter 重试 async 函数"""
    cfg = config or RetryConfig()
    last_error = None

    for attempt in range(cfg.max_retries + 1):
        try:
            return await fn()
        except retryable_errors as e:
            last_error = e
            if attempt < cfg.max_retries:
                delay = cfg.delay_for_attempt(attempt)
                await asyncio.sleep(delay)

    # 所有重试耗尽：last_error 一定非空（retryable_errors 至少包含 Exception）
    raise last_error  # type: ignore[misc]
