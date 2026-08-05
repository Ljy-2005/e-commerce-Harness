# E-Commerce Harness — 产品需求文档 (PRD)

## Problem Statement

电商卖家需要为每个商品制作高质量的商品展示图。中小商家面临：

- **人力成本高：** 商品分析 → 撰写生图提示词 → 调用 AI 生图 → 审查质量 → 合规检查，每个步骤需要不同技能
- **工具碎片化：** 生图 AI 分散在多个平台（Seedream、DALL-E、Midjourney、FLUX），各有不同提示词格式
- **质量不可控：** 没有系统化审查机制，生成的图片经常不符合平台规范
- **不同品类需求不同：** 保健品要查合规红线，化妆品要看肤质适配，3C 要看产品细节——固定流程无法应对
- **没有可靠性保障：** API 网络抖动、限流、服务故障时，简单脚本直接崩溃

## Solution

构建一个 **群聊式多智能体协作系统**：

- **中心决策者（Coordinator）** 是 LLM 驱动的"群主"，读取任务和群聊历史，动态决定邀请哪些 Agent、分配什么任务
- **Agent 池** 中有不同专长的 Agent，中心决策者按需邀请，不同场景可以邀请不同的 Agent 组合
- **广播模式**：所有 Agent 的发言对全体可见，中心决策者基于完整上下文做决策
- **Agent 可插拔**：新增 Agent 只需注册（名称 + 能力需求 + 输入/输出 Schema），中心决策者自动感知
- **模型与 Agent 解耦**：Agent 只声明需要什么能力（如 `requires: ["vision"]`），具体用 GPT-4o 还是 Claude 由配置文件决定，切换模型不改 Agent 代码

```
用户提交任务
    │
    ▼
┌─────────────────────────────────────┐
│            群聊空间                   │
│                                      │
│  中心决策者 ←────────────────────┐    │
│  (Coordinator)                   │    │
│    │                             │    │
│    │ 读群聊历史，决定邀请谁        │    │
│    ▼                             │    │
│  "邀请 @商品分析员，分析这张图"    │    │
│    │                             │    │
│    ▼                             │    │
│  商品分析员 发言 ──────────────────┘    │
│    │                                   │
│    ▼                                   │
│  中心决策者 读取分析结果                │
│    │                                   │
│    ▼                                   │
│  "邀请 @提示词生成员，生成淘宝风格提示词"│
│    │                                   │
│    ▼                                   │
│  提示词生成员 发言 ─────────────────┘    │
│    │                                   │
│    ▼                                   │
│  中心决策者 读取提示词，判断到位         │
│    │                                   │
│    ▼                                   │
│  "邀请 @生图员，用 Seedream 出图"       │
│    │                                   │
│    ... 继续直到中心决策者宣布 DONE ...   │
│                                      │
└─────────────────────────────────────┘
```

**核心原则：**

- **Mock Mode First：** 无 API Key 也能跑通（Mock Provider 返回模板数据）
- **Provider 不锁定：** 支持多个 AI 服务商，按可用性自动选择和降级
- **可靠性内建：** 重试/超时/熔断随 Agent 一起交付
- **场景驱动：** 保健品会邀请合规审查员，化妆品会邀请品类专项分析员—不同场景不同 Agent 组合

## User Stories

### 核心用户

1. As a 淘宝卖家, I want to 上传手机拍的商品照片，自动得到白底主图+场景图+社交种草图的完整套装, so that 我可以直接上架商品
2. As a 跨境电商运营, I want to 同一张商品图生成适配 Amazon/Shopee/淘宝的不同风格图片, so that 不需要为每个平台重新做图
3. As a 代运营公司员工, I want to 批量上传整个商品目录自动生成所有图片, so that 半天内完成 100 个商品的上架准备
4. As a 保健品商家, I want to 系统自动邀请合规审查员检查广告法风险, so that 不会因不合规图片被平台下架或罚款
5. As a 化妆品商家, I want to 系统自动邀请品类专项分析员识别质地/色号/适用肤质, so that 生成的图片能准确展示产品特征

### 功能交互

6. As a 开发者, I want to 在没有 API Key 的情况下跑通完整流程, so that 可以先验证系统逻辑再决定购买哪些 API
7. As a 运维人员, I want to 某 AI 服务商故障时自动切换到备选服务商, so that 不因单一服务中断导致业务停滞
8. As a 产品经理, I want to 查看每次生成的成本明细（哪个 Agent 用了多少 token）, so that 可以评估和优化 AI 成本

### 技术用户

9. As a 后端开发者, I want to 通过 REST API 提交任务并轮询进度, so that 可以把生图能力集成到公司后台
10. As a 命令行用户, I want to 通过 CLI 一键生成图片, so that 可以快速验证或在脚本中批量调用
11. As a 系统扩展者, I want to 新增一个 Agent 只需注册（名称+能力描述+Schema）不需要改核心代码, so that 可以快速适配新的品类或任务类型
12. As a 开源贡献者, I want to 新增一个 Provider 只需实现接口+注册, so that 可以贡献对新 AI 服务商的支持

## Implementation Decisions

### 架构决策

**D1. 群聊引擎（ChatEngine）替代固定流水线**

- 不再是固定的 A1→A2→A3→A4 串行链条
- 中心决策者（Coordinator）是 LLM-driven Agent，每轮读取群聊历史 → 输出决策 → 邀请 Agent → 读取结果 → 下一轮
- 决策类型：`invite`（邀请某 Agent 执行任务）、`done`（任务完成）、`retry`（要求某 Agent 重做）
- 终止条件：Coordinator 输出 `done`，或达到最大轮次上限
- 理由：灵活适配不同场景，天然支持 Agent 扩展

**D2. Agent 注册中心**

```
agents/
├── registry.py      # AgentRegistry: register, discover, list_capable
├── base.py          # BaseAgent: retry + timeout + logging
├── coordinator.py   # 中心决策者（LLM-powered）
├── analyst.py       # 商品分析员
├── category.py      # 品类专项分析员（保健品/化妆品/食品/3C）
├── prompt_gen.py    # 提示词生成员
├── image_gen.py     # 生图员
├── reviewer.py      # 审查员（质量评分）
├── compliance.py    # 合规审查员（广告法/平台规范）
└── post_process.py  # 图像后处理员（去背景/增强）
```

- 每个 Agent 注册时声明：name, description, capabilities[], input_schema, output_schema
- Coordinator 的系统提示词中包含所有已注册 Agent 的列表和能力描述
- 新增 Agent 只需在 registry 注册，Coordinator 自动感知
- 理由：插件化架构，Agent 可独立开发和测试

**D3. 广播式通信**

- 所有 Agent 的输出追加到共享消息历史（`SessionState.messages`）
- Coordinator 可以读到完整的群聊记录
- Agent 之间不直接对话，但可以看到彼此的产出
- 理由：信息透明，Coordinator 有完整上下文做决策

**D4. 项目结构**

```
src/
├── core/          # 数据模型 + 配置 + 消息类型
├── providers/     # AI 服务商适配（OpenAI + DeepSeek + Mock）
├── agents/        # Agent 注册中心 + 全部 Agent 实现
├── chat/          # 群聊引擎（ChatEngine + Session + Message）
├── harness/       # 可靠性模块（重试/超时/熔断）
├── api/           # FastAPI 服务
├── cli/           # CLI 入口
└── storage/       # 持久化（checkpoint）
```

- 依赖方向：core ← providers ← agents ← chat ← api/cli
- 每个目录是独立的功能区

**D5. 模型与 Agent 解耦：能力声明 + 配置驱动**

- Agent 只声明能力需求（如 `requires: ["vision"]`），不指定具体模型
- 模型映射在配置文件中管理：`config/models.yaml` 定义每种能力对应哪些可用模型及其优先级
- Provider 只需声明自己能提供哪些能力（如 OpenAI 注册为 `capabilities: [vision, text, image]`）
- 切换模型只需改配置，不改 Agent 代码
- 同一 Agent 在不同场景可以用不同模型（如分析员日常用 GPT-4o，省钱模式用 DeepSeek）

**D6. Agent 配置文件化**

- 每个 Agent 对应 `config/agents/{name}.yaml` 一个独立配置文件
- 新增 Agent = 新建一个 YAML 文件，放入 `config/agents/` 目录
- 系统启动时自动扫描目录，注册所有 Agent
- 配置格式标准化，包含：元信息、能力需求、可配置参数（供未来前端生成配置界面）
- 理由：Agent 管理不依赖代码改动，便于未来前端加载和配置

**Agent 配置文件格式：**

```yaml
# config/agents/analyst.yaml
name: 商品分析员
description: 分析商品图片，识别品类、材质、卖点、目标人群、风格约束
version: "1.0.0"
requires: [vision]
prompt: prompts/analyst.yaml       # 引用的 System Prompt 文件
timeout_ms: 30000
retry:
  max_retries: 3
  backoff: exponential
params:                             # 可配置参数（前端可据此生成表单控件）
  - key: detail_level
    label: 分析详细程度
    type: select
    options: [basic, standard, detailed]
    default: standard
  - key: focus_areas
    label: 分析重点
    type: multi_select
    options: [品类识别, 材质分析, 卖点提取, 人群画像, 风格建议, 合规风险]
    default: [品类识别, 材质分析, 卖点提取, 风格建议]
```

**D7. 前后端分离 + 分布式部署**

- 后端 = FastAPI REST + WebSocket 服务，运行 ChatEngine
- 前端 = 独立 SPA 应用，通过 HTTP/WebSocket 与后端通信，不包含业务逻辑
- API 是无状态的：每次请求携带 `session_id`，后端从存储加载 SessionState
- 未来可水平扩展：前端可连接任意后端实例，Session 通过共享存储（SQLite→PostgreSQL/Redis）访问
- CORS 配置白名单，支持多前端域名
- 部署方式：Docker 容器化，环境变量注入配置，`docker-compose up` 一键启动

**D8. Mock First + 3 个真实 Provider**

- P0：OpenAI（提供 vision + text + image 能力）、DeepSeek（提供 text 能力）、Mock（全能力）
- 所有测试基于 Mock 运行
- 理由：先打通核心链路，Provider 扩展是机械性工作

### 数据模型

**SessionState（TypedDict）— 群聊全局状态：**

```
session_id, status, created_at, updated_at
task: {product_images, product_info, platform, category}
messages[]  — 完整的群聊历史
artifacts: {analysis, prompts, images, reviews}  — 各 Agent 产出物
turn_count, max_turns, cost_so_far
error_history
```

**Message（Pydantic）— 群聊中的一条消息：**

```
id, turn, timestamp
role: "coordinator" | "agent" | "system"
sender: str  — Agent 名称（如 "商品分析员"）
action: "invite" | "respond" | "done" | "error"
content: dict  — 消息体
```

**CoordinatorDecision（Pydantic）— 中心决策者的决策：**

```
action: "invite" | "done"
agent_name: str | None  — 邀请哪个 Agent
task_brief: str | None  — 给 Agent 的任务描述
reasoning: str  — 为什么做这个决策（可审计）
```

**AgentMeta（Pydantic）— Agent 注册信息：**

```
name, description, capabilities[]
input_schema, output_schema
model_requirements[]  # ["vision"] | ["text"] | ["image"]
```

**RunStatus（枚举）：**

```
CREATED → RUNNING → COMPLETED
                  ↘ FAILED
```

### Agent 能力体系

Agent **不绑定模型**，只声明能力需求。运行时由配置决定分配哪个 Provider 的哪个模型。

**Agent 定义（7 个）：**

| # | Agent | 能力需求 | 何时被邀请 |
|---|-------|---------|-----------|
| 0 | 中心决策者 | `text` | 始终运行 |
| 1 | 商品分析员 | `vision` | 任务开始时 |
| 2 | 品类专项分析员 | `vision` | 特定品类（保健品/化妆品等） |
| 3 | 提示词生成员 | `text` | 分析完成后 |
| 4 | 生图员 | `image` | 提示词就绪后 |
| 5 | 审查员 | `vision` | 图片生成后 |
| 6 | 合规审查员 | `vision` | 敏感品类（保健品等） |
| — | 图像后处理员 | `local` | 图片生成后 |

**Provider 能力声明：**

| Provider | 提供的能力 | 备注 |
|----------|-----------|------|
| OpenAI | `vision`, `text`, `image` | GPT-4o + DALL-E 3 |
| DeepSeek | `text` | 极低成本 |
| Mock | `vision`, `text`, `image` | 始终可用 |

**模型配置（`config/models.yaml`）：**

```yaml
# 每种能力的默认模型 + 备选链
capabilities:
  vision:
    default: openai/gpt-4o
    fallback: [mock]
  text:
    default: openai/gpt-4o
    alternatives: [deepseek/deepseek-v3]  # 省钱方案
    fallback: [mock]
  image:
    default: openai/dall-e-3
    fallback: [mock]

# 按 Agent 覆盖（可选）
agent_overrides:
  提示词生成员:
    text: deepseek/deepseek-v3  # 提示词生成用 DeepSeek 省钱
```

**能力解析流程：**

```
Agent 声明 requires: ["vision"]
    → 查 agent_overrides 是否有该 Agent 的覆盖配置
    → 查 capabilities.vision.default
    → 检查该 Provider 是否可用（API Key 已设置？）
    → 是 → 使用
    → 否 → 尝试 alternatives → 尝试 fallback → mock
```

### API 契约（前后端分离）

前端 = 独立 SPA 应用。后端 = REST + WebSocket 服务。两者通过 HTTP/WebSocket 通信。

**REST API：**

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/sessions` | 创建会话，上传图片（multipart/form-data），返回 `{session_id}` |
| `GET` | `/api/sessions/{id}` | 获取会话完整状态（状态 + 全部消息 + 产出物） |
| `GET` | `/api/sessions/{id}/messages?since=N` | 增量拉取消息（前端轮询或补充 WebSocket 丢帧） |
| `DELETE` | `/api/sessions/{id}` | 删除会话及产出物 |
| `GET` | `/api/agents` | 列出所有已注册 Agent 及其 `params`（前端据此渲染配置面板） |
| `GET` | `/health` | 后端健康检查 + 各 Agent/Provider 可用状态 |

**WebSocket（实时群聊流）：**

| 路径 | 方向 | 说明 |
|------|------|------|
| `WS /ws/sessions/{id}` | 后端→前端 | 实时推送每条群聊消息，JSON 格式：`{turn, role, sender, action, content, timestamp}` |

前端连接 WebSocket 后可实时观看群聊：
```
Turn 1 | {role: "coordinator", action: "invite", sender: "中心决策者", content: {agent: "商品分析员", task: "..."}}
Turn 1 | {role: "agent", action: "respond", sender: "商品分析员", content: {analysis: {...}}}
...
Turn N | {role: "coordinator", action: "done"}
```

**CLI（也是 API 客户端）：**

```bash
python -m src.cli run <image_path> --platform taobao
python -m src.cli run --dir ./products/ --platform amazon
python -m src.cli agents list
python -m src.cli config-validate
```

### 部署架构

```
                    ┌──────────────────┐
                    │   前端 (SPA)      │  Nginx / CDN 静态托管
                    │  独立部署         │
                    └──────┬───────────┘
                           │ HTTP REST + WebSocket
                           ▼
              ┌────────────────────────┐
              │   后端 (FastAPI)        │  Docker 容器
              │   ChatEngine + Agents  │  可水平扩展
              └───────────┬────────────┘
                          │
              ┌───────────▼────────────┐
              │   持久化层              │
              │   SQLite (dev)         │
              │   PostgreSQL (prod)    │
              │   Redis (会话缓存)     │
              └────────────────────────┘
```

- 前端和后端独立部署，通过 API 通信
- 后端无状态设计：`session_id` 驱动，状态持久化在存储层
- Docker Compose 一键启动：`docker-compose up`（含后端 + 数据库）
- 前端另行部署（Nginx / Vercel / CloudFlare Pages）
- CORS 白名单通过环境变量配置 `ECOMM_CORS_ORIGINS`

### Coordinator 决策逻辑

Coordinator 的系统提示词包含：
1. 所有已注册 Agent 的列表和能力描述
2. 当前任务的目标（出商品图）
3. 典型工作流程参考（但不强制按顺序）
4. 决策 JSON Schema

Coordinator 每轮需要：
- 读群聊历史，评估当前进度
- 判断：还需要什么信息/产出？哪个 Agent 能提供？
- 输出 `invite`（指定 Agent 和任务）或 `done`（所有产出物就绪）

## Testing Decisions

### 测试缝

| 缝 | 位置 | 怎么测 | 覆盖什么 |
|---|---|---|---|
| **API 缝** | FastAPI → ChatEngine | TestClient，Mock Provider | 完整请求→响应 |
| **Chat 缝** | ChatEngine | 注入固定 Coordinator 决策序列，验证消息流 | 协调逻辑正确性 |
| **Agent 缝** | 每个 Agent | Mock Provider + 固定输入，断言输出结构 | Agent 业务逻辑 |
| **Coordinator 缝** | Coordinator | 给固定消息历史，断言决策 JSON Schema | 决策格式正确 |
| **Provider 缝** | Provider 层 | Mock 返回固定数据 | Provider 接口契约 |

### 测试范围

| 模块 | 最低测试数 |
|------|-----------|
| `core/models.py` | 5 |
| `providers/mock.py` | 3 |
| `harness/circuit.py` | 3 |
| `agents/registry.py` | 2 |
| `agents/coordinator.py` | 2 |
| `agents/*` (6 个 Agent × 2) | 12 |
| `chat/engine.py` | 3 |
| `api/main.py` | 3 |
| **合计** | **~33** |

## Out of Scope (P0)

- **多 Provider：** Anthropic / Seedream / 通义千问 / GLM-4V / FLUX / Midjourney / SD-WebUI — P2
- **上下文管理：** Token 压缩 + 窗口过渡 — P1
- **多租户：** — P3
- **Agent 记忆：** 历史经验召回 — P3
- **Web 前端 + 桌面仪表盘：** — P2
- **批量任务调度器：** — P1
- **审计日志 + 告警 + 幂等性 + 死信队列：** — P1
- **部署配置：** Docker + K8s — P1

## Further Notes

- 默认品类为保健品，Mock 数据均以此为示例
- 所有环境变量 `ECOMM_` 前缀
- 目标：~40 文件，每个文件有实质内容
