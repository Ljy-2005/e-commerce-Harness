"""三态熔断器（CLOSED / OPEN / HALF_OPEN）"""

import time
from enum import Enum


class CircuitState(str, Enum):
    CLOSED = "closed"        # 正常
    OPEN = "open"            # 熔断
    HALF_OPEN = "half_open"  # 半开探测


class CircuitBreaker:
    """三态熔断器，每个 Provider 独立实例"""

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout_ms: int = 30_000,
        half_open_max: int = 3,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout_ms = recovery_timeout_ms
        self.half_open_max = half_open_max

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time = 0.0
        self._half_open_requests = 0

    @property
    def state(self) -> CircuitState:
        self._maybe_recover()
        return self._state

    def record_success(self):
        if self._state == CircuitState.HALF_OPEN:
            # 计数器已由 allow_request() 递增，这里判断是否达到阈值
            if self._half_open_requests >= self.half_open_max:
                self._state = CircuitState.CLOSED
                self._failure_count = 0
                self._half_open_requests = 0
        else:
            self._failure_count = 0

    def record_failure(self):
        self._failure_count += 1
        self._last_failure_time = time.monotonic()
        # HALF_OPEN 下单次失败立即回到 OPEN
        if self._state == CircuitState.HALF_OPEN:
            self._state = CircuitState.OPEN
            self._half_open_requests = 0
        elif self._failure_count >= self.failure_threshold:
            self._state = CircuitState.OPEN
            self._half_open_requests = 0

    def allow_request(self) -> bool:
        self._maybe_recover()
        if self._state == CircuitState.CLOSED:
            return True
        if self._state == CircuitState.HALF_OPEN:
            if self._half_open_requests < self.half_open_max:
                self._half_open_requests += 1
                return True
            return False
        return False

    def _maybe_recover(self):
        if self._state == CircuitState.OPEN:
            elapsed = (time.monotonic() - self._last_failure_time) * 1000
            if elapsed >= self.recovery_timeout_ms:
                self._state = CircuitState.HALF_OPEN
                self._half_open_requests = 0
