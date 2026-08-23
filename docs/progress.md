# E-Commerce Harness — 项目状态总览

> 最后更新：2026-08-23
> 本文档汇总项目**进度**、**现有计划**、**开发思路**与**关键决策对话**，作为团队交接与后续开发的单一事实来源（Single Source of Truth）。

---

## 0. 新会话接续指南（复制这段话作为新对话的开场白）

```
这是 E-Commerce Harness 项目（群聊式多智能体电商商品图生成），
工作目录 D:\vscode-project\e-commerce Harness。
请先读 docs/progress.md（第 0 节之外的全部内容）、docs/workflow-design.md 和 docs/test-plan.md，
然后按 progress.md 第 5.3 节「遗留的开放决策」/ test-plan.md 的 P3-P6 阶段选择下一项任务继续。
常用命令：
  pytest                          # 后端全量（当前 468 通过）
  cd frontend && npm test         # 前端 Vitest（当前 63 通过）
  cd frontend && npm run build    # 前端构建
  python scripts/e2e_smoke.py     # E2E 冒烟（7 场景一键全链路，自动起停服务）
  python start.py                 # 一键启动前后端（Windows 也可双击 start.bat）
服务若未运行请先启动并验证 /health（启动后 /api/settings 可查运行模式与模型映射）。
```

---

## 1. 项目概览

**群聊式多智能体电商商品图生成系统** —— 9 个 AI Agent 在中心决策者（Coordinator）的协调下，像群聊一样协作完成"商品图生成"全流程任务。

| 维度 | 内容 |
|------|------|
| 版本 | v0.2.0 |
| 语言 / 运行时 | Python ≥ 3.12 |
| Web 框架 | FastAPI + Uvicorn（REST + WebSocket） |
| 前端 | React SPA（Vite） |
| 数据库 | 文件持久化（checkpoint JSONL / 审计日志 JSONL），无外部 DB 依赖 |
| 测试 | pytest（asyncio_mode=auto），41 个测试文件，446 个测试通过 |
| 部署 | Docker 多阶段构建（Node 前端 + Python 后端 + Nginx） |

---

## 2. 项目进度

### 2.1 里程碑时间线

按 git 提交顺序（当前 **30 笔提交**，工作树干净；表中标注「未提交」的历史行系早期记录，其工作已随后续提交固化）：

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
| *（未提交）* | **Workflow M4 SLA+连接器+导入导出** | human 节点 SLA 超时自动决策（auto_approve/auto_reject/keep_waiting + 事件溯源）；webhook_notify 出站工具 + 入站回调端点（token 鉴权触发状态流转）；模板导出/导入（YAML 三级校验/防覆盖）；light_approval 演示模板；14 个新测试 |
| *（未提交）* | **审计收尾·MEDIUM/LOW 全清** | 18 MEDIUM（文档/工程化）+ 18 LOW（类型/清理）全部完成：新增 8 个模块文档 + 更新 3 个（覆盖全部源文件）；`.env.example` 补 `ECOMM_API_KEY`/`ECOMM_WEBHOOK_TOKEN`；CORS 收敛进 `config/default.yaml` + `get_cors_origins()`；`main.py` 9 项魔法数字提取为常量；清理局部 import 与 `__import__` 残留；`state.py` 状态类型、`retry.py` 返回标注修正；加固 2 个时序脆弱测试（spy 化断言 + 协程 close）。65 项审计修复率 100%，pytest 370 全绿 |
| *（未提交）* | **工程化收尾·分层+拆包** | PRD D4 对齐：`auth.py` 迁至 `src/api/auth.py`（core 不再依赖 FastAPI），`main.py` 迁至 `src/api/main.py`，根目录保留 `sys.modules` 别名 shim（`uvicorn src.main:app` 与测试模块属性突变兼容）；deploy/README/文档引用全部更新。前端拆包：10 页面 `React.lazy` + `manualChunks`（charts 313KB / react-vendor 165KB / vendor 40KB），入口 603KB → 6KB，build 零告警 |
| *（未提交）* | **Workflow M5a 批量报表页** | `JobStore.get_batch_report`（总览/模板成功率/耗时直方图/失败原因 Top-5 归一化，租户隔离）+ `GET /api/workflows/batches/report`（路由置于 `/{batch_id}` 之前防吞）+ 前端批量页「📈 数据报表」标签（recharts 成功率/耗时分布图 + 失败原因条形 + 状态分布）；6 个新测试 |
| *（未提交）* | **四域审计·第一轮修复（4 CRITICAL + 10 HIGH）** | 安全/后端/测试/前端四域审计（决策 18）：① 模板路径穿越修复（`load_template`/export/import 白名单校验，Windows `%5C` 穿越实测复现后封堵）；② 租户隔离（未知租户 403 + 工作流控制/决策/复刻/批次控制/双 WS 全量租户校验）；③ 管理面保护（dev 仅本机）+ 前端 API Key 接线（X-API-Key 头 + WS ?api_key= + 设置页输入）；④ retry_step cancel→start 竞态（await 旧任务后重建）；⑤ A/B model_override 真生效（ContextVar）；⑥ webhook SSRF 防护；⑦ 上传流式限额（413）+ 批次上限 MAX_BATCH_ITEMS=100；⑧ WS 回放进 try/finally；⑨ 测试数据安全（tmp 隔离 + secrets 快照还原）；⑩ 前端路由状态重置 + WS 断线重连。新增 31 个测试，pytest 407 全绿 |
| `6d00da1` | **第二轮 MEDIUM 修复**（决策 19） | HITL retry start_index（reset 后生效；双跑竞态核实为误报）、Session TTL 惰性驱逐 + 配额只计活跃态、checkpoint/agent_memory 转 to_thread、AuditLogger 模块级锁、_runtimes 终态清理、批量 done_callback、SQLite 显式 close、限流参数防护；+6 测试，416 全绿 |
| `99fdb14` | **第三轮租户隔离+鉴权矩阵**（决策 20） | audit/memory 租户过滤（tenant_id 字段全链路）、鉴权矩阵 13 例（401/429/WS 4001·4004/管理面 403）、_mask_key 布尔化、A/B 变体/评审上限、require_api_key 死代码删除、batch requeue 竞态修复；+15 测试，431 全绿 |
| `2be168b` | **第四轮技术债+CI+部署**（决策 21） | reconcile_batch_counts 原子调和、熔断器测试隔离、Provider 探测真实断言重写、CSV 编码矩阵、hmac.compare_digest、公开前缀精确匹配、GitHub Actions CI、多阶段 nginx.Dockerfile；+8 测试，439 全绿 |
| `cb03920` | **M5b 审批 SLA 矩阵**（决策 22） | engine sla_matrix（表达式规则顺序匹配→default→遗留策略，事件携带命中来源）；approval_matrix 演示模板；Dashboard 轮询失败横幅；+7 测试，446 全绿 |
| `959e6e3` | **端到端检验修复** | 冒烟检验发现：前端 DEMO_ITEMS 缺 product_images 占位符（JSON 演示批次全死信）→ 修复；engine remember() 漏传 tenant_id（第三轮回归）→ 修复；TestHumanNode 轮询预算 20s→40s 防偶发 |
| `f817592` | **前端依赖核查** | `npm outdated`/`npm audit` 全量核查（npmmirror 无 advisory 端点，改用官方 registry）：5 个漏洞清零 —— vite 5.4.21→7.3.6（修复 esbuild GHSA-67mh-4wv8-2f99 dev-server 请求走私，plugin-react 4.7 兼容无需升）、react-router-dom 6.30.6→7.18.2（6.x 线已 EOL 无修复，CVE-2025-68470 开重定向仅 v7.18+ 修复；声明式路由 API 零改动）、nanoid 3.3.17→3.3.18（postcss 传递依赖）；React 18 / recharts 2 保持（非安全项，大版本延期）；build 零告警 + dev 冒烟 + pytest 446 全绿 |
| `4ef8668` | **C2 每租户独立 Key** | 决策一落地（方案①）：`auth.py` 双凭据体系（全局 admin Key + 租户 Key）——租户 Key 存储在 `config/tenant_keys.yaml`（0600+gitignore）或 `ECOMM_TENANT_KEYS` 引导；中间件重写 X-Tenant-ID 为密钥绑定租户（密钥即身份，防冒充）；管理端点仅 admin（租户 Key 403）；`POST /api/settings/tenant-keys` 分发/轮换/删除 + 设置页租户 Key 管理卡；env 供给的 Key 优先级最高且禁止经设置页修改（防改了不生效）；WS 双端点同权；+17 测试；决策二（平台连接器）经用户确认**不做**，文档关闭 |
| `5b0c7c0` | **DeepSeek V4 模型升级** | 用户指出 DeepSeek 默认模型过时——`deepseek-chat`/`deepseek-reasoner` 为 V3 旧别名，官方当前为 `deepseek-v4-flash`/`deepseek-v4-pro`/`deepseek-v4-flash-vision-exp`。升级：text 默认→`deepseek/deepseek-v4-flash`（+v4-pro 备选）；**DeepSeek 新获视觉能力**——provider 补 `chat_with_vision`（OpenAI 兼容多模态）+ 注册表能力 `["text","vision"]` + vision 备选加 `deepseek/deepseek-v4-flash-vision-exp`；设置页目录更新为 v4 三型号。+5 测试 |
| `db9964b` | **前端美术升级（玻璃拟态+流动动效）** | 用户主导的视觉打磨：① 全局——动态极光背景（双光斑漂移）、玻璃卡片（半透明+backdrop-blur+顶部高光）、流动渐变标题/主按钮、状态点呼吸、`prefers-reduced-motion` 可访问性降级；② 仪表盘——统计卡分色左带+渐变数值、快捷入口 hover 抬升；③ 群聊直播——消息条改为"头像列+角色配色玻璃气泡"、输入框改磨砂圆角 composer+圆形渐变发送钮、**「正在输入」typing 指示器**（会话 running 态显示，三点弹跳动画，带 role=status）；④ 工作流画布——节点玻璃化+运行中脉冲光环。纯前端、零功能/测试影响 |
| `7583530` | **生图占位防混淆 + 模型映射能力过滤** | 用户反馈"agent 看不到上传图"→ 实测诊断：分析员实为真实 DeepSeek 视觉结果，问题在生图环节（仅配 DeepSeek Key，DeepSeek 不支持生图 → 生图员回落 Mock 占位图，审查/合规基于占位图出模板）。修复：① 占位图打徽章+警示横幅+群聊消息提示；② 模型映射编辑器按能力过滤建议（封堵把 DeepSeek 视觉误配进 image 的坑，用户已在设置页误配过）；③ image 默认 seedream-5.0（用户选定即梦）、alternatives 修正为 dall-e-3/flux |
| `89a90bc` | **一键启动程序** | 用户要求：`start.py`（启动后端+前端、端口复用检测、健康就绪等待、自动开浏览器、Ctrl+C 优雅停止、依赖预检、UTF-8/ANSI 控制台、自定义端口参数）+ 双击 `start.bat` + `stop.bat`（按端口清理）+ README 快速开始章节；+6 启动器单测；实测：首次启动/复用/停止三流程全通过 |
| `9024da5` | **测试计划 + P1 前端自动化** | 输出 `docs/test-plan.md`（9 层测试体系 + P1-P6 路线，现状盘点：前端零自动化/real 标记空置/CLI 零测试/无负载测试四大盲区）；P1 完成——Vitest 4 + RTL + jsdom 基建，63 例全绿：ChatPanel 30（msgPreview 全分支/状态/typing/插话回退/过滤器）、api.js 12（含 authHeaders 导出）、ModelMappingEditor 7（能力过滤建议）、Settings 8（租户 Key env 禁用态）、StatusBadge 6；CI 接入 npm test；顺带修复：jsdom 无 scrollIntoView 桩、按钮 aria-label 匹配 |
| `87b2775` | **P2 E2E 冒烟脚本** | `scripts/e2e_smoke.py` 七场景一键全链路（详见 test-plan.md P2），实测多次全绿：自动启动**确定性 Mock** 后端+前端（清空 Provider Key 防 secrets 注入真实 API——实测发现 MOCK_MODE=true 下 Agent 仍走真实 DeepSeek 导致行为随机/视觉卡 SVG）、S6 租户 Key 生命周期需 admin Key（缺失自动跳过）、Windows 清理用 ctypes 双栈 TCP 表（IPv6 行字段顺序坑）+ TerminateProcess 零残留；CI 新增 e2e job |

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
| REST API | ✅ 完成 | 会话 CRUD + 列表 + 决策 + 插话 + 消息 + 记忆 + 审计 + A/B 测试 + 设置（API Key / Agent / 模型映射持久化）+ 工作流（模板/作业/控制/决策/复刻/批量/**导入导出**/**Webhook 回调**） |
| WebSocket | ✅ 完成 | 实时群聊流（双向插话）+ 工作流事件流（已补鉴权） |
| Workflow 编排层 | ✅ M1-M5b | `src/workflow/`：YAML 模板 DSL（6 类节点+group_chat）、极简表达式、SQLite+事件溯源、状态机（自动/手动挡、断点续跑、回跳重试、人工审批+SLA 自动决策+**业务矩阵**）、**7 个模板**；批量调度器；一键风格复刻；webhook 出站/入站连接器；模板导入导出；批量报表（M5a）；审批 SLA 矩阵（M5b） |
| React 前端 | ✅ 完成 | 侧边栏 9 页工作台：仪表盘 / 会话（群聊插话）/ 工作流（画廊+画布+一键复刻）/ 批量任务（列表+**数据报表**）/ Agent 配置 / 设置 / 审计 / 记忆 |
| CLI | ✅ 完成 | `python -m src.cli run` |
| Docker 部署 | ✅ 完成 | 多阶段构建（Node 前端 + Python 后端 + Nginx）+ 非 root 用户 + HEALTHCHECK |
| CI/CD | ✅ 已配置 | GitHub Actions：pytest 全量 + ruff（E/F/I/W）+ 前端生产构建（`.github/workflows/ci.yml`） |

### 2.3 审计修复进度

综合多智能体审计（ultracode）共发现 **65 个问题**，按严重度分级的修复进度：

| 级别 | 总数 | 已修复 | 剩余 | 修复率 |
|------|------|--------|------|--------|
| CRITICAL | 12 | 12 | **0** | 100% |
| HIGH | 17 | 17 | **0** | 100% |
| MEDIUM | 18 | 18 | **0** | 100% |
| LOW | 18 | 18 | **0** | 100% |

> 65/65 全部修复完毕（2026-08-16 收尾）。逐项复查结论见 `docs/code-review.md` §五。

### 2.4 测试覆盖

- **后端 44 个测试文件 / 468 个测试通过**（`pytest` 全量 Mock Mode，无自有 RuntimeWarning）
- **前端 63 个测试通过**（Vitest 4 + RTL，`cd frontend && npm test`，2026-08-23 P1 新增）
- **E2E 冒烟 7 场景**（`python scripts/e2e_smoke.py`，2026-08-23 P2 新增，CI 已接入）
- C2 每租户独立 Key（决策 31）：+17 测试（`test_tenant_keys.py` 16 例——密钥绑定身份/跨租户隔离/管理端点权限/Key CRUD 与持久化/env 供给保护/WS；`test_auth.py` +1 管理面租户角色 403）
- DeepSeek V4 模型升级：+5 测试（`test_deepseek.py`——V4 默认模型/JSON 模式/视觉多模态透传/API 错误）
- P1 前端自动化：+63 测试（ChatPanel 30 / api.js 12 / ModelMappingEditor 7 / Settings 8 / StatusBadge 6）
- P2 E2E 冒烟：S1 群聊 8 轮全产出物 / S2 SLA 自动审批 / S3 批量死信+报表+重跑 / S4 审计+记忆 / S5 设置闭环 / S6 租户 Key 生命周期 / S7 前端+代理
- 时序加固：批量死信重跑测试改为 spy 断言（不再依赖轮询瞬时状态），全量运行多次无偶发失败
- M5a 批量报表：聚合逻辑 3 测试（总览/模板/直方图/失败原因/租户隔离）+ 端点 3 测试（含路由防吞回归）
- 四域审计第一轮（决策 18）：+34 测试（路径穿越 10 参数、租户 403/404、retry_step 重启、model_override 传播、SSRF 8 组、上传 413、批次上限、报表幽灵条目、熔断计 error dict、cancel 唤醒等）
- 第二轮（决策 19）：+6 测试（Session TTL 3 例、HITL start_index 2 例、限流参数校验 1 例）
- 第三轮（决策 20）：+15 测试（鉴权矩阵 13 例：401/错 Key/200/429/WS 4001 与 4004/管理面 403；audit 租户过滤、memory 租户过滤、A/B 变体/评审上限）
- 第四轮（决策 21）：+8 测试（CSV GBK/BOM/无表头/空/不可解码矩阵 6 例、Provider 探测重写为真实断言 +2 净增）
- M5b（决策 22）：+7 测试（sla_matrix 单元 5 例：顺序匹配/default/遗留/缺失值安全 + approval_matrix 端到端 2 例：评分驱动 auto_approve、矩阵 default auto_reject）
- E2E 检验（959e6e3）：启动真实服务冒烟——会话 8 轮群聊 / 工作流 8 步 / 审批矩阵自动决策 / 批量 2/2 / 报表 / 记忆 / 审计 / 设置全部通过；修复演示数据与记忆租户透传 2 个缺陷
- 覆盖范围：
  - `test_api/` — FastAPI 端点测试（含设置/会话列表/密钥持久化/模型映射/群聊插话/**租户 Key**）
  - `test_agents/` — 9 个 Agent 单测（含风格拆解员插件化注册）
  - `test_chat/` — 引擎 / HITL / 反幻觉 / 模式
  - `test_harness/` — 熔断 / 限流 / 重试 / 超时 / 记忆 / 成本 / 集成 / 审计 / A-B / 图片预处理
  - `test_workflow/` — 表达式 / 模板 DSL / 引擎（分支/跳过/回跳/恢复）/ 手动挡控制 / 批量调度 / 风格复刻 / **SLA 超时决策 / webhook 工具 / 导入导出 / 入站回调 / 批量报表（M5a）** / API（100 个测试）
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

**MEDIUM（18 个）—— ✅ 2026-08-16 全部完成（文档与工程化）：**

1. ~~14 个模块缺少文档~~ → ✅ 新增 `docs/modules/` 8 篇：`workflow.md`（7 模块+6 模板）、`auth.md`、`tenant.md`、`logging.md`、`storage.md`、`harness-extended.md`（限流/成本/审计/上下文/记忆/输入输出管道）、`ab-testing.md`、`image-preprocessor.md`；更新 `agents.md`（补风格拆解员）、`api.md`（端点概览刷新）、`deploy.md`（环境变量表）。全部 56 个源文件均有文档覆盖
2. ~~`.env.example` 未含 `ECOMM_API_KEY`~~ → ✅ 补 `ECOMM_API_KEY` + `ECOMM_WEBHOOK_TOKEN`（M4 回调 token），含使用说明
3. ~~CORS 白名单硬编码待收敛~~ → ✅ 白名单迁入 `config/default.yaml` 的 `app.cors_origins`，新增 `config.get_cors_origins()`（env `ECOMM_CORS_ORIGINS` 覆盖 → YAML → 内置默认三级回退）
4. ~~若干魔法数字待提取为常量~~ → ✅ `main.py` 顶部 9 项常量（`MAX_UPLOAD_IMAGES`/`PREPROCESS_MAX_PIXELS`/`PREPROCESS_JPEG_QUALITY`/`MAX_CHECKPOINT_RESTORE`/`AUDIT_TAIL`/`EVENT_TAIL`/`JOB_LIST_LIMIT`/`MAX_INTERJECTION_CHARS`/`MAX_REPLICATE_IMAGES`）
5. ~~其余见 `docs/code-review.md` 完整清单~~ → ✅ M17-M29、N16-N28 逐项复查确认已修复（详见 `code-review.md` §五 复查结论表）

**LOW（18 个）—— ✅ 2026-08-16 全部完成（类型标注与清理）：**

- ~~类型标注补全~~ → ✅ `state.py` `status: RunStatus`、`retry.py` `-> Any`（原 `-> any` 误用内建函数名）、`timeout.py`/`retry.py` 的 `Optional`/`Coroutine` 标注已齐
- ~~死代码清理~~ → ✅ `InputPipeline`/`OutputPipeline` 确认已有调用方与测试覆盖（非死代码）；`checkpoint.py` 已被会话/引擎/启动恢复使用
- ~~`datetime.utcnow()` 残留替换等~~ → ✅ 全局无 `utcnow` 残留；清理 `main.py`/`post_process.py` 局部 `import asyncio`、`main.py`/`logging_config.py` 内联 `__import__("datetime")`
- **顺带加固**：`test_retry_failed_reruns_dead_letter` 时序偶发失败（轮询错过瞬时 running 态）→ 改为 spy 统计新一轮 instantiate 调用次数（确定性断言）；`test_timeout` 补 `coro.close()` 消除 "never awaited" RuntimeWarning

**架构待办（非阻塞）：**

- ~~`auth.py` 位于 `src/core/`，依赖 FastAPI（违反"core 不依赖上层"的依赖方向）~~ → ✅ 2026-08-16 迁至 `src/api/auth.py`，core 层不再依赖 FastAPI/Starlette
- ~~缺少 `src/api/` 分层：`main.py` 直接放在 `src/` 根目录，PRD D4 原设计有 `api/` 独立目录~~ → ✅ 2026-08-16 `main.py` 迁至 `src/api/main.py`；`src/main.py` 保留为 `sys.modules` 别名 shim（旧命令/导入路径兼容）

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
├── core/          # 数据模型 + 配置 + 消息类型 + 租户 + 日志
├── providers/     # AI 服务商适配（7 个 Provider）
├── agents/        # Agent 注册中心 + 9 个 Agent 实现
├── chat/          # 群聊引擎（ChatEngine + Session + Message + Broadcaster）
├── harness/       # 可靠性模块（重试/超时/熔断/限流/审计/记忆/预处理/A-B）
├── workflow/      # 工作流编排（模板/状态机/SQLite/批量）
├── api/           # FastAPI 入口 + 鉴权中间件（PRD D4）
├── cli.py         # CLI 入口
└── storage/       # 持久化（checkpoint）
```

依赖方向：`core ← providers ← agents ← chat ← api/cli`（workflow 在 chat 之上、api 之下）

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
| 21 | "继续M4" | 实现 M4：审批 SLA 超时自动决策 + webhook 出站/入站连接器 + 模板导入导出 + light_approval 演示模板，14 个新测试全绿 |
| 22 | "按 progress.md 第 5.3 节开放决策选择下一项任务继续" | 选择「MEDIUM/LOW 审计收尾」：全量基线发现 1 个时序偶发失败（死信重跑轮询错过瞬时 running）→ 加固为 spy 断言；完成 18 MEDIUM（8 篇新模块文档 + 3 篇更新、.env.example 补鉴权变量、CORS 收敛进 config、main.py 魔法数字提取常量）+ 18 LOW（类型标注、局部 import/`__import__` 清理）；code-review.md 补 §五 复查结论表；pytest 370 全绿、前端 build 成功 |
| 23 | "继续" | 继续 5.3 剩余工程化项：① auth.py 迁至 `src/api/`（修复依赖方向）+ main.py 迁入 `src/api/main.py`（PRD D4），`src/main.py` 保留 `sys.modules` 别名 shim（测试模块属性突变兼容）；② 前端拆包（10 页面 React.lazy + manualChunks），入口 603KB→6KB；pytest 370 全绿、/health 冒烟正常 |
| 24 | "先继续吧" | 实现 Workflow M5a 批量报表：`JobStore.get_batch_report` 聚合（总览/模板成功率/耗时直方图/失败原因 Top-5 归一化）+ `GET /api/workflows/batches/report` + 前端批量页「数据报表」标签（recharts 双图 + 失败原因条形 + 状态分布）；6 个新测试全绿、build 零告警 |
| 25 | "先检查一下现在的项目有什么问题" | 四域并行审计（安全/后端正确性/测试质量/前端+文档）+ 自查，发现 4 CRITICAL + 10 HIGH + ~25 MEDIUM + ~15 LOW；逐项核验（路径穿越、报表幽灵条目等实测复现）；经用户确认后完成第一轮修复（CRITICAL+HIGH 全清，见里程碑表），新增 31 测试，pytest 407 全绿、前端 build 成功 |
| 26 | "继续任务吧" | 提交 git `875382a`（固化前 6 轮 v0.2.0）；完成第二轮 MEDIUM（决策 19）：HITL retry start_index（reset 后生效，双跑竞态核实为误报）、Session TTL + 配额只计活跃态、checkpoint/agent_memory 转 to_thread、AuditLogger 模块级锁、_runtimes 终态清理、批量 done_callback、SQLite 显式 close、限流参数防护、批次上限双保险；+6 测试，pytest 416 全绿 |
| 27 | "继续" | 第三轮收尾（决策 20）：鉴权矩阵测试（401/错 Key/200/429/WS 4001·4004/管理面 403，`test_api/test_auth.py` 13 例）；audit/memory 租户过滤（AuditLogger 补 tenant_id 字段、AgentMemory 按租户过滤、端点传租户）；`_mask_key` 布尔化（不再返回密钥片段 + 前端适配 + 删除死代码）；A/B 变体（≤8）/评审（≤5）上限；删除 `require_api_key` 死代码；batch retry_failed 终态竞态（requeue 标志 + 循环重入）；+15 测试，pytest 431 全绿 |
| 28 | "后面还有什么计划" + "按你的建议来" | 第四轮 + 工程化配套（决策 21）：① 技术债收尾——`reconcile_batch_counts` 原子调和（只增不减，替代绝对覆盖）、熔断器全局单例测试隔离（autouse 快照/还原）、Provider 探测测试重写为真实断言（monkeypatch + 真注册表）、CSV GBK/BOM/无表头/空/不可解码矩阵、`hmac.compare_digest`（auth/webhook/双 WS）、公开前缀精确匹配（防 /healthX）；② CI 接入（GitHub Actions：pytest + ruff + 前端构建；ruff 本地因 pip 网络受限仅在 CI 运行）；③ Docker 部署口径修复（多阶段 `nginx.Dockerfile` 前端产物打进镜像 + `nginx.conf.template` envsubst + compose 改造，废除空 `frontend_dist` 卷）；+8 测试，pytest 439 全绿 |
| 29 | "继续" | M5b 审批 SLA 业务矩阵（决策 22）：engine `sla_matrix`（表达式规则顺序匹配 → default → 遗留 on_sla_timeout，超时事件携带命中策略）；`approval_matrix` 演示模板（评分≥75 自动通过/≥60 自动拒绝/默认继续等待）；前端 Dashboard 轮询失败横幅（审计 L6）；+7 测试，pytest 446 全绿 |
| 30 | "按 progress.md 第 5.3 节遗留的开放决策选择下一项任务继续" | 选择「前端依赖核查」（C2 租户凭据绑定需用户选方案、平台级连接器需业务决策，均非自足任务）：npm audit 5 漏洞清零 —— vite 7.3.6（esbuild 漏洞）+ react-router-dom 7.18.2（6.x EOL，CVE 仅 v7.18+ 修复）+ nanoid 3.3.18；声明式路由 API 零改动；build 零告警、dev server 冒烟、pytest 446 全绿；React 19 / recharts 3 / Vite 8 大版本升级记录为后续候选 |
| 31 | "决策1用第一个方案，决策2不需要打通" | 两项开放决策定案：**C2 租户凭据绑定 → 方案①每租户独立 Key**（实施完毕，见里程碑表）；**平台级连接器 → 明确不做**（文档关闭）。C2 实施要点：① 租户 Key 即身份——中间件把 X-Tenant-ID 重写为密钥绑定租户（`_set_scope_header` 改 ASGI scope，下游 FastAPI 按 scope 重建 Request 时生效），冒充租户头彻底封死；② 任一 Key 配置即强制鉴权（不再只看全局 Key），全未配置仍是开发模式开放（向后兼容）；③ 管理面按 `request.state.auth_role` 分权：admin 放行 / tenant 403 / 开发模式仅本机；④ WS 端点复用同一 `authenticate_api_key`，绑定身份覆盖 tenant 查询参数；⑤ 租户 Key 管理端点仅 admin，租户须已在 ECOMM_TENANTS 注册、Key 长度 16-256，轮换即时生效（旧 Key 立刻失效）；⑥ 环境变量供给的 Key 优先级最高、设置页禁止修改（冒烟实测暴露"改了不生效"陷阱后加固），UI 标注来源；⑦ 测试隔离教训——fixture teardown 在 monkeypatch 恢复环境前 reload 会把测试 Key 缓存进模块级状态导致后续模块全 401，改为 teardown 置 None 惰性重载；+17 测试，pytest 463 全绿 |
| 32 | "你配置模型的地方deepseek的默认模型是否太落后或者缺少了" | DeepSeek 官方 API 文档核实：`deepseek-chat`/`deepseek-reasoner` 为 V3 旧别名，当前为 `deepseek-v4-flash`/`deepseek-v4-pro`/`deepseek-v4-flash-vision-exp`（多模态）；用户选定 text 默认用 `deepseek-v4-flash`。升级：models.yaml text 默认、provider 默认参数、`_MODEL_CATALOG`、**补 `chat_with_vision`（OpenAI 兼容多模态透传 + 总是尝试 JSON 解析）+ 注册表能力扩为 `["text","vision"]`**；+5 测试 |
| 33 | "前端的美术效果怎么样，要不要优化一下" + "加上玻璃视觉，流动动效" + "群聊对话框/聊天条优化" + "加正在输入" | 前端美术升级（`db9964b`）：动态极光背景、玻璃卡片（backdrop-blur）、流动渐变标题/主按钮、统计卡分色、群聊"头像列+玻璃气泡"、磨砂 composer、**typing 指示器**、工作流节点运行脉冲；`prefers-reduced-motion` 降级。全程纯 CSS/布局，零功能影响 |
| 34 | "上传图片后 agent 好像看不到图片" | 实测诊断（真实 DeepSeek 视觉调用复现）：分析员**确实看到图**（读出了德国护肝胶囊细节），问题在**生图环节**——DeepSeek 不支持生图，生图员回落 Mock 占位图，审查/合规基于占位图出模板。修复（`7583530`）：占位图徽章+警示横幅+群聊提示；模型映射编辑器按能力过滤建议（用户已误配过一次）；image 默认 seedream-5.0 |
| 35 | "帮我做一个一键启动的程序" | `start.py` + `start.bat` + `stop.bat`（`89a90bc`）：一键拉起前后端、就绪等待、自动开浏览器、端口复用检测、Ctrl+C 优雅停止、依赖预检、自定义端口 |
| 36 | "先设计一个测试计划" + "按计划继续" | 输出 `docs/test-plan.md`（9 层体系 + P1-P6）；P1 前端自动化（`9024da5`，Vitest 63 例 + CI）；P2 E2E 冒烟（`87b2775`，7 场景 + CI e2e job）。P2 实测三大教训：**MOCK_MODE=true 不等于确定性**（secrets.yaml 注入 Key 会让 Agent 走真实 API）→ 自动启动清空 Provider Key；租户 Key 轮换/删除仅 admin 可操作；Windows 多级孙进程 + IPv6 监听清理（ctypes 双栈 TCP 表 + TerminateProcess） |

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

**决策 14：M4 连接器的边界**
① **SLA 归一化**：`auto_approve/auto_reject` 归一化到路由键 approve/reject，步骤输出保留原始决策 + `sla_timeout` 标记（可审计）；② **出站 best-effort**：`webhook_notify` 失败返回结构化错误而不抛异常，通知不中断主流程；③ **入站最小闭环**：webhook 接收端点复用 `decide_human` 达成"外部回调触发状态流转"验收，`ECOMM_WEBHOOK_TOKEN` 未配置即 503 停用；平台级连接器（淘宝/Amazon 商品拉取与回传）留作 M4 之外的后继工作。

**决策 15：审计收尾走"复查 + 补文档 + 加固测试"三线并行**
用户授权从 5.3 开放决策中自选任务，选择 MEDIUM/LOW 收尾使审计闭环（65/65 全绿）：① **逐项复查而非盲目标记** —— code-review.md 的 M/N 条目多数已被 CRITICAL/HIGH 轮顺带修复，本次先逐一代码核实再写复查结论表（含 3 个"部分修复/非缺陷"的诚实标注）；② **文档按关注点分组而非一文件一模块** —— 7 个 workflow 文件合一篇、7 个 harness 扩展模块合一篇，避免 docs/modules 膨胀到 20+ 文件；③ **测试加固继续 spy 化** —— 全量基线暴露 `test_retry_failed_reruns_dead_letter` 偶发失败（轮询 0.05s 错过瞬时 running 态），沿用决策 6/13 的思路改为统计新一轮 instantiate 调用次数做确定性断言，而非放宽轮询或加 sleep。

**决策 16：分层迁移用 `sys.modules` 别名 shim 保兼容**
把 `main.py` 迁入 `src/api/` 时，测试大量以 `import src.main as main_mod` + **突变模块属性**（`main_mod._provider_registry = ...`）方式 monkeypatch 端点全局状态。普通 `from src.api.main import *` 转发 shim 会把属性写入 shim 自身命名空间而端点不可见（隐性断链）。改用 `sys.modules[__name__] = _impl` 模块别名：所有 `import src.main` 拿到的是 `src.api.main` 模块对象本身，读写完全等价，测试零改动通过；同时把 deploy/README/文档的命令统一升级到 `uvicorn src.api.main:app`（shim 仅作对外兜底）。前端拆包则坚持「页面级 lazy 优先于 manualChunks」：先按路由拆 10 页（每页 4-21KB），再用 manualChunks 把 recharts+d3（313KB）从页面中抽出独立 charts chunk，实现「非图表页完全不带图表库」—— 两者叠加才把入口从 603KB 压到 6KB。

**决策 17：报表聚合走 SQL 联表 + Python 归一化，路由顺序防吞**
M5a 批量报表的三个实现要点：① **数据源联表**：条目耗时/成本取自 `batch_items LEFT JOIN jobs`（`job_id != ''` 防死信项错误关联），无 job 的死信项不计入耗时分布；② **失败原因归一化**：错误文本取首个冒号前的前缀（"必填输入缺失: product_images" → "必填输入缺失"）再聚合 Top-5，避免同类错误因参数差异被拆散；③ **路由顺序陷阱**：`GET /batches/report` 必须声明在 `/batches/{batch_id}` 之前，否则 `report` 被当作 batch_id 404 —— 用专项测试（`test_report_route_not_shadowed_by_batch_id`）锁死该回归。报表是租户隔离的聚合查询（`tenant_id=""` 时全量供管理视角）。

**决策 18：四域并行审计 + 第一轮修复（4 CRITICAL + 10 HIGH）**
"先检查项目有什么问题" → 派 4 个独立审计代理（安全/后端正确性/测试质量/前端+文档）并行扫描 + 自查，交叉印证后**关键指控全部实测复核**（路径穿越用 TestClient 复现读回 models.yaml、报表幽灵条目用临时库复现、Provider 探测零断言逐行确认）。修复遵循「先修能落地的高危项，架构级留作显式决策」：① **路径穿越**修在共享入口（`_safe_template_path` 白名单 + export 复用），import 端点补 `:`/`..` 校验——一处修复覆盖 export/instantiate/batch 三条攻击面；② **租户 IDOR**统一在端点层 `get_job/get_batch` 后比对 tenant（与读取端点同模式），WS 用可选 `tenant` 查询参数；未知租户从"回退 default"改为 403（`TenantRegistry.get` 返回 None）；③ **管理面保护**用"未配置 Key 时仅本机"的中间策略（`_require_admin_access`），把「凭据绑定租户」「每租户独立 Key」等架构级改造明确列为 C2 遗留、待用户决策；④ **A/B model_override**选择 ContextVar 而非实例属性——变体并行执行共享同一 Agent 实例，实例属性必然竞态，ContextVar 按协程上下文隔离恰好线程/并发安全，顺带删除死代码；⑤ **测试数据安全**（secrets.yaml 快照还原、memory tmp 隔离）优先于补测试——审计明确指出"新增测试会加剧污染"。

**决策 19：第二轮 MEDIUM 修复（正确性/资源治理）+ 首个提交点**
"继续任务吧" → 先提交 git（`875382a`，v0.2.0 里程碑，前 6 轮 60 文件），再修第二轮 9 项。三点值得记录：① **HITL 双跑竞态经代码核实为误报**——端点状态检查与 `resume_after_hitl` 置 RUNNING 之间无 await 点，单进程事件循环下不可能双跑；但同条目里的 `_workflow_index=5` 被 `reset()` 清零是真实 bug，修复方式是给 `run()` 加 `start_index` 参数（reset 之后生效），并诚实记录"误报 + 真 bug"的拆分；② **`_runtimes` 终态清理的兼容性**——弹出运行期镜像后，retry_step/replicate/control 都靠 `setdefault` 重建，两个既有测试直接访问 `engine._runtimes[...].task` 之所以不炸，是因为 asyncio 参数求值先于 await 捕获了 task 引用；③ **TTL 用惰性驱逐而非定时器**——`get/list/count` 入口清扫即可，避免引入后台任务生命周期管理。

**决策 20：第三轮收尾（隔离缺口 + 安全边界测试）**
继续"继续"→ 第三轮把四域审计剩余的高价值项清掉：① **租户过滤下沉到存储层**——audit 条目补 `tenant_id` 字段（旧条目无该字段 → 过滤时按空值处理，不影响存量）、AgentMemory 的 remember/recall/stats 全部加租户参数，API 端点传 `X-Tenant-ID`，调用链（engine 记忆召回、prompt_gen 相似召回）同步透传；② **鉴权矩阵测试优先于代码**——安全边界（401/429/WS/管理面）此前零覆盖，先写 `test_api/test_auth.py` 13 例再改代码，`_AUTH_FAILURES` 用 autouse fixture 复位防交叉污染；③ **`_mask_key` 布尔化**——密钥片段（前 4 后 4 共 8 字符）从 API 响应中彻底移除（配置态布尔即可），前端同步去掉 masked 展示，原 `_mask_key` 死代码删除；④ **batch retry_failed 竞态**用"requeue 标志 + run 循环重入"修复：控制指令在收尾窗口到达时不再丢项——标志让当前 run 在 finalize 后重新入队一轮（无标志时保持原有"一次收尾即终态"语义，避免空转）。

**决策 21：第四轮技术债收尾 + CI + 部署口径**
"按你的建议来"→ 三线并行：① **技术债**——`update_batch_counts` 竞态改为 `reconcile_batch_counts`（以 items 表为准、`max()` 只增不减的原子调和，替代绝对覆盖）；熔断器全局单例用 autouse fixture 快照/还原隔离（消除"定义在前才通过"的顺序耦合）；Provider 探测测试从零断言/恒真断言重写为 monkeypatch + 真实 `ProviderRegistry` 断言（含"部分火山密钥不探测"负例）；CSV 编码矩阵补 GBK/BOM/无表头/空/不可解码 6 例；`hmac.compare_digest` 覆盖 auth/webhook/双 WS；公开前缀从 `startswith` 改为精确路径或子路径匹配；② **CI**——新增 `.github/workflows/ci.yml`（pytest 全量 + ruff E/F/I/W + 前端 npm ci/build）；本地 pip 网络受限装不了 ruff，明确"ruff 仅在 CI 运行"并写进 deploy.md；③ **部署口径**——多阶段 `nginx.Dockerfile`（node 构建 → 产物打进 nginx 镜像）+ `nginx.conf.template`（envsubst 注入 `API_HOST`，compose 下为 `api` 服务名）+ compose 废除空 `frontend_dist` 卷，progress.md 的"Docker 多阶段构建"从漂移变成事实。

**决策 22：M5b 审批 SLA 业务矩阵**
"继续"→ M5b 第一块落地：① **矩阵 DSL 复用表达式引擎**——`sla_matrix` 是 `when: <极简表达式> + action` 规则列表，按声明序匹配（与 condition 节点同构），`default` 兜底，未命中回退遗留 `on_sla_timeout`；规则引用 `$steps.review.outputs.overall_score` 等上游产出，天然复用"缺失值恒 False"的安全语义；② **可审计性**——步骤输出保留原始决策（`auto_approve`，M4 决策 14 语义），路由键归一化，超时事件新增 `policy` 字段标记命中来源（`matrix:<expr>` / `matrix:default` / `legacy`）；③ **测试先行的两个教训**——`build_scope` 的 `step_outputs` 参数要求裸 outputs（测试助手多包一层 `outputs` 导致路径解析失败，先跑红再修）；端到端断言需区分"原始决策 vs 归一化路由键"。

### 5.3 遗留的开放决策

- ~~**依赖方向**：`auth.py` 放 `core/` 还是迁至独立中间件层？（PRD D4 的"core 不依赖上层"原则 vs 现实实现）~~ → ✅ 2026-08-16 迁至 `src/api/auth.py`（依赖 FastAPI/Starlette 的中间件归 API 层）
- ~~**`src/api/` 分层**：是否按 PRD 原设计将 `main.py` 迁入 `api/` 目录？~~ → ✅ 2026-08-16 按 PRD D4 迁入 `src/api/main.py`，根目录保留 `sys.modules` 别名 shim（`uvicorn src.main:app` 与测试模块属性突变均兼容）
- ~~**`ABTestRunner` 的 model_override 语义**：提示词注入不切换真实模型~~ → ✅ 2026-08-18 修复（决策 18）：`BaseAgent.execute(model_override=...)` 经 ContextVar 传递，`_model_kwargs()` 优先取覆盖值，并发变体互不干扰；删除提示词注入与死代码恢复
- ~~**前端构建产物体积**：单 chunk 603KB（recharts 占大头），可用 `manualChunks`/动态 import 拆包优化~~ → ✅ 2026-08-16 完成：10 个页面 `React.lazy` 按路由拆包（每页 3.8-21KB）+ `manualChunks` 分离 `charts`（recharts+d3，仅图表页按需加载）/ `react-vendor`（165KB）/ `vendor`（40KB）；入口 chunk 603KB → 6KB，build 零告警
- ~~**设置页敏感操作**：`/api/settings/*` 写操作在 `ECOMM_API_KEY` 未配置时开放（开发模式）~~ → ✅ 2026-08-18 修复（决策 18）：管理面增加 `_require_admin_access`——未配置 Key 时仅允许本机（127.0.0.1/::1/localhost），远程一律 403；配置 Key 后由 AuthMiddleware 全局保护
- ~~**C2 租户凭据绑定**（架构级）~~ → ✅ 2026-08-22 用户选定**方案①每租户独立 Key**并实施（决策 31）：`auth.py` 双凭据（全局 admin Key / 租户 Key）、租户 Key 即身份（X-Tenant-ID 被重写，防冒充）、管理端点仅 admin、`POST /api/settings/tenant-keys` + 设置页管理卡、WS 同权；+14 测试
- ~~**平台级连接器**（淘宝/Amazon 商品库拉取与发布回传）~~ → ✅ 2026-08-22 **用户确认不做**（决策 31）。现有 webhook 出站/入站连接器保留，平台对接留待未来需求
- **Workflow M5b**：~~审批 SLA 业务矩阵~~ ✅ 2026-08-20 已交付（决策 22，approval_matrix 模板）
- ~~MEDIUM/LOW 是否继续修~~ → ✅ 2026-08-16 已全部完成（决策 15），65/65 修复率 100%。
- ~~**前端依赖核查**（code-review.md §六 第五轮候选的 `npm outdated`）~~ → ✅ 2026-08-22 完成：`npm audit` 5 漏洞清零（vite 7.3.6 修复 esbuild GHSA-67mh-4wv8-2f99、react-router-dom 7.18.2 修复 GHSA-wrjc-x8rr-h8h6 + GHSA-337j-9hxr-rhxg、nanoid 3.3.18 修复 GHSA-2v37-7h3g-55p8）。**React 19 / recharts 3 / Vite 8 大版本升级**列为后续候选：无安全收益、需回归成本（recharts 3 为破坏性重写）

> ⚠️ **2026-08-23 快照**：四域审计 65/65（决策 18-21）+ M5b 审批矩阵（决策 22）+ 前端依赖核查 + C2 每租户独立 Key + DeepSeek V4 升级 + 前端美术升级 + 一键启动 + **测试计划 P1（前端 63 例）/ P2（E2E 7 场景）** 全部完成。**测试资产：后端 pytest 468 全绿 + 前端 Vitest 63 全绿 + E2E 冒烟 7 场景全绿**；前端 build 零告警（vite 7 / react-router 7）、`npm audit` 0 漏洞、CI 四道门禁（pytest / ruff / vitest+build / e2e）、24 笔提交工作树干净。**5.3 节遗留开放决策全部关闭**。后续候选：test-plan.md 的 **P3 后端补缺**（CLI 测试/SQLite 韧性/OpenAPI 快照）→ P4 真实 API 套件 → P5 安全+性能 → P6 CI 补强；React 19 / recharts 3 / Vite 8 大版本升级（无安全收益，暂缓）。

- **测试计划后续阶段（P3-P6，当前主待办）**——见 `docs/test-plan.md`：P3 后端补缺（CLI 测试 / SQLite 韧性 / OpenAPI 快照 / 错误响应契约）、P4 真实 API 套件（DeepSeek/Seedream 真实调用，需 Key）、P5 安全+性能（上传模糊测试 / slow 负载套件）、P6 CI 补强（Docker 构建 / npm audit 门禁）。**P1（前端 63 例）/ P2（E2E 7 场景）已完成**

---

## 6. 快速验证命令

```bash
# 后端全量测试（Mock Mode）
pytest

# 前端单元/组件测试
cd frontend && npm test

# E2E 冒烟（自动起停确定性 Mock 服务，7 场景）
python scripts/e2e_smoke.py

# 前端构建
cd frontend && npm run build

# 一键启动前后端（Windows 可双击 start.bat；停止用 stop.bat）
python start.py

# 后端启动（Mock Mode）
uvicorn src.api.main:app --reload --port 8000

# Docker 部署
docker-compose -f deploy/docker-compose.yml up -d
```

---

## 附：文档索引

| 文档 | 路径 | 内容 |
|------|------|------|
| 产品需求文档 | `docs/prd.md` | 完整 PRD（问题、方案、决策 D1-D8、数据模型、API 契约） |
| 代码审查报告 | `docs/code-review.md` | 65 个审计问题的完整清单与修复优先级（§五：全部复查通过） |
| 模块文档 | `docs/modules/*.md` | 16 篇：核心 8 篇 + workflow/auth/tenant/logging/storage/harness-extended/ab-testing/image-preprocessor，覆盖全部源文件 |
| **Workflow 设计** | `docs/workflow-design.md` | 编排层设计（模板 DSL / 状态机 / 批量 / 画布），借鉴 OiiOii 范式，M1-M5b 已实施 |
| **测试计划** | `docs/test-plan.md` | 9 层测试体系 + P1-P6 路线（P1 前端自动化 ✅ / P2 E2E 冒烟 ✅，P3-P6 待实施） |
| 本状态总览 | `docs/progress.md` | 进度 / 计划 / 思路 / 决策（本文档） |
