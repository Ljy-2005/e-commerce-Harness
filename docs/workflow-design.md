# Workflow 编排层设计文档

> 版本：v0.2（Phase 1 已实现）
> 日期：2026-08-16
> 状态：M1 已交付（见 §13 实施记录），M2-M4 待实施
> 参考：OiiOii 2.0 的产品范式（智能画布 / Skill 库 / 一键拉片复刻 / 7-Agent 团队 / 自动挡·手动挡）

---

## 1. 背景与目标

### 1.1 现状

E-Commerce Harness 目前是「单次会话的智能群聊引擎 + 可靠性壳」：

- 8 个 Agent 由 Coordinator 在群聊循环中动态调度（5 种硬编码协作模式）
- 会话状态为内存 dict + JSONL checkpoint，无步骤级状态机
- 无批量处理、无确定性的流程编排、无人工审批闭环、无对外集成

**问题**：真实业务（如"半天完成 100 个商品上架准备"的 PRD 用户故事）需要的是**可编排、可恢复、可审批、可批量**的生产工作流，而非每次一个会话的黑盒群聊。

### 1.2 借鉴 OiiOii 的产品范式

| OiiOii 概念 | 本项目对应物 | 状态 |
|------------|-------------|------|
| 智能画布（节点化工作流） | 商品图工作流画布（见 §8 前端） | ✅ 已建（M1） |
| Skill 库（预制技能模板） | 工作流模板库 `config/workflows/*.yaml` | ✅ 已建（M1/M3，5 个模板） |
| 一键拉片复刻 | 一键风格复刻（参考图 → 拆解 → 融合提示词） | ✅ 已建（M3） |
| 7-Agent 团队 + 代理对话 | 已有 8 Agent + 群聊直播；用户插话指挥 | ✅ 插话已建（M3，WS 双向 + REST） |
| 自动挡 / 手动挡 | 自动执行 / 逐步执行（每步暂停等指令） | ✅ 已建（M1） |

### 1.3 设计原则（沿用项目既有决策）

1. **ChatEngine 不重写，被"包裹"** —— 群聊降级为工作流中的一类节点，编排层管确定性，群聊层管智能性（呼应 PRD D1「群聊式协作，而非固定流水线」——两者并存，模板决定用哪种）
2. **Mock Mode First** —— 所有工作流功能在 Mock 模式下可完整验证
3. **配置驱动** —— 工作流是 YAML 数据，新增模板不改代码（呼应 D6）
4. **Agent 可插拔** —— 节点按名字引用 Agent，新增 Agent 自动可被模板使用（呼应 D2）
5. **可靠性内建** —— 复用现有 harness 的熔断/重试/超时/限流/审计，不重复造
6. **零外部依赖** —— 持久化用标准库 SQLite（经 `asyncio.to_thread` 包装），不引入 Redis/Celery
7. **多租户** —— job 归属租户，配额/预算沿用现有租户体系

---

## 2. 总体架构

```
┌──────────────────────────────────────────────────────────────┐
│ L3 模板层   config/workflows/*.yaml   Skill 库（画布的"图纸"）     │
│            输入 Schema + 节点 DAG + 条件分支 + 人工审批 + 输出     │
├──────────────────────────────────────────────────────────────┤
│ L2 编排层   src/workflow/                                     │
│   engine.py     节点状态机（自动挡/手动挡/单步调试、节点重试、断点续跑）│
│   job_store.py  SQLite 持久化（job/step/事件，事件溯源）          │
│   batch.py      批量调度器（队列/并发/配额/失败隔离/汇总）          │
│   templates.py  模板加载/校验/实例化                            │
│   expressions.py 极简表达式（$steps.x.outputs.y 路径 + 规则匹配）  │
│   tools.py      工具节点注册表（预处理/后处理/webhook…）          │
├──────────────────────────────────────────────────────────────┤
│ L1 现有层   ChatEngine + 8 Agent + Provider + harness + 审计     │
│            （基本不动；Agent 节点直接复用 BaseAgent.execute）      │
└──────────────────────────────────────────────────────────────┘
```

依赖方向：`core/providers/agents/chat ← workflow ← api/cli`。workflow 是 `chat` 的上层，不反向依赖。

---

## 3. 数据模型

### 3.1 工作流模板（YAML DSL）

```yaml
# config/workflows/white_bg_suite.yaml — 白底主图套装（示例）
name: 白底主图套装
version: "1.0.0"
description: 淘宝白底主图 + 场景图 + 社交种草图完整套装
category: 商品图                    # Skill 库分组
icon: "🛍️"
inputs:                             # 实例化时由前端渲染表单
  - key: product_images
    label: 商品图片
    type: images                    # 1-10 张
    required: true
  - key: platform
    label: 目标平台
    type: select
    options: [taobao, amazon, xiaohongshu, douyin, jd, shopify]
    default: taobao
  - key: category_hint
    label: 品类提示
    type: text
    required: false

nodes:
  validate:                         # 工具节点：确定性操作
    type: tool
    tool: validate_image
    inputs: { images: $inputs.product_images }

  analyze:                          # 智能节点：复用已注册 Agent
    type: agent
    agent: 商品分析员
    task: 分析上传的商品图片
    inputs: { images: $inputs.product_images }

  category:                         # 条件跳过（只有敏感品类才跑）
    type: agent
    agent: 品类专项分析员
    when: $steps.analyze.outputs.category in [保健品, 化妆品, 食品, 3C数码]

  prompt:
    type: agent
    agent: 提示词生成员
    task: 基于分析结果生成 {platform} 平台提示词   # 支持 {var} 插值

  generate:
    type: agent
    agent: 生图员

  review:
    type: agent
    agent: 审查员

  branch:                           # 条件节点：分流
    type: condition
    rules:
      - when: $steps.review.outputs.overall_score >= 75
        goto: compliance
      - when: $steps.review.outputs.verdict == retry and $ctx.retries < 2
        goto: prompt
      - default: human_review

  human_review:                     # 人工审批节点
    type: human
    prompt: 审查评分未达标，请人工判定（approve / retry / reject）
    sla_minutes: 60
    on_sla_timeout: keep_waiting    # keep_waiting | auto_approve | auto_reject
    routes:
      approve: compliance
      retry: prompt
      reject: failed_end

  compliance:
    type: agent
    agent: 合规审查员
    when: $inputs.category_hint == 保健品 or $steps.analyze.outputs.category == 保健品

  post_process:
    type: tool
    tool: post_process_images
    inputs: { images: $steps.generate.outputs.images }

  success_end:                      # 结束节点
    type: end
    status: completed

  failed_end:
    type: end
    status: failed

edges:                              # 默认顺序边（无 when 时按声明顺序）
  - [validate, analyze]
  - [analyze, category]
  - [category, prompt]
  - [prompt, generate]
  - [generate, review]
  - [review, branch]
  - [compliance, post_process]
  - [post_process, success_end]
```

**DSL 说明**：

- `nodes` 用名字互相引用；`edges` 给默认顺序，`goto`/`routes` 覆盖跳转
- 六类节点：`tool` / `agent` / `condition` / `human` / `subworkflow` / `end`
- `$inputs.*`（实例化输入）、`$steps.<node>.outputs.*`（上游产出）、`$ctx.*`（运行期变量如 retries）是表达式仅有的三种上下文；表达式语言只支持：路径取值、`== != >= <= > < in not in and or not`、数字/字符串/列表字面量——**不引入模板引擎**（安全 + 零依赖）
- `when`（节点跳过条件）、`task` 中的 `{var}` 插值（仅 inputs/steps 路径）

### 3.2 运行期模型（Pydantic）

```python
class JobStatus(str, Enum):
    CREATED → QUEUED → RUNNING → (WAITING_HUMAN) → COMPLETED | FAILED | CANCELLED | PARTIAL

class StepStatus(str, Enum):
    PENDING → RUNNING → SUCCEEDED | FAILED | SKIPPED | WAITING_HUMAN

class WorkflowJob(BaseModel):
    job_id: str              # uuid hex
    template_name: str
    version: str             # 模板版本（模板升级不影响已实例化的 job）
    tenant_id: str
    status: JobStatus
    mode: Literal["auto", "manual"]   # 自动挡 / 手动挡
    inputs: dict             # 实例化时的输入快照
    context: dict            # $ctx（retries、memory 注入等运行期变量）
    cost_so_far: float
    created_at / updated_at: datetime

class StepRecord(BaseModel):
    step_id: str
    job_id: str
    node: str                # 节点名
    type: str                # tool/agent/condition/human/subworkflow/end
    status: StepStatus
    attempt: int             # 第几次尝试（节点级重试）
    inputs: dict             # 快照（幂等重放用）
    outputs: dict
    error: str = ""
    elapsed_ms: float = 0
    cost_usd: float = 0
    started_at / finished_at: datetime | None
```

### 3.3 事件（事件溯源）

所有状态变更追加不可变事件流（写入 SQLite 的 `events` 表 + 复用现有 `AuditLogger` 记一行审计）：

```
job_created / job_queued / job_started / step_started / step_succeeded /
step_failed / step_retrying / step_skipped / human_waiting / human_decided /
job_completed / job_failed / job_cancelled
```

前端 WS 订阅事件流实现画布实时高亮；崩溃恢复 = 重放事件到最后一个持久化步骤（步骤输出先落库、后广播，保证**最多一次计费**语义）。

---

## 4. 编排引擎（`src/workflow/engine.py`）

### 4.1 执行模型

```
run(job, mode):
  1. 加载模板快照 → 构建节点图（校验 DAG：无环、跳转目标存在、输入路径合法）
  2. 定位当前步骤（新 job = 起始节点；恢复 = 最后 SUCCEEDED 的下一个）
  3. 循环：
     a. 评估节点 when（不满足 → SKIPPED，沿默认边前进）
     b. 执行节点（见 4.2）
     c. 结果落库 → 广播事件 → 按 goto/edges/routes 前进
     d. mode == manual → 本步成功后暂停，等 control API 指令
  4. 到达 end 节点 → 写终态；中途 WAITING_HUMAN → 暂停等决策 API
```

### 4.2 节点执行语义

| 节点 | 执行 |
|------|------|
| `agent` | 构造轻量 session（`{task, artifacts}` 复用现有结构）→ 调用 `BaseAgent.execute(task_brief, session)`。**自动获得**熔断/限流/重试/超时/成本追踪/审计；输出写入 `$steps.<node>.outputs` |
| `tool` | 从 `tools.py` 注册表查找（`validate_image`、`post_process_images`、`webhook_notify` 等），同步/异步纯函数，带超时 |
| `condition` | 纯表达式求值 → 匹配 rules 返回 goto；`default` 兜底 |
| `human` | 置 WAITING_HUMAN + 广播；决策 API 写入事件后按 routes 恢复（重试路径 `$ctx.retries += 1`）；SLA 超时按 `on_sla_timeout` 自动决策（Phase 2 实现，先 keep_waiting） |
| `subworkflow` | 以当前 `$steps/<node>` 为隔离命名空间实例化子模板（Skill 组合，Phase 2） |
| `end` | 写终态 |

### 4.3 节点级重试与失败

- 节点 `max_retries`（模板可配，默认 1）复用 `with_retry` 的指数退避+抖动；失败 N 次 → `step_failed` → 沿 `on_error` 边（模板可声明 `on_error: failed_end` 或进入人工节点）
- **重跑语义**：`retry_step` 控制指令 = 用 `StepRecord.inputs` 快照重放该步（输出不覆盖历史，事件追加 `step_retrying`）——保证可审计、可回滚

### 4.4 断点续跑与幂等

- JobStore 每步完成即事务性落库（step 状态 + outputs + cost）
- 进程崩溃 → 启动时扫描 RUNNING/WAITING 的 job → 重放事件 → 从下一待执行步骤继续
- 幂等：`step_succeeded` 只写一次；重复恢复不会重复调用 Agent/重复计费

### 4.5 群聊节点（保留现有能力）

新增第 7 类节点 `group_chat`（Phase 1 即支持）：

```yaml
  creative:
    type: group_chat
    max_turns: 15
    collaboration_mode: serial     # serial/ab_generate/debate/vote/ab_test
    outputs: { artifacts: $result.artifacts }
```

内部就是现有 `ChatEngine.run(session)` 原样调用（Coordinator 编排 + 群聊直播照旧）。这样**「自由群聊」模板**与**「确定性流水线」模板**并存：后者默认用 agent 节点+条件分支（更可控、更便宜），前者用于开放场景。

---

## 5. 批量调度器（`src/workflow/batch.py`，Phase 2 实现）

```
BatchJob = 商品列表 → 逐个实例化 WorkflowJob → 队列 → 并发窗口（默认 3，租户配额感知）→ 逐项执行 → 汇总
```

- **输入**：CSV / 商品图片目录 / API JSON（`POST /api/workflows/batches`）
- **并发控制**：窗口大小 × 租户 rpm/tpm/预算（调用现有 RateLimiter + 预算检查）
- **失败隔离**：单项失败不影响其他项；每项最多自动重试 2 次 → 进入**死信列表**（页面可单独重跑）
- **控制**：暂停/恢复/取消整个批次；进度 = 完成数/总数 + 实时失败数
- **汇总报表**：总耗时/总成本/成功率/每项耗时分布（审计日志聚合）

---

## 6. 持久化（`src/workflow/job_store.py`）

- **引擎**：标准库 `sqlite3`，文件 `data/workflow.db`（WAL 模式）；所有 IO 经 `asyncio.to_thread`
- **表**：

```sql
jobs(job_id, template_name, version, tenant_id, status, mode, inputs_json,
     context_json, cost_so_far, created_at, updated_at)
steps(step_id, job_id, node, type, status, attempt, inputs_json, outputs_json,
      error, elapsed_ms, cost_usd, started_at, finished_at)
events(seq, job_id, event, payload_json, created_at)
batches(batch_id, template_name, tenant_id, status, total, done, failed,
        created_at, updated_at)
```

- **迁移策略**：`PRAGMA user_version` 递增迁移（v0 起步，预留 Phase 3 的审批表/连接器表）
- 与现有 `data/checkpoints/*.json`（会话级）共存：workflow job 内部的 group_chat 节点仍写会话 checkpoint，双保险

---

## 7. API 契约

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/workflows/templates` | Skill 库列表（name/description/category/inputs Schema） |
| `POST` | `/api/workflows/templates/{name}/instantiate` | 实例化 → 创建 job（body = inputs） |
| `GET` | `/api/workflows/jobs` | 作业列表（租户隔离，分页） |
| `GET` | `/api/workflows/jobs/{id}` | 作业详情（含全部 steps + 事件流） |
| `POST` | `/api/workflows/jobs/{id}/control` | `{action: run_next\|pause\|resume\|retry_step\|skip_step\|cancel, step_id?}` 手动挡控制 |
| `POST` | `/api/workflows/jobs/{id}/decision` | 人工节点决策 `{action: approve\|retry\|reject, comment?}` |
| `POST` | `/api/workflows/jobs/{id}/replicate` | 一键风格复刻：上传参考图 → 拆解 → 融合提示词重跑（Phase 2） |
| `POST` | `/api/workflows/batches` | 创建批量任务 |
| `GET` | `/api/workflows/batches/{id}` | 批量进度 |
| `WS` | `/ws/workflows/jobs/{id}` | 实时事件流（画布高亮 + 节点日志） |

鉴权：全部走现有 `AuthMiddleware`（`ECOMM_API_KEY` 配置后自动保护）；租户经 `X-Tenant-ID`。

---

## 8. 前端（新增「工作流」页）

```
/workflows         Skill 库画廊（卡片：icon/名称/描述/输入表单）→ 实例化 → 画布
/workflows/:jobId  画布工作台
/batches           批量任务（Phase 2）
```

### 8.1 画布工作台

- **节点图**：自绘 SVG/绝对定位（不引入 reactflow 等重依赖），左→右拓扑；节点颜色 = 状态（灰=待执行/蓝=运行中/绿=成功/红=失败/黄=待人工/虚线=跳过）
- **节点详情**：点击节点 → 右栏显示 inputs/outputs JSON、耗时、成本、错误、重试按钮（`retry_step`）
- **手动挡控制条**：⏭ 运行到下一步 / ⏸ 暂停 / ▶ 继续 / 🔁 重跑本步 / ⏭️ 跳过本步
- **人工审批卡**：approve/retry/reject + 备注（复用 HITLPanel 样式）
- **实时**：WS 事件流驱动高亮与日志滚动
- 画布顶部：模板名、模式徽章（自动挡/手动挡）、总耗时/总成本、job 状态

### 8.2 批量页（Phase 2）

进度条（完成/失败/死信）、失败项列表（点击进入单 job 画布）、批次控制（暂停/恢复/取消）、汇总报表。

### 8.3 导航变更

侧边栏新增「🧩 工作流」入口；会话页保留不动（群聊模板的 job 内可跳转到对应 session 查看直播）。

---

## 9. 内置 Skill 库（Phase 1 交付 3 个模板）

| 模板 | 节点链 | 卖点 |
|------|--------|------|
| `white_bg_suite` 白底主图套装 | 验证→分析→[品类]→提示词→生图→审查→[人工]→合规→后处理 | 淘宝/亚马逊标准主图流水线 |
| `scene_suite` 场景图套装 | 分析→提示词(场景加权)→生图×2→对比审查(A/B 生成模式) | 生活方式场景图 |
| `compliance_hardened` 合规加固 | 验证→分析→品类→提示词→生图→审查→合规→人工终审 | 保健品/化妆品强合规品类 |
| `free_chat` 自由群聊（迁移现有模式） | group_chat 单节点 | 与现有 5 种协作模式完全等价，保证向后兼容 |

---

## 10. 测试策略

| 层 | 用例 |
|----|------|
| DSL | 模板加载/校验（非法 goto、环检测、未知 agent/tool 报错） |
| 引擎 | 自动挡全链路；条件分支三分支；when 跳过；human 节点三决策 + 重试计数；节点重试/on_error；手动挡逐步控制 |
| 恢复 | 中途杀进程 → 重建 store → 从断点续跑（Mock 下确定性验证） |
| 批量 | 10 项 1 失败 → 隔离 + 死信 + 重跑（Phase 2） |
| API | 上表全部端点（TestClient + Mock） |
| 前端 | build 校验 + 手动冒烟（画布渲染/控制条/WS 高亮） |

目标：全量 `pytest` 保持 Mock Mode 绿色；现有 274 个测试不回归。

---

## 11. 里程碑

| 阶段 | 交付 | 验收 |
|------|------|------|
| **M1（Phase 1）** | DSL + engine + job_store + expressions + tools（validate/post_process）+ 4 模板 + 全部 API + 画布页（自动/手动挡） | ✅ 已交付：54 个工作流测试通过，Mock 模式端到端跑通 4 模板，崩溃续跑/手动挡/人工审批/回跳重试均覆盖 |
| **M2（Phase 2a）** | batch.py + 批量页 + 死信重跑 | ✅ 已交付：BatchScheduler（并发窗口/单项自动重试 2 次/死信/暂停恢复取消/断点续跑）、4 个批量端点（JSON+CSV）、前端批量页（进度/控制/死信重跑）、13 个新测试 |
| **M3（Phase 2b）** | 一键风格复刻（新增「风格拆解员」Agent，YAML 注册零核心改动）+ 群聊插话（WS 双向指令） | ✅ 已交付：风格拆解员（插件化 class 注册，9 Agent）+ style_replicate 模板（双图片输入）+ replicate API + 引擎风格注入重跑；群聊插话（REST + WS 双向 + 前端输入框）；15 个新测试，风格要素匹配由 spy 测试验证 |
| **M4（Phase 3）** | 审批 SLA 超时自动决策 + webhook/连接器接口 + 模板导入导出 | 外部回调触发状态流转 |

---

## 12. 风险与开放问题

1. **SQLite vs 继续 JSONL**：workflow 需要列表/分页/步骤查询，SQLite（stdlib）更合适；确认无"零 DB 依赖"洁癖冲突（D7 原计划本就是 SQLite→PG）
2. **表达式语言边界**：极简表达式够不够？（Phase 2 复杂分支可考虑子条件节点拆分，而非引入 jinja2）
3. **group_chat 与 agent 节点并存**：同一模板混用会双写审计，需确认成本口径（会话成本 + job 成本如何汇总展示）
4. **模板版本化**：job 快照模板版本，模板升级不影响存量 job——但「重跑」用新还是旧版本？（草案：用旧版，显式"升级重跑"另行提供）
5. **与现有 `/api/sessions` 的关系**：是否把会话创建 API 逐步收编为 `free_chat` 模板实例化？（草案：并存，不破坏现有前端）
6. **SLA 定时器**：进程内 asyncio 定时即可（无分布式），单进程部署假设明确写死

---

## 附：文件清单（计划）

```
docs/workflow-design.md          ← 本文档
config/workflows/*.yaml          ← 模板（Skill 库）
src/workflow/__init__.py
src/workflow/models.py           ← WorkflowJob/StepRecord/JobStatus/StepStatus
src/workflow/templates.py        ← 加载/校验/实例化
src/workflow/expressions.py      ← 极简表达式求值
src/workflow/tools.py            ← 工具节点注册表
src/workflow/engine.py           ← 状态机
src/workflow/job_store.py        ← SQLite 持久化
src/workflow/batch.py            ← Phase 2
tests/test_workflow/*            ← DSL/引擎/恢复/批量/API 测试
frontend/src/pages/Workflows.jsx ← Skill 库画廊
frontend/src/pages/WorkflowJob.jsx ← 画布工作台
frontend/src/pages/Batches.jsx   ← Phase 2
```

---

## 13. 实施记录（M1 偏差与决策）

> 本节记录 Phase 1 实现与本文档 §1-§12 草案的偏差，作为后续阶段的输入。

1. **`JobStatus.PAUSED` 新增**：手动挡逐段执行需要"暂停"状态（草案未列），已加入状态机与前端徽章。
2. **`group_chat` 节点随 M1 交付**（草案原列 Phase 1 即支持，已实现）：内部复用 `ChatEngine.run`，`free_chat` 模板与现有 5 种会话协作模式完全等价。
3. **回跳语义**：条件分支/人工 retry 跳回已执行节点时，重置该节点及其声明顺序之后的所有步骤（PENDING + 清空输出），并递增 `$ctx.retries`；重跑始终用实例化时的模板快照（`job.context["_snapshot"]`）。
4. **信号竞态防护**：手动挡控制（run_next/resume/cancel）与人工决策可能先于引擎进入等待态到达，使用 `wake_pending` 标志 + 事件双保险；引擎每步前从 SQLite 刷新步骤状态，使外部 `skip_step` 立即可见。
5. **TestClient 限制**：Starlette TestClient 每个请求使用即弃的事件循环，端点上 `asyncio.create_task` 的后台任务在其中不会被推进（生产 uvicorn 循环常驻无此问题）。API 测试因此用测试自身事件循环驱动引擎 + `asyncio.to_thread` 转发 HTTP 调用。
6. **极简表达式扩充**：支持裸词字符串（`verdict == retry`）、`None` 安全比较（缺失值恒 False）、列表字面量、`not in`。
7. **测试隔离**：`JobStore` 支持自定义 `db_path`；工作流 API 测试用临时 SQLite + monkeypatch 全局 store/engine，不污染 `data/workflow.db`。

### 13.1 M2 实施补充

1. **多 worker 唤醒**：批处理多个 worker 并发等待控制信号，单事件对象会被覆盖 → 改用**世代计数器**模式（每次信号 `wake_generation += 1` + 常驻事件 + clear/双检），所有等待者同时醒来。
2. **计数原子性**：`done/failed` 计数用 SQL `done=done+?` 原子递增，避免并发 worker 的读改写竞态；批次状态与计数分离更新。
3. **限流测试隔离**：全局限流器（60rpm）会把批量测试节流到分钟级，`tests/test_workflow` 注入独立高额限流器（限流本身有专门测试）。
4. **CSV 无图片列**：CSV 批次注入合法 base64 占位符 `Y3N2`（b64("csv")），Mock 模式完整可用；真实图片请用 JSON 传 base64（连接器 URL 列留待 Phase 3）。
5. **单项重试计数语义**：每轮执行（含 retry_failed 重跑）尝试计数从 1 重新累计（1 次初始 + 2 次自动重试 = 最多 3 次/轮）。

### 13.2 M3 实施补充

1. **插件化 Agent 注册**：`AgentMeta` 新增 `class_name` 字段，注册中心对 `_MAP` 未命中的名称按 YAML 的 `class: 完整类路径` 动态导入——风格拆解员因此零核心代码改动（仅新增 YAML + Agent 文件）。
2. **多图片输入分发**：模板可声明多个 `type: images` 输入（参考图/商品图），实例化端点按 multipart 字段名分发（`reference_images=`、`product_images=`），`files=` 保持旧约定回退。注意 FastAPI/Starlette 的 UploadFile 类型不统一，文件字段用鸭子类型识别。
3. **风格要素匹配验证**：Mock 模式下提示词生成员返回模板数据，无法断言生成图风格——改用 spy Agent 捕获任务描述，断言「[风格复刻]」要素文本确实注入（验收标准"风格要素匹配"在 Mock 下的可测化）。
4. **replicate 重跑语义**：拆解结果存入 `job.context["_style_breakdown"]`，从 prompt 节点 `retry_step` 重跑；引擎在 `_run_agent` 中对提示词生成员的任务注入风格文本，重跑即生效。
