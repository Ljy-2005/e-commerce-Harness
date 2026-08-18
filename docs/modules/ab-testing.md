# A/B 测试框架

> 覆盖: `src/harness/ab_testing.py`
> 测试: `tests/test_harness/test_ab_testing.py`（19 个用例）

## 功能

同角色多版本并行对比：多个变体（模型/提示词/温度）并行执行，多评审员评分后给出排名与胜者。

## 关键类

### 配置

```
ABVariant(variant_id, label, model_override="", prompt_override="", temperature=None)
ABTestConfig(agent_name, variants, task_brief, review_count=3, scoring_method="multi_reviewer")
```

### 结果

```
VariantResult.avg_score / verdicts / majority_verdict / success
ABTestResult.winner / ranking（按分数降序）/ all_passed / total_cost_usd
```

### 工厂函数

```
make_model_variants(agent_name, [(id, model), ...])     # 模型对比（默认三模型）
make_prompt_variants(agent_name, [(id, label, prompt), ...])
make_temperature_variants(agent_name, [(id, temp), ...])
```

### ABTestRunner

```
run(config) → ABTestResult
```

- 变体并行执行（`asyncio.gather`）；每个变体用**独立 session 副本**（`copy.deepcopy`），消除共享 state 竞态（审计修复项）
- 评审阶段：审查员对每个变体评分（`review_count` 次），`avg_score > 0` 才参与送审 → 排名
- 未注册 Agent 分支直接报错（此前为 `UnboundLocalError`，已修复）

## 已知边界（progress.md §5.3 开放决策）

`model_override` 以**提示词注入**方式传递（避免共享 state 竞态），真实 Provider 下不会真正切换模型；真实模型对比需在 `BaseAgent.execute` 增加 model 参数（接口级改动），暂缓。

## 修改指南

- **新增对比维度** → 添加工厂函数（仿 `make_prompt_variants`）
- **调整评审策略** → `_review_variant`（当前 multi_reviewer 取均分）
