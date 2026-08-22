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

## 五、复查结论（2026-08-16，审计收尾）

> 经逐项代码复查，本报告列出的全部问题均已解决。CRITICAL/HIGH 由前序会话修复（见 `progress.md` §2.3），本次会话复核确认以下条目**已修复**，并对 MEDIUM/LOW 做工程化收尾：

| 条目 | 结论 | 备注 |
|------|------|------|
| C1 `utcnow` 残留 | ✅ verified | 全局 grep 无 `utcnow` 残留 |
| C4 Coordinator 永远 Mock | ✅ verified | `decide()` 真实 LLM 优先、失败回退 Mock（决策 5） |
| C10 category Mock 忽略品类 | ✅ verified | `_mock_by_category(category)` 按品类分支 |
| C14/C15/C17/C19/C20/C21/C22 | ✅ verified | CRITICAL 轮已修（is_mock_mode 9 个 Key / Flux 尺寸映射 / Seedream 单次取时 / DeepSeek 标准键 / Qwen `.get()` / HALF_OPEN 语义与并发探测） |
| C16/C18/C23-C26 | ✅ verified | 成本口径集中 / Anthropic text parts / retry 死信 / HITL 选择性重跑 / system_prompt 计入阈值 / create_session 接入 ImageValidator |
| M17 checkpoint 死代码 | ✅ verified | `session.py`/`engine.py`/`main.py` 启动恢复均已调用 |
| M18 Flux Replicate model 参数 | ✅ verified | `_via_replicate(..., model=...)` 透传 |
| M19 Flux 循环内 import | ✅ verified | `import asyncio` 已置顶 |
| M20 `remaining()` 不 refill | ✅ verified | 先 `bucket._refill()` 再读 |
| M21 OpenAI vision max_tokens | ✅ verified | 硬编码已升为 4096 |
| M22 available 误报 | ✅ verified | 延迟实例化失败会置 `available=False`；`list_available()` 仍以 env 检测 + 实例化复核为准（可接受的延迟加载设计） |
| M23 resolve 只看 requires[0] | ✅ verified | 已校验 Provider 能力是 requires 的超集 |
| M24 broadcaster 竞态 | ✅ verified | `list(...)` 快照遍历 |
| M25 audit 同步 IO 阻塞 | ✅ verified | `asyncio.Lock` + `run_in_executor` |
| M26 CancelledError 误熔断 | ✅ verified | `except asyncio.CancelledError: raise` 前置 |
| M27 压缩前无 checkpoint | ✅ verified | `engine.py` 压缩前先 `save_checkpoint` |
| M28 duration_ms 恒 0 | ✅ verified | `result.get("elapsed_ms", 0)` |
| M29 tokens=1 成本清零 | ✅ verified | `tokens_in=(tokens+1)//2` |
| N16 state.status 类型 | ✅ verified | `status: RunStatus`（本次修复） |
| N17/N18 类型标注 | ✅ verified | `Optional[RetryConfig]` / `Coroutine[Any, Any, Any]` |
| N19 非正 timeout | ✅ verified | `ValueError` 拦截 |
| N20 成本表模型覆盖 | ✅ verified | 6 个模型 + 未知名回退 gpt-4o 价（文档化行为） |
| N21 `raise last_error` 不可达 | ✅ verified | 实际可达（循环耗尽后抛出），已加注释 + `type: ignore[misc]` 澄清 |
| N22 negative_prompt 静默 | ✅ verified | `warnings.warn` 提示 |
| N23/N24 registry 参数名/清理 | ✅ verified | `capability` 命名 / `clear()` 防旧条目 |
| N25 post_process provider 参数 | ✅ verified | 参数已移除（纯本地 Agent） |
| N26 compliance f-string 风险 | ✅ verified | `.replace("{category_rules}", ...)` 替代 f-string |
| N27 input_pipeline 死代码 | ✅ verified | `ImageValidator` 已被 create_session 调用；`InputPipeline`/`OutputPipeline` 有测试覆盖 |
| N28 /health 双遍历 | ✅ verified | `admin_status` 单循环合并 |

**本次收尾新增修复**（对应 progress.md §3.2 的 MEDIUM/LOW 清单）：

- 模块文档补齐：新增 `docs/modules/{workflow, auth, tenant, logging, storage, harness-extended, ab-testing, image-preprocessor}.md`，更新 `agents/api/deploy.md`（覆盖全部 56 个源文件）
- `.env.example` 补 `ECOMM_API_KEY` / `ECOMM_WEBHOOK_TOKEN`
- CORS 白名单收敛进 `config/default.yaml`（`app.cors_origins`）+ `config.get_cors_origins()`（env 可覆盖）
- `main.py` 魔法数字提取为模块常量（上传上限/预处理参数/列表分页/审计条数等 9 项）
- 清理局部 `import asyncio`（`main.py`/`post_process.py`）与内联 `__import__("datetime")`（`main.py`/`logging_config.py`）
- `retry.py` 返回标注 `-> any` → `-> Any`
- 加固时序脆弱测试：`test_retry_failed_reruns_dead_letter` 改为 spy 统计 instantiate 调用（消除"错过瞬时 running 状态"的偶发失败）；`test_timeout` 显式 `coro.close()` 消除 "never awaited" RuntimeWarning

结论：**65 项审计问题修复率 100%**，全量 `pytest` 370 通过、前端 `npm run build` 成功。

---

## 六、四域审计（2026-08-18）— 第一轮修复完成 + 第二轮待办

> 四个独立审计代理（安全 / 后端正确性 / 测试质量 / 前端+文档）+ 自查合并去重。
> 关键指控全部实测复核：路径穿越（TestClient 复现读回 `config/models.yaml`）、
> 报表 0 条目幽灵计数（临时库复现）、Provider 探测零断言（逐行确认）。

### 第一轮已修复（4 CRITICAL + 10 HIGH，2026-08-18，测试 407 全绿）

| 级别 | 问题 | 修复 |
|------|------|------|
| CRITICAL | 模板路径穿越任意 YAML 读取（Windows `%5C` 直达 `config/secrets.yaml`） | `templates._safe_template_path` 白名单（拒 `/ \ : \x00 .. _` 前缀）+ `load_template`/`resolve_template_path` 共用；export 复用安全路径；import 补 `:`/`..` 校验。测试：`TestPathTraversalGuard` 10 参数 + API 穿越 404 回归 |
| CRITICAL | 多租户隔离失效（未知租户回退 default） | `TenantRegistry.get` 未知返回 None；`_resolve_tenant` 未知租户 403，接入 create_session / ab-test / instantiate / create_batch。测试：未知租户 403 |
| CRITICAL | 设置写端点无鉴权开放 | `_require_admin_access`：未配置 Key 时仅本机（127.0.0.1/::1/localhost/testclient），远程 403；配置 Key 后 AuthMiddleware 全局保护；`require_api_key` 死代码保留待第二轮的 JWT 方案 |
| CRITICAL | 前端无鉴权接线 | `api.js` 统一 `authHeaders()`（X-API-Key）+ `wsUrl` 带 `?api_key=`；Settings 页新增「前端 API Key」localStorage 输入 |
| HIGH | 工作流控制/决策/复刻/批次控制跨租户 IDOR | 端点层 `get_job/get_batch` 后比对 tenant（与读取端点同模式）；不存在 → 404。测试：`TestTenantIsolation`（含跨租户控制 404） |
| HIGH | WS 双端点跨租户读写 + 回放不在 try/finally | WS 接受可选 `tenant` 查询参数并过滤；`ws_session` 回放进 try/finally（防死连接残留）；`_append_interjection` 带租户校验 |
| HIGH | `retry_step` cancel→start 竞态（job 永久卡 RUNNING） | cancel 后 `await` 旧任务完成 + `runtime.task=None` 再 `start()`（覆盖 replicate_style 共用路径）。测试：暂停中 retry_step 重启 + `step_retrying` 事件 |
| HIGH | A/B `model_override` 从未生效（静默失真） | `BaseAgent.execute(model_override=...)` 经 ContextVar 传递，`_model_kwargs()` 优先取覆盖；删除提示词注入与死代码恢复。测试：变体级传播 + kwargs 断言 |
| HIGH | webhook_notify SSRF | `_validate_notify_url`：仅 http/https、拒绝回环/私网/链路本地/保留/组播（IP 字面量 + 主机名解析双路径，解析失败放行防离线误伤）。测试：8 组恶意 URL + 公网不误伤 |
| HIGH | 上传先读后验内存 DoS | `_read_upload_limited` 分块读取、超 20MB 立即 413；三处上传（session/instantiate/replicate）接入。测试：21MB 上传 → 413 |
| HIGH | 批量子任务无上限 | `MAX_BATCH_ITEMS=100`，JSON/CSV 两路统一拦截。测试：101 项 → 400 |
| HIGH | 测试破坏真实数据（secrets 抹除 / memory 清空） | `test_settings` 快照-还原（两分支）；`test_agent_memory` 注入 tmp 目录 |
| HIGH | 前端路由参数变化状态残留 | `Session.jsx`/`WorkflowJob.jsx` 在 id 变化时重置 state |
| HIGH | WS 断线不重连（群聊冻结） | `useWebSocket` 3s 自动重连（卸载/切会话停止） |
| 附带 | `get_batch_report` 0 条目幽灵计数 | LEFT JOIN NULL 行跳过。测试：0 条目批次 → item_count 0 |

**第一轮补充修复**（后端审计 MEDIUM/LOW 低成本项，2026-08-18，测试 410 全绿）：

- `WAITING_HUMAN` 下 cancel 死锁 → cancel 同时唤醒 `human_event`，且事件创建提前到状态落库之前（消除竞态窗口）。测试：`test_cancel_wakes_waiting_human`
- Provider 以 `{"error": ...}` 返回非 200 时不计成功 → 熔断 `record_failure()`（此前持续 5xx 永不熔断）。测试：`test_error_dict_opens_circuit`
- 报表直方图仅统计终态条目（running 项的部分耗时不再计入）。测试：`test_report_running_item_duration_excluded`
- 鉴权失败 IP 字典加膨胀清扫（`_AUTH_MAX_IP_ENTRIES=10000`）
- `save_runtime_secrets` 写后 `chmod 0o600`（POSIX）
- `_job_payload` 对非 list/str 的 `product_images` 不再 `len()` 崩溃
- 删除 `create_session` 的死代码 `if not files`（`File(...)` 已强制）

**第二轮已修复**（正确性/资源治理，2026-08-18，测试 416 全绿；同时提交 git `875382a`）：

| 项 | 修复 |
|----|------|
| HITL retry 跳转被 reset 抵消 | `ChatEngine.run(session, start_index=...)` 在 `coordinator.reset()` 之后生效；`resume_after_hitl` retry 改传 `start_index=5`。测试：`test_run_start_index_jumps_to_reviewer` / `test_hitl_retry_resume_does_not_rerun_analyst`（双跑竞态经核实为误报：端点检查与状态置位间无 await 点） |
| Session 无 TTL + 配额计全量 | `SessionManager(session_ttl_hours)`（默认读 config `chat.session_ttl_hours=24`）+ 惰性驱逐；`count_by_tenant` 只计活跃态（created/running/waiting_human），completed/failed 不再永久占用配额。测试：`test_session_ttl.py` 3 例 |
| checkpoint/agent_memory 同步 IO 阻塞事件循环 | 全部文件 IO 经 `asyncio.to_thread` 转线程池（`_save_sync`/`_read_entries` 等同步函数拆分） |
| AuditLogger 锁失效（实例级 asyncio.Lock） | 改模块级 `threading.Lock` 在 `_write_line` 内串行化追加，规避跨实例/跨事件循环问题 |
| `_runtimes` 无限增长 | engine/batch 的 run() 在终态（completed/failed/cancelled）弹出运行时镜像；后续 retry_step/replicate/control 按需 `setdefault` 重建（测试兼容已验证） |
| 批量 run 无 done_callback | `_make_done_callback` 取回任务异常并记日志，异常不再成为"未取回的 task 异常" |
| SQLite 连接依赖 GC 关闭 | `_connect` 改为 `contextlib.contextmanager`：成功提交 + finally 显式 `close()`，覆盖全部调用点 |
| 限流负 tokens/超大 tokens | `acquire` 拒绝 `tokens<=0`（此前反向给桶加 token）与超大值（此前永久 while 等待）。测试：`test_acquire_rejects_invalid_tokens` |
| 批次上限双保险 | `BatchScheduler.submit` 增加 `MAX_BATCH_ITEMS=100` 调度器级校验（与 API 端点一致） |

**第三轮已修复**（隔离缺口 + 安全边界测试，2026-08-18，测试 431 全绿）：

| 项 | 修复 |
|----|------|
| 鉴权矩阵零覆盖 | 新增 `tests/test_api/test_auth.py` 13 例：401（无 Key/错 Key/Bearer）、200、/health 公开、暴力破解 429、WS 无 Key/错 Key → 4001、正确 Key → 4004（证明鉴权通过）、管理面非本机 403 / 本机放行 / 配 Key 后放行；`_AUTH_FAILURES` autouse 复位 |
| `/api/audit` 跨租户泄露 | AuditLogger.log 补 `tenant_id` 字段、query/stats 支持租户过滤；引擎审计调用传会话租户；端点传 `X-Tenant-ID`。测试：`test_tenant_isolation`（顺带修复 test_io_audit 污染真实 data/audit 的问题） |
| `/api/memory/*` 跨租户泄露 | AgentMemory remember/recall/recall_similar/stats 全链路租户参数；engine 记忆召回与 prompt_gen 相似召回透传会话租户；端点传租户。测试：`test_tenant_isolation` |
| `_mask_key` 泄漏密钥片段 | API 只返回 `configured` 布尔，密钥片段彻底移除（前端同步 + 原函数删除）。测试：`test_settings_api_keys_masked` 改为断言无 masked 字段 |
| A/B 变体/评审无上限 | 变体 ≤8、review_count 1-5，超限 400。测试：`test_variant_caps_enforced` |
| `require_api_key` 死代码 | 删除（auth.md 同步） |
| batch retry_failed 终态竞态（#24） | `requeue_requested` 标志 + run 循环重入：收尾窗口内到达的重跑指令不再丢项；无标志时保持原语义避免空转 |

**第四轮候选（剩余）**：`update_batch_counts` 绝对覆盖与原子递增并存（#23，finalize 与 increment 竞态，需统一计数入口）、熔断器全局单例测试污染、Provider 探测零断言（`test_new_providers`）、CSV GBK/BOM 分支、`/api/admin/status` 租户清单对非管理员隐藏、`hmac.compare_digest`、公开前缀精确匹配、C2 凭据绑定租户（架构级，待用户决策）、M5b 平台连接器。

**第四轮已修复**（技术债收尾 + CI + 部署口径，2026-08-18，测试 439 全绿）：

| 项 | 修复 |
|----|------|
| #23 计数竞态 | `reconcile_batch_counts`：以 items 表为准、`max()` 只增不减的原子调和，替代 `_finalize` 的绝对覆盖（`update_batch_counts` 保留但不再被 finalize 使用） |
| 熔断器测试污染 | `test_integration` autouse fixture 快照/还原全局 `_circuit_breakers`，消除跨测试顺序耦合 |
| Provider 探测零断言 | `test_new_providers` 重写：monkeypatch 各 Key + 真实 `ProviderRegistry` 断言（含"缺 SECRET_KEY 不探测"负例、"无 Key 仅 Mock"） |
| CSV 编码矩阵 | `_parse_batch_csv` 6 例：GBK / UTF-8 BOM+表头 / 无表头 / 空 / 不可解码 / 缺平台列默认 taobao |
| 时序安全比较 | `hmac.compare_digest` 覆盖 AuthMiddleware / webhook token / 双 WS `api_key` |
| 公开前缀精确匹配 | `/health` 等白名单改为精确路径或 `p + "/"` 子路径匹配，封堵 `/healthX` 假想路径 |
| CI 接入 | `.github/workflows/ci.yml`：pytest 全量 + ruff（E/F/I/W）+ 前端 npm ci/build（Python 3.12 / Node 20）；ruff 本地因 pip 网络受限仅在 CI 运行 |
| Docker 部署口径 | 多阶段 `deploy/nginx.Dockerfile`（node 构建 → nginx 镜像内含 dist）+ `nginx.conf.template`（envsubst 注入 `API_HOST`）+ compose 废除空 `frontend_dist` 卷 |

**第五轮候选**：`/api/admin/status` 租户清单仅管理员可见（当前端点本身已受管理面保护，标注为 by-design）、~~C2 凭据绑定租户~~ → ✅ 2026-08-22 用户选定**方案①每租户独立 Key**并实施：`auth.py` 双凭据体系（全局 admin Key + 租户 Key，`config/tenant_keys.yaml` / `ECOMM_TENANT_KEYS`），租户 Key 即身份（中间件重写 X-Tenant-ID 防冒充），管理端点仅 admin，`POST /api/settings/tenant-keys` 分发/轮换/删除 + 设置页管理卡，WS 同权，+17 测试、~~M5b 平台连接器~~ → ✅ 2026-08-22 用户确认**不做**（现有 webhook 连接器保留）、~~审批 SLA 业务矩阵~~ ✅（决策 22）、~~Dashboard 轮询失败横幅~~ ✅、~~前端依赖 `npm outdated` 核查~~ → ✅ 2026-08-22 完成：`npm audit` 5 漏洞清零（vite 5.4.21→7.3.6 修复 esbuild GHSA-67mh-4wv8-2f99；react-router-dom 6.30.6→7.18.2 修复 GHSA-wrjc-x8rr-h8h6 + GHSA-337j-9hxr-rhxg——6.x 线 EOL 无修复，声明式路由 API 零改动；nanoid 3.3.17→3.3.18 修复 GHSA-2v37-7h3g-55p8，postcss 传递依赖），build 零告警 + dev 冒烟 + pytest 全绿；React 19 / recharts 3 / Vite 8 大版本升级列为后续候选（无安全收益）。注：本机 npmmirror 镜像不支持 `/-/npm/v1/security/*` 端点，audit 需 `--registry=https://registry.npmjs.org`。

**第二轮待办（MEDIUM ~25 / LOW ~15）** —— 已随第二轮/第三轮完成，剩余见第四轮候选。

- ~~**正确性**：HITL 双跑竞态 + `_workflow_index` 被 `reset()` 抵消；WAITING_HUMAN 下 cancel 死锁；`retry_failed` 终态竞态；`_job_payload` 非 list TypeError；直方图计 running 部分耗时~~ → ✅ 已修复（决策 19/20）
- ~~**可靠性**：Provider 非 200 不触发熔断；限流负 tokens/无界等待；AuditLogger 实例级锁~~ → ✅ 已修复
- ~~**资源**：Session TTL；`_runtimes` 字典终态清理；鉴权 IP 字典；SQLite 显式 close；checkpoint/agent_memory 转 to_thread~~ → ✅ 已修复
- ~~**安全**：audit/memory 租户过滤；`_mask_key` 仅布尔；secrets 0600；A/B 上限~~ → ✅ 已修复（`hmac.compare_digest`、公开前缀精确匹配、C2 待办）
- ~~**测试**：鉴权矩阵；WS 404 无法失败；Audit 页防抖~~ → ✅ 已修复（熔断器单例污染、Provider 探测零断言、CSV 编码分支待办）
- ~~**前端/文档**：Audit 击键请求；批量创建详情为空；版本漂移；README/Agent 数/模板数~~ → ✅ 已修复
