# Harness 扩展 — 限流 / 成本 / 审计 / 上下文 / 记忆 / 输入输出管道

> 覆盖: `src/harness/rate_limiter.py`, `src/harness/cost_tracker.py`, `src/harness/audit_logger.py`, `src/harness/context_manager.py`, `src/harness/agent_memory.py`, `src/harness/input_pipeline.py`, `src/harness/output_pipeline.py`
> 基础三件套（重试/超时/熔断）见 `harness.md`

## 功能

Harness 层的第二梯队模块，覆盖运行期治理与数据管道。

## 关键类

### RateLimiter (`rate_limiter.py`)

令牌桶限流，支持按 Provider/模型/token/租户多维计数。

```
acquire(provider, model, tokens, tenant_id)   # async，超限则等待
try_acquire(...) → bool                        # 非阻塞尝试
remaining(provider, ...) → int                 # 剩余 token（内部先 refill）
set_tenant_limit(tenant_id, rpm, tpm)          # 租户独立配额
configure(provider, model, rpm, ...)
```

- 桶结构 `_TokenBucket(rpm, tpm)`，双速率（请求数 + token 数）分别计量
- 全局单例经 `src/agents/base.py` 的 `get_rate_limiter()` 访问

### CostTracker (`cost_tracker.py`)

会话成本追踪与预算控制。

```
record(model, tokens_in, tokens_out, agent_name, provider)
record_image(model, count, agent_name)          # 图像成本单独计入 breakdown
total_cost() / remaining_budget() / is_over_budget() / is_warning()
breakdown() → {total, by_model, by_agent, images}
```

- 预算与告警阈值来自 `config/default.yaml` 的 `cost` 段
- `BudgetExceeded` 异常在超预算时由调用方决定策略（当前引擎记录但不中断）

### AuditLogger (`audit_logger.py`)

审计日志，按日期分文件写入 `data/audit/`（JSONL）。

```
log(session_id, event, agent_name, ...)         # async 追加一行
query(session_id, agent_name, date) → [entries]
stats(date) → dict                              # 汇总统计
```

- 同步文件 IO 经 `asyncio.to_thread` 转线程，避免阻塞事件循环
- 工作流事件溯源在 SQLite 双写审计（见 `workflow.md`）

### ContextManager (`context_manager.py`)

上下文窗口管理，防止 token 溢出。

```
check(messages, system_prompt) → ContextReport   # 包含估算 tokens + 建议动作
compact(messages, keep_first, keep_last) → (messages, summary)   # 压缩中间消息
new_window_summary(messages) → str               # 开新窗口摘要
```

- `WindowAction`：`NO_ACTION` / `COMPACT` / `NEW_WINDOW`
- system_prompt 计入阈值（审计修复 C25）；base64 图片载荷在估算时剔除

### AgentMemory (`agent_memory.py`)

跨会话记忆召回（JSONL 存储于 `data/memory/`）。

```
remember(category, content, score, ...)          # 写入成功经验
recall(category, limit) → [entries]              # 按分数召回
recall_similar(category, query, limit)           # 关键词相似召回
stats() / clear(category)
```

### 输入/输出管道

```
ImageValidator.validate(image_data) → ValidationResult   # 格式/大小/base64 校验（20MB 上限）
InputPipeline.process(image_data)                        # 验证 + 内容安全（占位）
SchemaValidator / FieldCompletenessChecker / BusinessRuleValidator
OutputPipeline.validate(agent_name, output) → OutputResult
```

- 输入管道在 `POST /api/sessions` 中被调用（上传图片先验证后预处理）
- 输出管道在 `chat/engine.py` 审查产出物时使用

## 修改指南

- **调整限流额度** → `RateLimiter(default_rpm, default_tpm)` 或 `set_tenant_limit`
- **调整成本定价** → `cost_tracker.py` 的 `_PRICES` 表（与各 Provider `_estimate_cost` 保持口径一致）
- **接入真实内容安全 API** → `input_pipeline.py` 的 `ContentSafetyChecker.check`
- **新增输出校验规则** → `output_pipeline.py` 的 `BusinessRuleValidator`
