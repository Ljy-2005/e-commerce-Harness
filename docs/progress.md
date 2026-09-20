# E-Commerce Harness — 项目状态总览

> 最后更新：2026-08-23（P3 后端补缺完成）
> 本文档汇总项目**进度**、**现有计划**、**开发思路**与**关键决策对话**，作为团队交接与后续开发的单一事实来源（Single Source of Truth）。

---

## 0. 新会话接续指南（复制这段话作为新对话的开场白）

```
这是 E-Commerce Harness 项目（群聊式多智能体电商商品图生成），
工作目录 D:\vscode-project\e-commerce Harness。
请先读 docs/progress.md（第 0 节之外的全部内容）、docs/workflow-design.md 和 docs/test-plan.md，
然后按 progress.md 第 5.3 节「遗留的开放决策」/ test-plan.md 的 P3-P6 阶段选择下一项任务继续。
常用命令：
  pytest                          # 后端全量（当前 1190 通过，Mock 确定性）
  cd frontend && npm test         # 前端 Vitest（当前 176 通过）
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
| 测试 | pytest（asyncio_mode=auto），91 个测试文件，1190 个测试通过（确定性 Mock，约 4 分钟全绿；运行期目录与生成图输出目录均已隔离到 tmp，不污染真实 data/、config/ 与 output/；凭据环境变量逐测试快照/恢复，清单含 secrets.yaml 全部键）；前端 Vitest 176 例；E2E 冒烟 7/7 场景通过 |
| 部署 | Docker 多阶段构建（Node 前端 + Python 后端 + Nginx） |

---

## 2. 项目进度

### 2.1 里程碑时间线

按 git 提交顺序（当前 **30 笔提交**，工作树干净；表中标注「未提交」的历史行系早期记录，其工作已随后续提交固化）：

| 提交 | 阶段 | 交付内容 |
|------|------|---------|
| *（未提交）* | **协调者上下文人话化 + 提示词契约修复 + 术语校正（A109，用户实测追问）** | 用户："agent 的对话里面显示的会很直白，会给出代码原文" → 查前端时**顺带发现协调者侧同一问题更贵**：`coordinator.py:498` 用 `str(content)[:400]` 拼群聊历史，是**单引号 Python 字典 repr**（非 JSON），实录 10 条里 **5 条被 400 字截断**、截掉的正是体检结论/生图说明/审查意见，且**每轮重复进上下文**（成本对账：会话 `bb6cc0fa56a54921` 审计只记 $0.010999、`cost_so_far` $0.018143，**差额 $0.007144 全在协调者每轮的 `decide` 调用**，且这些调用**没有审计行**）。**修复**：新增 `readable_content()`（`message`→`error`→邀请→计数摘要→**键名清单兜底，绝不吐原始结构**，与前端 `chatFormat.js` 同一纪律）+ `HISTORY_ITEM_CHARS=500`；实测同一会话 **2780 → 1982 字符、截断 5 → 0 条**，6 项关键信息（体检结论/丢失槽位/缺素材/生图失败原因/审查失败原因/体检播报句）**全部保住**。**同时修 4 处提示词缺陷**：① `analyst.yaml` schema **缺 `usage` 字段**（下游 `slot_copy.py:119` 一直在读它 → `main_usage` 必然 blocked，10 张只交 8 张）→ 补字段 + 要求**逐面转录**（侧/背/盒底）；② `prompt_gen.yaml:34` 的**过期指令**「同一套里不同槽位要用不同档案」（A97 改成单一风格后漏改）→ 改为"一轮会话只给你**一套**风格词，整套共用"；③ `prompt_reviewer.yaml` 的 `scene ≤180 字` 与系统 `MAX_SCENE_CHARS=260` 冲突（10/10 张触发静默裁剪）→ 对齐并补"缺背景/光位/版式/留白会被体检退回"（实录 6/10 被退回）；④ `coordinator.yaml` 补「怎么用当前产物状态」4 条（已就绪不要重邀 / ⚠⛔ 照它办 / 逐项对照交付物含图像后处理 / 同 Agent 不重复邀请）。**术语校正（用户："p3 应该是一套风格词，而不是一个风格词"）**：核实一个词条 = **12 个渲染字段一起用**，代码选中的本来就是"一套" → 全仓库 19 处说法统一为「一套风格词」（`config/style_library.yaml`、`config/default.yaml`、`style_library.py`/`style_store.py`/`core/config.py` 注释、`main.py` 响应文案、前端 `Styles.jsx`/`Settings.jsx`/`Session.jsx`/`StyleCard.jsx`/`StyleModal.jsx`/`api.js`、测试与模块文档），并在 `Styles.jsx` 明写"**一套 = 背景/构图/光影/材质/颜色分工等一组字段**"。测试：后端 **1645 → 1680**（新增 `test_prompt_contracts.py` 10 条契约断言 + coordinator 可读内容 6 条 + slot_copy 2 条），前端 313 全绿、`npm run build` 干净 |
| *（未提交）* | **群聊显示层脱敏（A108，用户实测反馈）** | 用户："会话里 agent 的对话里面显示的会很直白，会给出代码原文，但实际上我们就只要文字的内容显示，这样也能减少隐私的暴露"。**取证确认两处泄漏**：① `ChatPanel.msgPreview` 的兜底就是 `JSON.stringify(content).slice(0,120)` —— 本次会话的「生图体检」消息（`quality_report`/`set_plan_coverage`/`set_plan_summary`/`message`）没命中任何识别键，**本该显示的那句 `message` 被 JSON 顶掉**；② 「展开完整内容」是 `JSON.stringify(content, null, 2)` 全量原文 —— 生图员产物里带**方舟 TOS 签名地址**（查询串含 `X-Tos-Credential`/`X-Tos-Signature`）、整段 `base64_data`、`generation_params`；③ 会话详情页「完整提示词 / 完整分析结果」同款（`JSON.stringify`）。**落地**：新建 `frontend/src/chatFormat.js`（纯展示逻辑，可单测）——`msgPreview` 把 `message` 提升为通用文字字段并**彻底删掉 JSON 兜底**（退化为「字段清单摘要」）、`displayContent` 脱敏（base64 → 占位、签名地址 → `［图片地址（已隐藏签名）］`、落盘路径只留文件名）、`detailFields` 摘掉内部实现键（`generation_params` 等）、`hiddenKeys` **如实列出隐藏了什么**（"少暴露"≠"藏起来"）。ChatPanel 展开后正文改为人话（`message`/预览句 → 结构化卡片 → 详情字段），原始字段降到默认折叠的「技术详情（原始字段）」；Session 页 `DetailsJson` 改为「中文标签 + 值」的逐项渲染 + 技术详情折叠，逐张提示词复用可读列表。**刻意不动后端**：消息信封与协调者/审查员读到的上下文完全不变（脱敏只在展示层）→ 无流程风险。**真数据零成本回归**（会话 `bb6cc0fa56a54921`）：10 条消息折叠态 **0 条回落 JSON**；10 张真实生图产物里签名参数名/真实签名值/图片域名/本地目录结构/`reference_bytes` 在"给用户看的"口径下**全部消失**，技术详情侧仍可查。测试：前端 294→**313**（+19：`chatFormat.test.js` 14 条 + ChatPanel/Session 各若干），`npm run build` 干净；**后端零改动** |
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
| *（未提交）* | **P3 后端补缺（test-plan）** | 73 例：CLI 13 / config 边界 20 / SQLite 韧性 9 / 资源治理回归 5 / checkpoint 5 / OpenAPI 快照 4 / 错误契约 17。**4 处源码修复**：`_sweep_expired` 只驱逐非进行中会话；`checkpoint._load_sync` 损坏 JSON → None；CLI `run` 图片入口校验 + `config-validate` 失败 exit 1；AuthMiddleware 401/429 统一 `{"detail": ...}`。**关键基建：pytest 确定性 Mock**——conftest 会话级清空 Provider Key + 强制 MOCK_MODE + 重建注册表（顺序陷阱：`import src.providers` 会触发 secrets.yaml 重新注入 env，须先导入后清 Key 再重建）；修复前本地全量在打真实 DeepSeek（25min+/随机失败），修复后 547 例 4 分钟全绿。覆盖率基线 83%（`docs/coverage_baseline.json`） |
| *（未提交）* | **Provider 端点与模型配置（coding plan 支持）** | 用户反馈"每个运行商只能加 apikey，没法加模型 —— 用第三方 coding plan 会调用失败" → 诊断确认：openai/anthropic/deepseek/qwen 的 **base_url 全部硬编码**（Key 必然打到官方端点），且无自定义模型目录。按 DSH「provider 卡片 + 折叠自定义设置（baseURL + 模型目录）」补齐：① `config/providers.yaml`（gitignore，含私有端点）+ `resolve_base_url()` 三级解析（env `<ROUTE>_BASE_URL`（qwen 兼容 `DASHSCOPE_BASE_URL`）→ 文件 → 官方默认），4 个 LLM Provider（含 OpenAI 生图）构造函数接入；② 新端点 `POST /api/settings/providers/{route}`（URL 校验/图像路由拒端点/模型 id 校验/env 供给 403/保存即重建注册表）；③ `/api/settings` 新增 `provider_routes[]`（默认端点/生效端点/来源/是否自定义/自定义与官方模型），自定义模型**并入 `model_catalog`** 供模型映射建议；④ 前端从"一行一 env"重构为**一行一 Provider**（`ProviderCard` + `CredentialField`：凭据槽 + 自定义设置折叠区），Seedream 的即梦/火山 AK+SK 与 FLUX 的三家凭据归入同一张卡片；行头新增「自定义端点 / 自定义模型 ×N」标记。前端 93→102 例、后端 573→582 例；OpenAPI 漂移检测如期报警后显式更新快照 |
| *（未提交）* | **真实会话实测复盘：全链路 13 项修复（A30-A42）** | 用户："我执行了一次会话，里面出现了很多问题，你去查询一下问题出在哪里了，先别急着修，先将问题列给我"。从会话 `e11c464b1ac84502`（9 轮/5 分钟/0 张图/failed）的 checkpoint、审计、群聊事件重建现场，**并用真实 Key 复现每一条**，得到一条必死链：`方舟拒收 1024×1024（实拍 400：image size must be at least 3686400 pixels）→ 生图员把 Provider 错误吞掉、落 3 条空图且审计记 ok → 审查员只认 base64 而真实 Provider 只回 URL，永远拿不到图 → 协调者只看得到"最近 10 条 × 200 字"，以为图已生成，逼审查员对着不存在的图评分 → 审查员把结论写进 ```json 围栏 → 解析失败 → verdict 丢失 → 引擎 result.get("verdict","pass") 静默按通过 → 下一轮审查员自己吐 error 才停下 → 人工 reject → 会话失败`。13 项修复：**A30** 尺寸按路由解析（ark=2048×2048，config 可覆盖）+ 生图失败即停上报（实测 2048×2048 → 200 / 29.8s）；**A31** 新增 `harness/vision_payload.py` 统一四种图源（base64 魔数嗅探 / data URI / 本地落盘文件 / 远程 URL 下载，含路径穿越防护），无图时审查员确定性报错且不烧 token；**A32** `RouteSpec.max_tokens` + `providers/compat.py` 输出守卫（空/截断→可读错误，截断自动升 4 倍预算重试一次；实测同一 prompt 4096→空、16384→3470 字正文）；**A33** `providers/json_parse.py` 宽松解析（去围栏/取首个平衡对象）+ 审查门禁重写（error/缺 verdict/retry 无分数 一律转人工，只有 pass 放行）；**A34** YAML 超时/重试落回实例（原为死配置）+ 超时不再重试（原 15s×4 次＝白等 66s）+ 预算按实测重设（150s/90s/420s/120s）；**A37** 分数 None 不再拼成 "None/100"、不再 `None < 75` 崩溃；**A35** 协调者 prompt 新增「当前产物状态」（图片可用性逐张标注 + 禁止误判完成），记忆注入死代码修复；**A36** 记忆参考前置 + 限定风格/构图 + 禁止照搬事实（水飞蓟串味事故）+ 开关；**A38** `execute(stats=...)` 出参让审计拿到真实用量/耗时（原恒 0）+ 生图成本落账 + perf_counter 取代 Windows 15.6ms 粒度；**A39** checkpoint 剔除运行时对象 + `CostTracker.from_session` 自愈（恢复后不再"永久停止记账"）；**A40** artifacts 合并只收有效载荷、陈旧告警随轮次清理；**A41** 覆盖键与 requires 不匹配的告警（`agent_overrides_issues` + 前端能力下拉过滤/自动纠正）+ 修正自带配置里 4 条失效覆盖；**A42** 前端图格"生成失败"徽标+禁用下载、审查页未评分/阻断原因/审查原文渲染。测试：后端 805→**921**、前端 155→**162**，构建零告警 |
| *（未提交）* | **连续失败即停 + 轻量 checkpoint（A46/A47，用户指定）** | 用户在看到真实验收结论后指定两项：① **「审查/合规连续失败 N 次即停」可设置** —— 引擎新增 `_quality_guard()`（审查 `verdict∈{retry,fail}`/报错、合规 `passed=false`/报错各计 1 次，任一通过清零；达到阈值 → 会话 failed + `error_history(kind=abort)` + 群聊说明原因与调整方式；人工 approve/retry 清零），阈值在 `config/default.yaml → chat.max_consecutive_review_failures`（默认 2、0 = 关闭），设置页新增「🛑 会话策略」卡片 + `POST /api/settings/chat`，写入独立的 `config/chat.yaml`（不改写 default.yaml，注释不丢；已 gitignore）。② **checkpoint 落盘粒度** —— 此前只在人工暂停/终态/压缩前落盘，强停会丢整段轨迹（实测只剩 HITL 那 6 轮）；改为**每个 Agent 步骤后写轻量快照**（剔掉上传图 base64），完整快照仍在暂停/终态/压缩前写。实测（2.6MB 上传图）：运行中 7 次落盘 5.7→34.8KB（均值 22KB），收尾完整快照 2574KB → **缩小约 117 倍**，崩溃最多丢当前 1 步。测试：后端 938→**960**、前端 165 |
| *（未提交）* | **真实端到端验收 + 3 项新缺陷（A43-A45）** | 用户批准真实方舟验收后，用真实 Key 跑通「上传图 → 分析 → 提示词 → 真实出图 ×3 → 审查评分」：3 张 `doubao-seedream-5-0-260128` @2048×2048 落盘 `output/default/72d5ef86831c4f99/`（176/185/164KB），审查员**真的读到了图**并逐张评分（指出生成图里被臆造的 `NUTRIVA®` 品牌、缺失的 GMP 条与 AI 水印，判定 fail），合规审查员同样拿到图并判 `passed=false/risk=high`。**验收同时暴露 3 个新问题并已修复**：① **A43** 审查员按「对每张图评分」返回 `{"results":[…]}`，顶层没有 `verdict` → 门禁判为「无法解析」转人工（内容其实有效）→ 新增 `harness/review_normalize.py` 汇总（分数平均、判定取最严重、议题/维度合并），Agent 与引擎都调用，并给 reviewer 提示词补「多图也要给顶层判定」；顺带把 `verdict=fail` 纳入门禁（此前会静默跑完）。② **A44** 3 张图只按 1 张计费（`record_image(count=1)` 丢张数与 Provider 金额）→ 审计行 0.12 / 会话只涨 0.04，改为按实际张数与金额入账。③ **A45** 重启后历史会话从列表消失（只恢复非终态 checkpoint，而会话列表来自内存；本次排查亲自踩到）→ lifespan 恢复全部 checkpoint，实测重启后两个历史会话都在。**真实链路验证 A30/A31/A32/A35/A36/A37/A38 全部生效**（审计每行都有真实 tokens/耗时/金额：分析 19.1s、生图 101.7s/$0.12、审查 24.7s/15819tok）。**暴露的后续风险**：协调者会在合规失败后反复重生成（每轮约 ¥1，预算只告警不拦截，本次已人工止损）；checkpoint 只在 HITL/终态/压缩前落盘，强停会丢轨迹。测试：后端 921→**938**、前端 162 |
| *（未提交）* | **成图质量三件套 + 平台档案（A48-A58，用户指定）** | 用户四点反馈：①"产品分析员根本没有识别到我喂的图是什么品牌，商品名是什么都没强调或者提醒"；②"要保证生图过程中要文＋图生图，而不是单纯的图生图或者文生图"；③"生成的不是一套可直接上传的套图，而只是套图中一张的多张选择，这不符合生产"；④追问"商品品牌写死在代码里，不会影响我生产其他牌子吗"；⑤"我可能还会做拼多多，他们的风格也要写上"。**根因取证**：生图请求体只有 `model/prompt/n/size`（**参考图从未进入生图环节**，模型没见过包装 → 把 `DEFOEBUENA®` 编成 `NUTRIVA®`）；`analyst.yaml` 的 10 个字段里没有品牌/商品名/包装文字转录；`image_gen` 只取 `main_image.prompt` 连打 `variants` 次（实测确认"同一张图的三个候选"）；`MOCK_ANALYSIS` **写死了**"保健品/水飞蓟/蓝帽"且 `resolve()` 第 4 步会静默回落 mock（换任何牌子都会被套上这套事实）；平台清单散落 9 处、`prompt_gen.yaml` 只定义了 4 个平台风格。**修复**：**A48** 商品身份：分析员新增 `product_identity`（品牌/品名/规格/认证/置信度/识别依据/**来源溯源**）+ `visible_text`（包装文字逐字转录），引擎落成身份卡并在群聊**醒目播报**（`✅ 商品身份：品牌 …`），`_inject_identity_card()` 前置进提示词/生图/审查/合规简报；未确认时（默认）暂停等用户确认（`chat.require_identity_confirm`，识别成功不打扰；mock/无来源只提醒不拦）。**A49** 套图编排：提示词生成员输出 `set_plan.slots`（平台槽位逐个一张：白底/卖点/场景/细节/规格…），生图员**按槽位出图**，落盘名 `{平台}_{品类}_{槽位}_{序号}.ext`（序号放最后 → `find_session_file` 零改动），审查/协调者/前端按套图组织；旧路径（无 set_plan）保持 variants 行为。**A50** 文+图双条件：`BaseImageProvider.generate(reference_images=…, options=…)`，方舟把 `prompt` 与 `image:[data URI]` **同时**入体，平台参数按路由白名单生效（`watermark:false` 去掉实测存在的「AI生成」水印）；**不支持的一律回报 `ignored_params`，禁止静默丢弃**。**A51** 参考图治理与持久化：新 `harness/reference_images.py`（嗅探 MIME / 超 4MB 降采样 / 张数上限 / 失败留说明）+ 上传原图落盘 `{输出根}/{租户}/{会话}/inputs/`（轻量快照剔 base64 后仍能找回参考图，否则 i2i 会静默退化成文生图）。**A52** 零硬编码与跨商品隔离：`MOCK_ANALYSIS/MOCK_PROMPTS` 去掉全部具体商品事实（改为中性占位 + `is_mock` + `source=mock`，身份卡永远 uncertain，群聊标注"演示数据"），身份卡只由 vision/用户确认产生且**品类专项分析员不得覆盖**（实测被覆盖成无 status 的裸数据）。**A53** 平台档案 `config/platforms.yaml` + `core/platforms.py`（新增**拼多多**等 10 个平台：别名归一/长宽比/白底/文字策略/禁止元素/**风格提示词**/套图槽位/主图上限），提示词按档案渲染风格块，未登记平台显式报"未登记"不臆造。**A54** 本地体检 `harness/image_quality.py`（零成本确定性）：背景白度/水印区/主体占比/清晰度 + 与参考图的**身份相似度**与**复制检测**（既防"退化成纯文生图"也防"退化成纯图生图"）；**用真实产物回放验证**：实测三张图边缘 240/238/235（非纯白）、右下区 −30.7/−2.1/−27.7（水印）、身份相似度 0.29/0.25/0.38（全低于阈值）—— 即本次事故**不花钱就能被提前发现**。**A55** 审查闭环：审查员/合规审查员改为收到**图一（用户上传的真实商品图）+ 生成图**，逐项给 `fidelity_findings`（品牌/品名/规格/认证 一致/不一致/缺失），并附本地体检客观数值（"商品还原度"终于有基准）。**A56** 策略可设置：`config/image.yaml`（UI 写入，**不改写 models.yaml** 以保注释）+ `GET /api/platforms` + `POST /api/settings/image` + `POST /api/settings/image/probe`（真调一张 + 返回体检，缺凭据先拦下而不是抛 `Illegal header value b'Bearer '`）；设置页新增「🖼️ 生图质量策略」卡片，会话页新增身份卡/套图完成度/体检徽章/槽位徽章，平台选择器改为**接口驱动**（新增平台零前端改动）。**A57** 顺带修 `chat_settings()` 的一个静默 bug：`default.yaml` 里缺省的新键会被写入 `None` → `bool(None)` 让开关**静默变 False**（新开关 `require_identity_confirm` 首次运行即被关掉，实测发现）。**A58** E2E 冒烟脚本的凭据清空清单**漏了 `ARK_API_KEY`**（清单早于方舟路由写成），用户保存过方舟 Key 后 `secrets.yaml` 会把真实 Key 注入子进程 → Mock 确定性预检拒绝运行（预检正确，但脚本从此跑不起来）→ 改为从 `secrets.yaml` 派生。**验证**：后端 960→**1190**、前端 165→**176**、`npm run build` 干净、E2E 冒烟 7/7 通过（并新增 6 条 S1 断言覆盖身份卡/套图覆盖度/槽位命名/体检/生图参数）。**真实出图验证（Phase 6）待用户点头**（预计 ¥0.2 起） |
| *（未提交）* | **设计导向套图 + 提示词审美审核（A70-A78，用户实测反馈）** | 用户上传 4 张包装图跑了一轮拼多多套图（会话 `ee9a3b80e19b4010`，6 张、$0.2519）后反馈两条：①"生成的图片质量太差了，缺少了该有的商品图审美"；②"他未有明确约束每一张该有的提示词（第一张提示词：…第二张提示词：…）"。**取证**：提示词 550–610 字堆叠长句、**把包装版式逐字写进画面描述** → 模型重画小字（审查员独立认定 `Sickle Ligament→Sadle Ligement`、手写体 `Schneiski` 乱码，身份相似度 0.22–0.33）；6 张画面同质、卖点图**没有商品实物**；群聊只吐裸 JSON、`prompt_text` 被 `prompt[:200]` 截断、**平台规范块只渲染主图槽位**（4 张详情图一张没出）；信息图 2048 被缩到 1200、页脚"每粒 0.391g"被截成"每粒 0.3"；10 张 ≈505s > 生图员 420s 预算（必然超时且整键替换会丢图）；审查员只审 3 张。**修复**：**A70** 槽位契约（每槽位 `intent/design/must/forbid/keep_clear`）+ **编号槽位表**（含详情槽位，逐张约束）+ 按平台顺序编号；**A71** 身份词不进画面描述（`identity_terms`/`strip_identity_terms`，移除项记账），**品牌色值要进**；**A72** **六段式提示词**（`build_image_prompt`）+ 设计方向块 + 平台 `detail_style/art_style`，重写拼多多风格（删掉"弱化高级感/不要大面积留白"）；**A73** 排版自适应（二分断点换行 + 逐档缩字号 → 实测零截断）、保持底图分辨率、文字底卡、按模板真实容量限条；**A74** 生图超时按张数 + `artifacts["images"]` **按槽位合并**（重跑不丢已出的图）；**A75** 审查分批送审覆盖整套 + 第 6 维度 `realism` + 排版可读性；**A76** `prompt_text` 存全文 + 群聊结构化渲染编号清单 + 会话页"第N张＋提示词折叠区"；**A77** 零成本**提示词体检**（14 条确定性规则）；**A78** 新 Agent **「提示词审核优化员」**（设计七项打分，**低于阈值直接给改写稿**，改写只允许动《画面》段且**必须过体检才落地**），引擎 `_prompt_stage` **统一三处生成提示词入口**，硬伤打回至多 N 轮、审核失败不阻塞出图，四键可配（设置页「🛑 会话策略」）。**用户两次更正被采纳**：① 白底并非全局硬规则 → `background_policy` 三档（默认仅 Amazon 强制纯白），本地体检按策略显式判定；② 审美标准从"摄影工艺"改为**平面设计**（背景可以是品牌浅色底/渐层/微质感，写实只用于场景槽位），反模式清单写入审核 rubric。**顺带**：品牌色板（分析员零额外成本取样）、会话级**槽位子集**（最小付费冒烟）、`real_suite_run.py --slots/--variants`（A/B 冒烟 ≈$0.16）、`platforms.yaml` 解析缓存（平台规范块 10.8s → 0.02s，后端全量 427s → 306s）、修 `resume_after_hitl(approve)` 伪造审查产物。测试：后端 1280→**1363**、前端 184→**196**、build 干净 |
| *（未提交）* | **一轮会话一个风格词 + 套图结构 + 张数上限收口（A97-A107，用户三条追问）** | 用户三条：①"我给的是一套图片，那应该不止是单纯的分析图片的美术风格，还有套图的制作习惯，例如『第一张：美术+纯白商品图，第二张：美术+成分图，第三张：美术+面向群体图』"；②"为什么限定只能输入 6 张图片？"；③"生图的话只能使用一种套图，但是你是否有在风格词库那里限定启用一个风格另一个风格自动停用"。**取证**：①**成立** —— `config/prompts/style_archivist.yaml` 原话写着"归纳**共性**、**不要逐张描述**"，把用户给的套图编排整段抹掉，而字段表里也没有能放"第几张是什么角色"的位置；②**6 是虚数** —— 同一个数字在 `style_store.MAX_PHOTOS`/`api.MAX_STYLE_IMAGES`/`style_archivist.MAX_VISION_IMAGES`/前端 `MAX_STYLE_FILES` 各写一遍，**无供应商或成本依据**（用户实测 6 张 1 次调用 $0.000932、落盘 617KB）；③**成立且更严重** —— 用用户真实词条实测（拼多多+保健品）：**10 个槽位张张 2 条**「同色清新」+内置（净白硬照/信息图版式/植物语境…）且互相矛盾（"浅粉渐层+亚克力几何体" vs "纯白无缝、无道具无装饰无文字"），而 `describe_selection()` 只报 `picked[0]`，群聊里完全看不出第二条；且提示词生成员/审核优化员各自检索，**中途切词库会让同一套图前后用两个风格**。**交付**：**A97 一轮会话一个风格词** —— 会话锁（`task.style_entry_id` → `style_refs.locked_entry_id` → 词库唯一启用条 → 内置原型）贯穿生成/审核/体检/重跑；radio（启用一条自动停用其他，`auto_disabled` 如实回报）；严格模式（默认 `style_library_max` **2→1**：用户风格覆盖的槽位只注入它，未覆盖的只按平台槽位契约，不再塞第二个风格）；播报改为逐张列出**实际注入项**；新增 `POST /api/sessions/{id}/style`（换风格）+ 弹窗补 `kinds/requires_policy/not_slots`（此前**没有入口** —— 那是避免"浅粉渐层 vs Amazon 首图必须纯白"的正确开关）；**A98 套图结构** —— `shot_flow` + `shot_roles`（角色由槽位目录派生、序号与照片严格对齐、界面**逐张行**核对/修正）；**A99-A100 张数收口** —— 上限单一来源（20 张/单批 12/单张 10MB/总量 100MB 经 `stats.limits` 下发，**前端不再各写一份**，顺带修掉"界面写 20MB、处理器真实 10MB"的旧账）；超出**分批**（第 1 批全量、后续批只补逐张角色，`usage.batches`/金额按批聚合，全部回报才算得出来）；`timeout_budget` 与"卡 analyzing"收割阈值随批数放宽（否则 20 张会"先报失败、随后又变 ready"）；**A101-A105** 追加/移除照片（移除会重排序号 → **清空 `shot_roles` 并回报**）、逐图打标（"第N张"）、体积预算（超 8MB 先降采样 1280/q78，仍超送前缀并记账）、`prompt_lint` 槽位级照抄比对、`--check` 区分内置/用户词条冲突（并修掉"文字/数字"导致的 **36 处误报**）。测试：后端 1602→**1700+**、前端 280→**293**、`--check` 10 平台每张注入 [1] 条且 0 冲突、build 干净 |
| *（未提交）* | **风格词库 + 成本金额诚实化（A79-A96，用户指定 + 用户质疑）** | 用户三件事：①"在左边页面栏记忆库下面加一个『风格词库』……导入照片、命名，点击开始生成会有专门的 agent 帮我分析这组照片的风格"；②质疑成本显示（"不同的模型花费不同，而且模型商的价格会来回改，除非每次都及时更新，不然会出现很大的误导"）；③质疑后台 Agent（"为什么不跟中心决策者说明它不参与会话，会不会被拒一次、白耗 token"）。**取证**：②**成立且更严重** —— 当时的 `$0.04/张` 来自 `OpenAI._estimate_cost(size)` 的**兜底常量**（`2048x2048` 没命中任何键），`cost_tracker` 对未知模型用 `(1.0,3.0)`/1M **凭空造金额**，`seedream.py` 甚至 `return 0.0 # 通常有免费额度`；③**成立** —— 名单是**每轮重建并整段进系统提示**的（`coordinator._build_agent_list`），在提示词里加一句说明等于每轮付费，还把名字塞进上下文、反而诱导模型去提。**交付**：**A79** 内置 8 条设计档案 `config/style_library.yaml`（净白硬照/白底棚拍/品牌色块/柔光渐层/植物语境/临床严谨/深色科技/信息图版式，均含背景·构图·光影·材质·元素·色板角色·留白·禁忌）+ `harness/style_library.py`（mtime 缓存、逐槽位检索「品类×100+平台×10+槽位×5+用户词条 20」、事实中立三闸、`entry_conflicts` 与槽位契约零冲突校验）；**A80** `scripts/style_preview.py`（零成本自查：全平台 100% 命中、0 冲突）；**A81-A83** 逐槽位档案注入**提示词生成员/提示词审核优化员/成图审查员（只锚点）**、体检新规则 `style_template_copy`（与档案逐字重合 ≥12 字 → warning）、设置项 `chat.style_library_enabled/style_library_max`、**Mock 路径也算 `style_refs`**（改前 Mock 早退 → 前端整行没数据）；**A84-A85** `harness/style_store.py`（照片落 `data/style_library/<id>/`，**永不进 `data/inputs/`、不作生图参考图**，静态测试钉住）+ 新 Agent **「风格档案员」**（白名单归一、图片文字只作 `removed_brand_text`、防图片提示注入、6 图失败回落 3 图、失败带 `maybe_billed`）+ **`AgentMeta.invitable`**（后台 Agent **移出群聊名单**：三层落地含"`load_from_config` 必须显式取该字段，否则静默失效"的回归测试 + 引擎侧幻觉邀请**不调用任何 Provider**）；**A86** 7 个 `/api/style-library*` 端点（异步分析、用量前置、编辑再过事实中立清洗、内置只能启停）+ OpenAPI 快照；**A87-A90** 前端「🎨 风格词库」页（＋ 卡、悬浮弹窗、拖拽/点选/粘贴导入、每 2 秒轮询、零成本预览、用量行）+ 会话/群聊展示；**A91-A93** `scripts/style_anchor.py`（照片→判词，默认只打印、`--append` 先备份）、e2e 新 S8 场景、文档；**A94-A96** `harness/pricing.py`（**用量是事实、金额是估算**：未标定一律 `amount=None`，删掉全部伪价格，前端 `cost.js` 成为全站唯一金额口径）+ 设置页「💰 计价」与 `/api/settings/pricing*`（三端点：现状/写单价/按实际花费标定）+ `--max-images` 张数硬闸门 + `real_suite_run.py --style-library off,on`（A/B 回答"这套档案到底有没有让审美上去"）。测试：后端 1363→**1602**、前端 196→**280**、build 干净 |
| *（未提交）* | **模型映射的可用性可见（A29，用户实测反馈）** | 用户："为什么模型映射模块拉取出来的模型不只有已配置好的模型" —— **这是设计使然但确实误导**：映射建议来自后端的 `model_catalog`，而它是**平台目录**（`{route: list(spec.models)}`，所有内置服务商的官方模型），与是否配置 Key **无关**；设计意图是"可以先配好映射、之后再补 Key"，但**完全没标可用性** —— 这正是更早那次"生图默认值指向未配置的 seedream → 静默回落 Mock"的土壤。修复（纯前端，不破坏后端契约）：① 建议列表按**可用性排序**（已配置的在前）+ 每个 option 带 `label`（已配置 / XXX 未配置）；② 表格上方常驻一行说明"当前可用：… ；未配置：…（选它们的模型会回落 Mock）"；③ **当前映射里指向未配置服务商的条目直接告警**（含"生图 → seedream/seedream-5.0"这类实测踩过的组合），`mock` 除外（它永远可用）；④ 顺手修正生图建议的判定：从写死前缀改为**按 `models_by_capability` 分组判定**（路由级能力不够细——openai 同时有 text 与 image，会把 `gpt-4o` 误判成生图模型），自定义生图服务商的模型现在也能进生图建议。测试：前端 +5（ModelMappingEditor 11 例；前端总数 151→**155**，后端未改动仍为 805） |
| *（未提交）* | **拉取结果命名与过滤修复 + 401 解释（A28，用户实测反馈）** | 用户："我怎么感觉模型拉取有问题，名字有些问题" —— **直觉正确，实测三处缺陷**：① 上游每条都带 `name`/`version`（实测 133/133），我的接口**只传了 4 个字段**，界面只能显示原始 id（`doubao-seed-2-1-pro-260915` 这种），看着就是"怪名字"；② 只按 `output_modalities` 分类，把**专用模型混进建议**：`-character-`（角色扮演）、`-translation-`（翻译）、`smart-router`（路由）、`-code-preview-`（代码）；③ 只挡了 `Shutdown`，**`Retiring`（即将下线）13 个仍被推荐**。修复：`models[]` 透传 `name`/`version`/`status` 并新增 `recommended`/`skip_reason`；`applicable` 只含可推荐项（排除已下线/即将下线/专用家族/代码专用）且按 `created` **新→旧**排序；**多模态模型同时进「视觉」与「文本」**（只归一类会让文本里只剩纯文本模型）；前端 chip **显示模型名、填入完整 id**、标题带版本、并显示"已过滤 N 个"。**真实账号复验**：133 个 → 可推荐 28、过滤 105（即将下线 69 / 已下线 22 / 专用 13 / 代码 1）；生图建议 `doubao-seedream-5-0-pro`/`-5-0`/`-4-5`/`-4-0` 可读且新→旧。**另把 401 做成自解释**（用户上一条提问）：`401 AuthenticationError「The API key format is incorrect」` 现在会点明"这是鉴权失败不是模型问题"+方舟 Key 形态（`ark-…` 约 46 位）+常见填错项（AK/SK、`ep-…`、别家 Key），并与 404 模型提示**互斥**（有反向断言）。测试：后端 800→**805**，前端 150→**151** |
| *（未提交）* | **人工审批竞态修复（A27，全量 flake 追查）** | 最后一轮全量出现**新失败**：`test_reject_fails_job` 单跑通过、整目录失败。统计复现：`TestHumanNode` 整类连跑约 **25% 概率失败**（2 用例组合 4/4 通过），确认是**既有 flake**而非本轮改动引入（新增用例只是加大了负载）。**根因是生产代码竞态**：`_run_human` 里 `action` 是每轮新建的局部变量，`if not runtime.human_action:` 默认"决策已就绪 ⇒ action 已赋值"；但决策若落在「WAITING_HUMAN 落库(421) → 引擎进入等待(435)」这段窗口内（窗口内还有 `_emit` 的落库与广播两个 await），就会跳过等待、`action` 保持空串 → 路由回落**默认边 = 批准** —— **用户点「拒绝」若卡在这几十毫秒里会被静默当成批准**。修复：`action = action or runtime.human_action`（消费决策）+ 消费后清空 `human_action`（否则同 job 后续 human 节点/回跳重跑会复用上一次决策）。新增两条回归：①「决策落在发布窗口内不被丢弃」用 `store.update_job` 钩子在落库瞬间注入 reject，**实测去掉修复即红、加上即绿**；②「决策必须被消费」。`TestHumanNode` 连跑 6 次全绿 |
| *（未提交）* | **方舟模型 id 校准 + 「拉取可用模型」（A26，用户实测反馈）** | 用户："❌ 火山引擎方舟（Ark） API error: 404 … The model or endpoint **Doubao-Seedream-5.0-lite** does not exist"。**用其真实 Key 探测方舟 `GET /api/v3/models`（只读免费）拿到事实**：该接口可用且返回 131 个模型（含 `status` 与 `modalities`），**账号里根本没有 `Doubao-Seedream-5.0-lite` 这个 id** —— 方舟 id 是「小写字母 + 短横线 + 日期后缀」；账号生图模型只有 `doubao-seedream-5-0-260128` / `-5-0-pro-260628` / `-4-5-251128` / `-4-0-250828`。**并发现我内置的 ark 默认 id 就是错的**（`doubao-seed-1-6-250815` 不存在、`doubao-seed-1-6-vision-250815` 已 Retiring，这正是上一轮测试连接 404 的真因）。三项修复：① **校准内置默认**（routes.py 的 ark 模型与 `models.yaml` 生图默认 → `ark/doubao-seedream-5-0-260128`，全部改用真实账号校准过的 id）；② **新增「⬇️ 拉取可用模型」**（`GET /api/settings/providers/{route}/models` + 卡片按钮）：按账号实际可见列表返回并按能力分组，点一下即填入模型目录 —— 从根上消除"猜 id"（已下线的 Shutdown 模型不进建议）；③ **id 形态提示 + 开通指引**：404 除"鉴权已通过"外，id 含大写/点号时点明正确形态，`ModelNotOpen` 则明确指出"账号未开通该模型（文本与生图要分别开通）"。**并修正测试连接的模型选择顺序**（自定义 ∩ 本能力 → 自定义中非他能力的[coding plan 自有模型名] → 能力映射 → 官方模型），避免方舟路由把生图模型发给 chat 接口。**用你的真实 Key 全程复验**：拉取到 131 个模型（生图 4 个可用）；文本测试最终得到方舟权威答复 `ModelNotOpen: Your account 2122038908 has not activated the model doubao-seed-2-1-pro-260628` —— **即：Key 有效、端点正确，只差在方舟控制台「开通管理」里开通模型**。测试：后端 784→**800**，前端 146→**150** |
| *（未提交）* | **火山方舟接入排错（A25，用户实测反馈）** | 用户："为什么我配置了火山方舟的 apikey 但是测试连接显示 Seedream 需要 VOLCANO_ACCESS_KEY + VOLCANO_SECRET_KEY 或 SEEDREAM_API_KEY"。**实测定位到真实根因**：用户的方舟 Key（`ark-` 前缀，46 位）被填进了旧卡片「火山视觉智能（旧版 AK/SK 签名）」的 `VOLCANO_ACCESS_KEY` 槽 → ark 路由无凭据不可用；旧路由只有半个 AK（缺 SK）→ 判为不可用 → 生图回落 Mock；测试连接只回一句内部变量名，完全没指出 Key 该放哪儿。**四项修复**：① **凭据预检**——测试连接在发请求前先判「没配凭据 / AK-SK 只配一半 / `ark-` 前缀 Key 落在旧卡片」，返回可执行说明（此前没配 Key 时直接抛 `LocalProtocolError: Illegal header value b'Bearer '` 这种裸错误）；② **一键迁移** `POST /api/settings/providers/move-credential`（把密钥从错误槽位搬到正确路由并清空源槽，不回显密钥）+ 前端按钮；③ **旧路由标记 `deprecated` + 引导横幅**（明确写出"方舟 Key 属于 Ark 卡片"）；④ **模型兜底**——方舟端点没有默认模型，不带 `model` 直接 400 `MissingParameter`（实测踩到），故路由表新增 `models_by_capability`（文本/视觉/生图各自的模型），测试连接按"自定义模型 → 模型映射 → 该能力官方模型"解析。**用用户真实 Key 全程复验**：迁移后 `image → ark/doubao-seedream-4-5-251128`（不再回落 Mock）；ark 测试连接已通过鉴权（返回的是 Ark 业务响应 404 `InvalidEndpointOrModel.NotFound`：该模型未开通/未授权）→ 已为该类错误追加"鉴权已通过，请开通模型或改用接入点 `ep-…`"的说明。**顺带修测试基建**：conftest 的动态清空清单漏了 secrets.yaml 里的键（`env_provided_secret_keys()` 语义上只记"部署环境提供"的键），导致实盘新增的 `ARK_API_KEY` 残留并让 `test_no_keys_only_mock` 误判；现按"secrets.yaml 全部键 + 部署环境键 + 路由表凭据"取并集，并新增逐测试的凭据环境快照/恢复（生产代码会直接写 `os.environ`，monkeypatch 回滚不了）。测试：后端 770→**784**，前端 143→**146** |
| *（未提交）* | **自定义模型服务商 + 火山方舟建模订正（A24，用户实测反馈）** | 用户："我无法自己添加模型服务商，局限性太大了；还有即梦不是一个模型提供商吧，它是存在于火山引擎里可调用的模型"。取证确认**两处都成立**：① 路由/凭据槽/模型目录/**可保存密钥白名单**全硬编码在 `src/api/main.py`（`_PROVIDER_ROUTES`/`_API_KEY_META`/`_MODEL_CATALOG`/`_ALLOWED_SECRET_KEYS`）+ `ProviderRegistry.__init__` 的逐个 `if os.getenv(...)`，用户一个都改不了；② 即梦是字节的消费者产品，**Seedream/Seedance 是模型、服务商是火山引擎方舟（Ark）**（OpenAI 兼容 `https://ark.cn-beijing.volces.com/api/v3`，生图 `/images/generations`，模型 id 形如 `doubao-seedream-4-5-251128`），而原 `seedream` 路由打的是 `visual.volcengineapi.com` + `req_key=jimeng_t2i_v51` 的旧版 AK/SK 签名通道。落地：**① 新增 `src/providers/routes.py` 统一服务商目录表**（RouteSpec：kind/能力/凭据/端点/模型，内置 7 条含新 `ark`；`key_envs` 任一可用 vs `all_key_envs` 必须成对——火山 AK/SK 的半对配置按既有约定仍判不可用）；**② Provider 类参数化**（openai/anthropic 的 api_key/base_url/name/capabilities/label 可注入，兼容端点自动剔除 DALL-E 专有参数并补 `response_format=url`）；**③ 自定义服务商**持久化到 `config/custom_providers.yaml`（已 gitignore），注册表与 API 动态读取，新增后凭据槽/密钥白名单/模型建议同步放行；**④ 新端点**：`GET providers/presets`（智谱/Kimi/硅基流动/OpenRouter/方舟/Ollama 一键预设，已存在的标记不可添加）、`POST providers/custom`（结构校验 + 热重载）、`DELETE providers/custom/{route}`（内置不可删）；**⑤ 前端**设置页「➕ 添加服务商」表单（预设一键填充 + 协议/端点/变量名/能力/模型），自定义卡片带标记与两步确认删除；**⑥ 旧 `seedream` 降级为「火山视觉智能（旧版 AK/SK 签名）」保留兼容，`models.yaml` 生图默认改指 `ark/doubao-seedream-4-5-251128`**（旧通道/OpenAI/FLUX 依次回落——此前只配方舟 Key 的用户生图仍会静默回落 Mock）。测试：后端 717→**770**，前端 138→**143** |
| *（未提交）* | **生成图落盘与导出（A23，用户实测反馈）** | 用户："我发现个问题，里面没法自己设置生成图片的导出的路径" → 取证发现**根本没有导出能力**：`deploy/Dockerfile` 早已 `mkdir output` 并挂 `harness_output` 卷，但 `src/` 里没有任何代码往里写（又一处"声明了没人用"）。① **输出根可配置**：新建 `config/output.yaml` + `ECOMM_OUTPUT_DIR`（优先级 env → 文件 → `<项目根>/output`），设置页新增「生成图输出目录」卡片（**真实 mkdir + 探针写入**判定可写；env 锁定时只读 + 写入 403；非法路径 400 且不落坏配置）。② **生成即自动落盘**：`src/storage/image_export.py` — `{根}/{租户}/{会话ID}/{平台}_{品类}_{序号}.{ext}`，聊天引擎与工作流引擎都接（工作流用 job_id），扩展名按**魔数**判定（Mock 的 SVG 占位图不会写成 .png），三种来源都支持（内联 base64 / data URI / **远程 URL 下载**），失败只告警不影响任务；落盘相对路径回写 `saved_path` 供界面显示。③ **下载与导出**：单张 `GET /api/sessions/{id}/images/{index}/download` + 整会话 `GET /api/sessions/{id}/export`（ZIP），均租户隔离。④ 会话页图片 tab 显示落盘路径 + 单张下载 + 导出全部。**顺带修掉一个接缝 bug**：A21 让前端读 `session.error_history`、引擎也在写，但 `GET /api/sessions/{id}` 根本没返回该字段（两边各自有测试、接缝没人测）。测试：后端 678→**717**，前端 129→**138** |
| *（未提交）* | **第二轮复查·B3 产品体验三项（A20-A22）** | 按复查报告优先级执行第三批（价值最高的产品缺口）：① **「测试连接」**——`available` 只表示"环境变量非空"，第三方 coding plan（端点与模型 id 自填）配好无法验证；新增 `POST /api/settings/providers/{route}/test`（仅 admin）：文本/视觉发 1 次最小 chat 并回显**实际模型/耗时/生效端点/状态码与上游原文**，图像路由默认跳过（真实生成有费用）需 `allow_image=true`，Mock 路由如实标记不发真实调用，失败仍 200 + `ok=false`；前端卡片新增按钮与三态结果行（含生图勾选）。② **失败原因可见**——审计早已落 `error[:500]` 但无「错误」列；更根本的是 `SessionState.error_history` **从没人写过**（只在创建时初始化）。引擎新增 `_record_error()`（agent 报错/超中止/协调者缺失/人工拒绝四写入点，限 20 条），会话页新增失败原因卡片 + 审计深链 `/audit?session=`，审计页新增错误列（省略 + title 全文）、「只看失败（N）」过滤与深链预置。③ **生图回落可见**——`/api/settings`（含租户脱敏子集）新增 `capabilities[]`（能力→provider/model/is_mock，解析异常降级不 500），新建任务表单顶部显示能力落点条，生图回落 Mock 时给出醒目警示 + 设置页入口。测试：后端 657→**678**，前端 111→**129**，构建零告警 |
| *（未提交）* | **第二轮复查·B1 正确性/资源五项（A15-A19）** | 按复查报告优先级执行第二批：① **输出校验遇畸形 LLM 输出直接抛异常**——`0 <= "85" <= 100` → TypeError，调用处无 try → **整轮群聊失败**；同文件另两条同类崩溃（`category` 非字符串 `.strip()`、`main_image.prompt` 为 dict 当字符串 strip）。新增 `is_number`/`is_non_blank_str` 类型判定 + 管道兜底 try/except（校验器只返回结果、绝不抛异常）。② **Agent 记忆并发丢条目**——`_append_entry` 无锁，实测 200 并发只落盘 183 条；模块级 `_MEMORY_WRITE_LOCK` + 先序列化再持锁写（同 audit_logger 修法）。③ **checkpoint 非原子写**——`open("w")` 截断 + 流式 dump，中断/并发让旧 checkpoint 变半截 JSON；改临时文件 + fsync + `os.replace`，失败清理临时文件，写入串行化（Windows 并发 replace 会 WinError 5，实测踩到）+ 退避重试。④ **checkpoint 磁盘无回收**——TTL 只清内存；新增 `cleanup_checkpoints()`（终态超期→删 / 损坏超期→删 / 进行中与未超期保留 / TTL≤0 不回收）+ 会话驱逐时同步删文件 + lifespan 启动清扫。**真实世界验证：真实 data/checkpoints 从 1689 个文件 / 56.4 MB 降到 8 个 / 318 KB**（首次重启回收 1580 个超期终态，二次重启回收 103 个从未启动会话）。⑤ **恢复会话 TTL 失效**——写入侧 `str(datetime)` 而驱逐只认 datetime（`ts=0`）→ 恢复的会话永不驱逐；新增 `parse_timestamp()` 统一解析。测试：后端 631→**657**，前端 111 例与构建零告警（本批无前端改动） |
| *（未提交）* | **第二轮复查·B0 安全四项 + 工程化两项（A9-A14）** | 按复查报告优先级执行第一批：① **`GET /api/settings` 管理面守卫**——实测租户 Key 能读到带内嵌凭据的私有端点（响应体里就有 `https://ops:SUPERSECRET@internal-gw...`）、全租户清单与 Agent 系统提示词；新增 `_tenant_settings_payload()` 脱敏子集（`redacted: true`，前端据此显示提示并隐藏管理面区块）+ `_require_admin_access`。② **模板导入守卫**——任意租户 Key 可 `force=true` 覆盖全局模板；端点首行加守卫，回归测试断言被拒时文件字节不变。③ **批量并发双跑**——`batch.py` 用 `await engine.run(job)` 不登记 task，导致 `retry_step` 的 cancel→start 保护失效（实测同 job `step_started` 12 次/应为 6）；`run()` 登记 `runtime.runner`、`start()` 复用在飞 run、`retry_step` 对批量驱动的在飞 run 明确拒绝（且在重置步骤之前判定）。④ **取消被推翻**——job 被 cancel 后调度器当可重试失败，新建 job 重跑全程还记 succeeded（实测 job 数 1→2）；非失败终态跳出重试、批次取消后不再起新一轮、死信统一 `_mark_dead_letter`。⑤ **E2E 冒烟端口语义**——`--existing` 也会按端口强杀 8000/5173（哨兵实测对照：旧路径确实杀掉无关进程，新逻辑自动/复用两种模式哨兵均存活）；`_stop_children` 只清本次启动的端口 + `_auto_mode_conflict()` 端口预检。⑥ **测试污染**——新增 `ECOMM_DATA_DIR`/`ECOMM_PROJECT_ROOT` 可重定向根（checkpoint/审计/记忆/workflow.db/模板路径全部改为使用时解析），conftest 会话级 `_isolate_runtime_dirs` 指向 tmp 副本（排除密钥文件）；**实测全量 631 例跑完真实目录增量为 0**（此前 checkpoint 已累积 1698 个文件）。测试：后端 608→**631**，前端 109→**111** |
| *（未提交）* | **四域复查（第二轮）· 高价值项即修** | 用户"检查一下有什么好优化的" → 派 4 个独立审计（前端/后端/测试工程化/产品UX）+ 自查，**关键指控逐条实测复核**后修复 6 项：① **Agent 静默 Mock 掩盖失败**（6 个 Agent 的 `result.get("content", self._mock_xxx())` 在 Provider 报错时伪造成功；新增 `BaseAgent._content_or_error`：有 error 原样上报 → 引擎标记 error、工作流 fail）；② **前端 3 处裸 fetch 漏发鉴权头**（`getSession`/`submitDecision`/`exportTemplate` → 启用 ECOMM_API_KEY 后会话详情页永久 401、HITL 与模板导出必失败）；③ **租户 Key「保存/轮换」空输入可点 = 静默删除密钥**（placeholder 却承诺"留空不修改"）→ 空输入禁用 + 删除改两步确认；④ **非 ASCII 租户 Key 打穿认证链**（`hmac.compare_digest` 抛 TypeError → 所有请求 500）→ 保存入口按可打印 ASCII 校验 + `_safe_compare` 包装；⑤ **Provider 错误只有状态码**（配错端点/模型无法定位）→ 统一 `provider_error()`：状态码 + 生效端点 + 上游原文（命中密钥自动遮蔽），12 处替换；⑥ **ProviderCard 草稿被父级刷新静默回滚**（改模型/端点时保存凭据 → 编辑内容消失）→ 基线对比，仅在本地干净时采纳服务端新值。**另有 10+ 项已复核但不属本轮修复范围**（越权读设置、模板导入缺 admin、批量并发跑同一 job 等）—— 详见交付说明的优先级清单 |

### 2.2 功能模块完成度

| 模块 | 状态 | 说明 |
|------|------|------|
| 群聊引擎（ChatEngine） | ✅ 完成 | 异步群聊循环、上下文管理、checkpoint 持久化、A/B 模式 |
| Agent 注册中心 | ✅ 完成 | 9 个 Agent：8 个内置 + 风格拆解员（插件化 class 注册，新增 Agent 只需 YAML） |
| Provider 层 | ✅ 完成 | 7 个：OpenAI/DeepSeek/Anthropic/Seedream/Qwen/FLUX/Mock，能力声明式解析 + 降级 + **端点可覆盖**（env → `config/providers.yaml` → 官方默认，支持第三方 coding plan / 中转） |
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
| React 前端 | ✅ 完成 | 侧边栏 9 页工作台：仪表盘 / 会话（群聊插话）/ 工作流（画廊+画布+一键复刻）/ 批量任务（列表+**数据报表**）/ Agent 配置 / **设置（一行一 Provider**：状态点/多凭据只写密钥/字段级校验/**端点与模型自定义** + 模型映射 + 租户与前端 Key + 运行信息**）** / 审计 / 记忆 |
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

- **后端 62 个测试文件 / 1664 个默认套件测试通过**（`pytest`，确定性 Mock，≈6.0 分钟全绿；另 10 例 slow/real 按标记排除——`pytest -m slow` / `pytest -m real` 手动触发）
- **前端 313 个测试通过**（Vitest 4 + RTL，16 个文件，`cd frontend && npm test`；`npm run build` 零告警）
- **E2E 冒烟 8 场景**（`python scripts/e2e_smoke.py`，2026-08-23 P2 新增 S1-S7，2026-09-18 加 **S8 风格词库**；CI 已接入）
- **覆盖率基线**（P3）：总 **83%**（4966 语句，`docs/coverage_baseline.json`）。模块亮点：core/chat/storage/harness 多数 ≥90%（models/state/circuit/retry/timeout/checkpoint 100%），workflow 84-96%，providers 偏低（qwen 21%/flux 31%/anthropic 54% 属未接 Key 的探测路径）
- **P4 真实 API（2026-08-23）：+6 测试（默认不跑）**——`test_real_deepseek.py` 5 例：真实 chat 解析（tokens/cost）/json_mode dict/vision-exp 真实读图（图案识别）/错 Key 401/harness 集成（cost_tracker 计入 + 熔断保持 closed），**本地按用户批准跑最小验证 5 passed**；`test_real_seedream.py` 1 例（无 Key 自动 skip）。机制：pyproject `addopts=-m 'not real and not slow'` 默认排除；`pytest -m real` 手动触发；`.github/workflows/real-api.yml` manual workflow（secrets 注入）
- **P5 安全+性能（2026-08-23）：+22 测试**——上传模糊 7（截断 JPEG/PNG/解压炸弹 PNG-IHDR 25000²/空文件/RGBA 奇数宽/11MB 超限/300 字符文件名）、头伪造 5（大小写/URL 编码/重复头/Key 前导空格/Bearer 畸形）、表达式注入 6（`__import__/os.system/eval/exec/属性链/拼接/算术/路径/切片/三元` 全拒绝 + 插值原样保留 + 合法对照）、slow 负载 4（10 并发会话全产出物、100 项批量计数精确、3×15 轮长跑无泄漏、限流 429 后恢复）。**暴露并修复核心缺陷：Coordinator 状态实例级→会话级**（`_CoordState` 写穿：有 session 存 session、无 session 回退实例属性——并发会话不再互相覆盖 `_workflow_index`/`_memory_context`，engine 调用点同步改造）
- **设置页重做 + Provider 端点/模型（2026-08-23）：前端 +39 / 后端 +17**——`apiKeyRules` 7（空=保持现状/纯空白/非可打印 ASCII/`NAME=value`/带引号包裹）、`CredentialField` 11（状态三态、字段级校验、应用成功/失败保留输入、显示隐藏、两步确认清除、env 只读锁定）、`ProviderCard` 15（路由状态、行头标记、多凭据槽、端点编辑/恢复官方/env 锁定、模型增删与校验、保存 payload、图像路由无端点、失败诊断）、`Settings` 6（概览指标、一行一 Provider 分组、单卡展开、按 Provider 保存、清除、租户/前端 Key）；后端 `TestProviderConfig` 9（路由元信息、端点+模型写入与目录合并、URL/模型校验、未知路由 404、图像路由拒端点、清除回官方、env 优先与 403 锁定、Provider 实例生效、qwen 别名）+ `TestSecretKeySource` 4 + 设置端点 4。**测试卫生修复**：`tests/test_core/test_config.py` autouse 快照-还原 fixture（env 变量 + `_ENV_PROVIDED_SECRETS` 集合）——`apply_runtime_secrets_to_env()` 直接写 env 且原地追加集合，monkeypatch 拦不住，会跨文件泄漏（实测导致设置测试被误判 env 供给而 403）
- **A70-A78（第十八批，2026-09-18）：后端 +52 / 前端 +12** —— 提示词体检（14 条确定性规则，$0）、提示词审核优化员（设计七项 + 低于阈值改写 + 改写稿过体检才落地）、六段式提示词组装（第N张 + 画面/品牌色系/必须/留白/禁止/文字/身份）、槽位契约（intent/design/must/forbid/keep_clear）、品牌色板、背景策略三档、排版自适应与保分辨率、审查分批全审 + `realism` 维度、生图超时按张数、产物按槽位合并、逐张提示词前端可见、会话级槽位子集。**顺带修**：`platforms.yaml` 访问器无缓存导致平台规范块渲染 10.8s → 0.02s（后端全量 427s → 306s）、`resume_after_hitl(approve)` 伪造审查产物
- **A79-A96（第十九批，2026-09-18）：后端 +232 / 前端 +84** —— 风格档案库（8 条内置设计档案 + 逐槽位检索/渲染 + 事实中立三闸 + 与槽位契约冲突检测）、`style_store`（照片落独立目录、永不进生图链路、原子写、中断收割）、`AgentMeta.invitable`（后台 Agent 移出群聊名单 + 引擎侧不调用 Provider + **"YAML 字段必须显式取出"的回归测试**）、新 Agent「风格档案员」（白名单归一 + 防图片提示注入 + 6→3 图回落 + `maybe_billed`）、`/api/style-library*` 7 端点（异步分析、用量前置、编辑再过清洗、内置只能启停）、前端「风格词库」页（＋卡/弹窗/拖拽粘贴导入/2 秒轮询/零成本预览/用量行）、体检新规则 `style_template_copy`、`pricing.py`（**未标定不显示金额**，删掉 11 类伪价格 + `--max-images` 张数闸门 + `cost.js` 全站唯一口径 + `/api/settings/pricing*` 三端点）、`scripts/style_anchor.py`（照片→判词，默认只打印）+ `style_preview.py`（零成本自查）。**顺带修**：Mock 路径不算 `style_refs`（前端整行没数据）、`apply_analysis` 用空串覆盖用户起的名字（词条随后因"缺少 name"被丢弃）、停用的内置档案无法重新启用、预览请求非法槽位时静默放宽成"全部槽位"、相似度用 Jaccard 被长短差异稀释（改**包含度**）
- **A97-A107（第二十批，2026-09-20）：后端 +43 / 前端 +14** —— **一轮会话一个风格词**（会话锁贯穿生成/审核/体检/重跑；radio 启用即停用其他；严格模式默认 `style_library_max=1`：覆盖的槽位只注入用户风格，未覆盖的只按槽位契约；播报逐张列出实际注入项）、**套图结构**（`shot_flow`/`shot_roles`，角色由槽位目录派生、序号与照片严格对齐、界面逐张行可改）、**张数收口**（上限单一来源 20/单批 12/单张 10MB/总量 100MB 经 `stats.limits` 下发；超出分批且金额只在全部调用回报时求和；`timeout_budget` 与收割阈值随批数放宽）、追加/移除照片（移除重排序号 → 清空逐张角色并回报）、逐图打标、体积预算（超 8MB 先降采样）、`prompt_lint` 槽位级照抄比对、`POST /api/sessions/{id}/style`、弹窗补 `kinds/requires_policy/not_slots`。**顺带修**：界面"每张 20MB"与处理器真实 10MB 不一致（界面在撒谎）、`entry_conflicts` 把「文字/数字」当冲突导致 **36 处误报**、按位置重编号会让"第2张没识别出来 → 第3张被挤成第2张"（映射指错照片）、OpenAPI 快照与默认值断言同步更新
- C2 每租户独立 Key（决策 31）：+17 测试（`test_tenant_keys.py` 16 例——密钥绑定身份/跨租户隔离/管理端点权限/Key CRUD 与持久化/env 供给保护/WS；`test_auth.py` +1 管理面租户角色 403）
- DeepSeek V4 模型升级：+5 测试（`test_deepseek.py`——V4 默认模型/JSON 模式/视觉多模态透传/API 错误）
- P1 前端自动化：+63 测试（ChatPanel 30 / api.js 12 / ModelMappingEditor 7 / Settings 8 / StatusBadge 6）
- P2 E2E 冒烟：S1 群聊 8 轮全产出物 / S2 SLA 自动审批 / S3 批量死信+报表+重跑 / S4 审计+记忆 / S5 设置闭环 / S6 租户 Key 生命周期 / S7 前端+代理
- **P3 后端补缺（2026-08-23）：+73 测试**——CLI 13（`test_cli.py`：参数/退出码/坏图入口校验/Mock 全流程/选项透传/批量/agents-list/config-validate）、config 边界 20（`test_config.py`：缺文件/坏 YAML/CORS 三级优先级/密钥落盘往返/env 优先/Mock 检测/未知 provider 兜底/agent_overrides 优先级）、SQLite 韧性 9（`test_sqlite_resilience.py`：空文件/垃圾字节/截断库/目录路径报错、schema 重入幂等、20 并发计数、reconcile 只增不减、库内损坏 JSON 显式报错）、资源治理 5（TTL 进行中不驱逐 + `_runtimes` 终态清理/按需重建 + 熔断全流程）、checkpoint 5（损坏 JSON → None）、OpenAPI 快照 4（漂移检测 + `scripts/update_openapi_snapshot.py`）、错误契约 17（~26 个错误路径统一 `{"detail": ...}` 形态）。顺带修复 4 处源码：`_sweep_expired` 状态区分、`_load_sync` 损坏容忍、CLI 图片校验 + config-validate 退出码、auth 401/429 形态统一。**确定性 Mock 基建**：`tests/conftest.py` 会话级清 Key + MOCK_MODE=true + 重建注册表（顺序陷阱见里程碑表）
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

### 3.3 重要待办（用户指定，按提出顺序）

> 2026-09-16 真实会话验收后由用户指定：**先做上面两条（连续失败即停 / checkpoint 落盘粒度），
> 完成后再回来处理本条**。

1. ✅ **「审查/合规连续失败 N 次即停」可设置** —— 已完成（见 §2.1 里程碑，`config/default.yaml → chat.max_consecutive_review_failures`，默认 2，设置页「🛑 会话策略」可改，0 = 关闭）
2. ✅ **checkpoint 落盘粒度** —— 已完成（每个 Agent 步骤后写**轻量快照**：3.5MB → 128KB，剔除上传图 base64；完整快照仍在人工暂停/终态/压缩前写）
3. ✅ **【已完成·用户强调】成图质量：包装文字与品牌还原** —— 真实会话（`72d5ef86831c4f99`）3 张图链路全部打通，但审查员给 62.4/64.0 判 `fail`，核心问题：臆造第三方商标 `NUTRIVA®`（原包装 `DEFOEBUENA®`）、繁体品名/`60's`/GMP 条/肝脏解剖图整体缺失、半糊化乱码字形、`®` 漂浮、右下角 `AI生成` 水印、背景非纯白（≈#D8DDE6）。
   **已完成（A48-A58，见 §2.1 里程碑）**：① 商品身份识别 + 身份卡 + 群聊醒目提醒（未确认默认暂停）；② **文+图双条件生图**（`prompt` 与 `image:[data URI]` 同时入体，参考图来自上传原图并落盘 `inputs/`）；③ **可上传套图**（`set_plan.slots` 按槽位出图 + 落盘名带槽位 + 覆盖度校验）；④ 零硬编码与跨商品隔离（`MOCK_*` 去商品事实 + 溯源 + 静态扫描测试）；⑤ **拼多多等 10 个平台档案**（风格/白底/文字策略/槽位/上限，新增平台只改配置）；⑥ 本地体检（白度/水印/身份相似度/复制检测，零成本且已用真实产物回放验证：三张图边缘 240/238/235、右下区 −30.7/−27.1、身份相似度 0.29/0.25/0.38 全部命中）；⑦ 审查/合规拿到**原图作基准**逐项比对；⑧ 策略可在设置页改（文字策略/参考图模式/水印/阈值）+「试生成一张」验证入口。**代码层优化点 21 项中 L5 的三项（本地合成节点 / `sequential_image_generation` / 自动白平衡去水印）按计划不做**（治标、语义待实测、有伪造嫌疑）。
   **真机验证已完成（A59-A60，详见 `docs/code-review-round2.md` 第十六批）**：① 探针 1 张（29.5s/$0.04）**六项验收全过** —— 参考图确实入体（`reference_count=1`，3.5MB data URI 被接受）、`watermark:false` 生效（右下区 −0.28 vs 事故 −30.7）、背景 250.75（事故 240/238/235）、身份相似度 0.5648（事故 0.29/0.25/0.38）、非复制、**包装文字逐字一致**（品牌 `德國 樂美寶 DEFOEBUENA` / 繁体品名 / `60's` / GMP 认证 / 图案配色全部 same，verdict=pass —— 上次这三处被编成 `NUTRIVA®`）。② 真实会话（**拼多多** 5 张套图，8 轮，$0.4078）：身份识别 `confirmed`（品牌/品名/规格/认证齐、source=vision、置信度 0.86）、套图 5 槽位全覆盖、每张文+图 `reference_count=1`、审查员拿原图逐项比对并独立指出"白底图丢了肝臟解剖圖与 6 条英文引线"。③ **真机又抓出 4 项缺陷并已修**：体检把场景图/特写图误判为"水印 + 身份丢失 + 背景不合格"，害协调者白跑一轮重生成（$0.2）；文案出现"250 < 250"。修正后复核：事故三张图真阳性全保留（水印 2/3、身份 2/3、白底 3/3），本次套图误报全消。④ **仍待改进（非 bug）**：背景未达严格纯白（249.7~251.8）、包装图形元素被简化。真实花费合计 ≈ **¥3.2**。测试：后端 1190→**1196**（+6 条误报回归）。
   **仍待验证**：无（Phase 6 已完成）。
4. ⏳ **【用户追问后新增】套图角色与信息图**（A61-A63，见 `docs/code-review-round2.md` 第十七批）—— 用户："为什么生成的全是白背景＋商品的图，我记得我要求一次性生成的要一套可以实际使用的图片，例如『纯商品图+成分图+商品面向人群图片+商品效果列举图』"。取证确认**是我的设计失误**：槽位词表只有"白底/卖点/场景/细节/规格"（**没有成分/人群/功效角色**），且为防臆造文字把"模型不画字"推到极端 → 卖点图/规格图退化成"白底商品照 + 一块空白"。**已修完**：① 槽位词表扩为 10 类真实套图角色（纯商品/卖点/功效/成分/人群/规格/用法/场景/资质/对比，声明 kind·usage·copy·layout，**主图与详情图分别限额**）；② 信息图文字由**本地排版引擎**绘制（8 套版式 + 系统中文字体探测，字形 100% 准确、零成本）；③ 文案只取**已确认事实**，缺依据（如包装正面看不到成分表）→ `blocked` + 可执行指引（"请上传包装背面/成分表照片"），**绝不编造**；④ 违禁词过滤（简繁并列）。本地样例已渲染成功（卖点/功效/人群/规格；成分图按预期拦下）。**测试**：后端 1196→**1280**、前端 176→**184**、`npm run build` 干净（E2E 冒烟上一轮 7/7；本轮改动由全量测试覆盖，下次重启服务时顺带复跑）；Mock 模式也已覆盖新链路（摄影槽位是占位图、信息槽位产出真 JPEG），顺带抓出 A64-A68 五个潜伏 bug（压缩摘要消息缺消息信封、品类专项分析员会清掉低置信度告警、**用户手工补充的事实被内置兜底挤掉**、**部分保存排版/体检参数会把其余项重置成默认值**、**补充事实漏 await 导致重启即丢**）。**排版样式可设置（A67）**：`config/image.yaml → typography`（字号倍率 0.6–1.8 / 每图条目 1–8 / 主色 / 辅色 / 页脚），设置页「🖼️ 生图质量策略」四个控件，排版结果回传生效参数与"哪些文字被截断"。**用户补事实入口（A68）**：`POST /api/sessions/{id}/facts` + 会话页「✍️ 补充素材」表单，填完自动请协调者重新出图（实测：给会话补一条用法事实后，「使用方法图」从"缺素材"变成可出图）。**验收工具（A69）**：`python scripts/real_suite_run.py --images 正面.jpg 背面.jpg --platform pinduoduo`（成本闸门默认 $3、`--dry-run` 预估、报告落盘 `output/real-suite/`）。零成本预览：`python scripts/render_info_samples.py --all-slots [--user-copy-file facts.json] [--typography-file style.json]` —— 实测 8 类信息槽位渲染出 6 张（卖点/功效/人群/规格/资质/用法），成分/对比按预期拦下。**待办**：用户上传包装背面/成分表照片 → 跑真实套图验收（拼多多 6 主图 + 4 详情图 ≈ ¥2）。
   **补充入口已就绪（A68）**：`POST /api/sessions/{id}/facts` + 会话页"✍️ 补充素材"表单 —— 成分/用法/规格/对比等**包装看不到的信息**，用户可以直接在界面上填（或由你转述给我），填完自动请协调者重新出图；**不填就保持"缺素材·未生成"，绝不由模型编造**。
5. ✅ **【用户指定·已完成】风格档案库 / 风格词库（文字形态，不做图片参考图库）** —— 2026-09-18 用户提出"要不要一个底层图库用于你们拿来专门做对比和提示词生成风格灵感"，并明确 **"先帮我计入重要待办里面，等这一轮的计划完成以后提醒我"**；随后自己指定了界面形态："在左边页面栏记忆库下面加一个『风格词库』……导入照片、命名，点击开始生成会有专门的 agent 帮我分析这组照片的风格"。
   **已完成（A79-A96，见 §2.1 里程碑与 `docs/code-review-round2.md` 第十九批）**：① 内置 8 条设计档案 + 逐槽位检索注入（提示词生成员 / 提示词审核优化员）与锚点注入（+ 成图审查员）；② 新增后台 Agent「风格档案员」（照片 → 文字档案 + 审美判词，事实中立三闸、防图片提示注入）；③ 「风格词库」页面（＋卡 / 悬浮弹窗导入 / 命名 / 开始分析 / 轮询 / 编辑 / 启停 / 删除 / **零成本预览**）；④ 锚点工具 `scripts/style_anchor.py`；⑤ 成本金额诚实化（未标定不显示金额）。
   **维持的结论：做文字档案；不做图片直进生图的参考图库。** 理由：图片进生图链路会把**别人包装上的文字/图案**带进本商品（本仓库两次同类事故：记忆参考把"水飞蓟"抄进成分未确认的商品、`DEFOEBUENA®` 被编成 `NUTRIVA®`），且挤占实拍图参考额度、有版权风险。**照片只落 `data/style_library/` 供分析与缩略图，永不进 `data/inputs/`、永不作生图参考图**（有静态测试钉住）。
   **残留（等用户输入）**：`anchors:` 默认是空的 —— 需要用户给 1–2 张"就要这种感觉"的图（界面导入即可）或口述偏好，才会有一条**他自己的**打分准绳；在此之前审核员用的是内置设计标准。
   **用户实测后的三条追问已处理（A97-A107，见 §2.1 里程碑与 `docs/code-review-round2.md` 第二十批）**：①「我给的是一套图片……还有套图的制作习惯」→ **套图结构**（`shot_flow` + 逐张 `shot_roles`，含"第几张是什么角色"与"该张与共同美术不同在哪"）；②「为什么限定只能输入 6 张」→ 上限单一来源化为 **20 张**（单批 12、超出分批、单张 10MB、总量 100MB，全部经 `stats.limits` 下发）；③「有没有限定启用一个风格另一个自动停用」→ **一轮会话一个风格词**（radio + 会话锁 + 严格单风格）。**需要用户点一次「再分析」**才会拿到新结构（若已有词条只导入了一部分照片，可先「追加照片」补齐）。
6. ⏳ **【新增待办】「风格拆解员」是否也设 `invitable: false`** —— 它与「风格档案员」同族（由工作流 `style_replicate` / `POST /api/workflows/jobs/{id}/replicate` 调用，却仍出现在中心决策者的"可用 Agent"名单里）。本轮**刻意不改既有行为**（避免影响 M3 一键风格复刻的现有用法）；要改就是一行 YAML + 一条测试。判断依据：如果希望协调者完全不去邀请它 → 设 `invitable: false`；如果希望它能被邀请（例如"顺手指个参考图让群里复刻"）→ 保持现状。
7. ⏳ **【新增待办】真实价目标定** —— `config/pricing.yaml` 目前是空的（未标定时界面显示"未标定（N 张图）"，不显示金额）。用户从供应商控制台读到实际扣费后，在设置页「💰 计价」填一次（或做一次「标定」）即可让金额重新可信；**不要靠猜价格**。
8. ⏳ **【新增待办·等用户拍板】生成时是否要按参考套图的顺序/组合重排本平台套图** —— 现在参考套图只影响"每张怎么写"（该张做法 + 叙事顺序）并如实报出覆盖差；**组合与顺序仍由 `config/platforms.yaml` 的 `slots/detail_slots` 决定**（那是平台契约的单一事实来源，也是导出 ZIP 的顺序）。若用户要的是"第2张就放成分配方图"，两条路：改 `config/platforms.yaml` 里该平台的槽位顺序，或加一个"按参考套图重排主图"的会话开关（后者要动 `platform_slot_table` 与导出顺序，属独立一轮）。
9. ⏳ **【新增待办·等用户需要】单条词条的磁盘上限** —— 20 张 × 每租户 50 条 ≈ 最坏 2GB（预处理后单张 ≤10MB）。`delete_entry` 会清目录、`remove_photo` 可单张删，但没有"自动清理旧照片/按需保留 N 张"的策略；用户若觉得占盘，可加一个设置项。
10. ⏳ **【新增·待用户拍板】风格词只到「提示词生成员/审核优化员」，没进最终生图提示词** —— 会话 `bb6cc0fa56a54921` 取证：`style_refs` 证明风格注入成功（10 张里 9 张），但那 1272 字的风格块**只活在推理期**；`harness/image_prompt.build_image_prompt()` 的签名里**没有任何风格参数**，最终提示词 7 段中风格只以「模型改写过的措辞」间接存在于【画面】，而【画面】被 `MAX_SCENE_CHARS=260` 裁掉（10/10 触发：298→260、382→260、391→260…）。另一条冲突：风格词是粉色系（样本实测 hue 305°–352°），【品牌色系】注入的是包装实测 `#1B3A8C`/`#4CA83F`，生成图实测粉色 **0.0%**。用户已拍板**「包装事实优先」**（品牌色不动，风格只作用于背景/光/留白/道具，并如实报出被弃用的风格条目）。修法已定（风格字段由代码后置注入成独立【风格】段 + 分层长度预算），**尚未实施**——等用户决定是否与其余两项（逐张落盘止血、审查员超时一行配置）一并做。
11. ⏳ **【新增·用户已明确方向】风格套图永不进生图参考图** —— 用户 2026-09-20 明确"风格套图还是不要放入对话里"，维持 A79-A96 的既有决策与静态测试；风格还原度只能靠文字链路，因此第 10 条的"落进最终提示词"是唯一正路。

**架构待办（非阻塞）：**- ~~`auth.py` 位于 `src/core/`，依赖 FastAPI（违反"core 不依赖上层"的依赖方向）~~ → ✅ 2026-08-16 迁至 `src/api/auth.py`，core 层不再依赖 FastAPI/Starlette
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
| 37 | "apikey 的配置页面太粗糙了，可以仿照 deepseek harness 的模型设置的方式" | 读 DSH 源码包 `@deepseek-ai/dsh-client-ui-settings-models`（README.zh + `ProviderEditor.d.ts` + `apiKey.d.ts`）提炼范式并落到本项目设置页：**提供方行 + 一次只展开一张卡片**、**状态点四态**（绿=已配置且注册表生效 / 黄=已配置未激活 / 灰=未配置 / 蓝=env 供给只读）、**只写密钥框**（空=保持现状、保存后清空、可显示/隐藏）、**字段级校验**（镜像 DSH `apiKeyFailure`：纯空白/非可打印 ASCII/`NAME=value`/引号包裹全拒绝）、**失败保持展开 + Host 诊断**、**无障碍状态消息**、**两步确认清除 + 高级信息折叠**。后端配套补 `provider`/`available`/`source` 三元数据 + env 供给写入 403；前端 63→93 例、后端 565→573 例 |
| 38 | "每个运行商只能添加 apikey，但是没法添加模型，如果我是用的谁家的 coding plan 那会出现调用的问题" | 诊断证实两处硬缺口（**base_url 全部硬编码** → 第三方端点根本不可用；**无自定义模型目录** → coding plan 的模型 id 只能手打且不在建议里）。按 DSH 的「provider 卡片 = 主字段 API key + 折叠自定义设置（baseURL + 模型目录）」补齐：① 新增 `config/providers.yaml`（gitignore）+ `resolve_base_url()` 三级解析（env → 文件 → 官方），4 个 LLM Provider 与 OpenAI 生图接入端点覆盖；② 新端点 `POST /api/settings/providers/{route}`（保存即重建注册表）；③ 自定义模型并入 `model_catalog`，可在模型映射里设为能力默认；④ 前端重构为**一行一 Provider**（`ProviderCard`/`CredentialField`，多凭据归入同卡）。**要点**：图像路由（seedream/flux）端点多上游签名 → 明确不支持自定义端点（400 + UI 说明），仅模型可配；env 供给的端点沿用 C2 约定拒绝写入（403）

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

> ⚠️ **2026-08-23 快照**：四域审计 65/65（决策 18-21）+ M5b 审批矩阵（决策 22）+ 前端依赖核查 + C2 每租户独立 Key + DeepSeek V4 升级 + 前端美术升级 + 一键启动 + **测试计划 P1-P6 全部完成**（P1 前端 63 例 / P2 E2E 7 场景 / P3 后端补缺 73 例 + 确定性 Mock 基建 + 覆盖率基线 83% / P4 真实 API 6 例按批准最小验证 / P5 安全+性能 22 例 + Coordinator 会话级状态修复 / P6 CI 补强）+ **设置页重做（仿 DSH 模型设置范式**，决策 37）+ **Provider 端点与模型配置（coding plan 支持**，决策 38）。**测试资产：后端默认套件 582 全绿（+4 slow +6 real 按标记运行）、前端 Vitest 102 全绿、E2E 冒烟 7 场景全绿**；前端 build 零告警（vite 7 / react-router 7）、`npm audit` 0 漏洞、CI 五道门禁（pytest / ruff / vitest+build / npm audit / e2e / docker）、工作树待提交。**5.3 节遗留开放决策全部关闭，test-plan P1-P6 全部收官**。后续候选：React 19 / recharts 3 / Vite 8 大版本升级（无安全收益，暂缓）；平台级连接器（已确认不做）；真实 API 套件定期手动回归（CI manual workflow）。

- **测试计划 P1-P6 全部完成**（2026-08-23）——见 `docs/test-plan.md`。后续候选（非测试路线）：React 19 / recharts 3 / Vite 8 大版本升级（无安全收益，暂缓）；真实 API 套件定期手动回归（`pytest -m real` 或 CI manual workflow `real-api.yml`，需用户批准触发）

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
| 代码审查报告 | `docs/code-review.md` | 第一轮 65 个审计问题的完整清单与修复优先级（§五：全部复查通过） |
| **复查报告（第二轮）** | `docs/code-review-round2.md` | 4 域复查 + 用户实测反馈：**A 节 42 项已修**（第一批 8 项：静默 Mock 掩盖失败/成本链路失效/产物被覆盖/鉴权头遗漏/租户 Key 误删/非 ASCII 撞库/错误不可诊断/草稿回滚；第二批 6 项 A9-A14：设置面越权/模板导入越权/批量并发双跑/取消被推翻/冒烟端口误杀/测试污染真实目录；第三批 5 项 A15-A19：输出校验崩溃/记忆并发丢条目/checkpoint 非原子写/磁盘无回收/恢复会话 TTL 失效；第四批 3 项 A20-A22：测试连接/失败原因可见/生图回落可见；第五批 A23：生成图落盘与导出；第六批 A24：自定义服务商与火山方舟建模；第七批 A25：方舟接入排错与一键迁移；第八批 A26：模型 id 校准与拉取可用模型；第九批 A27：人工审批竞态；第十批 A28：拉取结果命名/401 解释；第十一批 A29：模型映射可用性可见；**第十二批 A30-A42：真实会话实测复盘（方舟生图尺寸/审查图源/推理预算/门禁/审计/记忆串味）**）+ **B 节其余 15 项待修清单**（正确性与资源 7 / 工程化 5 / 产品体验 3）+ C 节已关闭方向 |
| 模块文档 | `docs/modules/*.md` | 16 篇：核心 8 篇 + workflow/auth/tenant/logging/storage/harness-extended/ab-testing/image-preprocessor，覆盖全部源文件 |
| **Workflow 设计** | `docs/workflow-design.md` | 编排层设计（模板 DSL / 状态机 / 批量 / 画布），借鉴 OiiOii 范式，M1-M5b 已实施 |
| **测试计划** | `docs/test-plan.md` | 9 层测试体系 + P1-P6 路线（**P1-P6 全部完成**：前端 63 例 / E2E 7 场景 / 后端补缺 73 例 / 真实 API 6 例 / 安全+性能 22 例 / CI 补强） |
| 本状态总览 | `docs/progress.md` | 进度 / 计划 / 思路 / 决策（本文档） |
