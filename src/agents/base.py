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


def _retryable_errors() -> tuple:
    """可重试的异常类型：只重试**传输/网络类抖动**

    - 包含：httpx 的错误族（连接失败、上游 5xx 重试、Provider 侧超时）、OSError；
    - 不包含 `src.harness.timeout.TimeoutError`（Agent 预算超时）：重试它只是把同一个
      上限重复 4 次——实测提示词生成员 15s 上限 × 4 次 = 白等 66 秒，而错误文案还写着
      "超时 (15000ms)"。预算不够应该改配置，不是重试；
    - 不包含编程错误（TypeError/KeyError/ValueError…）：重试掩盖缺陷且无意义烧钱。
    """
    errors: tuple = (OSError,)
    try:
        import httpx
        errors = (httpx.HTTPError, OSError)
    except Exception:  # noqa: BLE001 — httpx 缺失时退化为 OSError
        pass
    return errors


class BaseAgent(ABC):
    """Agent 基类

    子类只需实现 _execute_impl(task_brief, session)，
    自动获得：熔断检查 → 速率限制 → 重试 + 超时 → 成本追踪
    """

    # 子类覆盖
    meta_name: str = "base"
    timeout_ms: int = 30_000

    def timeout_budget(self, session) -> int:
        """本次执行的超时预算（毫秒）；默认取类/YAML 配置的 `timeout_ms`

        生图员覆写它：**张数决定预算** —— 实测单张 2048×2048 约 50s，
        6 张串行 303s；若按固定 420s，补上详情图（10 张 ≈ 505s）必然整轮超时，
        而 `artifacts["images"]` 是整键替换 → 已出的图会一起丢。
        """
        return int(self.timeout_ms)

    def __init__(self, provider=None, retry_config: RetryConfig | None = None):
        self.provider = provider
        self.retry_config = retry_config or RetryConfig()
        # 由注册表从 config/models.yaml 解析注入的模型名（能力声明 → 模型映射）
        self.model_name = ""

    async def execute(self, task_brief: str, session, model_override: str | None = None,
                      stats: dict | None = None) -> dict:
        """统一执行入口 — 完整的 Harness 保护链

        model_override: 单次调用级模型覆盖（A/B 变体），经 ContextVar 传给
        _model_kwargs()，仅本次调用生效，并发调用互不干扰。
        stats: 可选的出参字典。Harness 把本次调用的真实用量/耗时写进去
        （`elapsed_ms/tokens_used/tokens_in/tokens_out/cost_usd/model_used/error`）。
        引擎据此写审计日志——此前审计从 Agent 返回的 content 里取用量（那里根本没有），
        导致 tokens/cost/duration 恒为 0（A38）。
        """
        token = _model_override_ctx.set(model_override) if model_override else None
        try:
            return await self._execute_with_harness(task_brief, session, stats=stats)
        finally:
            if token is not None:
                _model_override_ctx.reset(token)

    async def _execute_with_harness(self, task_brief: str, session,
                                    stats: dict | None = None) -> dict:
        start_time = time.perf_counter()
        provider_name = getattr(self.provider, "name", "unknown")

        def _finish(payload: dict) -> dict:
            self._record_stats(stats, payload, start_time)
            return payload

        # 1. 熔断检查
        circuit = _get_circuit(provider_name)
        if not circuit.allow_request():
            return _finish({
                "error": f"Provider '{provider_name}' 已熔断 (state={circuit.state.value})",
                "elapsed_ms": 0,
            })

        # 2. 速率限制（租户感知）
        limiter = _get_limiter()
        tenant_id = session.get("tenant_id", "") if isinstance(session, dict) else ""
        await limiter.acquire(provider_name, tenant_id=tenant_id)

        # 3. 执行（含重试 + 超时）
        async def _run():
            return await self._execute_impl(task_brief, session)

        try:
            result = await with_retry(
                lambda: execute_with_timeout(self.meta_name, _run(), self.timeout_budget(session)),
                config=self.retry_config,
                retryable_errors=_retryable_errors(),
            )
            # 审计修复：Provider 以 {"error": ...} 返回非 200 时不计成功，
            # 持续 5xx 应触发熔断（此前无条件 record_success）
            if isinstance(result, dict) and result.get("error"):
                circuit.record_failure()
            else:
                circuit.record_success()

            # 4. 成本追踪
            self._track_cost(session, result, provider_name)

            return _finish(result)
        except asyncio.CancelledError:
            raise  # 不记录为 Provider 故障
        except Exception as e:
            circuit.record_failure()
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            return _finish({
                "error": str(e),
                "elapsed_ms": elapsed_ms,
            })

    def _record_stats(self, stats: dict | None, result: dict, start_time: float) -> None:
        """把本次调用的真实用量/耗时写进出参 stats（A38，供审计与批量报表使用）

        `cost_usd=None` 表示**价格未标定**（不是 0、不是缺键）：用量照报，金额不猜。
        此前写成 `result.get("cost_usd") or usage.get(...) or 0.0` —— None 被静默变成
        0.0，等于把"未知"又变回"编出来的钱"，且界面显示 $0.0000。
        """
        if stats is None:
            return
        usage = getattr(self, "_last_usage", None) or {}
        result = result if isinstance(result, dict) else {}
        cost = self._resolve_cost(result, usage)
        stats.update({
            "elapsed_ms": round((time.perf_counter() - start_time) * 1000, 2),
            "tokens_used": result.get("tokens_used") or usage.get("tokens_used", 0) or 0,
            "tokens_in": result.get("tokens_in") or usage.get("tokens_in", 0) or 0,
            "tokens_out": result.get("tokens_out") or usage.get("tokens_out", 0) or 0,
            "cost_usd": cost,
            "cost_unknown": cost is None,
            "images": result.get("images") if isinstance(result.get("images"), list) else None,
            "model_used": (result.get("model_used") or usage.get("model_used")
                           or self._get_model() or getattr(self.provider, "name", "")),
            "error": str(result.get("error") or ""),
        })

    @staticmethod
    def _resolve_cost(result: dict, usage: dict) -> float | None:
        """取本次调用的金额：**显式区分 None（未标定）与 0.0（真的是 0）**

        规则：两边都有值时以 result 为准；只有一边有值就用那一边；两边都没有
        （或都是 None）→ None。**绝不 `or 0.0`**。
        """
        values = [result.get("cost_usd"), usage.get("cost_usd")]
        present = [value for value in values if value is not None]
        if not present:
            return None
        if result.get("cost_usd") is not None:
            return result.get("cost_usd")
        return usage.get("cost_usd")

    def _track_cost(self, session, result: dict, provider_name: str):
        """记录本次调用的成本到 session 的 CostTracker"""
        if result.get("error"):
            return  # 失败不记录成本

        try:
            from src.harness.cost_tracker import CostTracker

            # 从 session 获取或重建 tracker（A39：checkpoint 恢复后 `_cost_tracker`
            # 可能是个字符串，直接用会 AttributeError 被吞掉 → 该会话不再记账）
            tracker = CostTracker.from_session(session)

            # 记录成本（用量兜底：Agent 只把 content 交给上层，用量经 _last_usage 暂存；
            # 模型名兜底到本次实际下发的模型，而非 Provider 路由名——后者的定价查不到会
            # 回落默认价，导致金额偏差）
            usage = getattr(self, "_last_usage", None) or {}
            tokens = result.get("tokens_used") or usage.get("tokens_used", 0) or 0
            cost = self._resolve_cost(result, usage)   # 可能是 None（价格未标定）
            model = (result.get("model_used") or usage.get("model_used")
                     or self._get_model() or getattr(self.provider, "name", ""))
            route = getattr(self.provider, "route", "") or provider_name

            # 图像生成走 record_image（保证 breakdown 正确）
            is_image = bool(result.get("image_url") or usage.get("image_url")
                            or isinstance(result.get("images"), list))
            if is_image:
                # 张数与金额都如实计入（A44：此前固定 count=1，3 张图只算 1 张的钱）；
                # 金额未知时把张数记下并计入 unknown_calls，**不按常量估一个价**
                images = result.get("images")
                count = len(images) if isinstance(images, list) and images else 1
                # 部分标定的情况（多张图里有的有价、有的没有）：分两条记，
                # 否则"另有没有价格的那几张"会被并进金额里、看起来全都有价
                unknown_count = 0
                if cost is not None:
                    try:
                        unknown_count = int(result.get("cost_unknown_images") or 0)
                    except (TypeError, ValueError):
                        unknown_count = 0
                    unknown_count = max(0, min(unknown_count, count))
                if unknown_count == 0:
                    # 全部都有价（或全部都没价）：一条记录，金额照传
                    tracker.record_image(
                        model=model, count=count, agent_name=self.meta_name,
                        amount=cost, provider=route,
                        usage={"images": count,
                               "size": getattr(self.provider, "default_size", "")},
                        cost_unknown=cost is None)
                else:
                    # 部分标定：已标定的那几张按金额记，未标定的那几张按未知记
                    priced_count = count - unknown_count
                    if priced_count > 0:
                        tracker.record_image(
                            model=model, count=priced_count, agent_name=self.meta_name,
                            amount=cost, provider=route,
                            usage={"images": priced_count,
                                   "size": getattr(self.provider, "default_size", "")})
                    tracker.record_image(
                        model=model, count=unknown_count, agent_name=self.meta_name,
                        amount=None, provider=route,
                        usage={"images": unknown_count,
                               "size": getattr(self.provider, "default_size", "")},
                        cost_unknown=True)
            elif tokens > 0:
                tracker.record(
                    model=model,
                    tokens_in=result.get("tokens_in") or usage.get("tokens_in") or (tokens + 1) // 2,
                    tokens_out=result.get("tokens_out") or usage.get("tokens_out") or tokens // 2,
                    agent_name=self.meta_name,
                    provider=provider_name,
                    usage={"tokens_used": tokens},
                    cost_unknown=cost is None,
                )
            elif cost is not None and cost > 0:
                # 有金额但无 token/图像（如某些 image provider 的返回格式），直接追踪
                tracker._total_cost += cost
                if model:
                    tracker._by_model[model] = tracker._by_model.get(model, 0) + cost
                if self.meta_name:
                    tracker._by_agent[self.meta_name] = tracker._by_agent.get(self.meta_name, 0) + cost
            elif cost is None:
                # 既没有 token/图像、金额也未知：仍要留一条"未标定"记录，
                # 否则界面上这次调用会凭空消失（用量是事实，永远要显示）
                tracker.record(model=model, agent_name=self.meta_name, provider=provider_name,
                               cost_unknown=True)

            # 同步到 session
            session["cost_so_far"] = tracker.total_cost
            session["cost_unknown_calls"] = tracker.unknown_calls

            # 预算检查
            if tracker.is_warning() and not tracker.is_over_budget():
                session.setdefault("_warnings", []).append(
                    f"[Cost] {self.meta_name}: 预算已用 {tracker.total_cost:.2f}/{tracker.budget_usd:.2f}"
                    + (f"（另有 {tracker.unknown_calls} 次未标定）" if tracker.unknown_calls else "")
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

    def _content_or_error(self, result: dict, fallback: dict) -> dict:
        """从 Provider 返回值里取业务内容，**故障优先上报**，并留存用量用于记账。

        真实 Provider 失败时返回 `{"error": "..."}`（无 content 键）。此前各 Agent 直接
        `result.get("content", self._mock_xxx())` —— 默认值生效 → 把失败静默替换成 Mock
        模板数据且不带 error，会话照常"成功"结束（用户配错 Key/端点/模型时完全无感知）。
        现在：有 error 原样返回（引擎据此把消息标记为 error、工作流引擎直接 fail），
        只有 Provider 正常但未给 content 时才回落 fallback。

        同时把 Provider 返回的用量（tokens/cost/model）暂存到实例上：Agent 只把
        `content` 交给上层，若不暂存，`_track_cost` 读到的 tokens 恒为 0 → 成本链路整体失效
        （会话成本/租户预算/审计 token/批量报表金额全为 0）。
        """
        if isinstance(result, dict):
            self._last_usage = {
                "tokens_used": result.get("tokens_used", 0) or 0,
                "tokens_in": result.get("tokens_in", 0) or 0,
                "tokens_out": result.get("tokens_out", 0) or 0,
                # 显式保留 None（价格未标定）：写成 `or 0.0` 就把"未知"变成"编出来的 0"
                "cost_usd": result.get("cost_usd"),
                "model_used": result.get("model_used", "") or "",
                "image_url": result.get("image_url", "") or "",
            }
        if isinstance(result, dict) and result.get("error"):
            return result
        content = result.get("content") if isinstance(result, dict) else None
        return content if content is not None else fallback

    def _model_kwargs(self) -> dict:
        """把模型名传给 Provider（单次调用级覆盖优先；为空时用 Provider 默认模型）"""
        override = _model_override_ctx.get()
        model = override or self._get_model()
        return {"model": model} if model else {}
