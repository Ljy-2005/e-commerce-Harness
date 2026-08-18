# Workflow — 编排层

> 覆盖: `src/workflow/models.py`, `src/workflow/templates.py`, `src/workflow/expressions.py`, `src/workflow/tools.py`, `src/workflow/engine.py`, `src/workflow/job_store.py`, `src/workflow/batch.py`, `config/workflows/*.yaml`
> 设计文档: `docs/workflow-design.md`（模板 DSL / 状态机 / 里程碑）

## 功能

Workflow 层在群聊引擎之上提供**可编排、可恢复、可审批、可批量**的生产工作流（借鉴 OiiOii 范式）：

- **模板 DSL** — 工作流是 YAML 数据（Skill 库 `config/workflows/*.yaml`），新增模板不改代码
- **状态机引擎** — 自动挡（一路跑完）/ 手动挡（每步暂停等指令），断点续跑、回跳重试
- **事件溯源** — 所有状态变更追加不可变事件流（SQLite `events` 表 + 审计日志双写）
- **批量调度** — 商品列表 → 并发窗口 → 失败隔离 + 死信重跑
- **人工审批** — human 节点 + SLA 超时自动决策（auto_approve / auto_reject / keep_waiting）
- **连接器** — webhook 出站通知 + 入站回调端点
- **一键风格复刻** — 参考图 → 风格拆解员 → 注入提示词重跑

## 模块

### models.py — 运行期数据模型

| 类 | 说明 |
|----|------|
| `JobStatus` | CREATED → QUEUED → RUNNING → PAUSED / WAITING_HUMAN → COMPLETED / FAILED / CANCELLED |
| `StepStatus` | PENDING → RUNNING → SUCCEEDED / FAILED / SKIPPED / WAITING_HUMAN |
| `WorkflowJob` | job_id（uuid16）、template_name、version（模板快照版本）、tenant_id、status、mode、inputs、context（`$ctx`）、cost_so_far |
| `StepRecord` | step_id、node、type、order（声明顺序，回跳重置用）、attempt、inputs/outputs 快照、error、elapsed_ms、cost_usd |

### templates.py — Skill 库加载 / 校验 / 实例化

```
load_template(name) → dict                # 加载 YAML（不存在返回 {}）
list_templates() → [{template_name, name, icon, description, inputs, node_count}]
validate_template(tpl, agent_names) → [errors]   # 结构/Agent/跳转目标/on_error 校验
build_graph(nodes, edges, start) → (graph, start)
instantiate(name, inputs, tenant_id, mode) → WorkflowJob
get_snapshot(job) → dict                  # 实例化时的模板快照（重跑始终用快照）
```

### expressions.py — 极简表达式（零模板引擎依赖）

- **三种上下文**：`$inputs.*` / `$steps.<node>.outputs.*` / `$ctx.*`（仅这三类路径根）
- **运算符**：`== != >= <= > < in not in and or not` + 括号 + 列表字面量
- **安全边界**：无函数调用、无属性访问、缺失值参与比较恒 False（不抛异常）
- API：`eval_expr(expr, scope)`、`render_string(template, scope)`（`{var}` 插值）、`build_scope(...)`

### tools.py — 工具节点注册表

```
register_tool(name, fn) / get_tool(name) / list_tools() / run_tool(name, inputs)
```

内置工具（确定性操作，均可 Mock 验证）：

| 工具 | 说明 |
|------|------|
| `validate_image` | base64 列表有效性校验 |
| `post_process_images` | 后处理（Phase 1 透传 + 状态标记） |
| `webhook_notify` | M4 出站通知（best-effort：失败返回结构化错误，不中断流程） |

### engine.py — 状态机（`WorkflowEngine`）

```
start(job) → Task                 # 异步启动（自动挡/手动挡统一入口）
control(job_id, action, step_node) → dict   # run_next | pause | resume | retry_step | skip_step | cancel
decide_human(job_id, action) → dict         # approve | retry | reject（SLA 超时归一化到此）
replicate_style(job_id, reference_images) → dict   # M3 一键复刻
```

- 节点执行：`agent` 复用 `BaseAgent.execute`（自动获得熔断/限流/重试/超时/成本）；`tool` 查注册表；`condition` 纯表达式；`human` 等待决策 + SLA 定时；`group_chat` 原样调用 `ChatEngine.run`；`end` 写终态
- 信号竞态防护：`wake_pending` + asyncio.Event 双保险；每步前从 SQLite 刷新步骤状态
- 回跳语义：分支/retry 跳回已执行节点 → 重置其声明顺序之后所有步骤 + `$ctx.retries += 1`

### job_store.py — SQLite 持久化 + 事件溯源

- 引擎：标准库 `sqlite3`，`data/workflow.db`（WAL），所有 IO 经 `asyncio.to_thread`；`PRAGMA user_version` 迁移
- 表：`jobs` / `steps` / `events` / `batches` / `batch_items`
- 关键语义：步骤输出**先落库后广播**（最多一次计费）；`reset_failed_items` 重置死信；`increment_batch` 用 SQL 原子递增避免并发读改写竞态
- 测试隔离：`JobStore(db_path=...)` 自定义路径，不污染生产库

### batch.py — 批量调度器（`BatchScheduler`）

```
submit(template_name, items, tenant_id, mode, max_concurrency) → batch
control(batch_id, action) → dict    # pause | resume | cancel | retry_failed
```

- 并发窗口（默认 3，上限 10）；每项最多自动重试 2 次 → 死信列表
- 断点续跑：重启后跳过 succeeded/failed 项，从剩余项继续
- 多 worker 唤醒：世代计数器（`wake_generation` + 常驻事件 + clear/双检）
- **M5a 报表**：`JobStore.get_batch_report(tenant_id)` 聚合总览/模板成功率/耗时直方图（5 桶）/失败原因 Top-5（冒号前缀归一化），条目耗时与成本经 `batch_items LEFT JOIN jobs` 联表取得，租户隔离；端点 `GET /api/workflows/batches/report`

## Skill 库（内置模板）

| 模板 | 卖点 |
|------|------|
| `white_bg_suite` 白底主图套装 | 验证→分析→[品类]→提示词→生图→审查→[人工]→合规→后处理 |
| `scene_suite` 场景图套装 | 场景加权提示词 + 生图×2 对比 |
| `compliance_hardened` 合规加固 | 保健品/化妆品强合规流水线 |
| `free_chat` 自由群聊 | group_chat 单节点，与旧 5 种协作模式等价 |
| `style_replicate` 风格复刻（M3） | 参考图 + 商品图双输入 |
| `light_approval` 轻审批演示（M4） | SLA 超时自动决策演示 |

## 修改指南

- **新增工作流模板** → `config/workflows/` 新建 YAML（节点引用已注册 Agent/工具，`validate_template` 会拦截非法引用）
- **新增工具节点能力** → `tools.py` 中 `register_tool(name, fn)`，签名 `async fn(inputs: dict) -> dict`
- **修改状态机语义** → `engine.py`（注意保持"步骤输出先落库后广播"）
- **调整批量并发/重试** → `batch.py` 的 `MAX_ITEM_RETRIES` 与 `submit(max_concurrency)`（上限 10）
- **完整 API/WS 契约与里程碑记录** → `docs/workflow-design.md` §7 / §11-13
