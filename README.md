# E-Commerce Harness

**群聊式多智能体电商商品图生成系统** — 上传一张商品图，自动产出一整套可上架的商品图。

[![Python](https://img.shields.io/badge/Python-%E2%89%A53.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/React-18-61DAFB?logo=react&logoColor=white)](https://react.dev/)
[![Tests](https://img.shields.io/badge/tests-1664%20passed-3fb950)](#测试与质量)
[![Coverage](https://img.shields.io/badge/coverage-88%25-3fb950)](#测试与质量)
[![CI](https://github.com/Ljy-2005/e-commerce-Harness/actions/workflows/ci.yml/badge.svg)](https://github.com/Ljy-2005/e-commerce-Harness/actions/workflows/ci.yml)
[![中间件](https://img.shields.io/badge/外部中间件-无需-orange)](#设计原则)

---

## 目录

- [这是什么](#这是什么)
- [核心特性](#核心特性)
- [架构](#架构)
- [Agent 一览](#agent-一览)
- [支持的平台与图片槽位](#支持的平台与图片槽位)
- [Web 工作台](#web-工作台)
- [快速开始](#快速开始)
- [CLI 用法](#cli-用法)
- [API](#api)
- [配置](#配置)
- [测试与质量](#测试与质量)
- [部署](#部署)
- [项目结构](#项目结构)
- [敏感内容防护](#敏感内容防护)
- [常见问题](#常见问题)
- [文档索引](#文档索引)

---

## 这是什么

### 问题

中小电商卖家为每个商品制作展示图时，要接连完成这些事：

- **环节多且技能割裂** — 商品分析 → 提炼卖点 → 撰写生图提示词 → 调 AI 出图 → 审查质量 → 合规检查
- **工具碎片化** — 生图服务分散在多家平台，提示词格式各不相同，一家故障就卡死
- **质量不可控** — 没有系统化审查，图片经常不符合平台规范；生成的包装文字还常被模型编造
- **品类差异大** — 保健品要查广告法红线，化妆品要看肤质适配，3C 要看产品细节，固定流水线应付不了
- **成本不清楚** — 每个环节烧了多少 token、花了多少钱，事后无从对账

### 方案

把它建模成一场**有主持人的群聊**：11 个各有所长的 AI Agent 在一个共享会话里协作，由一个 LLM 驱动的**中心决策者**动态调度。

```
用户上传商品图
      │
      ▼
┌──────────────────────────────────────────────────────┐
│  群聊空间                                              │
│                                                       │
│   中心决策者 ──「邀请 @商品分析员，分析这张图」          │
│        ▲              │                               │
│        │              ▼                               │
│        │        商品分析员 发言（品类/卖点/包装文字转录）│
│        │              │                               │
│        └──────────────┘                               │
│        │                                              │
│        ├──「邀请 @提示词生成员，生成淘宝套图提示词」      │
│        ├──「邀请 @提示词审核优化员，做审美审核」          │
│        ├──「邀请 @生图员，按槽位出图」                   │
│        ├──「邀请 @审查员，逐张打分」                     │
│        └──「邀请 @合规审查员，查广告法风险」             │
│                       │                               │
│            直到中心决策者宣布 DONE                       │
└──────────────────────────────────────────────────────┘
      │
      ▼
一整套可上传的商品图（白底主图 / 卖点图 / 场景图 / 成分图 / 人群图 …）
```

### 为什么是「群聊」而不是「流水线」

固定流水线只能应对一种顺序。而真实需求是**场景驱动**的：

- 保健品 → 自动邀请**合规审查员**查广告法红线
- 化妆品 → 自动邀请**品类专项分析员**识别质地与适用肤质
- 审查不达标 → 中心决策者自己判断是**重出图**还是**重写提示词**，而不是无脑重跑

中心决策者每轮读取完整群聊历史与当前产物状态，自己决定下一步请谁——新增 Agent 后它自动感知，无需改动调度逻辑。

---

## 核心特性

### 🗣️ 多智能体群聊协作

- **LLM 驱动的动态调度** — 中心决策者读群聊历史 + 产物状态，输出 `invite` / `retry` / `done` 决策
- **广播式上下文** — 所有 Agent 发言进共享历史，彼此可见但不直接对话，决策者始终持有完整上下文
- **插件化扩展** — 新增 Agent = `config/agents/` 放一个 YAML + 一个实现文件，注册中心动态导入，**零核心代码改动**
- **后台 Agent 机制** — `invitable: false` 的 Agent 移出群聊名单（如「风格档案员」由界面直接调用），不占决策 token、也不可能被误邀请

### 🔀 工作流编排层

群聊负责「智能」，编排层负责「确定性」。两者并存，用 YAML 模板选择：

- **YAML DSL 声明式模板** — 6 类节点：`tool` / `agent` / `condition` / `human` / `subworkflow` / `end`，支持条件分支与 `goto` 回跳
- **自动挡 / 手动挡** — 全自动执行，或每步暂停等指令（单步调试）
- **断点续跑** — 基于 SQLite 事件溯源 + 检查点，崩溃最多丢当前 1 步
- **批量调度** — 并发窗口（默认 3，上限 10）、每项自动重试 2 次、死信列表、暂停/恢复/取消、租户隔离报表
- **人工审批（HITL）** — `human` 节点 + **SLA 超时矩阵**：按上游评分自动通过/拒绝/继续等待，超时事件带命中策略
- **7 个内置模板** — 见 [支持的平台与图片槽位](#支持的平台与图片槽位) 下方说明
- **零外部依赖** — 持久化用标准库 SQLite（`asyncio.to_thread` 包装），**不引入 Redis / Celery**

### 🔌 多模型可降级接入

- **Agent 与模型解耦** — Agent 只声明能力需求（`requires: ["vision"]`），用哪家模型由 `config/models.yaml` 决定，**切换模型不改 Agent 代码**
- **7 家服务商** — OpenAI / DeepSeek / Anthropic / 通义千问 / 火山方舟 / 火山视觉智能 / FLUX（详见 [架构](#架构)）
- **三类能力路由** — `text` / `vision` / `image`，每类配降级链：默认 → 备选 → 兜底
- **自定义端点** — 支持第三方 coding plan、中转站、自建网关（`config/providers.yaml`，端点三级解析：环境变量 → 文件 → 官方默认）
- **文+图双条件生图** — 火山方舟支持参考图入参，把用户原图与文本提示词同时送进模型

### 🖼️ 平台槽位契约

不是「生成几张图」，而是「生成这套平台规范要求的图」：

- **10 个平台档案** — 每个平台独立定义画幅、导出尺寸、背景策略、禁例、风格描述、主图槽位与详情槽位
- **17 类图片槽位** — `main_white` 白底主图 / `main_scene` 场景图 / `main_selling_point` 卖点图 / `main_ingredients` 成分图 / `main_audience` 人群图 / `main_cert` 资质图 …
- **逐张契约** — 每个槽位携带 `intent`（这张要让买家看懂什么）/ `design` / `must` / `forbid` / `keep_clear`
- **信息图本地排版** — 模型只画**无字底图**，文字层由本地用系统中文字体绘制（文案只取已确认事实）；缺事实依据时**不产图也不编造**，如实报告「请上传包装背面照片」

### 🎨 风格词库

- 导入一组参考照片 → 「风格档案员」拆成**文字风格档案**（背景/构图/光影/材质/元素/色板/留白）+ **套图结构**（第几张是什么角色）+ **审美判词**
- **一轮会话只用一套风格词** — 会话锁保证同一套图不会前几张用 A、后几张用 B
- 档案与判词**不进最终提示词**，只给「写提示词」和「审提示词」的角色当基准
- **一键风格复刻** — 参考图 → 拆解 → 融合提示词 → 重跑

### 🛡️ 可靠性与成本治理

- **可靠性四件套** — 熔断器、令牌桶限流（按 Provider/模型/token/租户多维）、指数退避重试、超时，随 Agent 一起交付
- **只重试传输类异常** — 超时不重试、编程错误不重试（避免用户白等）
- **精确到单次调用的成本账** — 每次调用记录模型、耗时、token、金额；图像成本单独计入 breakdown
- **价格口径诚实** — 价格表可配置 + 从审计日志聚合观察值；**只有全部调用都回报金额才求和**，否则显式标注「金额不可得」而不是编一个数
- **止损线** — 审查/合规连续失败 N 次自动停会话，避免合规不通过时反复重新生成把预算烧光
- **审计日志** — 按日期分文件写 `data/audit/`（JSONL），租户隔离

### 🖥️ 实时协作

- **REST + WebSocket 双通道** — 群聊过程实时直播，用户可**随时插话指挥**（指令作为 system 消息进群聊流，决策者下一轮自然感知）
- **A/B 测试框架** — 并行跑多变体，自动评审选出 winner
- **5 种协作模式** — `serial` / `ab_generate` / `debate` / `vote` / `ab_test`

### ⚙️ 工程化

- **Mock Mode First** — 无 API Key 也能跑通全流程（Mock Provider 返回模板数据），先验证系统逻辑再决定买哪些 API
- **确定性测试** — 会话级清空 Provider Key + 强制 Mock，**1,664 例约 6 分钟全绿**，不烧钱不抖动
- **多租户隔离** — 每租户独立 API Key，**密钥即身份**（`X-Tenant-ID` 声明被重写，杜绝跨租户越权）
- **CI 五道门禁** — 后端测试 + ruff / 前端测试 + 构建 + 依赖审计 / Docker 构建 / E2E 冒烟 / 密钥扫描

---

## 架构

```
┌────────────────────────────────────────────────────────────────────┐
│ L4 接入层                                                           │
│   FastAPI (63 REST 端点)  ·  WebSocket ×2  ·  Typer CLI            │
├────────────────────────────────────────────────────────────────────┤
│ L3 应用层 —— 两条并存的路径                                          │
│                                                                     │
│   ┌─────────────────────────┐   ┌──────────────────────────────┐   │
│   │ 群聊引擎 ChatEngine      │   │ 工作流编排 WorkflowEngine     │   │
│   │  中心决策者动态调度       │   │  YAML DSL + 节点状态机        │   │
│   │  广播式上下文             │   │  条件分支 / 人工审批 / 断点续跑 │   │
│   └───────────┬─────────────┘   └──────────────┬───────────────┘   │
│               │                                 │                   │
│               └────────────┬────────────────────┘                   │
│                            ▼                                        │
│                 群聊被"包裹"为编排层的一类节点                        │
├────────────────────────────────────────────────────────────────────┤
│ L2 智能体层  AgentRegistry（11 个 Agent，插件化注册）                 │
│   中心决策者 · 商品分析 · 品类专项 · 提示词生成 · 提示词审核优化       │
│   生图 · 审查 · 合规审查 · 图像后处理 · 风格拆解 · 风格档案(后台)      │
├────────────────────────────────────────────────────────────────────┤
│ L1 能力层                                                           │
│   Provider 适配（7 家 · 三类能力 · 降级链）                          │
│   可靠性四件套（熔断 / 限流 / 重试 / 超时）                           │
│   本地处理（去背景 · 信息图排版 · 图片体检 · 视觉图源统一）            │
├────────────────────────────────────────────────────────────────────┤
│ L0 存储层   SQLite 事件溯源（workflow.db） · JSONL（审计/记忆）       │
│             checkpoint 会话快照 · 生成图落盘                          │
└────────────────────────────────────────────────────────────────────┘
```

**Provider 全景**

| 路由 | 服务商 | 能力 | 备注 |
|------|--------|------|------|
| `openai` | OpenAI | vision / text / image | |
| `deepseek` | DeepSeek | text / vision | 省钱方案 |
| `anthropic` | Anthropic | vision / text | 长上下文 |
| `qwen` | 通义千问 | vision / text | 国内备选 |
| `ark` | 火山引擎方舟 | text / vision / image | **支持参考图**，国内电商首选 |
| `seedream` | 火山视觉智能 | image | 旧版 AK/SK 签名 |
| `flux` | FLUX | image | 写实场景图 |

### 设计原则

1. **Mock Mode First** — 无 Key 也能跑通，测试确定性可回归
2. **Provider 不锁定** — 按可用性自动选择与降级，一家故障不阻塞业务
3. **可靠性内建** — 重试/超时/熔断随 Agent 一起交付，而不是让调用方自己兜
4. **场景驱动** — 不同品类邀请不同 Agent 组合
5. **零外部中间件** — 不引入 Redis / Celery，部署面收敛为单进程 + 一个 SQLite 文件

---

## Agent 一览

共 **11 个** Agent，其中 10 个参与群聊，1 个为后台 Agent。

| Agent | 能力 | 职责 | 群聊 |
|-------|------|------|:----:|
| **中心决策者** | text | 读群聊历史与产物状态，决定邀请谁、分配什么任务 | ✅ |
| **商品分析员** | vision | 识别品类/材质/卖点/人群；**逐字转录包装文字**、输出商品身份卡 | ✅ |
| **品类专项分析员** | vision | 按品类深度分析（保健品→合规维度，化妆品→肤质/色号/质地） | ✅ |
| **提示词生成员** | text | 分析结果 → 逐张编号的平台套图提示词初稿 | ✅ |
| **提示词审核优化员** | text | 按设计七项逐张打分，低于阈值**直接给改写稿** | ✅ |
| **生图员** | image | 按槽位调用生图 API（每槽 1 张，尺寸按路由解析） | ✅ |
| **审查员** | vision | 6 维度评分（质感/光影/构图/还原度/平台适配/真实感）+ pass/retry 判定 | ✅ |
| **合规审查员** | vision | 广告法 + 平台规范检查，输出风险等级 | ✅ |
| **图像后处理员** | local | 去背景 + 增强，**纯本地计算** | ✅ |
| **风格拆解员** | vision | 拆解参考图的构图/光影/色调/元素（一键复刻） | ✅ |
| **风格档案员** | vision | 一组照片 → 文字风格档案 + 套图结构 + 审美判词 | ❌ 后台 |

### 新增一个 Agent

```yaml
# config/agents/my_agent.yaml
name: 我的分析员
description: 做什么用的
version: "1.0.0"
requires: [vision]                    # 只声明能力，不指定模型
class: src.agents.my_agent.MyAgent    # 指向实现类（不在内置映射表时必填）
prompt: prompts/my_agent.yaml
timeout_ms: 150000
params:                               # 前端据此自动生成配置表单
  - key: detail_level
    label: 分析详细程度
    type: select
    options: [basic, standard, detailed]
    default: standard
```

实现类只需继承 `BaseAgent` 并实现 `_execute_impl(task_brief, session) -> dict`，即可自动获得熔断、限流、重试、超时与成本记账。中心决策者下次启动就会感知到它。

---

## 支持的平台与图片槽位

### 10 个平台

| 平台 | 画幅 | 导出尺寸 | 图位上限 | 背景策略 |
|------|:----:|:--------:|:--------:|----------|
| 淘宝 `taobao` | 1:1 | 800×800 | 5 主 / 20 详情 | 允许设计 |
| 天猫 `tmall` | 1:1 | 800×800 | 5 主 / 20 详情 | 允许设计 |
| 拼多多 `pinduoduo` | 1:1 | 800×800 | 10 | 允许设计 |
| 京东 `jd` | 1:1 | 800×800 | 5 主 / 20 详情 | 优先白底 |
| Amazon `amazon` | 1:1 | 2000×2000 | 9 | **强制纯白** |
| 小红书 `xiaohongshu` | **3:4** | 1242×1656 | 9 | 允许设计 |
| 抖音 `douyin` | 1:1 | 800×800 | 9 主 / 20 详情 | 允许设计 |
| Shopify `shopify` | 1:1 | 2048×2048 | 8 主 / 20 详情 | 允许设计 |
| Shopee `shopee` | 1:1 | 1024×1024 | 9 | 允许设计 |
| 1688 | 1:1 | 800×800 | 5 | 优先白底 |

> 每个平台的完整档案（含禁例、风格描述、主图与详情槽位清单）在 `config/platforms.yaml`，可直接编辑扩展。Amazon 的白底要求最硬：主图必须纯白 RGB(255,255,255)、主体占 85%、无任何装饰与文字。

### 图片槽位

**主图槽位**：`main_white` 白底主图 · `main_selling_point` 卖点图 · `main_benefits` 功效图 · `main_ingredients` 成分图 · `main_audience` 人群图
**详情槽位**：`main_spec` 规格 · `main_usage` 用法 · `main_cert` 资质 · `main_compare` 对比
**其他**：`main_scene` 场景图 · `main_detail` 细节图 · `note_cover` / `note_scene` / `note_detail` 种草笔记三件套 · `activity_banner` 活动横幅

### 内置工作流模板

| 模板 | 分类 | 流程卖点 |
|------|------|----------|
| 🛍️ `white_bg_suite` 白底主图套装 | 商品图 | 验证→分析→[品类]→提示词→生图→审查→[人工]→合规→后处理 |
| 🛋️ `scene_suite` 场景图套装 | 商品图 | 场景加权提示词 + 生图×2 对比 |
| 🛡️ `compliance_hardened` 合规加固 | 商品图 | 保健品/化妆品强合规流水线 |
| 🎨 `style_replicate` 风格复刻 | 风格复刻 | 参考图 + 商品图双输入 |
| 💬 `free_chat` 自由群聊 | 智能协作 | 单节点群聊，与 5 种协作模式等价 |
| 📨 `light_approval` 轻量审核通知 | 智能协作 | SLA 超时自动决策演示 |
| 🧮 `approval_matrix` 审批矩阵演示 | 智能协作 | 评分 ≥75 自动通过 / ≥60 自动拒绝 / 默认继续等待 |

---

## Web 工作台

启动后访问 **http://localhost:5173**（开发）或 **http://localhost**（Docker）。

| 页面 | 路径 | 用途 |
|------|------|------|
| 📊 仪表盘 | `/` | Provider 状态、熔断、限流、租户总览 |
| 💬 会话列表 | `/sessions` | 新建会话（上传商品图）+ 历史会话 |
| 🗣️ 会话工作台 | `/session/:id` | 群聊直播（按轮分组）+ 产物 tabs + 图片网格 + A/B 运行器 + 插话输入 |
| 🧩 工作流 | `/workflows` | Skill 库画廊 + 画布 |
| ▶️ 工作流作业 | `/workflows/:id` | 节点状态机、逐步执行、人工审批 |
| 📦 批量任务 | `/batches` | 进度条、控制、死信重跑、数据报表 |
| 🤖 Agent 配置 | `/agents` | Agent 列表与可配置参数表单 |
| ⚙️ 系统设置 | `/settings` | API Key 管理、模型映射、租户 Key、输出目录、会话策略 |
| 📜 审计日志 | `/audit` | 逐次调用的模型/耗时/token/金额 |
| 🧠 记忆库 | `/memory` | 跨会话 Agent 记忆 |
| 🎨 风格词库 | `/styles` | 导入照片 → 生成风格档案 → 启用风格 |

> 交互式 API 文档：**http://127.0.0.1:8000/docs**

---

## 快速开始

### 环境要求

| 依赖 | 版本 | 说明 |
|------|------|------|
| Python | **≥ 3.12** | `pip install -e` 会校验；版本不符直接拒绝 |
| Node.js | ≥ 20 | 仅前端需要 |

### 一键启动（推荐）

Windows 双击 `start.bat`，或命令行：

```bash
python start.py                 # 启动后端 + 前端，就绪后自动打开浏览器
python start.py --no-browser    # 不自动打开浏览器
python start.py --backend-only  # 只启动后端
python start.py --frontend-only # 只启动前端
python start.py --backend-port 8001 --frontend-port 5174   # 自定义端口
```

- 服务已在运行时**自动复用**，不重复启动；就绪前显示等待进度
- 停止：双击 `stop.bat`（Windows），或在启动窗口按 `Ctrl+C`
- 首次使用前安装依赖：

```bash
pip install -e ".[dev]"     # 后端（会顺便自动装好敏感内容防护 git 钩子）
cd frontend && npm install  # 前端
```

### 手动启动（开发模式）

```bash
# 后端（Mock 模式，无需 API Key）
uvicorn src.api.main:app --reload --port 8000

# 前端（另开终端）
cd frontend && npm run dev    # http://localhost:5173
```

> 前端 Vite 已配好 `/api`、`/ws`、`/health` 到 `localhost:8000` 的代理，无需额外配置 CORS。

### 配置 API Key（可选）

不配也能跑——默认走 Mock Provider，返回模板数据，全链路可验证。要接真实模型：

```bash
cp .env.example .env          # Windows: copy .env.example .env
# 编辑 .env，至少填一个 Provider 的 Key
```

也可以启动后在 **设置页** 里填，即时生效无需重启。

---

## CLI 用法

```bash
# 单个商品生图
python -m src.cli run ./product.jpg --platform taobao --category 保健品 --product-info "护肝胶囊"

# 批量处理目录（扫描 .jpg/.jpeg/.png/.webp）
python -m src.cli batch ./products --platform taobao

# 列出所有已注册 Agent 及其可配置参数
python -m src.cli agents-list

# 校验配置文件格式（default.yaml / models.yaml / agents/*.yaml）
python -m src.cli config-validate
```

`run` 支持 5 种协作模式（`--mode`）：

| 模式 | 说明 |
|------|------|
| `serial` | 串行协作（默认）：分析 → 品类 → 提示词 → 生图 → 后处理 → 审查 → 合规 |
| `ab_generate` | 同一提示词生成两套不同风格的图，审查员对比后选出最佳方案 |
| `debate` | 两次审查分别持**正方/反方**立场，再综合双方意见判定 |
| `vote` | 邀请 3 个审查员各自独立评分，取多数意见为最终判定 |
| `ab_test` | 交给 A/B 测试框架接管，并行跑多变体选 winner |

---

## API

完整的端点清单与请求/响应契约见 [`docs/modules/api.md`](docs/modules/api.md) 与 [`docs/workflow-design.md`](docs/workflow-design.md) §7，交互式文档见 `/docs`。

| 分组 | 主要端点 | 说明 |
|------|----------|------|
| 会话 | `POST /api/sessions` · `GET /api/sessions` / `{id}` / `{id}/messages` | 创建（上传商品图）/ 列表 / 状态 / 增量消息 |
| 人工介入 | `POST /api/sessions/{id}/decision` / `interject` / `ab-test` | 审批决策 / 插话 / A/B 对比 |
| 会话补充 | `POST /api/sessions/{id}/facts` / `style` · `GET /{id}/export` | 补充事实 / 指定风格 / 导出 |
| 工作流 | `/api/workflows/templates` · `jobs` · `batches` · `batches/report` | 模板画廊 / 实例化 / 作业控制 / 审批 / 复刻 / 批量 / 报表 / 导入导出 |
| Webhook | `POST /api/webhooks/workflows/{id}/decision` | 入站审批回调（token 鉴权） |
| 风格词库 | `GET` / `POST` / `PATCH` / `DELETE` `/api/style-library/*` | 词条 CRUD / 导入照片 / 重新分析 / 预览 |
| 设置 | `GET /api/settings` + 多个 `POST` | 设置汇总 / API Key / 租户 Key / Agent 参数 / 模型映射 / 计价 |
| 平台 | `GET /api/platforms` | 平台档案与槽位 |
| 观测 | `GET /api/audit` · `GET /api/memory/*` · `GET /api/admin/status` · `GET /health` | 审计 / 记忆 / 系统状态 / 健康检查 |
| Agent | `GET /api/agents` · `GET /api/capabilities` | Agent 清单与能力 |
| 实时 | `WS /ws/sessions/{id}` · `WS /ws/workflows/jobs/{id}` | 群聊直播（双向插话）/ 工作流事件流 |

**鉴权**：配置 `ECOMM_API_KEY` 后所有端点需带 `X-API-Key`（或 `Bearer`）；管理端点仅管理员可用。多租户用 `ECOMM_TENANT_KEYS` 分发租户密钥。

---

## 配置

| 文件 | 用途 |
|------|------|
| `config/default.yaml` | 全局配置（群聊轮次、会话策略、超时、熔断、限流、预算） |
| `config/models.yaml` | 能力 → 模型映射 + 每家 Provider 的降级链 |
| `config/agents/*.yaml` | 每个 Agent 一个配置（能力需求、超时、重试、可配参数） |
| `config/prompts/*.yaml` | 每个 Agent 的 System Prompt |
| `config/platforms.yaml` | 10 个平台档案 + 槽位目录 + 美术方向 |
| `config/workflows/*.yaml` | 工作流模板（Skill 库） |
| `config/style_library.yaml` | 内置风格档案原型 |
| `config/secrets.yaml` | API Key（**已 gitignore，永不入库**） |
| `config/providers.yaml` | 自定义服务商端点（**已 gitignore**） |

### 环境变量

环境变量（`ECOMM_` 前缀覆盖 YAML）优先级高于配置文件。

**Provider Key**（填一个即可跑通，也可在设置页填）

| 变量 | 能力 |
|------|------|
| `OPENAI_API_KEY` | vision / text / image |
| `DEEPSEEK_API_KEY` | text / vision |
| `ANTHROPIC_API_KEY` | vision / text |
| `DASHSCOPE_API_KEY` | 通义千问 vision / text |
| `VOLCANO_ACCESS_KEY` + `VOLCANO_SECRET_KEY` | 火山方舟 |
| `SEEDREAM_API_KEY` | 即梦 AI |
| `BFL_API_KEY` / `FAL_KEY` / `REPLICATE_API_KEY` | FLUX（三选一） |

**应用与鉴权**

| 变量 | 说明 |
|------|------|
| `ECOMM_MOCK_MODE` | `true` 强制 Mock（无需 Key） |
| `ECOMM_LOG_LEVEL` | 日志级别，默认 `INFO` |
| `ECOMM_CORS_ORIGINS` | CORS 白名单 |
| `ECOMM_DATA_DIR` | 运行期数据根目录（checkpoint / 审计 / 记忆 / workflow.db） |
| `ECOMM_OUTPUT_DIR` | 生成图输出目录 |
| `ECOMM_API_KEY` | 全局 API Key（= 管理员身份） |
| `ECOMM_TENANT_KEYS` | 租户密钥，格式 `租户ID:密钥,...` |
| `ECOMM_TENANTS` | 租户注册，格式 `租户ID:套餐,...`（`free`/`pro`/`enterprise`） |
| `ECOMM_WEBHOOK_TOKEN` | 入站 Webhook 鉴权 |

完整清单见 [`.env.example`](.env.example)。

---

## 测试与质量

```bash
pytest                      # 默认套件：确定性 Mock，约 6 分钟，1,664 例
pytest -m slow              # 负载/压力测试
pytest -m real              # 真实 API（会花钱，需 Key）
pytest -m "not real"        # 含 slow 的全量（CI 后端 job 用）

cd frontend && npm test     # 前端 313 例
python scripts/e2e_smoke.py # 7 场景端到端冒烟（自启 Mock 服务，跑完自停）
```

| 指标 | 数值 |
|------|------|
| 后端测试 | **1,664 例通过**（`pytest -m "not real and not slow"`） |
| 后端覆盖率 | **88%**（12,373 语句） |
| 前端测试 | **313 例通过**（Vitest + React Testing Library，16 个文件） |
| E2E 冒烟 | **7/7 场景** |
| 后端代码 | 81 个模块 / 19,444 行 |
| 前端代码 | 49 个文件 / 11,336 行 |

**确定性来自哪里**：`conftest` 会话级清空 Provider Key + 强制 `MOCK_MODE` + 重建注册表，并用 tmp 隔离运行期目录——所以测试既不烧钱也不污染 `data/`、`config/`、`output/`。

### 其他脚本

```bash
python scripts/quality_probe.py        # 真机质量探针（会花钱，1 张 ≈ ¥0.2）
python scripts/real_suite_run.py       # 真实套图验收（会花钱）
python scripts/render_info_samples.py --all-slots   # 本地渲染信息图样例（零成本）
python scripts/style_preview.py        # 风格档案预览（零成本，不调模型）
python scripts/style_anchor.py         # 参考图转审美锚点判词（可能花钱）
```

### CI 门禁（5 个作业）

| 作业 | 内容 |
|------|------|
| `backend` | pytest 全量（含 slow）+ ruff 静态检查 |
| `frontend` | Vitest + 生产构建 + `npm audit`（高危即红） |
| `docker` | nginx 多阶段镜像构建 |
| `e2e` | 7 场景冒烟 |
| `secrets` | 密钥扫描（项目规则 + gitleaks 全历史） |

---

## 部署

```bash
cd deploy
cp ../.env.example .env      # 填真实 API Key
docker-compose up -d
```

访问 **http://localhost** —— nginx 服务前端静态产物并把 `/api`、`/ws` 代理到后端。

| 服务 | 端口 | 资源限制 |
|------|:----:|----------|
| `nginx` | 80 | 0.5 CPU / 128M |
| `api` | 8000 | 2 CPU / 2G |

- 前端产物由 `nginx.Dockerfile` **多阶段构建**打进镜像，无需手工填充静态卷
- 数据卷：`harness_data`（`/app/data`）、`harness_output`（`/app/output`）
- 生产环境记得设置 `ECOMM_API_KEY` 与 `ECOMM_CORS_ORIGINS`

---

## 项目结构

```
e-commerce-harness/
├── src/
│   ├── core/          数据模型 · 配置系统 · 平台档案与槽位契约 · 多租户
│   ├── providers/     7 家服务商适配 · 能力路由 · 降级链 · 自定义端点
│   ├── agents/        11 个 Agent + 插件化注册中心 + BaseAgent 保护链
│   ├── chat/          群聊引擎 · 会话状态 · WebSocket 广播
│   ├── workflow/      YAML 模板 · 节点状态机 · SQLite 事件溯源 · 批量调度
│   ├── harness/       熔断 · 限流 · 重试 · 成本 · 审计 · 上下文 · 记忆
│   │                  图片体检 · 提示词组装与把关 · 信息图排版 · 风格库
│   ├── api/           FastAPI 接入层 · 鉴权中间件
│   ├── storage/       checkpoint 落盘 · 生成图导出
│   └── cli.py         Typer CLI
├── frontend/          React 18 + Vite 7 工作台（11 个页面）
├── config/            YAML 配置（agents / prompts / platforms / workflows）
├── tests/             pytest 1,664 例
├── scripts/           E2E 冒烟 · 质量探针 · 信息图样例 · 密钥扫描 · 钩子安装
├── deploy/            Dockerfile · nginx.Dockerfile · docker-compose · nginx.conf
├── docs/              PRD · 工作流设计 · 测试计划 · 11 篇模块文档
├── data/              运行期数据（已 gitignore）
└── output/            生成图输出（已 gitignore）
```

---

## 敏感内容防护

仓库是公开的，因此对「密钥与个人信息」设了四层防护：

| 层 | 机制 | 说明 |
|---|------|------|
| 1 | `.gitignore` | 忽略 `config/secrets.yaml`、`.env`、`*.pem`、简历类文件等 |
| 2 | `pre-commit` 钩子 | 提交前扫描暂存区，命中即阻止 |
| 3 | `pre-push` 钩子 | 推送前复查 + 未跟踪文件，并提示「已推送即视为已泄漏」 |
| 4 | CI + GitHub 推送保护 | 服务端兜底，本地 `--no-verify` 绕过也能拦住 |

钩子随 `pip install -e ".[dev]"` 自动安装；丢失时手动补装：

```bash
python scripts/setup_hooks.py            # 安装
python scripts/setup_hooks.py --check    # 检查
python scripts/check_secrets.py --tree   # 扫描全部已跟踪文件
python scripts/check_secrets.py --history # 扫描全部提交历史
```

分级策略：确定性特征（密钥格式 / 敏感文件名 / 明文口令）**直接阻断**；启发式特征（高熵串 / JWT / 手机号）**仅警告**。误报可在行末加 `# secret-scan: allow`，或在 `scripts/secret_rules.json` 登记豁免。

**使用前请手动开启 GitHub 的推送保护**：`Settings → Code security → Secret protection`（公开仓库免费）。

误报处理与泄漏应急流程见 [`.github/SECURITY.md`](.github/SECURITY.md)。

---

## 常见问题

**没有 API Key 能跑吗？**
能。默认 Mock Provider 返回模板数据，全链路（含前端、E2E 冒烟）都能跑通，方便先验证系统逻辑再决定采购哪些 API。设 `ECOMM_MOCK_MODE=true` 可强制 Mock。

**`pip install -e` 报 Python 版本不符？**
本项目要求 **Python ≥ 3.12**。用 `py -0p`（Windows）或 `python3.12 --version` 确认解释器版本。

**端口被占用怎么办？**
`python start.py` 检测到服务已在运行会**自动复用**；要换端口用 `--backend-port` / `--frontend-port`。

**生成的图存在哪里？**
默认 `output/{租户}/{会话ID}/{平台}_{品类}_{序号}.png`，也可在设置页或 `ECOMM_OUTPUT_DIR` 改。相同规模的中间数据在 `data/`。

**怎么换某个 Agent 用的模型？**
改 `config/models.yaml` 的 `agent_overrides`，或在设置页的「模型映射」里改，**无需改代码**。注意覆盖键必须在 Agent 的 `requires` 里，否则会被忽略（`/api/settings` 的 `agent_overrides_issues` 会报出来）。

**怎么加一个新 Agent？**
`config/agents/` 新建 YAML（声明 `requires` 与 `class`）+ 实现 `BaseAgent` 子类的 `_execute_impl`。注册中心动态导入，中心决策者自动感知，不用改调度逻辑。

**为什么不让中心决策者「别邀请」某个 Agent，而要 `invitable: false`？**
写在提示词里说明，**每次决策都要付 token**，还会把名字塞进上下文反而可能诱导它提及。移出名单是零成本且不可能被邀请的做法。

**为什么外部中间件一个都不用？**
部署面收敛为「单进程 + 一个 SQLite 文件」，`docker-compose up` 就能起。可靠性需求（重试/熔断/限流/持久化）由 `src/harness/` 和 `src/workflow/` 自己实现，不引入 Redis / Celery。

---

## 文档索引

**总览**

| 文档 | 内容 |
|------|------|
| [`docs/prd.md`](docs/prd.md) | 产品需求：问题、方案、用户故事、架构决策 |
| [`docs/workflow-design.md`](docs/workflow-design.md) | 工作流编排层设计：DSL 规范、运行期模型、里程碑 |
| [`docs/test-plan.md`](docs/test-plan.md) | 9 层测试体系与 P1–P6 实施记录 |
| [`docs/progress.md`](docs/progress.md) | 开发全过程：里程碑、决策记录、实测数据 |
| [`docs/code-review.md`](docs/code-review.md) · [`docs/code-review-round2.md`](docs/code-review-round2.md) | 四域审计报告与逐项修复记录 |

**模块文档**（`docs/modules/`）

| 文档 | 覆盖 |
|------|------|
| [`agents.md`](docs/modules/agents.md) | 多智能体层：注册中心、Agent 池、配置格式 |
| [`chat.md`](docs/modules/chat.md) | 群聊引擎：消息格式、协作模式、典型会话 |
| [`workflow.md`](docs/modules/workflow.md) | 编排层：状态机、Skill 库、批量调度 |
| [`providers.md`](docs/modules/providers.md) | 服务商适配：路由表、自定义服务商、端点覆盖 |
| [`api.md`](docs/modules/api.md) | REST / WebSocket 接入层 |
| [`auth.md`](docs/modules/auth.md) | 鉴权：两类凭据、管理面分权 |
| [`tenant.md`](docs/modules/tenant.md) | 多租户隔离 |
| [`harness.md`](docs/modules/harness.md) | 可靠性保障层：熔断/限流/重试/超时 |
| [`harness-extended.md`](docs/modules/harness-extended.md) | 成本 · 审计 · 上下文 · 记忆 · 图片体检 · 提示词把关 · 风格库 |
| [`core.md`](docs/modules/core.md) | 数据模型 · 状态 · 配置系统 |
| [`storage.md`](docs/modules/storage.md) | 持久化：checkpoint、生成图导出 |
| [`image-preprocessor.md`](docs/modules/image-preprocessor.md) | 图片预处理与安全防护 |
| [`cli.md`](docs/modules/cli.md) | 命令行接口 |
| [`deploy.md`](docs/modules/deploy.md) | 部署配置与流程 |
| [`logging.md`](docs/modules/logging.md) | 统一日志 |
| [`ab-testing.md`](docs/modules/ab-testing.md) | A/B 测试框架 |
