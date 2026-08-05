"""with_retry 单元测试"""

import asyncio
import pytest
from src.harness.retry import with_retry, RetryConfig


class TestRetryConfig:
    """RetryConfig 参数验证"""

    def test_default_config(self):
        cfg = RetryConfig()
        assert cfg.max_retries == 3
        assert cfg.base_delay_ms == 1000
        assert cfg.max_delay_ms == 30_000
        assert cfg.backoff_multiplier == 2.0
        assert cfg.jitter is True

    def test_delay_increases_with_attempt(self):
        """退避延时随重试次数指数增长"""
        cfg = RetryConfig(base_delay_ms=1000, backoff_multiplier=2.0, jitter=False)
        d0 = cfg.delay_for_attempt(0)
        d1 = cfg.delay_for_attempt(1)
        d2 = cfg.delay_for_attempt(2)
        assert d1 > d0  # 第二次 > 第一次
        assert d2 > d1  # 第三次 > 第二次

    def test_delay_capped_at_max(self):
        """延时不超过 max_delay_ms（jitter 可能导致轻微超出，允许 2x 容差）"""
        cfg = RetryConfig(max_delay_ms=100, base_delay_ms=1000, backoff_multiplier=10)
        delay = cfg.delay_for_attempt(3)
        # jitter 范围 0.5x-1.5x，max_delay 是 base 上限
        assert delay <= 100 / 1000 * 1.5 + 0.001

    def test_delay_with_jitter_varies(self):
        """Jitter 产生不同的延时"""
        cfg = RetryConfig(jitter=True)
        delays = [cfg.delay_for_attempt(0) for _ in range(10)]
        # 10 次延时不应全部相同（概率极低）
        assert len(set(round(d, 2) for d in delays)) > 1


class TestWithRetry:
    """with_retry 重试逻辑"""

    @pytest.mark.asyncio
    async def test_success_first_attempt(self):
        """首次成功不重试"""
        call_count = 0

        async def succeed():
            nonlocal call_count
            call_count += 1
            return "ok"

        result = await with_retry(succeed, RetryConfig(max_retries=3))
        assert result == "ok"
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_retry_on_failure_then_succeed(self):
        """失败一次后成功"""
        call_count = 0

        async def fail_once():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ValueError("transient error")
            return "recovered"

        result = await with_retry(
            fail_once,
            RetryConfig(max_retries=2, base_delay_ms=1),
            retryable_errors=(ValueError,),
        )
        assert result == "recovered"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_exhaust_retries(self):
        """重试耗尽后抛出异常"""
        async def always_fail():
            raise RuntimeError("persistent failure")

        with pytest.raises(RuntimeError, match="persistent failure"):
            await with_retry(
                always_fail,
                RetryConfig(max_retries=2, base_delay_ms=1),
                retryable_errors=(RuntimeError,),
            )

    @pytest.mark.asyncio
    async def test_non_retryable_error_not_caught(self):
        """非 retryable 异常直接抛出，不重试"""
        async def fail_immediate():
            raise TypeError("not retryable")

        with pytest.raises(TypeError, match="not retryable"):
            await with_retry(
                fail_immediate,
                RetryConfig(max_retries=3, base_delay_ms=1),
                retryable_errors=(ValueError,),  # TypeError 不在列表中
            )

    @pytest.mark.asyncio
    async def test_zero_max_retries(self):
        """max_retries=0 时一次失败即终止"""
        calls = 0

        async def fail():
            nonlocal calls
            calls += 1
            raise ValueError("fail")

        with pytest.raises(ValueError):
            await with_retry(
                fail,
                RetryConfig(max_retries=0, base_delay_ms=1),
                retryable_errors=(ValueError,),
            )
        assert calls == 1  # 只尝试一次
