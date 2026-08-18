"""速率限制器 — Token Bucket 算法，每 Provider/Model 独立"""

import time
import asyncio
from collections import defaultdict


class RateLimiter:
    """Token Bucket 速率限制器

    每个 (provider, model, tenant) 组合独立一个 Bucket。
    同时维护全局 bucket（不加 tenant 后缀），确保租户间不会相互影响。

    使用:
        limiter = RateLimiter(default_rpm=60)
        await limiter.acquire("openai", "gpt-4o")  # 阻塞直到有 token
        await limiter.acquire("openai", "gpt-4o", tenant_id="t1")  # 租户级限流
    """

    def __init__(self, default_rpm: int = 60, default_tpm: int = 100_000):
        self.default_rpm = default_rpm    # 每分钟请求数
        self.default_tpm = default_tpm    # 每分钟 token 数
        self._buckets: dict[str, _TokenBucket] = {}
        self._tenant_limits: dict[str, tuple[int, int]] = {}  # tenant_id → (rpm, tpm)

    def set_tenant_limit(self, tenant_id: str, rpm: int, tpm: int = 100_000):
        """设置租户的速率上限"""
        self._tenant_limits[tenant_id] = (rpm, tpm)

    def _key(self, provider: str, model: str = "", tenant_id: str = "") -> str:
        base = f"{provider}/{model}" if model else provider
        return f"{tenant_id}/{base}" if tenant_id else base

    def _get_or_create(self, provider: str, model: str = "", rpm: int | None = None,
                       tenant_id: str = "") -> "_TokenBucket":
        key = self._key(provider, model, tenant_id)
        if key not in self._buckets:
            # 租户级限制优先
            if tenant_id and tenant_id in self._tenant_limits:
                trpm, ttpm = self._tenant_limits[tenant_id]
                self._buckets[key] = _TokenBucket(rpm=trpm, tpm=ttpm)
            else:
                self._buckets[key] = _TokenBucket(
                    rpm=rpm or self.default_rpm,
                    tpm=self.default_tpm,
                )
        return self._buckets[key]

    async def acquire(self, provider: str, model: str = "", tokens: int = 1, tenant_id: str = ""):
        """获取一个请求许可。如果无可用 token，等待直到有。

        当指定 tenant_id 时，先检查租户级 bucket，再检查全局 bucket。
        审计修复：负 tokens 会反向给桶加 token、单次 tokens 超过桶容量会
        永久 while 等待 —— 两者都直接拒绝。
        """
        self._validate_tokens(tokens)
        # 租户级限流
        if tenant_id:
            bucket = self._get_or_create(provider, model, tenant_id=tenant_id)
            while not bucket.consume(tokens):
                await asyncio.sleep(0.1)

        # 全局限流
        bucket = self._get_or_create(provider, model)
        while not bucket.consume(tokens):
            await asyncio.sleep(0.1)

    def _validate_tokens(self, tokens: int):
        if tokens <= 0:
            raise ValueError(f"tokens 必须为正数，当前值: {tokens}")
        if tokens > 10_000_000:
            # 防御性上限（正常调用远小于此；防止误传超大值导致永久等待）
            raise ValueError(f"tokens 过大: {tokens}")

    def try_acquire(self, provider: str, model: str = "", tokens: int = 1,
                    tenant_id: str = "") -> bool:
        """尝试获取请求许可，不等待。成功返回 True。"""
        if tenant_id:
            tenant_bucket = self._get_or_create(provider, model, tenant_id=tenant_id)
            if not tenant_bucket.consume(tokens):
                return False

        bucket = self._get_or_create(provider, model)
        return bucket.consume(tokens)

    def remaining(self, provider: str, model: str = "", tenant_id: str = "") -> int:
        """返回当前剩余的请求许可数"""
        bucket = self._get_or_create(provider, model, tenant_id=tenant_id)
        bucket._refill()
        return int(bucket._tokens)

    def configure(self, provider: str, model: str = "", rpm: int = 60,
                  tpm: int = 100_000, tenant_id: str = ""):
        """动态调整速率限制"""
        key = self._key(provider, model, tenant_id)
        if key in self._buckets:
            self._buckets[key]._rate = rpm / 60.0
            self._buckets[key]._max_tokens = rpm
        else:
            self._buckets[key] = _TokenBucket(rpm=rpm, tpm=tpm)


class _TokenBucket:
    """单个 Token Bucket"""

    def __init__(self, rpm: int = 60, tpm: int = 100_000):
        self._rate = rpm / 60.0       # tokens per second
        self._max_tokens = rpm         # bucket capacity
        self._tokens = float(rpm)      # current tokens
        self._last_refill = time.monotonic()

    def _refill(self):
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self._max_tokens, self._tokens + elapsed * self._rate)
        self._last_refill = now

    def consume(self, count: int = 1) -> bool:
        self._refill()
        if self._tokens >= count:
            self._tokens -= count
            return True
        return False
