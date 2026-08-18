# E-Commerce Harness — 项目状态总览

> 最后更新：2026-08-16
> 本文档汇总项目**进度**、**现有计划**、**开发思路**与**关键决策对话**，作为团队交接与后续开发的单一事实来源（Single Source of Truth）。

---

## 1. 项目概览

**群聊式多智能体电商商品图生成系统** —— 7 个 AI Agent 在中心决策者（Coordinator）的协调下，像群聊一样协作完成"商品图生成"全流程任务。

| 维度 | 内容 |
|------|------|
| 版本 | v0.1.0 |
| 语言 / 运行时 | Python ≥ 3.12 |
| Web 框架 | FastAPI + Uvicorn（REST + WebSocket） |
| 前端 | React SPA（Vite） |
| 数据库 | 文件持久化（checkpoint JSONL / 审计日志 JSONL），无外部 DB 依赖 |
| 测试 | pytest（asyncio_mode=auto），25 个测试文件，203 个测试通过 |
| 部署 | Docker 多阶段构建（Node 前端 + Python 后端 + Nginx） |

---

## 2. 项目进度

### 2.1 里程碑时间线

按 git 提交顺序（7 次提交）：

| 提交 | 阶段 | 交付内容 |
|------|------|---------|
| `7fbb734` | **P0 核心** | 初版：群聊引擎 + 8 Agent + Provider 层 + CLI + 基础测试 |
| `3129f76` | **P3 功能** | A/B 测试框架、Agent 记忆召回、多租户隔离 |
| `ea04e81` | **第一优先级** | 结构化日志、API 鉴权、图片预处理、rembg 去背景集成 |
| `703cd49` | **第二优先级** | Agent 单测、重试/超时测试、前端状态机（loading/error/empty） |
| `24acb73` | **审计修复·CRITICAL** | 12 个 CRITICAL 问题修复 + 部分 HIGH + 26 个 API 测试 |
| `1b429b3` | **审计修复·HIGH** | 10 个 HIGH 问题修复（WS 鉴权、/health 加固、Provider 错误泄露、A/B 竞态、前端） |
| *（未提交）* | **审计修复·HIGH 收尾** | 剩余 4 个 HIGH：解压炸弹防护 + ab_testing/openai/image_preprocessor 补测（+52 测试）；顺带修复 ab_testing 2 个缺陷、加固时序脆弱测试 |
| *（未提交）* | **启动冒烟·CRITICAL 修复** | 启动前后端冒烟测试发现：`create_session` 因 `_FakeImage.base64_data` 恒为空 → **所有图片上传被 400 拒绝**（核心链路不可用，API 测试因断言 `in (200,201,400)` 容忍而漏网）。修复 + API 测试加固（有效图片上传必须 200）
| *（未提交）* | **前端重构·多页工作台** | 后端新增 4 端点（会话列表 / 设置汇总 / API Key 运行时持久化 / Agent 参数持久化）；前端从 2 页重构为侧边栏 7 页工作台：仪表盘（Provider/熔断/限流/租户总览）、会话列表+新建（5 种协作模式）、会话工作台（群聊直播按轮分组+产出物 tabs+图片网格+A/B 运行器）、Agent 配置页、系统设置页（10 项 API Key 管理）、审计日志、记忆库 |
| *（未提交）* | **模型配置能力** | 修复模型名被注册表丢弃的缺陷（`resolve()` 返回的 model 未注入 Agent → 永远用 Provider 默认模型）；新增 `POST /api/settings/models` 端点（能力→模型映射持久化+热重载），设置页提供可视化编辑器（默认/备选/兜底 + 按 Agent 覆盖），Agent 页显示当前生效模型 |
| *（未提交）* | **Workflow 编排层 M1** | 按 `docs/workflow-design.md` 实现：`src/workflow/` 六模块（YAML 模板 DSL / 极简表达式 / 工具注册表 / SQLite+事件溯源 / 状态机引擎 自动·手动挡+断点续跑+回跳重试+人工审批）；4 个内置模板（白底套装/场景套装/合规加固/自由群聊）；9 个 API 端点 + WS 事件流；前端工作流页（Skill 库画廊 + 画布 + 控制条）；54 个新测试 |
| *（未提交）* | **Workflow M2 批量调度** | `src/workflow/batch.py`（BatchScheduler：并发窗口/单项自动重试2次/死信列表/暂停恢复取消/断点续跑，多 worker 世代计数器唤醒 + SQL 原子计数）；4 个批量端点（JSON/CSV 创建、列表、详情、控制含 retry_failed）；前端批量任务页（进度条/控制/死信重跑/逐项详情）；13 个新测试 |
| *（未提交）* | **Workflow M3 风格复刻+插话** | 风格拆解员 Agent（插件化 class 注册，Agent 数 8→9）；style_replicate 模板（多图片输入分发）；`POST /api/workflows/jobs/{id}/replicate`（拆解→注入→重跑）；群聊插话（`POST /api/sessions/{id}/interject` + WS 双向 + 前端输入框）；15 个新测试 |

### 2.2 功能模块完成度

| 模块 | 状态 | 说明 |
|------|------|------|
| 群聊引擎（ChatEngine） | ✅ 完成 | 异步群聊循环、上下文管理、checkpoint 持久化、A/B 模式 |
| Agent 注册中心 | ✅ 完成 | 9 个 Agent：8 个内置 + 风格拆解员（插件化 class 注册，新增 Agent 只需 YAML） |
| Provider 层 | ✅ 完成 | 7 个：OpenAI/DeepSeek/Anthropic/Seedream/Qwen/FLUX/Mock，能力声明式解析 + 降级 |
| 可靠性模块（harness） | ✅ 完成 | 熔断器（3 态）、限流器（令牌桶）、重试（指数退避+抖动）、超时 |
| 多租户 | ✅ 完成 | TenantContext（ContextVar）+ 租户配额（rpm/tpm/预算/会话/存储） |
| Agent 记忆 | ✅ 完成 | 跨会话召回（JSONL 存储） |
| A/B 测试框架 | ✅ 完成 | 并行变体执行 + 多评审员评分（已修复共享 state 竞态） |
| 图片预处理 | ✅ 完成 | Pillow resize/压缩/EXIF/RGBA→RGB + rembg 去背景 |
| 鉴权 | ✅ 完成 | API Key（X-API-Key / Bearer）+ 白名单 + IP 限流 |
| 结构化日志 | ✅ 完成 | `get_logger()` + UTC 时间戳 + JSON adapter |
| REST API | ✅ 完成 | 会话 CRUD + 列表 + 决策 + **插话** + 消息 + 记忆 + 审计 + A/B 测试 + 设置（API Key / Agent / 模型映射持久化）+ 工作流（模板/作业/控制/决策/**复刻**/批量） |
| WebSocket | ✅ 完成 | 实时群聊流（**双向插话**）+ 工作流事件流（已补鉴权） |
| Workflow 编排层 | ✅ M1-M3 | `src/workflow/`：YAML 模板 DSL（6 类节点+group_chat）、极简表达式、SQLite+事件溯源、状态机（自动/手动挡、断点续跑、回跳重试、人工审批）、5 个内置模板；批量调度器（并发/死信/控制）；一键风格复刻（风格拆解员 + 注入重跑）；连接器（M4）待实施 |
| React 前端 | ✅ 完成 | 侧边栏 9 页工作台：仪表盘 / 会话（群聊插话）/ 工作流（画廊+画布+一键复刻）/ 批量任务 / Agent 配置 / 设置 / 审计 / 记忆 |
| CLI | ✅ 完成 | `python -m src.cli run` |
| Docker 部署 | ✅ 完成 | 多阶段构建 + 非 root 用户 + HEALTHCHECK + nginx |

### 2.3 审计修复进度

综合多智能体审计（ultracode）共发现 **65 个问题**，按严重度分级的修复进度：

| 级别 | 总数 | 已修复 | 剩余 | 修复率 |
|------|------|--------|------|--------|
| CRITICAL | 12 | 12 | **0** | 100% |
| HIGH | 17 | 17 | **0** | 100% |
| MEDIUM | 18 | 0 | **18** | 0% |
| LOW | 18 | 0 | **18** | 0% |

### 2.4 测试覆盖

- **36 个测试文件**，**356 个测试通过**（`pytest` 全量 Mock Mode）
- 覆盖范围：
  - `test_api/` — FastAPI 端点测试（含设置/会话列表/密钥持久化/模型映射/**群聊插话** 21 个新测试）
  - `test_agents/` — 9 个 Agent 单测（含风格拆解员插件化注册）
  - `test_chat/` — 引擎 / HITL / 反幻觉 / 模式
  - `test_harness/` — 熔断 / 限流 / 重试 / 超时 / 记忆 / 成本 / 集成 / 审计 / A-B / 图片预处理
  - `test_workflow/` — 表达式 / 模板 DSL / 引擎（分支/跳过/回跳/恢复）/ 手动挡控制 / 批量调度（并发/死信/控制/续跑）/ **风格复刻（注入/多图片输入/重跑）** / API（80 个测试）
  - `test_core/`、`test_providers/` — 模型 / Mock / 新 Provider / OpenAI

---

## 3. 项目现有计划

### 3.1 已完成优先级

| 优先级 | 内容 | 状态 |
|--------|------|------|
| P0 | 核心链路：群聊引擎 + 8 Agent + 3 Provider（OpenAI/DeepSeek/Mock） | ✅ |
| P1 | 上下文管理、批量调度器、审计日志、部署配置（Docker） | ✅ 部分 |
| P2 | 多 Provider（Anthropic/Seedream/Qwen/FLUX）、Web 前端 | ✅ |
| P3 | A/B 测试、Agent 记忆、多租户隔离 | ✅ |

### 3.2 剩余待办（按优先级）

**HIGH（4 个）—— ✅ 2026-08-15 全部完成：**

1. ~~图片解压炸弹防护~~ → ✅ `ImagePreprocessor` 新增 `max_total_pixels`（默认 64MP）头部尺寸校验（先于任何像素访问）、尺寸缩放前置到 RGB 转换之前、显式拦截 `DecompressionBombError`。测试：`tests/test_harness/test_image_preprocessor.py`（含手工构造无像素数据 PNG 的解压炸弹用例）
2. ~~`ab_testing.py` 单测缺失~~ → ✅ 新增 `tests/test_harness/test_ab_testing.py`（19 测试）。测试暴露并顺带修复 2 个缺陷：① 未注册 Agent 分支 `UnboundLocalError`；② 审查阶段门槛循环依赖（以 `avg_score>0` 作为送审条件，而评分是审查的产物）导致评审**从未执行**
3. ~~`openai.py` 单测缺失~~ → ✅ 新增 `tests/test_providers/test_openai.py`（15 测试，Mock HTTP 层覆盖 chat/chat_with_vision/generate/成本估算/JSON 提示注入/错误路径/negative_prompt 告警）
4. ~~`image_preprocessor.py` 单测缺失~~ → ✅ 新增 `tests/test_harness/test_image_preprocessor.py`（18 测试）

**MEDIUM（18 个）—— 文档与工程化：**

- 14 个模块缺少文档（`docs/modules/` 已有 8 个，`harness/ab_testing`、`harness/image_preprocessor`、`core/tenant`、`core/auth`、`core/logging_config` 等新增模块未记录）
- `.env.example` 未含 `ECOMM_API_KEY`（鉴权新增但未写入示例）
- CORS 白名单硬编码待收敛
- 若干魔法数字待提取为常量
- 其余见 `docs/code-review.md` 完整清单

**LOW（18 个）—— 类型标注与清理：**

- 类型标注补全（`Optional[...]`、`coro` 参数）
- 死代码清理、`datetime.utcnow()` 残留替换等
- 详见 `docs/code-review.md`

**架构待办（非阻塞）：**

- `auth.py` 位于 `src/core/`，依赖 FastAPI（违反"core 不依赖上层"的依赖方向）→ 建议迁至 `src/api/` 或新建中间件层
- 缺少 `src/api/` 分层：`main.py` 直接放在 `src/` 根目录，PRD D4 原设计有 `api/` 独立目录

---

## 4. 项目开发思路

### 4.1 核心架构原则

1. **群聊式协作，而非固定流水线** —— Coordinator（中心决策者）每轮读群聊历史 → 动态决定邀请哪个 Agent，不同场景邀请不同 Agent 组合（保健品邀合规审查员，化妆品邀品类分析员）
2. **Mock Mode First** —— 无 API Key 也能跑通全流程（Mock Provider 返回模板数据），先验证逻辑再接入真实 API
3. **模型与 Agent 解耦（能力声明 + 配置驱动）** —— Agent 只声明 `requires: ["vision"]`，具体用 GPT-4o 还是 DeepSeek 由 `config/models.yaml` 决定，切换模型不改 Agent 代码
4. **Provider 不锁定** —— 多 AI 服务商按可用性自动选择与降级（default → alternatives → fallback → mock）
5. **可靠性内建** —— 重试/超时/熔断随 Agent 一起交付，不依赖调用方处理
6. **Agent 可插拔** —— 新增 Agent 只需注册（名称 + 能力 + Schema）+ YAML 配置，Coordinator 自动感知

### 4.2 分层结构

```
src/
├── core/          # 数据模型 + 配置 + 消息类型 + 租户 + 鉴权 + 日志
├── providers/     # AI 服务商适配（7 个 Provider）
├── agents/        # Agent 注册中心 + 8 个 Agent 实现
├── chat/          # 群聊引擎（ChatEngine + Session + Message + Broadcaster）
├── harness/       # 可靠性模块（重试/超时/熔断/限流/审计/记忆/预处理/A-B）
├── storage/       # 持久化（checkpoint）
└── main.py        # FastAPI 入口
```

依赖方向：`core ← providers ← agents ← chat ← api/cli`

### 4.3 关键设计决策（摘自 PRD）

| 决策 | 内容 | 理由 |
|------|------|------|
| **D1** | ChatEngine 替代固定流水线 | 灵活适配不同场景，天然支持 Agent 扩展 |
| **D2** | Agent 注册中心 | 插件化架构，Agent 可独立开发测试 |
| **D3** | 广播式通信 | 信息透明，Coordinator 有完整上下文做决策 |
| **D5** | 能力声明 + 配置驱动 | 切换模型不改 Agent 代码 |
| **D6** | Agent 配置文件化（YAML） | Agent 管理不依赖代码改动 |
| **D7** | 前后端分离 + 分布式部署 | 后端无状态，`session_id` 驱动 |
| **D8** | Mock First + 真实 Provider | 先打通核心链路，Provider 扩展是机械性工作 |

### 4.4 可靠性机制

- **熔断器**：CLOSED → OPEN → HALF_OPEN 三态，HALF_OPEN 单次失败立即回 OPEN，并发探测计数防超限
- **限流器**：令牌桶，支持按 Provider/模型/token 计数，多租户独立配额
- **重试**：指数退避 + 抖动，`CancelledError` 不计入 Provider 故障
- **超时**：`execute_with_timeout`，非正超时抛 ValueError

---

## 5. 关键对话与决策记录

> 记录本次开发会话（会话 ID `91874d75-d4b2-436e-abd5-3aec9b16b498`）的用户指令 → 执行内容的决策链。

### 5.1 决策时间线

| # | 用户指令 | 执行内容 |
|---|---------|---------|
| 1 | "Read 交接文件" | 读取 handoff，了解项目当前状态 |
| 2 | "继续之前没搞完的" | 完成代码审查遗留修复 |
| 3 | "目前还有什么任务没做完" | 做差距分析（gap analysis） |
| 4 | "那就按你的优先级来" | 按优先级顺序修复代码审查问题 |
| 5 | "先验证" | 验证测试/后端/前端全部可运行 |
| 6 | "按你的优先级来" | git init + P3 功能（A/B、记忆、多租户） |
| 7 | "是否还有什么缺漏，功能模块上还有什么建议吗" | 全量差距分析 |
| 8 | "开始吧" | 实现第一优先级（日志、鉴权、图片预处理、rembg） |
| 9 | "继续" | 实现第二优先级（测试、前端状态） |
| 10 | "先重新检查整个项目"（/effort → ultracode） | 全面多智能体审计，发现 65 个问题 |
| 11 | "可以，按你的计划来执行" | 修复 12 个 CRITICAL 问题 |
| 12 | "继续修复" | 修复 10 个 HIGH 问题 |
| 13 | "先通读一下项目" + "开始吧" | 修复剩余 4 个 HIGH：解压炸弹防护 + ab_testing/openai/image_preprocessor 三个模块补测（新增 52 测试），顺带修复测试暴露的 2 个 `ab_testing.py` 缺陷 |
| 14 | "你现在可以先帮我启动项目吗" | 启动前后端（uvicorn 8000 + Vite 5173），端到端冒烟测试发现并修复 `create_session` 上传全拒缺陷（见里程碑表），验证 8 轮群聊全链路 completed |
| 15 | "前端太烂了，缺少功能模块" | 系统性补齐：后端 +4 管理端点，前端重构为 7 页工作台（仪表盘/会话/Agent/设置/审计/记忆），API Key 与 Agent 参数可视化配置 + 持久化 |
| 16 | "配置了 apikey 但没法设置 agent 用哪个模型" | 修复模型名被注册表丢弃（`resolve()` 结果未注入 Agent）+ 新增 `POST /api/settings/models` 端点 + 设置页模型映射编辑器 + Agent 页显示生效模型 |
| 17 | "实际应用是否还涉及工作流的内容" + "模仿 OiiOii.ai 的工作流" + "先把详细设计文档写出来" | 调研 OiiOii 2.0 产品范式（智能画布/Skill 库/一键复刻/自动·手动挡），输出 `docs/workflow-design.md`（模板 DSL + 状态机引擎 + 批量调度 + 画布前端），待评审后实施 |
| 18 | "按你的推荐来吧" | 实现 Workflow 编排层 M1：`src/workflow/` 六模块 + 4 模板 + 9 端点 + WS + 前端工作流页（画廊/画布），54 个新测试全绿 |
| 19 | "看 progress.md 继续" | 实现 M2 批量调度：BatchScheduler（并发/死信/控制/续跑）+ 4 批量端点 + 前端批量页，13 个新测试全绿 |
| 20 | "继续M3" | 实现 M3：风格拆解员（插件化注册）+ style_replicate 模板 + replicate API + 群聊插话（REST/WS 双向），15 个新测试全绿 |

### 5.2 关键决策点

**决策 1：信任优先级委派**
用户反复使用"按你的优先级来"，授权 AI 自主判断修复顺序。最终形成"CRITICAL → HIGH → MEDIUM → LOW"的清晰降级路径。

**决策 2：全面审计而非局部修补**
用户选择"先重新检查整个项目"（ultracode），触发多智能体并行审计，一次性暴露 12 CRITICAL + 17 HIGH + 18 MEDIUM + 18 LOW = 65 个问题，而非零散修复。

**决策 3：安全优先于功能**
审计揭示的安全问题（跨租户数据泄露、审计日志路径穿越、WS 鉴权绕过、Provider 错误泄露）被列为最高优先级，优先于新功能开发。

**决策 4：/health 最小化**
将 `/health` 从"暴露全部内部状态"简化为仅 `status` + `mock_mode`，详细信息移至 `/api/admin/status`，避免信息泄露。

**决策 5：真实 LLM 驱动 Coordinator**
`CoordinatorAgent.decide()` 从硬编码 Mock 改为 async 真实 LLM 调用，仅在非 Mock Provider 不可用时回退 Mock（附 warning 日志）。

**决策 6：TDD 揭示隐性缺陷**
补测试时发现 `ab_testing.py` 两个此前未暴露的缺陷（① 未注册 Agent 分支列表推导缺少 `for` 子句 → `UnboundLocalError`；② 审查阶段以 `avg_score > 0` 作为送审门槛，而评分是审查的产物 → 审查从未执行，winner 恒为 None），先跑红确认再修复。

**决策 7：冒烟测试优先于"能跑就行"**
用户要求启动项目后，用端到端冒烟测试（真实上传 → 群聊 → 产出物）验证，发现 `create_session` 上传链路全拒的 CRITICAL 缺陷——单测断言 `status in (200, 201, 400)` 的"宽容写法"掩盖了它。教训：**关键路径的成功断言必须严格**（有效输入 → 必须 200），不允许把失败状态当作可接受结果。

**附注**：仓库根目录的 `test_product.jpg` 是截断的 1x1 JPEG（Pillow 可读头部、加载像素报 "broken data stream"），会被预处理正确拒绝——用它做冒烟测试会得到 400，属预期防护行为，非缺陷。

**决策 8：前端补齐以"后端能力全覆盖"为目标**
用户指出前端只有单页、缺少 API Key/Agent 配置入口与工作监听。据此把后端每个能力模块映射为独立前端页面（仪表盘/会话/Agent/设置/审计/记忆），并新增 4 个后端管理端点支撑配置能力：API Key 经 `config/secrets.yaml` 持久化（gitignore + import 时注入 os.environ + 保存后重建 Provider 注册表），Agent 参数经 `config/agents/*.yaml` 持久化并热重载。前端从"创建表单"升级为完整工作台。

**决策 9：模型配置要"改得动、真生效"**
用户配置 API Key 后发现无法设置 Agent 模型。排查发现两层缺口：① `models.yaml` 虽驱动解析，但 `resolve()` 返回的模型名在 `registry._create_agent()` 中被丢弃，Agent 永远用 Provider 默认模型（配置形同虚设）；② 设置页只读展示。修复：注册表把模型名注入 `agent.model_name`，各 Agent 通过 `_model_kwargs()` 显式传给 Provider；新增 `POST /api/settings/models` 端点（部分更新语义 + 落盘 + 热重载）+ 可视化编辑器。

**决策 10：以 OiiOii 范式补工作流编排层（设计已定，待实施）**
用户提出 harness 要实际应用还需工作流能力，并点名借鉴 OiiOii.ai。调研其 2.0 产品范式（智能画布/Skill 库/一键拉片复刻/自动·手动挡）后，形成 `docs/workflow-design.md` 的核心决策：① ChatEngine 不重写，降级为工作流中的 `group_chat` 节点，与确定性 agent 节点并存；② 工作流是 YAML 数据（模板 DSL），六类节点 + 极简表达式，零模板引擎依赖；③ SQLite（stdlib）+ 事件溯源做持久化与断点续跑；④ 自动挡/手动挡两种执行模式；⑤ 批量调度与死信列表 Phase 2。里程碑 M1-M4，待用户评审后开工。

**决策 11：M1 实施中的四个关键取舍**
① **信号竞态**：手动挡控制与人工决策可能先于引擎进入等待态到达——用 `wake_pending` 标志 + asyncio.Event 双保险，并在每步执行前从 SQLite 刷新步骤状态（外部 skip 立即可见）；② **TestClient 限制**：Starlette TestClient 的即弃事件循环不会推进端点上 `create_task` 的后台任务（生产 uvicorn 无此问题），API 测试改为测试循环驱动引擎 + `to_thread` 转发 HTTP；③ **回跳语义**：分支/retry 跳回已执行节点时重置其声明顺序之后的所有步骤并递增 `$ctx.retries`，重跑始终用模板快照；④ **测试隔离**：`JobStore` 支持自定义 `db_path`，测试不污染 `data/workflow.db`。

**决策 12：M2 批量调度的三个工程问题**
① **多 worker 唤醒**：并发 worker 共享一个事件对象会被互相覆盖导致信号丢失——改用世代计数器（`wake_generation` + 常驻事件 + clear/双检）让所有等待者同时醒来；② **计数原子性**：`done/failed` 用 SQL 原子递增（`done=done+?`），状态与计数分离，避免并发读改写竞态；③ **测试节流**：全局限流器（60rpm）把批量测试拖慢到分钟级，`tests/test_workflow` 注入独立高额限流器隔离（限流器本身已有专门测试）。

**决策 13：M3 风格复刻与插话的三个设计点**
① **插件化注册**：`AgentMeta.class_name` + 注册中心动态导入——风格拆解员只新增 YAML 和 Agent 文件，不动核心代码，兑现 D2 的"新增 Agent 只需注册"；② **风格要素匹配的可测化**：Mock 模式下提示词生成员返回模板数据，无法断言生成图风格——用 spy Agent 捕获任务描述，断言风格文本确实注入提示词任务（验收标准的 Mock 化落地）；③ **插话语义**：用户指令作为 system 消息进群聊流，Coordinator（真实 LLM 模式）下一轮 `decide()` 读最近消息时自然感知，无需改引擎调度逻辑；Mock 模式插话仅记录展示。另：FastAPI/Starlette 的 UploadFile 类型不统一，multipart 文件字段按鸭子类型识别。

### 5.3 遗留的开放决策

- **依赖方向**：`auth.py` 放 `core/` 还是迁至独立中间件层？（PRD D4 的"core 不依赖上层"原则 vs 现实实现）
- **`src/api/` 分层**：是否按 PRD 原设计将 `main.py` 迁入 `api/` 目录？
- **`ABTestRunner` 的 model_override 语义**：`_run_variant` 目前以提示词注入方式传递模型覆盖（避免共享 state 竞态），真实 Provider 下并不会真正切换模型。真实模型对比需在 `BaseAgent.execute` 增加 model 参数（接口级改动），暂缓。
- **前端构建产物体积**：单 chunk 589KB（recharts 占大头），可用 `manualChunks`/动态 import 拆包优化。
- **设置页敏感操作**：`/api/settings/*` 写操作在 `ECOMM_API_KEY` 未配置时开放（开发模式），生产环境由 AuthMiddleware 全局保护。
- **Workflow M4 待实施**：审批 SLA 超时自动决策 + webhook/连接器（平台回传）+ 模板导入导出（见 `docs/workflow-design.md` §11）。
- **MEDIUM/LOW 是否继续修**：18 MEDIUM（文档/工程化）+ 18 LOW（类型/清理）尚未启动，等待用户确认优先级。

---

## 6. 快速验证命令

```bash
# 全量测试（Mock Mode）
pytest

# 前端构建
cd frontend && npm run build

# 后端启动（Mock Mode）
uvicorn src.main:app --reload --port 8000

# Docker 部署
docker-compose -f deploy/docker-compose.yml up -d
```

---

## 附：文档索引

| 文档 | 路径 | 内容 |
|------|------|------|
| 产品需求文档 | `docs/prd.md` | 完整 PRD（问题、方案、决策 D1-D8、数据模型、API 契约） |
| 代码审查报告 | `docs/code-review.md` | 65 个审计问题的完整清单与修复优先级 |
| 模块文档 | `docs/modules/*.md` | 8 个核心模块的详细文档 |
| **Workflow 设计** | `docs/workflow-design.md` | 编排层设计（模板 DSL / 状态机 / 批量 / 画布），借鉴 OiiOii 范式，**待评审后实施** |
| 本状态总览 | `docs/progress.md` | 进度 / 计划 / 思路 / 决策（本文档） |
