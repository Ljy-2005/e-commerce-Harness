"""BaseAgent — 所有 Agent 的抽象基类，自动重试+超时+熔断+限流+成本"""

import asyncio
import time
from abc import ABC, abstractmethod
from contextvars import ContextVar

from src.harness.retry import with_retry, RetryConfig
from src.harness.timeout import execute_with_timeout
from src.core.models import Message
from src.core.logging_config import get_logger

_base_logger = get_logger(__name__)

# 单次调用级模型覆盖（A/B 测试变体用）：ContextVar 协程上下文隔离，
# 并发变体并行执行时互不干扰（审计修复：此前 model_override 只拼进提示词文本）
_model_override_ctx: ContextVar[str | None] = ContextVar("model_override", default=None)


# ── 全局 Harness 组件 ──

# 全局熔断器注册表（per provider_name）
_circuit_breakers: dict[str, "CircuitBreaker"] = {}

# 全局速率限制器
_rate_limiter: "RateLimiter | None" = None


def _get_circuit(provider_name: str) -> "CircuitBreaker":
    """获取或创建 Provider 对应的熔断器"""
    from src.harness.circuit import CircuitBreaker
    if provider_name not in _circuit_breakers:
        _circuit_breakers[provider_name] = CircuitBreaker(name=provider_name)
    return _circuit_breakers[provider_name]


def _get_limiter() -> "RateLimiter":
    """获取全局速率限制器（延迟初始化）"""
    global _rate_limiter
    if _rate_limiter is None:
        from src.harness.rate_limiter import RateLimiter
        _rate_limiter = RateLimiter(default_rpm=60)
    return _rate_limiter


def get_circuit_breaker(provider_name: str) -> "CircuitBreaker":
    """外部查询：获取某 Provider 的熔断器状态（供 /health 使用）"""
    return _get_circuit(provider_name)


def get_rate_limiter() -> "RateLimiter":
    """外部查询：获取速率限制器（供 /health 使用）"""
    return _get_limiter()


class BaseAgent(ABC):
    """Agent 基类

    子类只需实现 _execute_impl(task_brief, session)，
    自动获得：熔断检查 → 速率限制 → 重试 + 超时 → 成本追踪
    """

    # 子类覆盖
    meta_name: str = "base"
    timeout_ms: int = 30_000

    def __init__(self, provider=None, retry_config: RetryConfig | None = None):
        self.provider = provider
        self.retry_config = retry_config or RetryConfig()
        # 由注册表从 config/models.yaml 解析注入的模型名（能力声明 → 模型映射）
        self.model_name = ""

    async def execute(self, task_brief: str, session, model_override: str | None = None) -> dict:
        """统一执行入口 — 完整的 Harness 保护链

        model_override: 单次调用级模型覆盖（A/B 变体），经 ContextVar 传给
        _model_kwargs()，仅本次调用生效，并发调用互不干扰。
        """
        token = _model_override_ctx.set(model_override) if model_override else None
        try:
            return await self._execute_with_harness(task_brief, session)
        finally:
            if token is not None:
                _model_override_ctx.reset(token)

    async def _execute_with_harness(self, task_brief: str, session) -> dict:
        start_time = time.monotonic()
        provider_name = getattr(self.provider, "name", "unknown")

        # 1. 熔断检查
        circuit = _get_circuit(provider_name)
        if not circuit.allow_request():
            return {
                "error": f"Provider '{provider_name}' 已熔断 (state={circuit.state.value})",
                "elapsed_ms": 0,
            }

        # 2. 速率限制（租户感知）
        limiter = _get_limiter()
        tenant_id = session.get("tenant_id", "") if isinstance(session, dict) else ""
        await limiter.acquire(provider_name, tenant_id=tenant_id)

        # 3. 执行（含重试 + 超时）
        async def _run():
            return await self._execute_impl(task_brief, session)

        try:
            result = await with_retry(
                lambda: execute_with_timeout(self.meta_name, _run(), self.timeout_ms),
                config=self.retry_config,
            )
            # 审计修复：Provider 以 {"error": ...} 返回非 200 时不计成功，
            # 持续 5xx 应触发熔断（此前无条件 record_success）
            if isinstance(result, dict) and result.get("error"):
                circuit.record_failure()
            else:
                circuit.record_success()

            # 4. 成本追踪
            self._track_cost(session, result, provider_name)

            return result
        except asyncio.CancelledError:
            raise  # 不记录为 Provider 故障
        except Exception as e:
            circuit.record_failure()
            elapsed_ms = (time.monotonic() - start_time) * 1000
            return {
                "error": str(e),
                "elapsed_ms": elapsed_ms,
            }

    def _track_cost(self, session, result: dict, provider_name: str):
        """记录本次调用的成本到 session 的 CostTracker"""
        if result.get("error"):
            return  # 失败不记录成本

        try:
            from src.harness.cost_tracker import CostTracker

            # 从 session 获取或创建 tracker
            tracker = session.get("_cost_tracker")
            if tracker is None:
                budget = session.get("cost_budget_usd", 10.0)
                tracker = CostTracker(budget_usd=budget)
                session["_cost_tracker"] = tracker

            # 记录成本
            tokens = result.get("tokens_used", 0) or 0
            cost = result.get("cost_usd", 0.0)
            model = result.get("model_used", "") or getattr(self.provider, "name", "")

            # 图像生成走 record_image（保证 breakdown 正确）
            is_image = result.get("image_url") or "images" in str(result.keys())
            if cost > 0 and tokens == 0:
                tracker.record_image(model=model, count=1, agent_name=self.meta_name)
            elif tokens > 0:
                tracker.record(
                    model=model,
                    tokens_in=(tokens + 1) // 2,
                    tokens_out=tokens // 2,
                    agent_name=self.meta_name,
                    provider=provider_name,
                )
            elif cost > 0:
                # 有 cost 但无 token（如某些 image provider 的返回格式），直接追踪
                tracker._total_cost += cost
                if model:
                    tracker._by_model[model] = tracker._by_model.get(model, 0) + cost
                if self.meta_name:
                    tracker._by_agent[self.meta_name] = tracker._by_agent.get(self.meta_name, 0) + cost

            # 同步到 session
            session["cost_so_far"] = tracker.total_cost

            # 预算检查
            if tracker.is_warning() and not tracker.is_over_budget():
                session.setdefault("_warnings", []).append(
                    f"[Cost] {self.meta_name}: 预算已用 {tracker.total_cost:.2f}/{tracker.budget_usd:.2f}"
                )
        except Exception as e:
            _base_logger.warning("成本追踪失败 (agent=%s): %s", self.meta_name, e, exc_info=True)

    @abstractmethod
    async def _execute_impl(self, task_brief: str, session) -> dict:
        """子类实现具体逻辑，返回 dict"""
        ...

    def _get_model(self) -> str:
        """Helper: 获取当前解析到的模型名（config/models.yaml 注入）"""
        if self.model_name:
            return self.model_name
        if self.provider and hasattr(self.provider, "_current_model"):
            return self.provider._current_model
        return ""

    def _model_kwargs(self) -> dict:
        """把模型名传给 Provider（单次调用级覆盖优先；为空时用 Provider 默认模型）"""
        override = _model_override_ctx.get()
        model = override or self._get_model()
        return {"model": model} if model else {}
