"""熔断器三态切换测试"""

import time
from src.harness.circuit import CircuitBreaker, CircuitState


class TestCircuitBreaker:
    def test_initial_state(self):
        cb = CircuitBreaker("test")
        assert cb.state == CircuitState.CLOSED

    def test_open_after_failures(self):
        cb = CircuitBreaker("test", failure_threshold=3, recovery_timeout_ms=99999)
        for _ in range(3):
            cb.record_failure()
        assert cb.state == CircuitState.OPEN
        assert not cb.allow_request()

    def test_half_open_after_recovery(self):
        cb = CircuitBreaker("test", failure_threshold=2, recovery_timeout_ms=1)
        for _ in range(2):
            cb.record_failure()
        assert cb.state == CircuitState.OPEN
        time.sleep(0.01)  # 等待恢复
        assert cb.state == CircuitState.HALF_OPEN
        assert cb.allow_request()

    def test_close_after_half_open_success(self):
        cb = CircuitBreaker("test", failure_threshold=2, recovery_timeout_ms=1, half_open_max=2)
        for _ in range(2):
            cb.record_failure()
        time.sleep(0.05)  # 等待恢复
        assert cb.state == CircuitState.HALF_OPEN  # 触发 _maybe_recover()
        # allow_request() 递增计数器（模拟请求通过 gate）
        assert cb.allow_request()
        cb.record_success()
        assert cb.state == CircuitState.HALF_OPEN  # 第 1 次成功
        assert cb.allow_request()
        cb.record_success()  # 第 2 次成功 → CLOSED
        assert cb.state == CircuitState.CLOSED

    def test_reopen_on_half_open_failure(self):
        cb = CircuitBreaker("test", failure_threshold=2, recovery_timeout_ms=1)
        for _ in range(2):
            cb.record_failure()  # OPEN
        time.sleep(0.01)  # HALF_OPEN
        cb.record_failure()  # 立即 OPEN
        assert cb.state == CircuitState.OPEN

    def test_allow_request_closed(self):
        cb = CircuitBreaker("test")
        assert cb.allow_request()
