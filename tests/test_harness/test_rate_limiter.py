"""Token Bucket 速率限制器测试"""

import time
import pytest
from src.harness.rate_limiter import RateLimiter


class TestRateLimiter:
    def test_initial_remaining(self):
        limiter = RateLimiter(default_rpm=60)
        assert limiter.remaining("openai", "gpt-4o") == 60

    def test_consume_reduces_remaining(self):
        limiter = RateLimiter(default_rpm=100)
        assert limiter.try_acquire("openai", "gpt-4o")
        assert limiter.remaining("openai", "gpt-4o") < 100

    def test_try_acquire_exhausted(self):
        limiter = RateLimiter(default_rpm=10)
        # 消耗所有 token
        for _ in range(10):
            limiter.try_acquire("test")
        assert not limiter.try_acquire("test")

    def test_different_providers_independent(self):
        limiter = RateLimiter(default_rpm=10)
        # 消耗 openai
        for _ in range(10):
            limiter.try_acquire("openai")
        # openai 耗尽
        assert not limiter.try_acquire("openai")
        # deepseek 仍有 token
        assert limiter.try_acquire("deepseek")

    def test_refill_over_time(self):
        limiter = RateLimiter(default_rpm=600)  # 10 tokens/sec
        # 消耗所有
        for _ in range(600):
            limiter.try_acquire("test")
        assert not limiter.try_acquire("test")
        # 等待 0.2 秒，应有 2 tokens refill
        time.sleep(0.2)
        assert limiter.try_acquire("test")

    def test_configure_updates_rate(self):
        limiter = RateLimiter(default_rpm=10)
        limiter.configure("openai", rpm=1)  # 1 token/min
        # 消耗唯一的 token
        assert limiter.try_acquire("openai")
        assert not limiter.try_acquire("openai")

    @pytest.mark.asyncio
    async def test_acquire_waits_for_token(self):
        limiter = RateLimiter(default_rpm=600)  # 10/s
        for _ in range(600):
            limiter.try_acquire("test")
        assert not limiter.try_acquire("test")
        # acquire 应该等待然后成功
        import asyncio
        async def delayed():
            await asyncio.sleep(0)
            return True
        await delayed()
        # 短暂等待后 acquire 应在 0.5s 内完成
        try:
            await asyncio.wait_for(limiter.acquire("test"), timeout=0.5)
        except asyncio.TimeoutError:
            pytest.fail("acquire timed out")


class TestRateLimiterSingleton:
    def test_global_singleton(self):
        """验证全局速率限制器在 BaseAgent 中正确初始化"""
        from src.agents.base import _get_limiter
        limiter1 = _get_limiter()
        limiter2 = _get_limiter()
        assert limiter1 is limiter2
