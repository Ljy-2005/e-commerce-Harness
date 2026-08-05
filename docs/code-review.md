# 代码审查报告

> 审查日期：2026-07-18（第二次审查）  
> 审查范围：全部 32 个源文件，2 个审查 agent 并行扫描  
> 统计：上次 45 个 → 已修复部分 → 本次新增 23 + 17 = 40 个新发现 → 合计 ~50 个待处理问题

---

## 一、上次审查（07-14）未修复的关键问题（10 个）

以下问题在上次审查后仍未被修复，需优先处理：

### C1. `datetime.utcnow()` 已废弃（部分修复）

- **文件：** `src/core/models.py:149`, `src/chat/session.py:26,53`, `src/providers/seedream.py:116-117`
- **状态：** `engine.py` 已修复。其余文件仍使用废弃 API
- **修复：** 全局替换为 `datetime.now(tz=datetime.timezone.utc)`

### C4. `CoordinatorAgent.decide()` 永远返回 Mock 结果

- **文件：** `src/agents/coordinator.py:45-47`
- **问题：** `decide()` 硬编码 `return self._mock_decision()`，即使配置了真实 Provider 也走 Mock

### C9. Provider 为 None 时 Coordinator 被静默丢弃

- **文件：** `src/agents/registry.py:90-91`
- **问题：** `if provider else None` — Coordinator 可在无 Provider 时运行，不应丢弃

### C10. `_mock_by_category()` 忽略品类参数

- **文件：** `src/agents/category.py:32-39`
- **问题：** 永远返回保健品维度数据

### C2. Provider 返回 dict key 不一致

- **文件：** `base.py`（doc） vs 全部 provider（实现）
- **问题：** doc 写 `"tokens"`，实现返回 `"tokens_used"`

### C6. CostTracker 定价表混用

- **文件：** `src/harness/cost_tracker.py:9-14`
- **问题：** 同一 tuple 承载 per-token 和 per-image 两种语义

### C7. 图像成本不出现在 breakdown

- **文件：** `src/agents/base.py:131-140`
- **问题：** `_total_cost += cost` 绕过 `_by_model` 和 `_by_agent`

### C8. TPM 整条链路未实现

- **文件：** `src/harness/rate_limiter.py:62-82`

### C11. Agent 执行无异常保护

- **文件：** `src/chat/engine.py:97`

### M17. `checkpoint.py` 完全未被调用（死代码）

- **文件：** `src/storage/checkpoint.py`

---

## 二、第二次审查（07-18）新发现

### Critical

#### C14. `is_mock_mode()` 只检查 2 个 API Key，其余 Provider Key 全部被忽略

- **文件：** `src/core/config.py:64`
- **问题：** 只检查 `OPENAI_API_KEY` 和 `DEEPSEEK_API_KEY`。用户若只配了 Anthropic/Seedream/Qwen/FLUX 的 Key，会被错误判定为 Mock Mode
- **修复：** `key_vars` 扩展到全部 8 个 provider 的 Key 变量

#### C15. Flux Fal.ai 后端无视 size 参数，永远生成方图

- **文件：** `src/providers/flux.py:78`
- **问题：** `size if size in ("square_hd", ...) else "square_hd"` — 调用方传的 `"1024x1024"` 从不匹配 Fal.ai 预设名，永远回退 `"square_hd"`
- **修复：** 映射像素尺寸到 Fal.ai 预设

#### C16. CostTracker 定价与各 Provider 自报价格不一致

- **文件：** `src/harness/cost_tracker.py:9-31` vs `openai.py:98`, `qwen.py:112`, `deepseek.py:64`
- **问题：**
  - GPT-4o：Provider 报 $2.50/1M flat，Tracker 存 $(2.50, 10.00)/1M split
  - Qwen-Max：Provider 报 ~$5.60/1M（¥0.04/1K × 0.14 × 1000），Tracker 存 $(0.56, 0.56)/1M — 相差 10 倍
  - DeepSeek：Provider 报 $0.14/1M flat，Tracker 存 $(0.14, 0.28)/1M split
- **修复：** 集中定价到一处，去重，对齐真实 API 价格

#### C17. Seedream 双重 `datetime.utcnow()` 可能跨 UTC 日期边界导致签名失败

- **文件：** `src/providers/seedream.py:116-117`
- **问题：** `timestamp` 和 `datestamp` 分两次取，若跨越 UTC 午夜，签名 credential scope 含过期日期 → 403
- **修复：** `now = datetime.now(tz=datetime.timezone.utc)` 一次取值

#### C18. Anthropic `_convert_messages` 对非 str system content 产生垃圾输出

- **文件：** `src/providers/anthropic.py:85`
- **问题：** `str(content)` 对 list 型 content 输出 `"[{'type': 'text', ...}]"` 字面量
- **修复：** 提取 text parts: `" ".join(p["text"] for p in content if p.get("type") == "text")`

#### C19. DeepSeek `chat_with_vision` 返回 dict 缺少标准 key

- **文件：** `src/providers/deepseek.py:60-62`
- **问题：** 只返回 `{"error": "..."}`，缺少 `"content"`, `"tokens_used"`, `"cost_usd"`。调用方 `result["content"]` 会 KeyError
- **修复：** 返回 `{"content": {"error": "..."}, "tokens_used": 0, "cost_usd": 0.0}`

#### C20. Qwen `_convert_for_vision` 对缺 key 的 message 直接 KeyError

- **文件：** `src/providers/qwen.py:103,109`
- **问题：** `m["role"]` / `m["content"]` 直接索引，malformed 消息会崩溃
- **修复：** 改用 `m.get("role", "user")` / `m.get("content", "")`

#### C21. Circuit breaker HALF_OPEN 下单次失败不立即重新熔断

- **文件：** `src/harness/circuit.py:48-53`
- **问题：** HALF_OPEN 是探测状态，单次失败应立即回到 OPEN。当前仍按 `failure_threshold` 累积判断
- **修复：** 在 `record_failure()` 中检测 `self._state == HALF_OPEN` → 立即 `OPEN`

#### C22. Circuit breaker 并发 HALF_OPEN 探测数可能超限

- **文件：** `src/harness/circuit.py:39-44, 55-61`
- **问题：** `allow_request()` 检查 `_half_open_requests < half_open_max`，但计数器在 `record_success()` 才加。N 个并发请求同时在 gate 外 → 全通过
- **修复：** 在 `allow_request()` 内部递增计数器

### Medium

#### M18. Flux Replicate 后端忽略 `model` 参数

- **文件：** `src/providers/flux.py:104`
- **问题：** Replicate 请求 body 硬编码 `"black-forest-labs/flux-dev"`，改 model 参数无效

#### M19. Flux 轮询循环内 `import asyncio`

- **文件：** `src/providers/flux.py:148`
- **修复：** 移到文件顶部

#### M20. RateLimiter.remaining() 不触发 refill，返回过期 token 数

- **文件：** `src/harness/rate_limiter.py:50`
- **问题：** 直接读 `bucket._tokens` 不调 `_refill()`，报告比实际少
- **修复：** 先调 `bucket._refill()`

#### M21. OpenAI `chat_with_vision` 硬编码 `max_tokens: 2000`

- **文件：** `src/providers/openai.py:69`
- **问题：** 视觉响应限 2000 tokens 无覆盖途径，可能截断分析结果

#### M22. ProviderRegistry `_meta.available` 不反映真实实例化能力

- **文件：** `src/providers/__init__.py:38-60`
- **问题：** `list_available()` 基于 env var 存在性标记 available，但实际 import/init 可能失败。UI 显示的"可用" Provider 可能无法服务

#### M23. ProviderRegistry.resolve() 只看 requires[0]，忽略其余能力

- **文件：** `src/providers/__init__.py:168`
- **问题：** `requires: ["vision", "text"]` 只检查 vision，选到的 Provider 可能无 text 能力
- **修复：** 检查 resolved provider 的 capabilities 是 requires 的超集

### Minor

#### N16. SessionState.status 类型是 `str` 而非 `RunStatus`

- **文件：** `src/core/state.py:22`
- **修复：** 改为 `status: RunStatus`

#### N17. `with_retry` config 参数无 Optional 标注

- **文件：** `src/harness/retry.py:35`
- **修复：** `config: Optional[RetryConfig] = None`

#### N18. `execute_with_timeout` coro 参数无类型标注

- **文件：** `src/harness/timeout.py:14`

#### N19. `execute_with_timeout` 不拒绝非正 timeout_ms

- **文件：** `src/harness/timeout.py:17`
- **修复：** `if timeout_ms <= 0: raise ValueError(...)`

#### N20. OpenAI `_estimate_cost` 仅含 2 个模型

- **文件：** `src/providers/openai.py:98-100`
- **问题：** gpt-4.1 / o3 / o4-mini 等新模型全回退到 gpt-4o 价格

#### N21. `with_retry` 末尾 `raise last_error` 不可达代码

- **文件：** `src/harness/retry.py:52`

#### N22. OpenAI DALL-E `negative_prompt` 参数被静默忽略

- **文件：** `src/providers/openai.py:113-127`
- **修复：** 至少 log warning

---

## 三、第二次审查（07-18）— Agent 2 发现（agents + chat + api + cli）

### Critical

#### C23. engine.py — retry-on-low-score 是死信：feedback 追加了但从未被 Coordinator 读取

- **文件：** `src/chat/engine.py:222-234`, `src/agents/coordinator.py:49-73`
- **问题：** 审查员 `verdict == "retry"` 时只追加 feedback 到 messages，但 Coordinator 的 Mock 决策是固定数组，不看 feedback。即使用上真实 LLM，也没有结构化信号告诉 Coordinator "需要回到提示词生成"
- **修复：** 设置 `session["pending_retry"] = True`，下一轮引擎直接跳转到提示词生成

#### C24. engine.py — resume_after_hitl "retry" 从头重跑整个 workflow

- **文件：** `src/chat/engine.py:274-314`
- **问题：** `resume_after_hitl` 调用 `self.run(session)` → `coordinator.reset()` 重置 `_workflow_index=0` → 7 个 Agent 全部重跑，不是只重跑生图
- **修复：** 选择性重置 workflow index 到提示词生成步骤，或注入合成决策跳过 coordinator

#### C25. context_manager.py — System Prompt token 对压缩阈值不可见

- **文件：** `src/harness/context_manager.py:94-113`, `src/chat/engine.py:62`
- **问题：** `ctx_mgr.check()` 不接收 system_prompt 参数。Coordinator 的 system prompt（~1500-3000 tokens）从未被计入，低限额模型（qwen-max 32K）可能悄悄溢出
- **修复：** 传入 active system prompt，或加固定 overhead（如 +3000 tokens）

#### C26. main.py — InputPipeline 已实现但从未被调用（死代码 + 无输入防护）

- **文件：** `src/main.py:113-116`, `src/harness/input_pipeline.py`
- **问题：** create_session 端点直接 base64 编码上传文件，不检查格式/大小/内容。`InputPipeline` 和 `ImageValidator` 已完整实现但未被任何运行时导入
- **修复：** 在 `main.py` 的 create_session 中调用 `ImageValidator.validate()`

### Medium

#### M24. broadcaster.py — broadcast 循环内 await 导致并发竞态

- **文件：** `src/chat/broadcaster.py:29-40`
- **问题：** `broadcast()` 遍历 `self._connections[session_id]`，循环内的 `await send_json()` 让出控制权。期间 `connect()`/`disconnect()` 可能修改同一列表，导致消息跳过或 KeyError
- **修复：** 遍历前 copy 列表：`for ws in list(self._connections.get(session_id, []))`

#### M25. audit_logger.py — threading.Lock 用在 async 上下文 + 同步 I/O 阻塞事件循环

- **文件：** `src/harness/audit_logger.py:24,58`
- **问题：** `threading.Lock` + `open()` 同步写文件阻塞事件循环
- **修复：** 换成 `asyncio.Lock` + `aiofiles` 或 `run_in_executor`

#### M26. base.py — asyncio.CancelledError 被当作 Provider 故障触发熔断

- **文件：** `src/agents/base.py:95-96`
- **问题：** Python 3.9+ 中 `CancelledError` 是 `Exception` 子类，被 `except Exception` 捕获后调 `circuit.record_failure()`。用户取消任务不应计为 Provider 故障
- **修复：** `except asyncio.CancelledError: raise` 放在 `except Exception` 之前

#### M27. engine.py — 上下文压缩后无持久化，崩溃即丢失全部消息

- **文件：** `src/chat/engine.py:79-93`
- **问题：** NEW_WINDOW/COMPACT 直接替换 session 消息列表，若此时进程崩溃，旧消息永久丢失
- **修复：** 压缩前先 `save_checkpoint()`

#### M28. engine.py — 审计日志 duration_ms 永远为 0

- **文件：** `src/chat/engine.py:174`
- **问题：** `duration_ms=0` 硬编码，实际耗时在 `result.get("elapsed_ms")` 中可用
- **修复：** 提取 `result.get("elapsed_ms", 0)`

#### M29. base.py — 单 token 成本被清零

- **文件：** `src/agents/base.py:126-127`
- **问题：** `tokens // 2` 当 tokens=1 时两个半都是 0，不记录任何成本
- **修复：** `tokens_in = (tokens + 1) // 2`

### Minor

#### N23. registry.py — `list_capable(requires: str)` 参数名误导

- **文件：** `src/agents/registry.py:31`
- **修复：** 改为 `capability: str`

#### N24. registry.py — load_from_config 不清除旧条目

- **文件：** `src/agents/registry.py:36-61`
- **修复：** 顶部加 `self._agents.clear(); self._meta.clear()`

#### N25. post_process.py — provider 参数静默丢弃

- **文件：** `src/agents/post_process.py:12-13`
- **修复：** 移除参数或透传

#### N26. compliance.py — f-string 插值风险

- **文件：** `src/agents/compliance.py:22-32`
- **问题：** `_rules_for_category()` 返回的字符串如含 `{}` 会导致 KeyError

#### N27. input_pipeline.py 整个模块是死代码

- **文件：** `src/harness/input_pipeline.py`

#### N28. main.py — /health 两次遍历同一 provider 列表

- **文件：** `src/main.py:56-67`
- **修复：** 合并为一个循环

---

## 四、修复优先级

```
第一轮（阻塞级，2h）:
  C14  is_mock_mode 漏检 Key    C15  Flux 尺寸被忽略
  C17  Seedream 签名日期竞态    C19  DeepSeek 返回缺 key
  C20  Qwen KeyError            C21  HALF_OPEN 不立即熔断

第二轮（准确性和安全，3h）:
  C16  Cost 定价不一致           C18  Anthropic system prompt 垃圾
  C22  Circuit breaker 并发超限  M20  RateLimiter.remaining 过期
  M22  Provider 可用性误报       M23  resolve 不看全部能力
  C4   Coordinator 永远 Mock     C9   Coordinator 静默丢弃

第三轮（整洁和类型安全，2h）:
  C1   utcnow 残留               C10  category Mock 错
  M17  checkpoint 死代码          M18-M21 小修
  N16-N22 类型标注和清理
```

---

> **更新指南：** 修复一个问题后标注 `✅ fixed`。复查通过标注 `✅ verified`。
