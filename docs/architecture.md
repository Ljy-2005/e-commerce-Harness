# Architecture — 架构总览

> 覆盖: 全仓（`src/` 8 个包、`config/`、`frontend/`、`deploy/`）
> 定位: 跨模块视角的**骨架文档**。单模块细节见 `docs/modules/*.md`（索引见文末 §6）。
> 口径: 本文所有结论均以**当前代码**为准并附 `file:line`；与模块文档冲突处标注在 §6 的「与代码不符」列。
> 生成方式: 2026-09-21 全库通读（4 组并行精读 + 主读核心引擎 + 8 条高严重度结论逐条回读代码验证）。

---

## 1. 总览与分层

系统是一台「**群聊式多智能体电商商品图生成机**」：用户上传商品图，一群各自负责一段工序的 Agent
在中心决策者的召集下依次发言、互相接力，最终产出一整套可上传电商平台的商品图。

### 1.1 一句话架构

```
                     ┌──────────────── 两个入口 ────────────────┐
                     │  FastAPI (src/api/main.py)   Typer (src/cli.py)  │
                     └───────────────┬──────────────────────────┘
                                     │
        ┌────────────────────────────┴────────────────────────────┐
        │            两条并存的编排路径（可互相嵌套）              │
        │  ① chat/engine.py     群聊引擎：LLM 决策者驱动            │
        │  ② workflow/engine.py 工作流引擎：YAML DSL 驱动           │
        └────────────────────────────┬────────────────────────────┘
                                     │  统一经 BaseAgent.execute()
                     ┌───────────────┴───────────────┐
                     │  11 个 Agent（文件即注册）     │
                     └───────────────┬───────────────┘
                                     │  harness 可靠性保障链
                     ┌───────────────┴───────────────┐
                     │  7 条 Provider 路由（+ Mock）  │
                     └───────────────────────────────┘
```

### 1.2 包职责与依赖方向

PRD D4 的分层约定（`docs/modules/auth.md:84` 声明）为 `core ← providers ← agents ← chat ← api/cli`。
代码实际依赖方向一致，`harness/` 是**被所有层共享的横切包**（不被 core 依赖）。

| 包 | 文件数 | 职责 | 关键入口 |
|---|---|---|---|
| `src/core/` | 6 | 数据模型、会话状态、配置加载、租户、平台与槽位目录、日志 | `config.py` `models.py` `state.py` `tenant.py` `platforms.py` `logging_config.py` |
| `src/harness/` | 26 | 横切能力：重试/超时/熔断/限流/成本/审计/记忆/上下文/图片管道/风格库/定价 | `retry.py` `timeout.py` `circuit.py` `rate_limiter.py` `pricing.py` |
| `src/providers/` | 12 | AI 服务商适配（7 内置路由 + 4 种 kind + 自定义路由） | `routes.py` `compat.py` `base.py` |
| `src/agents/` | 15 | 11 个 Agent + 注册中心 + 基类保护链 | `registry.py` `base.py` |
| `src/chat/` | 4 | 群聊引擎（决策循环 / HITL / 质量门禁）、会话管理、WS 广播 | `engine.py` `session.py` `broadcaster.py` |
| `src/workflow/` | 8 | 编排引擎（YAML DSL、7 种节点声明、重试/人工节点）、SQLite 事件溯源、批量调度 | `engine.py` `job_store.py` `templates.py` `batch.py` |
| `src/storage/` | 3 | 会话 checkpoint（JSON 原子写）、生成图落盘 | `checkpoint.py` `image_export.py` |
| `src/api/` | 3 | FastAPI 接入层 + 鉴权中间件 | `main.py`（3506 行）`auth.py` |
| `src/cli.py` | 1 | Typer CLI（4 个命令），**不绕 HTTP，直接调 ChatEngine** | `cli.py:18-81` |

### 1.3 两条编排路径的关系（本项目最需要先理解的一点）

| | 群聊引擎 | 工作流引擎 |
|---|---|---|
| 驱动者 | LLM（中心决策者）动态决定下一个 Agent | `config/workflows/*.yaml` 静态图 |
| 文件 | `src/chat/engine.py` | `src/workflow/engine.py` |
| 状态 | `SessionState`（内存 + JSON checkpoint） | `WorkflowJob` / `WorkflowStep`（SQLite 事件溯源） |
| 循环 | `run()` 主循环直到 done/超轮次（`chat/engine.py:196`） | `_execute` 按图遍历节点（`workflow/engine.py:289+`） |
| 人工介入 | `resume_after_hitl()`（`chat/engine.py:790`） | `human` 节点 + `asyncio.Event`（`workflow/engine.py:420`） |
| 相互嵌套 | 工作流 `group_chat` 节点内部**实例化 ChatEngine**（`workflow/engine.py:386-405`） | — |

> `group_chat` 节点是两者的接缝：工作流把一段编排委托给群聊引擎，用完即弃。
> 这一设计带来一个**已确认缺陷**（见 §7.1）：它自建私有 `SessionManager`，
> 群聊会话不会出现在 `/api/sessions`。

### 1.4 零外部中间件

整个后端**不依赖任何外部服务**（无 Redis / 无 Celery / 无 Postgres）：

| 需求 | 实现 | 出处 |
|---|---|---|
| 会话持久化 | 每会话一个 JSON + 同目录临时文件 + `fsync` + `os.replace` | `storage/checkpoint.py:129-139` |
| 工作流持久化 | SQLite（标准库 `sqlite3`），IO 走 `asyncio.to_thread` | `workflow/job_store.py` |
| Agent 记忆 | JSONL 追加 + 模块级写锁 | `harness/agent_memory.py` |
| 审计日志 | 按日期分文件 | `harness/audit_logger.py` |
| 同进程广播 | 进程内 `Broadcaster` → WebSocket | `chat/broadcaster.py` |

代价（见 §7）：单进程假设、无跨进程事件总线、批量调度器与 API 同进程。

### 1.5 规模基线（**由代码实测，非文档转抄**）

以下数字均于本轮从代码/配置直接统计，非引用既有文档：

| 项 | 实测值 | 统计方式 |
|---|---|---|
| REST 端点 | **63**：62 个 `/api/*` + 1 个 `/health` | 遍历 `app.routes`（`_arch_routes.txt` 为 `/api/*` 全量清单；另有 FastAPI 自带的 `/docs` `/redoc` `/openapi.json` `/docs/oauth2-redirect`，不计入业务端点） |
| WebSocket 端点 | **2** | `/ws/sessions/{id}`、`/ws/workflows/jobs/{id}` |
| Agent | **11**（10 可邀请 + 1 后台） | `config/agents/*.yaml` 11 个文件；`invitable: false` 仅 `style_archivist.yaml` |
| Agent 能力类 | **3**：`text` / `vision` / `image`（+ `local` 后处理） | `providers/routes.py` `CAPABILITIES` |
| Provider 内置路由 | **7**：openai / deepseek / anthropic / qwen / ark / seedream / flux | `routes.py` `BUILTIN_SPECS` |
| Provider 预设（一键添加） | **6**：zhipu / moonshot / siliconflow / openrouter / ark / ollama | `routes.py` `PROVIDER_PRESETS` |
| Provider kind | **4**：`openai` / `anthropic` / `volc_cv` / `flux` | `routes.py` `KINDS` |
| 平台档案 | **10**：淘宝/天猫/拼多多/京东/1688/抖音/小红书/Amazon/Shopify/Shopee | `config/platforms.yaml` `platforms` |
| 槽位目录 | **15** 个槽位（跨平台实际用到 14 个，`activity_banner` 未被任何平台引用） | `config/platforms.yaml` `slot_catalog` |
| 工作流模板 | **7** | `config/workflows/*.yaml` 7 个文件（`templates.list_templates()`） |
| 工作流节点类型 | **7 种**：tool / agent / condition / human / group_chat / end / subworkflow | `workflow/templates.py:12` `_NODE_TYPES` |

> ⚠️ **`subworkflow` 只声明未实现**：`_NODE_TYPES` 含它，但引擎在
> `workflow/engine.py:330-331` 直接 `raise RuntimeError(f"节点类型 '{ntype}' 暂未实现")`。
> 写进模板即运行时失败（见 §7.1）。

---

## 2. 端到端数据流

### 2.1 路径 A：一次群聊会话（上传商品图 → 一整套图）

```
① 上传     POST /api/sessions
           ├─ 租户校验：会话数 + 月度预算（api/main.py:360-372，超限 429）
           ├─ ImageValidator 校验 → ImagePreprocessor 预处理（解压炸弹防护、EXIF、长边缩放）
           └─ SessionManager.create() → asyncio.create_task(ChatEngine.run())

② 决策     chat/engine.py:196 run() 主循环
           ├─ CoordinatorAgent.decide(session)
           │    ├─ _build_user_prompt()：任务 + **当前产物状态** + 群聊人话历史
           │    └─ _artifact_status()：逐图 [槽位] 标注 / 身份是否确认 / 套图覆盖度
           ├─ action == "invite" → registry.get_invitable(agent_name)
           │    └─ 幻觉邀请（不在可邀请名单）→ 不调用任何 Provider，只记一条可读错误
           └─ action == "done" → 退出循环

③ 执行     agent.execute(task_brief, session, stats)   agents/base.py:120
           └─ _execute_with_harness()  agents/base.py:125
              ① 熔断检查（provider 级）   ② 限流 acquire(provider, tenant_id)
              ③ with_retry(execute_with_timeout(_execute_impl()))
              ④ 成功/失败回写熔断器 + _track_cost 记账

④ 门禁     chat/engine.py 回合后处理
           ├─ 提示词阶段：_prompt_stage() → 体检($0) → 提示词审核优化员 → 有界重写
           ├─ 生图阶段：图片落盘（storage/image_export.py）
           ├─ 审查门禁：**只有 verdict == "pass" 放行**；报错/缺失/非法 → 转人工
           └─ _quality_guard()  chat/engine.py:1369

⑤ 落盘     每步轻量快照 update(slim=True)，关键节点完整快照
           └─ storage/checkpoint.py:119 _save_sync（tmp → fsync → os.replace）

⑥ 推送     Broadcaster → WS /ws/sessions/{id}
```

**产物（artifacts）是唯一的跨 Agent 契约载体**。协调者不看"最近 10 条消息"判断进度，
而是看 `_artifact_status()`——这是 A35 的事故修复：此前 3 条空图记录也会被当成"生图已完成"。

### 2.2 路径 B：一次工作流 job

```
① 实例化   POST /api/workflows/templates/{name}/instantiate
           └─ templates.instantiate() → 变量渲染 → WorkflowJob（SQLite）

② 执行     workflow/engine.py 按图遍历
           节点类型分发：condition(:296) / human(:310) / agent|tool|group_chat(:324-329)
           └─ agent / tool / group_chat 三型带节点级重试（:313-368，max_retries 来自节点 cfg）

③ 人工     human 节点 workflow/engine.py:420 _run_human
           ├─ 先建 human_event 再落 WAITING_HUMAN（审计修复：避免 cancel 落在状态窗口内唤醒失效）
           └─ 等待 asyncio.Event ← POST /api/workflows/jobs/{id}/decision 唤醒

④ 产物     _update_artifacts()  workflow/engine.py:562
           └─ runtime.artifacts[key] = outputs   ← ⚠️ 见 §7.2（写入的是整个 dict）

⑤ 落盘     step 状态 / 事件 / job 状态 → SQLite 事件溯源
           └─ 生成图另经 _export_images() 落盘
```

### 2.3 路径 C：后台 Agent（不进群聊）

「风格档案员」由「风格词库」页面 / `scripts/style_anchor.py` 直接调用，
通过三层机制保证**永不被协调者邀请**（`docs/modules/agents.md` 详述）：

1. `config/agents/style_archivist.yaml → invitable: false`；
2. `registry.load_from_config()` 显式取该字段（`AgentMeta` 逐字段构造，漏取即静默失效）；
3. `coordinator._build_agent_list()` 用 `list_all(invitable_only=True)`，
   引擎邀请路径走 `registry.get_invitable()`。

---

## 3. 跨模块契约

这一节是本文最有复用价值的部分：**改任何一个模块前，先确认没破坏这些契约**。

### 3.1 `session` —— 贯穿全链路的字典（非模型）

Agent 收到的 `session` 是普通 `dict`，不是 Pydantic 模型。字段来自 `SessionState`（`core/state.py`）
+ 运行时注入。关键约定：

| 键 | 含义 | 读取方 |
|---|---|---|
| `session_id` / `tenant_id` | 身份与隔离 | 全链路（限流、落盘、审计） |
| `task.product_images` / `task.reference_images` | 用户上传图（base64） | 分析员、生图员、审查员 |
| `task.slot_override` | 只出指定槽位（`POST /api/sessions` 的 `slots`） | 生图员 |
| `artifacts.*` | **跨 Agent 唯一契约载体** | 所有 Agent 只读、引擎只写 |
| `messages[]` | 群聊历史 | 协调者（经 `readable_content()` 转人话） |
| `cost_so_far` / `error_history[]` | 记账与故障史（`_MAX_ERROR_HISTORY=20`） | 引擎门禁 |

### 3.2 `artifacts` 键契约

| 键 | 生产者 | 消费者 |
|---|---|---|
| `analysis` | 商品分析员 | 品类专项分析员、提示词生成员 |
| `prompts`（含 `style_refs.locked_entry_id`） | 提示词生成员 | 生图员、审查员、**风格会话锁** |
| `images` | 生图员 / 后处理员 | 审查员、合规审查员、落盘、前端 |
| `prompt_lint` / `prompt_review` | 引擎（提示词阶段） | 协调者产物状态、审查员 header |

> ⚠️ `images` 的**形状在两条路径上不一致**：群聊路径是 `list`，工作流路径
> `_update_artifacts` 写入的是 `outputs`（`dict`）。审查员 `_batch_images` 按
> `for img in (images or [])` 迭代（`agents/reviewer.py:149-156`）→ 拿到的是 str 键 →
> 全部判为不可用 → 直接返回 `NO_IMAGE_ACCESSIBLE`。见 §7.2。

### 3.3 `Agent.execute()` 返回契约

```
{"content": ...}                     正常
{"error": "..."}                     Provider 失败（**原样上报，绝不静默换 Mock 模板**）
{"content": ..., "cost_usd": ...}    生图员 / 风格档案员额外回传金额
```

- `_content_or_error()`（`agents/base.py:329-355`）**只返回 content 或 error**，
  用量暂存在实例属性 `self._last_usage`，供 `_track_cost` 记账。
- ⚠️ 后果：**工作流 `agent` 节点的 `step.cost_usd` 恒为 0**（`agent.execute()` 的返回值里
  没有 `cost_usd`，而 `workflow/engine.py:337-338` 从 `outputs["cost_usd"]` 取）。
  写金额的只有 `image_gen.py:190-191` 与 `style_archivist.py:260`。见 §7.1 D3。

### 3.4 `stats` 出参（可选，A38）

`execute(task_brief, session, stats=...)` 会回填
`elapsed_ms / tokens_used / tokens_in / tokens_out / cost_usd / model_used / error`。
`_run_agent`（`workflow/engine.py:381`）**没有传 `stats`** → 工作流路径完全没有用量数据。

### 3.5 事件与 WebSocket

| 通道 | 事件 |
|---|---|
| `/ws/sessions/{id}` | 会话消息流（经 Broadcaster，进程内） |
| `/ws/workflows/jobs/{id}` | `step_started` / `step_succeeded` / `job_completed` / `job_failed` |
| `/api/webhooks/workflows/{job_id}/decision` | 工作流入站回调（独立 token `ECOMM_WEBHOOK_TOKEN`） |

### 3.6 身份与隔离

- **两类凭据**（`api/auth.py`）：全局 Key = admin（租户由 `X-Tenant-ID` 声明）；
  租户 Key = tenant，**密钥即身份**——中间件把 `X-Tenant-ID` 重写为该 Key 绑定的租户
  （`auth.py:163-164`，防伪造）。
- 未知租户返回 403（`core/tenant.py:96-102`，不静默回退 `default`）。
- 管理面 `_require_admin_access`：tenant → 403；未配 Key（开发模式）→ 仅本机。

---

## 4. 关键不变量

这些是系统的"红线"，改动时若破坏它们，通常会静默产生错误数据而不是报错。

| # | 不变量 | 落地处 |
|---|---|---|
| I1 | **Mock 模式零成本、零网络**：`is_mock_mode()` 时全部 Agent 返回确定性模板 | 各 Agent `_execute_impl` 的 `isinstance(self.provider, MockLLMProvider)` 分支 |
| I2 | **价格只从 `harness/pricing.py` 来**；未标定 → `cost_usd = None`，**绝不写 0 冒充** | `pricing.py` 模块头四条规则；`base.py:347-348` |
| I3 | **审查只有显式 `pass` 放行**；报错 / `verdict` 缺失或非法 → 转人工 + 写 `error_history` | `chat/engine.py:550-602`（`:558` 注释记录"`result.get("verdict", "pass")` 会让解析失败静默通过"的事故） |
| I4 | **幻觉邀请不调用任何 Provider** | `registry.get_invitable()` |
| I5 | **产物状态是进度唯一权威依据**，不靠消息条数推断 | `coordinator._artifact_status()` |
| I6 | **`artifacts` 只由引擎写，Agent 只读** | `chat/engine.py` / `workflow/engine.py:562` |
| I7 | **未标定金额的调用要单独回报**（`cost_unknown_calls`），不混进总额 | `ab_testing.py:163-170`、`workflow/engine.py:406-415`、`image_gen.py:190`（`cost_usd=None` 而非 `0.0`） |
| I8 | SQLite 无迁移机制：只有 `CREATE TABLE IF NOT EXISTS`，**加列需手工改库** | `workflow/job_store.py`；`engine.py:334-336` 注释记录此约束 |
| I9 | 租户隔离按 `tenant_id` 字段贯穿 session / job / batch 查询 | `api/main.py` 各 list 端点 |
| I10 | 图片送审须**图一（用户原图）+ 生成图**同时送，还原度以图一为基准 | `reviewer.py` / `compliance.py`；`vision_payload` |
| I11 | 路径**使用时解析**，不在模块级固化成常量（否则 import 期锁定真实目录） | `storage/checkpoint.py`；`ECOMM_DATA_DIR` / `ECOMM_PROJECT_ROOT` |
| I12 | 画面里不画字：`text_in_image` 一律 false，信息图文字由本地排版绘制 | `config/prompts/prompt_gen.yaml`；`harness/image_compose.py` |

---

## 5. 故障传播与兜底

### 5.1 保护链的层级与顺序

```
熔断（provider 级，最先，不消耗配额）
  └─ 限流（provider + 租户）
      └─ 重试（仅传输类异常：httpx.HTTPError / OSError）
          └─ 超时（agent 级，来自 config/agents/*.yaml 的 timeout_ms）
              └─ Provider 调用
```

- **超时不重试**：`_retryable_errors()` 返回 `(httpx.HTTPError, OSError)`，
  **不含 `harness.timeout.TimeoutError`**（`agents/base.py:63-76`）。
  理由：实测 15s 上限 ×4 次 = 用户白等 66s。
- **Provider 以 `{"error": ...}` 返回非 200 时不计成功**（`base.py:157-159`），
  持续 5xx 应触发熔断。

### 5.2 故障各处如何收敛

| 故障点 | 行为 | 兜底 |
|---|---|---|
| Provider 熔断 | `execute` 直接返回 `{"error": "已熔断"}`，不发请求 | 会话记 error 消息；工作流 step 失败 |
| 限流超限 | `await acquire()` **阻塞等待**（不是立即失败） | — |
| Agent 超时 | `TimeoutError(agent_name, timeout_ms)` 抛给引擎 | 会话转人工；工作流按 `max_retries` 重试 |
| Agent 报错 | `{"error": ...}` **原样上报** | 工作流引擎 `raise RuntimeError(result["error"])`（`workflow/engine.py:383`） |
| 生成图落盘失败 | 只告警，**绝不影响生成任务** | 图仍在内存/checkpoint |
| 提示词审核不可用 | 记 `skipped\|error` 并**继续出图**（体检仍生效） | 不静默 |
| 审查员报错 / 分数缺失 | 转人工，文案显示"未给出分数" | `_quality_guard()`（`chat/engine.py:1369`） |
| 条件节点无匹配规则 | `raise RuntimeError("条件节点无匹配规则（缺少 default）")`（`workflow/engine.py:307`） | job 失败 |
| 节点重试耗尽 | `raise RuntimeError(f"节点 {node} 重试耗尽")`（`workflow/engine.py:368`） | job 失败 |
| checkpoint 损坏 | `load_checkpoint` 返回 `None`（旧文件始终完整可读） | 启动扫描恢复非终态会话 |
| 成本追踪实例丢失 | `CostTracker.from_session()` 按 `cost_so_far` 重建 | 避免会话**永久停止记账** |

### 5.3 有界性设计（防失控烧钱）

| 机制 | 上界 | 出处 |
|---|---|---|
| 群聊轮次 | `max_turns` | `SessionState` |
| 错误历史 | `_MAX_ERROR_HISTORY = 20` | `chat/engine.py` |
| 提示词硬伤打回 | `prompt_review_max_rounds`（默认 1，会话级计数，人工介入清零） | `chat/engine.py` |
| 审查分批 | 每批 ≤3、≤4 批 | `reviewer.py` |
| 风格档案照片 | 词条 ≤20 张，单次视觉调用 ≤12 张，超出分批 | `style_store.MAX_PHOTOS` / `VISION_BATCH_SIZE` |
| 生图超时 | `max(420s, 张数 × 90s + 60s)` | `image_gen.timeout_budget()` |
| 工作流节点重试 | 节点 `cfg.max_retries` | `workflow/engine.py:314` |
| 启动恢复 | `MAX_CHECKPOINT_RESTORE = 100` | `api/main.py:72` |

---

## 6. 模块文档索引

16 篇模块文档的验证状态。**状态口径**：

- **✅ 已核**：本轮逐条回读代码核对覆盖范围内的关键事实，未发现与代码不符（措辞层面的小偏差记在最后一列）。
- **⚠️ 部分核**：抽核了核心结论，但覆盖范围内存在**已确认的与代码不符**（列在最后一列）。

| 文档 | 覆盖范围 | 状态 | 与代码不符（已确认） |
|---|---|---|---|
| `modules/core.md` | `core/models.py` `state.py` `config.py`、`default.yaml` | ⚠️ 部分核 | `_bounded_int` 在 `config.py:132`（`(raw, default, low, high)`）与 `:951`（`(raw, name, low, high)`）定义两次，后定义遮蔽前定义；`_normalize_chat_policy` 的"越界回落默认值"意图失效，且报错文案把默认值当字段名（详见 §7.2 D7） |
| `modules/harness.md` | `harness/retry.py` `timeout.py` `circuit.py` | ⚠️ 部分核 | 文档 `:99-103` 的 `timeout` 段写 `default_ms: 30000 / text_ms: 15000 / image_ms: 60000`，`config/default.yaml:65-69` 实为 `150000/150000/150000/420000`；`circuit_breaker` 段（`default.yaml:57-60`）**从未被读取**，`CircuitBreaker` 只用自身默认值（`circuit.py:19-21`，数值恰好相同）；`half_open_max_requests` 与构造参数名 `half_open_max` 不一致 |
| `modules/harness-extended.md` | 限流/成本/审计/记忆/上下文/IO 管道/视觉图源 | ⚠️ 部分核 | `set_tenant_limit()`（`rate_limiter.py:25`）**全仓无调用方** → 租户级限流实际不生效；`TenantQuota.tpm` 无任何读取方；文档 `:62` 称"启动恢复会加载**全部** checkpoint"，实际上限 100（`api/main.py:200`）；`RateLimiter.remaining()` 只回报 `default_rpm`（`api/main.py:307`），`default_tpm` 不出现在任何接口 |
| `modules/agents.md` | 11 个 Agent + registry + base | ⚠️ 部分核 | `_fallback_system_prompt`（`coordinator.py:401-429`，含"套图完整性/身份未确认"规则）因 `config/prompts/coordinator.yaml:1` 存在 `system:` 键而**永不生效**（`coordinator.py:354-365` 的取值顺序：YAML 优先）；`reviewer.py:58` 类默认 `timeout_ms = 300_000` 与 `config/agents/reviewer.yaml` 的 `150000` 不一致（YAML 生效） |
| `modules/chat.md` | `chat/engine.py` `session.py` `broadcaster.py` | ✅ 已核 | — |
| `modules/workflow.md` | `workflow/*` 全部、`config/workflows/*.yaml` | ⚠️ 部分核 | `subworkflow` 在 `templates.py:12` 声明但引擎未实现（`engine.py:330-331` 抛错）；`_update_artifacts`（`engine.py:562`）对 `images` 写入整个 dict，`_export_images`（`:568-569`）则防御性地解包 |
| `modules/api.md` | `api/main.py` `api/auth.py` | ⚠️ 部分核 | 开篇称"共 4 组 REST"但正文列 5 组；风格词库 9 个端点未进总览；端点总数应为 **63 REST（62 个 `/api/*` + `/health`）+ 2 WS**（`api.md` 未给总数）；`POST /api/webhooks/workflows/{job_id}/decision` 缺租户校验（见 §7.1） |
| `modules/auth.md` | `api/auth.py` | ✅ 已核 | — |
| `modules/providers.md` | `providers/*`、`config/models.yaml` 等 | ✅ 已核 | 内置路由表 7 路由的 kind/能力/凭据变量名与 `BUILTIN_SPECS` 逐项一致；仅字段名表述偏差：文档写 `api_key_env`，内置路由实际用 `key_envs` / `all_key_envs`（`routes.py:52` 注释已说明 `api_key_env` 是自定义路由专用） |
| `modules/storage.md` | `storage/checkpoint.py`、`data/` | ✅ 已核 | — |
| `modules/tenant.md` | `core/tenant.py` | ⚠️ 部分核 | 文档 `:63` 称"限流器：`set_tenant_limit()` 按租户注入独立配额" —— 该方法无调用方（见上）；文档 `:49` 的 `ECOMM_TENANTS` 格式写 `tenant:层级[:rpm]`，`_load_from_env`（`tenant.py:83-94`）**忽略第三段**，只用层级内置配额 |
| `modules/image-preprocessor.md` | `harness/image_preprocessor.py` | ✅ 已核 | 唯一措辞偏差：`max_bytes` 是**原始文件**大小上限（`image_preprocessor.py:71-72` 在压缩前检查），文档 `:25` 注为"压缩后文件大小上限"（模块内注释同样如此，默认 10MB 对两者都够宽松，无实际影响） |
| `modules/ab-testing.md` | `harness/ab_testing.py` | ⚠️ 部分核 | 文档 `:41` 称"`avg_score > 0` 才参与送审"，代码 `ab_testing.py:151-158` 恰好相反并注明原因（"不能以 avg_score > 0 作为送审门槛，否则永远无人送审"），实际门槛是 `not vr.error`；文档结论"已解决"与代码一致，但描述句过时。另：`ABTestConfig.variants` 注释写"2-5 个"（`ab_testing.py:86`）而文档 `:46` 写"≤8"，代码内**未见**对两者的强制校验 |
| `modules/cli.md` | `cli.py` | ✅ 已核 | 4 个命令（run/batch/agents-list/config-validate）与 `cli.py:18/32/57/80` 一致 |
| `modules/logging.md` | `core/logging_config.py` | ✅ 已核 | 格式、UTC 转换、级别环境变量、结构化适配器、命名规整逐项一致（`logging_config.py:20-24/48/52-60`） |
| `modules/deploy.md` | `deploy/*`、`.env.example`、`pyproject.toml` | ⚠️ 部分核 | 文档引用的 Dockerfile 片段是**旧版**：实际 `deploy/Dockerfile` 是两阶段构建（node:22 前端构建 → python:3.12-slim 运行），安装 `.[prod]`（非 `.[dev]`），含非 root 用户 / HEALTHCHECK / nginx.conf 拷贝，`CMD` 带 `--proxy-headers --log-level warning`；`CMD` 与 `RUN pip install` 两行均不符 |

**未核**：无（16 篇均已抽核或逐条核）。

> 修复建议：`⚠️` 各项中，`api.md` 的端点总览、`harness.md` 的 timeout 段、`deploy.md` 的 Dockerfile
> 片段属**纯文本过期**，可直接改；其余（`set_tenant_limit` 无调用方等）是**代码缺陷**，
> 应改代码或降级文档措辞，不能只改文档（见 §7）。

---

## 7. 已知边界与未完成项

本节只记录**本轮通读代码时确认的**问题，均附 `file:line`。
按计划约定：**本轮不修改业务代码**，只报告。分级按影响面。

### 7.1 确认缺陷（高）

| # | 问题 | 位置 | 影响 |
|---|---|---|---|
| D1 | `group_chat` 节点自建私有 `SessionManager`，群聊会话不进全局会话表 | `workflow/engine.py:395-396` | 该会话不出现在 `/api/sessions`，无法查询/删除/下载；checkpoint 仍会写盘 → 磁盘上存在"孤儿会话"。影响面：7 个模板中仅 `free_chat.yaml` 用 `group_chat` 节点（1/7） |
| D2 | `images` artifacts 形状不一致：工作流写整个 dict，审查员按 list 迭代 | `workflow/engine.py:542` `_update_artifacts`（`:562` `runtime.artifacts[key] = outputs`）+ `agents/reviewer.py:149-156` | 工作流内审查员必然返回 `NO_IMAGE_ACCESSIBLE`（`for img in dict` 拿到 str 键 → 全判不可用）。**影响 6/7 模板**（`approval_matrix` / `compliance_hardened` / `light_approval` / `scene_suite` / `style_replicate` / `white_bg_suite` 均含"生图员→审查员"链路，其中 2 个另含合规审查员）；仅 `free_chat` 免疫（它走 `:548-549` 的 `artifacts.update` 分支，且不经审查员） |
| D3 | 工作流 `agent` 节点 `step.cost_usd` 恒为 0 | `workflow/engine.py:337-338` + `agents/base.py:329-355` | 批量报表金额系统性偏低；`job.cost_so_far` 低估。只有生图/风格档案员写金额 |
| D4 | `_run_agent` 未传 `stats`，工作流路径无用量数据 | `workflow/engine.py:381` | 工作流无 token 记账，审计里 token 恒 0 |
| D5 | `POST /api/webhooks/workflows/{job_id}/decision` 缺租户校验 | `api/main.py`（webhook 端点） | 持同一 token 的调用方可对**任意租户**的 job 做审批决策（横向越权） |
| D6 | `subworkflow` 声明未实现 | `workflow/templates.py:12` + `workflow/engine.py:330-331` | 模板写该类型 → 运行时 `RuntimeError`。当前 7 个模板均未使用，属"未完成的扩展点" |

### 7.2 确认缺陷（中）

| # | 问题 | 位置 | 影响 |
|---|---|---|---|
| D7 | `_bounded_int` 重复定义、后定义遮蔽前定义，且两者**签名语义不同** | `core/config.py:132` `(raw, default, low, high)` 被 `:951` `(raw, name, low, high)` 遮蔽 | 两族调用方行为分叉（已运行期实证）：① `:113` / `:118`（`_normalize_chat_policy`）本意是"越界/非数字→回落默认值"，实际变成**抛 `ValueError`**，且报错文案把默认值当字段名（实测 `_bounded_int(5, 1, 0, 3)` → `ValueError: 1 必须在 0–3 之间`）→ 手改 `config/chat.yaml` 填入越界值时，`chat_settings()` 抛错，`chat/engine.py:994` 与 `GET /api/settings` 一并 500；② `:873` / `:875` / `:885`（保存生图设置）**恰好与 `:951` 的签名匹配**，工作正常（实测越界 → `ValueError: variants 必须在 1–8 之间`）—— 即保存路径是该签名的正确用法，读取路径才是被遮蔽的受害者。修法应是删除 `:132` 的旧定义并把 `:113`/`:118` 改为显式回落，而不是简单删 `:951` |
| D8 | 租户级限流不生效 | `harness/rate_limiter.py:25`（无调用方） | 所有租户共用 `(60 rpm, 100K tpm)` 默认桶；`TenantQuota.rpm/tpm` 与实际限流脱钩 |
| D9 | `TenantQuota.tpm` 无读取方 | `core/tenant.py:25` 等 | 配额表里 tpm 是装饰性字段 |
| D10 | `config/default.yaml` 的 `circuit_breaker` 段从未被读取 | `default.yaml:57-60` | 改这段配置**无任何效果**（当前值与类默认值恰好相同，所以未暴露）。另：配置名 `half_open_max_requests` ≠ 参数名 `half_open_max` |
| D11 | `_fallback_system_prompt` 是死代码 | `agents/coordinator.py:401-429` | 其中的"套图完整性 / 身份未确认"规则**从未进入提示词**；实际规则以 `config/prompts/coordinator.yaml` 为准 |
| D12 | 启动恢复上限与文档不符 | `api/main.py:200`（100）vs `harness-extended.md:62`（"全部"） | 超过 100 个非终态 checkpoint 时，重启后部分历史会话从列表消失 |

### 7.3 确认缺陷（低 / 文档漂移）

| # | 问题 | 位置 |
|---|---|---|
| D13 | `reviewer.py` 类默认 `timeout_ms = 300_000` 与 YAML 的 `150000` 不一致（YAML 生效，属易误导） | `agents/reviewer.py:58` |
| D14 | `ECOMM_TENANTS` 第三段 `:rpm` 被解析器忽略 | `core/tenant.py:83-94` |
| D15 | `api.md` 端点总览过期（4 组 vs 5 组、风格词库 8 端点缺失、无端点数） | `docs/modules/api.md:18+` |
| D16 | `harness.md` 的 timeout 段数值过期 | `docs/modules/harness.md:99-103` |
| D17 | `deploy.md` 的 Dockerfile 片段是旧版（单阶段、`.[dev]`、`CMD` 不同） | `docs/modules/deploy.md:14-22` |
| D18 | `ab-testing.md` 的"`avg_score > 0` 才送审"与代码相反 | `docs/modules/ab-testing.md:41` |

### 7.4 设计边界（非缺陷，但需知情）

- **单进程假设**：Broadcaster 是进程内对象，多 worker 部署时 WS 推送会分裂。
- **无迁移机制**：SQLite 只有 `CREATE TABLE IF NOT EXISTS`，加列需手工改库（`workflow/engine.py:334-336` 有注释说明）。
- **批量调度器与 API 同进程**：`workflow/batch.py` 的调度随 API 进程生命周期。
- **风格拆解员可被邀请**：工作流用它是正规用法，但它同时在群聊可邀请名单里（既有隐患，已记 `docs/progress.md §3.3`）。
- **`activity_banner` 槽位未被任何平台引用**（`config/platforms.yaml` 目录 15 个 vs 跨平台实际用到 14 个）。

---

## 附：本文的验证方式

- **主读**：`chat/engine.py`（1700+ 行全读）、`workflow/engine.py`（734 行全读）、
  `agents/base.py`、`harness/{pricing,retry,circuit,rate_limiter}.py`、`core/{config,tenant,logging_config}.py`。
- **4 组并行精读**：core+providers / agents / harness / storage+workflow，产出带 `file:line` 的结论 + 文档漂移表。
- **抽查**：每组的最高严重度结论**逐条回读代码验证**，共 8 条全部通过（D1 D2 D3 D4 D8 D10 D11 + `chat/engine.py` 门禁函数）。
  其中 D7 为**运行期实证**（`inspect.signature` + 分别以两族调用方的实参形态触发），非仅静态阅读；
  `_ARTIFACT_KEYS`（`workflow/engine.py:21-29`）与 7 个模板的节点清单亦为脚本实测，用于确定 D1/D2 的影响面。
- **规模数字**：由遍历 `app.routes` / 解析 `config/*.yaml` / import 模块直接统计，不转抄既有文档。
- **未做**：未核历史工作量/工时类表述（计划约定以代码为准）；未逐行读测试代码（`tests/` 共 129 个文件、21,324 行）。
