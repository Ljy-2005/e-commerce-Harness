"""A/B 测试框架 — 同角色多版本并行对比，自动选优

核心概念:
- ABVariant: 一个变体 = 对同一个 Agent 的不同配置 (model, prompt params, temperature)
- ABTestConfig: 测试场景定义 = 变体列表 + 评估方式 + 评审人数
- VariantResult: 单个变体的执行结果
- ABTestRunner: 并行执行 → 多审查员评分 → 自动选优

使用:
    config = ABTestConfig(
        agent_name="提示词生成员",
        variants=[
            ABVariant("v1_deepseek", model_override="deepseek-chat"),
            ABVariant("v2_gpt4o", model_override="gpt-4o"),
        ],
        review_count=3,
    )
    runner = ABTestRunner(registry, session)
    result = await runner.run(config)
    print(f"Winner: {result.winner.variant_id}")
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Optional
from collections import Counter


# ── 数据模型 ──

@dataclass
class ABVariant:
    """A/B 测试的一个变体"""
    variant_id: str                                     # 唯一标识
    label: str = ""                                     # 人类可读标签
    model_override: str = ""                            # 覆盖模型
    prompt_override: str = ""                           # 覆盖 system prompt
    params_override: dict = field(default_factory=dict) # 覆盖 Agent 参数
    temperature: float = 0.7


@dataclass
class VariantResult:
    """单个变体的执行结果"""
    variant_id: str
    label: str = ""
    output: dict = field(default_factory=dict)
    elapsed_ms: float = 0.0
    cost_usd: float = 0.0
    tokens_used: int = 0
    error: str = ""
    scores: list[dict] = field(default_factory=list)   # 审查评分列表

    @property
    def avg_score(self) -> float:
        scores = [s.get("overall_score", 0) for s in self.scores if s]
        return round(sum(scores) / len(scores), 1) if scores else 0.0

    @property
    def verdicts(self) -> list[str]:
        return [s.get("verdict", "fail") for s in self.scores if s]

    @property
    def majority_verdict(self) -> str:
        if not self.verdicts:
            return "fail"
        return Counter(self.verdicts).most_common(1)[0][0]

    @property
    def success(self) -> bool:
        return not self.error and self.avg_score > 0


@dataclass
class ABTestConfig:
    """A/B 测试配置"""
    agent_name: str                                     # 测试哪个 Agent
    variants: list[ABVariant]                           # 变体列表（2-5 个）
    task_brief: str = ""                                # 给 Agent 的任务描述
    scoring_method: str = "multi_reviewer"              # single | multi_reviewer | vote | debate
    review_count: int = 3                               # 评审人数
    review_agent: str = "审查员"                        # 使用哪个 Agent 审查
    min_score_threshold: float = 70.0                   # 最低通过分数
    parallel: bool = True                               # 是否并行执行变体


@dataclass
class ABTestResult:
    """A/B 测试完整结果"""
    config: ABTestConfig
    variants: list[VariantResult] = field(default_factory=list)
    winner: Optional[VariantResult] = None
    runner_up: Optional[VariantResult] = None
    total_elapsed_ms: float = 0.0
    total_cost_usd: float = 0.0

    @property
    def all_passed(self) -> bool:
        return all(v.avg_score >= self.config.min_score_threshold for v in self.variants)

    @property
    def ranking(self) -> list[VariantResult]:
        return sorted(self.variants, key=lambda v: v.avg_score, reverse=True)


# ── 执行器 ──

class ABTestRunner:
    """A/B 测试执行器

    1. 并行（或串行）运行所有变体
    2. 每个变体的输出送审（多审查员评分）
    3. 按平均分排名，选出最优变体
    """

    def __init__(self, registry, session):
        self.registry = registry
        self.session = session

    async def run(self, config: ABTestConfig) -> ABTestResult:
        """执行完整的 A/B 测试"""
        start_time = time.monotonic()
        agent = self.registry.get(config.agent_name)
        if agent is None:
            return ABTestResult(
                config=config,
                variants=[VariantResult(v.variant_id, error=f"Agent '{config.agent_name}' 未注册")]
            )

        # Stage 1: 并行执行所有变体
        if config.parallel:
            variant_results = await self._run_parallel(agent, config)
        else:
            variant_results = []
            for v in config.variants:
                variant_results.append(await self._run_variant(agent, v, config))

        # Stage 2: 审查每个变体的结果
        reviewer = self.registry.get(config.review_agent)
        if reviewer and any(vr.success for vr in variant_results):
            for vr in variant_results:
                if vr.success:
                    vr.scores = await self._review_variant(reviewer, vr, config)

        # Stage 3: 排名和选优
        result = ABTestResult(
            config=config,
            variants=variant_results,
            total_elapsed_ms=(time.monotonic() - start_time) * 1000,
            total_cost_usd=sum(vr.cost_usd for vr in variant_results),
        )

        # 按平均分排名
        ranked = result.ranking
        if ranked and ranked[0].avg_score >= config.min_score_threshold:
            result.winner = ranked[0]
            if len(ranked) >= 2 and ranked[1].avg_score >= config.min_score_threshold:
                result.runner_up = ranked[1]

        return result

    async def _run_parallel(self, agent, config: ABTestConfig) -> list[VariantResult]:
        """并行执行所有变体"""
        tasks = [self._run_variant(agent, v, config) for v in config.variants]
        return list(await asyncio.gather(*tasks))

    async def _run_variant(self, agent, variant: ABVariant, config: ABTestConfig) -> VariantResult:
        """执行单个变体"""
        start = time.monotonic()

        # 保存并覆盖 Agent 配置
        original_model = getattr(agent.provider, "_current_model", "") if agent.provider else ""

        try:
            # 模型覆盖
            if variant.model_override and agent.provider:
                agent.provider._current_model = variant.model_override

            # 执行
            task_brief = config.task_brief or variant.label
            if variant.prompt_override:
                task_brief = f"{task_brief}\n\n[变体指令] {variant.prompt_override}"

            output = await agent.execute(task_brief, self.session)

            elapsed = (time.monotonic() - start) * 1000
            return VariantResult(
                variant_id=variant.variant_id,
                label=variant.label,
                output=output,
                elapsed_ms=elapsed,
                cost_usd=output.get("cost_usd", 0.0),
                tokens_used=output.get("tokens_used", 0),
                error=output.get("error", ""),
            )
        except Exception as e:
            elapsed = (time.monotonic() - start) * 1000
            return VariantResult(
                variant_id=variant.variant_id,
                label=variant.label,
                elapsed_ms=elapsed,
                error=str(e),
            )
        finally:
            # 恢复原始配置
            if variant.model_override and agent.provider and original_model:
                agent.provider._current_model = original_model

    async def _review_variant(self, reviewer, vr: VariantResult, config: ABTestConfig) -> list[dict]:
        """使用审查员（单人或多人）评审变体结果"""
        scores = []

        for i in range(config.review_count):
            try:
                if config.scoring_method == "vote" and i > 0:
                    review_task = f"[审查员{i+1}] 独立审查并评分 — 变体 {vr.variant_id}"
                elif config.scoring_method == "debate" and i == 1:
                    review_task = f"[反方审查] 指出缺点 — 变体 {vr.variant_id}"
                else:
                    review_task = f"审查变体 {vr.variant_id}（{vr.label}）的输出质量"

                result = await reviewer.execute(review_task, self.session)
                if not result.get("error"):
                    scores.append(result)
            except Exception:
                pass

        return scores


# ── 快捷工厂 ──

def make_model_variants(agent_name: str, models: list[tuple[str, str]]) -> ABTestConfig:
    """快捷创建「同 Agent 不同模型」的对比测试

    Args:
        agent_name: Agent 名称
        models: [(variant_id, model_name), ...]
            例: [("v_gpt4o", "gpt-4o"), ("v_deepseek", "deepseek-chat"), ("v_qwen", "qwen-max")]
    """
    return ABTestConfig(
        agent_name=agent_name,
        variants=[
            ABVariant(variant_id=vid, label=f"{agent_name}@{model}", model_override=model)
            for vid, model in models
        ],
        review_count=3,
    )


def make_prompt_variants(agent_name: str, prompts: list[tuple[str, str, str]]) -> ABTestConfig:
    """快捷创建「同 Agent 不同 Prompt」的对比测试

    Args:
        agent_name: Agent 名称
        prompts: [(variant_id, label, prompt_override), ...]
            例: [("v_style_a", "简洁风", "请使用极简风格"), ("v_style_b", "场景风", "请展示使用场景")]
    """
    return ABTestConfig(
        agent_name=agent_name,
        variants=[
            ABVariant(variant_id=vid, label=label, prompt_override=prompt)
            for vid, label, prompt in prompts
        ],
        review_count=3,
    )


def make_temperature_variants(agent_name: str, temperatures: list[tuple[str, float]]) -> ABTestConfig:
    """快捷创建「同 Agent 不同温度」的对比测试

    Args:
        agent_name: Agent 名称
        temperatures: [(variant_id, temperature), ...]
    """
    return ABTestConfig(
        agent_name=agent_name,
        variants=[
            ABVariant(variant_id=vid, label=f"T={t}", temperature=t)
            for vid, t in temperatures
        ],
        review_count=3,
    )
