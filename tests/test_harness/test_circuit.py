"""熔断器三态切换测试

时序测试不使用 wall-clock sleep（在满载机器上会偶发失败），
改为回拨 `_last_failure_time` 确定性模拟恢复窗口已过。
"""

import time
from src.harness.circuit import CircuitBreaker, CircuitState


def _simulate_recovery_elapsed(cb: CircuitBreaker):
    """把上次失败时间回拨 1 秒，模拟恢复窗口已过"""
    cb._last_failure_time = time.monotonic() - 1.0


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
        cb = CircuitBreaker("test", failure_threshold=2, recovery_timeout_ms=1000)
        for _ in range(2):
            cb.record_failure()
        assert cb.state == CircuitState.OPEN
        _simulate_recovery_elapsed(cb)  # 恢复窗口已过
        assert cb.state == CircuitState.HALF_OPEN
        assert cb.allow_request()

    def test_close_after_half_open_success(self):
        cb = CircuitBreaker("test", failure_threshold=2, recovery_timeout_ms=1000, half_open_max=2)
        for _ in range(2):
            cb.record_failure()
        _simulate_recovery_elapsed(cb)
        assert cb.state == CircuitState.HALF_OPEN  # 触发 _maybe_recover()
        # allow_request() 递增计数器（模拟请求通过 gate）
        assert cb.allow_request()
        cb.record_success()
        assert cb.state == CircuitState.HALF_OPEN  # 第 1 次成功
        assert cb.allow_request()
        cb.record_success()  # 第 2 次成功 → CLOSED
        assert cb.state == CircuitState.CLOSED

    def test_reopen_on_half_open_failure(self):
        cb = CircuitBreaker("test", failure_threshold=2, recovery_timeout_ms=1000)
        for _ in range(2):
            cb.record_failure()  # OPEN
        _simulate_recovery_elapsed(cb)
        assert cb.state == CircuitState.HALF_OPEN  # 先进入半开
        cb.record_failure()  # HALF_OPEN 下单次失败 → 立即 OPEN
        assert cb.state == CircuitState.OPEN

    def test_allow_request_closed(self):
        cb = CircuitBreaker("test")
        assert cb.allow_request()

    def test_full_recovery_lifecycle(self):
        """P3 回归（test-plan L2）：熔断恢复全流程 OPEN → HALF_OPEN → CLOSED，
        计数器归零、半开窗口放行上限内请求"""
        cb = CircuitBreaker("full", failure_threshold=3, recovery_timeout_ms=1000, half_open_max=2)
        for _ in range(3):
            cb.record_failure()
        assert cb.state == CircuitState.OPEN
        assert not cb.allow_request()

        _simulate_recovery_elapsed(cb)  # 恢复窗口已过 → HALF_OPEN
        assert cb.state == CircuitState.HALF_OPEN

        # 半开窗口：放行前 half_open_max 个探测请求，超出被拒
        assert cb.allow_request()
        assert cb.allow_request()
        assert not cb.allow_request()

        cb.record_success()
        cb.record_success()
        assert cb.state == CircuitState.CLOSED
        assert cb._failure_count == 0
        assert cb.allow_request()

        # CLOSED 下成功保持计数清零
        cb.record_success()
        assert cb._failure_count == 0
