# Harness — 可靠性保障层

> 覆盖: `src/harness/retry.py`, `src/harness/timeout.py`, `src/harness/circuit.py`, `config/default.yaml` (harness 段)

## 功能

Harness 层为所有 Agent 提供自动化的可靠性保障，P0 实现 3 个核心模块：

- **智能重试** — 指数退避 + Jitter，仅重试可恢复错误
- **超时控制** — 每个 Agent 独立超时上限，防止无限等待
- **三态熔断** — Provider 故障时自动熔断，保护下游并加速恢复

所有模块通过 `BaseAgent.execute()` 自动应用，Agent 实现者无需感知。

## 关键类

### RetryConfig + with_retry (`retry.py`)

```
RetryConfig(max_retries=3, base_delay_ms=1000, max_delay_ms=30000,
            backoff_multiplier=2.0, jitter=True)

async with_retry(fn, config, retryable_errors) → result
```

**退避公式**: `delay = base_delay_ms * (multiplier ^ attempt) / 1000`，上限 max_delay_ms，Jitter 随机化 50%-150%。

### TimeoutError + execute_with_timeout (`timeout.py`)

```
async execute_with_timeout(agent_name, coro, timeout_ms) → result
```

超时抛出 `TimeoutError(agent_name, timeout_ms)`，包含 Agent 名称便于日志追踪。

### CircuitBreaker (`circuit.py`)

三态熔断器，每个 Provider 独立实例：

```
CLOSED (正常) → [failures >= threshold] → OPEN (熔断)
OPEN → [等待 recovery_timeout_ms] → HALF_OPEN (探测)
HALF_OPEN → [连续成功 half_open_max 次] → CLOSED
HALF_OPEN → [任意失败] → OPEN
```

**关键方法:**

```
allow_request() → bool    # 是否允许发起请求
record_success()          # 记录成功
record_failure()          # 记录失败
state → CircuitState      # 当前状态（property，自动调用 _maybe_recover）
```

**配置参数:**

```
failure_threshold: 5       # 失败 N 次后熔断
recovery_timeout_ms: 30000 # 冷却时间
half_open_max_requests: 3  # 半开探测请求数
```

## 集成方式

Harness 模块在 `BaseAgent.execute()` 中自动应用：

```python
# src/agents/base.py
class BaseAgent:
    async def execute(self, task_brief, session) -> dict:
        async def _run():
            return await self._execute_impl(task_brief, session)

        return await with_retry(
            lambda: execute_with_timeout(self.meta_name, _run(), self.timeout_ms),
            config=self.retry_config,
        )
```

Agent 子类只需实现 `_execute_impl()`，自动获得重试+超时能力。熔断器在 Provider 层面独立使用。

## 配置 (`config/default.yaml`)

```yaml
harness:
  retry:
    max_retries: 3
    base_delay_ms: 1000
    max_delay_ms: 30000
    backoff_multiplier: 2.0
    jitter: true

  circuit_breaker:
    failure_threshold: 5
    recovery_timeout_ms: 30000
    half_open_max_requests: 3

  timeout:
    default_ms: 30000
    vision_ms: 30000
    text_ms: 15000
    image_ms: 60000
```

## 修改指南

- **调整重试策略** → 修改 `config/default.yaml` 的 `harness.retry` 段，或创建 Agent 时传入自定义 `RetryConfig`
- **调整熔断敏感度** → 降低 `failure_threshold` 更敏感，提高更容忍
- **新增 Harness 模块** → 在 `src/harness/` 创建文件，在 `BaseAgent.execute()` 中集成
