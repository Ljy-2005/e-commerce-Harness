# 测试计划（Test Plan）

> 版本：v1.2
> 日期：2026-08-23
> 状态：P1-P6 **全部完成** ✅（P4 真实套件按批准最小验证；P5 安全+性能；P6 CI 补强）
> 背景：项目已有 48 个测试文件、565 个默认套件测试全绿（另含 4 slow + 6 real 按标记排除）；本计划的
> 目标是**系统性地测试项目各方面问题**，补齐现有盲区并建立可持续的质量门槛。

---

## 1. 目标与原则

### 1.1 目标

1. **守住核心链路** —— 群聊会话、工作流、批量、鉴权/租户隔离 四大链路 100% 回归保护
2. **补齐三大盲区** —— 前端零自动化、真实 API 零验证、E2E 冒烟靠手工
3. **量化质量** —— 覆盖率基线 + 门槛，防止长期漂移
4. **问题早暴露** —— 每类问题（安全/并发/时序/资源泄漏）都有对应的自动化探针

### 1.2 原则

- **Mock First**：默认套件无网、无 Key、确定性（沿用项目传统）
- **真实调用显式化**：需要真钱的测试一律 `@pytest.mark.real`，默认不跑，CI 提供可选 job
- **不污染真实数据**：一切持久化（secrets/tenant_keys/workflow.db/memory/audit）测试走 tmp 隔离
- **时序断言 spy 化**：不轮询瞬时状态（项目已有教训，见 progress.md 决策 6/13）
- **测试金字塔**：单元 > 集成 > API > E2E，越往上越少但覆盖关键路径

---

## 2. 现状盘点（2026-08-23）

| 层 | 现状 | 缺口评级 |
|----|------|---------|
| L1 单元（后端） | ✅ 48 文件 / 565 默认测试：agents 9、harness 14、workflow 12、providers 4(+1 real)、chat 5、api 8、core 2、CLI 1、启动器 1 | 已清零（CLI/config 边界/覆盖率基线 83%） |
| L2 集成（后端） | ✅ 引擎/工作流恢复/批量并发/审计/记忆 | 已清零（SQLite 韧性/资源治理/checkpoint） |
| L3 API 契约 | ✅ 鉴权矩阵 13+16、租户隔离、全端点 | 已清零（OpenAPI 快照/错误契约统一） |
| L4 前端 | ✅ 63 例（P1）+ CI 接入 | 已清零 |
| L5 E2E 冒烟 | ✅ 脚本化 7 场景（P2）+ CI e2e job | 已清零 |
| L6 真实 API | ✅ **6 例已补（P4）**：DeepSeek 5 例本地真实验证通过；Seedream 1 例（无 Key skip）；CI manual workflow | 已清零（真实协议细节由 L6 兜底，按需触发） |
| L7 安全 | ✅ 穿越/SSRF/租户/限流矩阵较全 | 已清零（上传模糊 7 + 头伪造 5 + 表达式注入 6） |
| L8 性能/负载 | ✅ **4 例已补（P5，slow）**：10 并发会话/100 项批量/长跑/限流恢复 | 已清零 |
| L9 部署 | ✅ **P6**：CI Docker 构建 job + npm audit 门禁（0 漏洞） | 已清零（前端 lint 决策：不引入 ESLint，以 Vitest+build 代替） |

---

## 3. 分层测试计划

### L1 — 单元测试补缺（后端）

| 项 | 内容 | 验收 |
|----|------|------|
| **CLI 测试（新增）** | ✅ `tests/test_cli.py`（13 例）：`run` 参数解析/缺参 exit 2、文件不存在/非文件/坏图 exit 1、Mock 全流程（含产出物摘要）、选项透传（spy 记录 SessionManager.create）、未知平台透传、`batch` 目录/过滤、`agents-list`、`config-validate` OK 与失败路径 | ≥10 用例 ✅ |
| **config 边界** | ✅ `tests/test_core/test_config.py`（20 例）：缺文件→{}、坏 YAML 报错、agent 列表跳过 `_` 前缀、CORS 三级优先级、secrets 落盘往返/删除/env 优先级、Mock 检测显式/自动、models.yaml 未知 provider 兜底、agent_overrides 优先级 | +4 用例 ✅ |
| **覆盖率基线** | ✅ 已用 pytest-cov 跑基线：**总覆盖率 83%**（`docs/coverage_baseline.json`；chat/core/storage/harness ≥90%，workflow 84-96%，providers 31-97%），记录在 progress.md 2.4 | 生成基线报告 ✅ |

### L2 — 集成测试补缺

| 项 | 内容 | 验收 |
|----|------|------|
| **SQLite 韧性** | ✅ `tests/test_workflow/test_sqlite_resilience.py`（9 例）：空文件按 SQLite 语义初始化、垃圾字节/截断库/目录路径 → 明确报错（DatabaseError/OperationalError）、schema 重入幂等 + user_version 不漂移、20 并发 increment_batch 计数精确、reconcile 只增不减、库内损坏 JSON 显式 JSONDecodeError | +5 用例 ✅ |
| **资源治理回归** | ✅ Session TTL 进行中会话（running/waiting_human）不驱逐（`test_session_ttl.py` +1，修复 `_sweep_expired` 未区分状态）；`_runtimes` 终态清理 + 按需重建（`test_resource_governance.py` 3 例）；熔断器 OPEN→HALF_OPEN→CLOSED 全流程 + 半开窗口门控（`test_circuit.py` +1） | +3 用例 ✅ |
| **checkpoint 崩溃恢复** | ✅ `tests/test_harness/test_checkpoint.py`（5 例）：损坏/半写 JSON → load 返回 None（`_load_sync` 加固，不再依赖外层 try/except）、非 dict 内容 → None、save/load/delete 往返 | 补 1 例损坏 JSON ✅ |

### L3 — API 契约测试补缺

| 项 | 内容 | 验收 |
|----|------|------|
| **OpenAPI 漂移检测** | ✅ 快照 `tests/fixtures/openapi_snapshot.json` + 生成脚本 `scripts/update_openapi_snapshot.py` + `test_openapi_snapshot.py`（4 例：快照同步/关键端点/关键方法/错误 Schema 声明）；端点变更必须显式更新快照 | +1 用例 ✅ |
| **错误响应契约** | ✅ `tests/test_api/test_error_contract.py`（17 例，抽查 ~26 个错误路径）：404/400/413/403/401/429/422/503 全部统一 `{"detail": str \| list}` 形态；**顺带修复 AuthMiddleware 401/429 原返回 `{"error","hint"}` 的不一致形态**（前端 `detail \|\| error` 兼容无需改动）；成功路径回归护栏 | +5 用例 ✅ |

### L4 — 前端自动化（最大缺口，全新）

**技术选型**：Vitest + @testing-library/react + jsdom（Vite 生态原生、无需大改构建）+ 可选 Playwright 做 E2E。

| 项 | 内容 | 验收 |
|----|------|------|
| **基建** | `package.json` 加 test 脚本与依赖；`vitest.config`；测试 utils（渲染 + mock api 模块） | `npm test` 可跑 |
| **纯函数组件** | `ChatPanel`（msgPreview 分支/过滤器/typing 条件）、`StatusBadge`、`ModelMappingEditor`（能力过滤/保存 payload）、`api.js`（authHeaders/wsUrl） | ≥30 用例 |
| **页面状态机** | `Settings`（加载失败/保存成功/租户 Key env 禁用态）、`Session`（路由切换重置）、`Dashboard`（轮询失败横幅） | ≥15 用例 |
| **快照（低优先）** | 关键组件轻量快照，防无意识 UI 回归 | 视情况 |

### L5 — E2E 自动化冒烟（脚本化手工冒烟）

**技术选型**：Python 脚本 `scripts/e2e_smoke.py`（零新依赖，复用 httpx），或 pytest `@mark.e2e`（需先起真实服务）。

| 场景 | 内容 |
|------|------|
| S1 群聊全链路 | 上传图 → 会话创建 → 等 8 轮 → 断言 completed + 产出物（analysis/prompts/images/review/compliance）齐全 |
| S2 工作流 | 实例化 approval_matrix → 自动挡跑完 → 审批自动决策事件 |
| S3 批量 | JSON 5 项 → 全完成 → 报表聚合数一致 → 死信重跑 |
| S4 记忆/审计 | 会话后记忆召回命中、审计条目含 tenant_id |
| S5 设置闭环 | 保存模型映射 → 重载生效；租户 Key 创建/轮换/删除 |
| S6 鉴权冒烟 | 无 Key 401 / 错 Key 401 / 租户 Key 绑定 / 管理面 403 |
| S7 前端冒烟 | 首页 200 + 关键资源 200（配合 dev/prod build） |

验收：`python scripts/e2e_smoke.py` 单命令全绿（Mock 模式，可加 `--real` 开关跑真实链）。

### L6 — 真实 API 测试（新增，默认不跑）

| 项 | 内容 |
|----|------|
| **DeepSeek V4** | ✅ `tests/test_providers/test_real_deepseek.py`（5 例，`@pytest.mark.real`）：真实 chat 解析（tokens/cost 字段）、json_mode 返回 dict、**vision-exp 真实读图**（红圆蓝块图案被识别）、错误 Key → 401 路径、**真实调用走完整 harness 链**（cost_tracker 计入 session.cost_so_far + 熔断保持 closed）。本地已按用户批准跑最小验证：5 passed |
| **Seedream 生图** | ✅ `tests/test_providers/test_real_seedream.py`（1 例）：真实生成 1 张 → 断言 image_url/base64 有效 + model_used + cost 字段；缺失 Key 自动 skip（本地无 Key → skipped） |
| **成本与超时** | ✅ 并入 harness 集成用例：真实调用计入 cost_tracker；成功调用不误熔断（超时路径行为由既有 mock 超时测试覆盖） |
| **运行方式** | ✅ 默认套件排除：pyproject `addopts = -m 'not real and not slow'`；本地 `pytest -m real`；CI 新增 manual workflow `.github/workflows/real-api.yml`（workflow_dispatch + secrets） |
| **conftest 联动** | ✅ `-m real` 时保留 Key 与真实环境（`_selects_real` 用 pytest 标记表达式求值器判断——**不能子串匹配**：addopts 默认表达式含 "real" 字样会误判为选中 real，P5 实测踩坑后修复） |

### L7 — 安全测试补缺（P5 完成）

| 项 | 内容 | 验收 |
|----|------|------|
| 上传模糊测试 | ✅ `tests/test_api/test_upload_fuzz.py`（7 例）：截断 JPEG/PNG 400、**解压炸弹**（PNG IHDR 声明 25000² 无像素数据 → 头部尺寸校验 400）、空文件 400、RGBA 奇数宽度正常处理、11MB 超限 400、300 字符文件名不崩溃 | +8 用例 ✅ |
| 头伪造变体 | ✅ 同文件（5 例）：X-Tenant-ID 大小写等价、URL 编码按字面处理（403 不穿越）、重复头取第一个、X-API-Key 前导空格 401（不 trim）、Bearer 畸形 401 | +5 用例 ✅ |
| 表达式注入 | ✅ `tests/test_workflow/test_expression_injection.py`（6 例）：`__import__/os.system/eval/exec/属性链/语句拼接/算术/路径/切片/三元` 全部拒绝（ValueError 或安全求值 None/False）、`{...}` 插值注入原样保留不执行、合法表达式对照不受影响 | +4 用例 ✅ |

### L8 — 性能/负载基础测试（P5 完成，标记 slow）

| 项 | 内容 | 门槛 |
|----|------|------|
| 并发会话 | ✅ `tests/test_harness/test_load.py`：10 并发 Mock 群聊全 completed + 全产出物 + 无串扰（**回归防护：Coordinator 状态从实例级改为会话级**——并发会话曾互相覆盖 `_workflow_index` 导致流程跳步、产出物缺失，P5 实测暴露并修复） | 完成率 100% ✅ |
| 批量压力 | ✅ 100 项批量（MAX_BATCH_ITEMS 边界，99 好 1 坏）：done=99 failed=1 计数精确、死信隔离、attempts=3 | 计数一致 ✅ |
| 长跑稳定性 | ✅ 3 会话 × 15 轮上限连续跑：全 completed、删除后 `_sessions` 归零（无泄漏） | 0 泄漏 ✅ |
| 限流有效性 | ✅ 高频错误请求 429 → 回拨失败窗口模拟过期（确定性）→ 恢复 401、正确 Key 立即可用 | 429 出现且可恢复 ✅ |

### L9 — 部署/CI 补强（P6 完成）

| 项 | 内容 |
|----|------|
| Docker CI | ✅ ci.yml 新增 `docker` job：`docker build -f deploy/nginx.Dockerfile`（多阶段缓存友好；本机无 Docker，job 由 CI 验证） |
| 前端 lint | 保持现状（决策点：以 Vitest + build 零告警代替，不引入 ESLint） |
| 依赖安全 | ✅ ci.yml 前端 job 新增 `npm audit --audit-level=high --registry=https://registry.npmjs.org` 门禁（本地实测 0 漏洞、exit 0；官方 registry——npmmirror 无 advisory 端点） |

---

## 4. 测试数据与环境管理

| 项 | 约定 |
|----|------|
| 隔离目录 | 全部走 `tmp_path`；secrets/tenant_keys 快照-还原（沿用现有 fixture 模式） |
| 真实图片 fixture | `tests/fixtures/`：合法小图（JPEG/PNG/RGBA）+ 恶意样本（炸弹/截断/EXIF），统一维护 |
| 真实模式凭证 | 仅读环境变量，测试内不硬编码 Key；`real` 标记套件缺失 Key 时自动 skip |
| 时序 | 禁 `time.sleep` 轮询断言；用 spy/事件/状态查询（既有约定） |

---

## 5. 分阶段实施路线

| 阶段 | 内容 | 预估新增用例 | 优先级 |
|------|------|-------------|--------|
| **P1 前端自动化基建** | ✅ 2026-08-23 完成：Vitest 4 + @testing-library/react + jsdom 基建（vitest.config / setup 含 scrollIntoView 桩）；ChatPanel 30 例（msgPreview 全分支/渲染状态/typing/插话回退/过滤器）、api.js 12 例（localStorage/authHeaders/wsUrl/request 错误映射/请求体）、ModelMappingEditor 7 例（能力过滤建议/保存 payload）、Settings 8 例（租户 Key env 禁用态/创建失败/保存流）、StatusBadge 6 例；共 63 例全绿，CI 接入 npm test | ~45 | 🔴 ✅ |
| **P2 E2E 冒烟脚本** | ✅ 2026-08-23 完成：`scripts/e2e_smoke.py` 七场景一键全链路（S1 群聊 8 轮全产出物 / S2 approval_matrix SLA 自动审批 / S3 批量 4+1 死信与报表与重跑 / S4 审计租户字段+记忆 / S5 模型映射闭环 / S6 租户 Key 全生命周期 / S7 前端+代理），自动启动确定性 Mock 服务、跑完自停（ctypes 双栈端口清扫零残留）；CI 新增 e2e job。实施教训：① **MOCK_MODE=true 不等于确定性**——secrets.yaml 注入的 Key 会让 Agent 走真实 API（协调器行为随机、视觉读 SVG 卡死），自动启动必须清空 Provider Key；② 无管理 Key 时租户 Key 轮换/删除只能走 admin（S6 需 ECOMM_API_KEY，缺失自动跳过）；③ Windows 清理：npm→cmd→node 多级孙进程 + vite 绑 IPv6 ::1，需 ctypes GetExtendedTcpTable 双栈查询（IPv6 行 dwState 在 dwOwningPid 前，结构体顺序坑）+ TerminateProcess | 1 脚本/7 场景 | 🔴 ✅ |
| **P3 后端补缺** | ✅ 2026-08-23 完成：**73 例**——CLI 13（参数/退出码/坏图入口校验/Mock 全流程/选项透传）+ config 边界 20 + SQLite 韧性 9 + 资源治理回归 5（TTL/_runtimes/熔断）+ checkpoint 5 + OpenAPI 快照 4 + 错误契约 17。**顺带 4 处源码修复**：① `_sweep_expired` 只驱逐非进行中会话（进行中/等待人工不驱逐）；② `checkpoint._load_sync` 损坏 JSON → None；③ CLI `run` 入口加图片校验（复用 ImagePreprocessor）+ `config-validate` 失败退出码 1；④ AuthMiddleware 401/429 统一 `{"detail": ...}` 形态。**关键基建：pytest 确定性 Mock 环境**（conftest 会话级清空 Provider Key + 强制 MOCK_MODE + 重建注册表——实测修复前本地全量测试在打真实 DeepSeek：耗时 25min+/行为随机；修复后 547 例 4 分钟全绿）。覆盖率基线：**83%** | ~25 | 🟡 ✅ |
| **P4 真实 API 套件** | ✅ 2026-08-23 完成（用户批准最小验证）：DeepSeek 5 例（chat/json_mode/**vision-exp 真实读图**/401/harness 集成成本+熔断）全部真实调用通过；Seedream 1 例（无 Key 自动 skip）；pyproject 默认排除 real/slow（`addopts`）；CI manual workflow `.github/workflows/real-api.yml`；conftest `_selects_real` 用标记表达式求值器判断（踩坑：子串匹配会把 `not real` 误判为选中 real） | 6 | 🟡 ✅ |
| **P5 安全+性能** | ✅ 2026-08-23 完成：**22 例**——上传模糊 7 + 头伪造 5（`test_upload_fuzz.py`）+ 表达式注入 6（`test_expression_injection.py`）+ slow 负载 4（`test_load.py`：10 并发会话/100 项批量/3×15 轮长跑/限流恢复）。**顺带修复 1 处核心缺陷**：Coordinator 状态实例级 → 会话级（`_CoordState` + engine 调用点改造），并发会话不再互相覆盖 `_workflow_index`/`_memory_context`（Mock 流程跳步、真实模式记忆串台） | ~30 | 🟢 ✅ |
| **P6 CI 补强** | ✅ 2026-08-23 完成：ci.yml 新增 docker job（多阶段 nginx 构建）+ 前端 job 新增 `npm audit --audit-level=high` 门禁（本地实测 0 漏洞）；后端 job 改 `pytest -m "not real"`（含 slow 负载套件） | 0 | 🟢 ✅ |

## 6. 质量门槛（实施后生效）

| 门槛 | 值 |
|------|-----|
| 全量测试 | 必须 100% 绿（含新套件）—— **P5 后默认套件 565 例全绿**（另 4 slow + 6 real 按标记运行） |
| 前端 | `npm test` 全绿 + `npm run build` 零告警 —— 63 例全绿 + build 零告警 |
| E2E | CI 每次 push 跑 Mock E2E 冒烟 —— 7 场景全绿 |
| 覆盖率（后端） | **基线已测：总 83%**（core/chat/storage ≥90%，workflow 84-96%；providers 部分低覆盖：qwen 21%/flux 31% 属未接 Key 的探测路径）。首期仅记录基线，硬门槛建议 core/chat/storage ≥90%、workflow ≥85%、providers ≥70% |
| 依赖安全 | `npm audit --audit-level=high` 0 漏洞 —— **P6 门禁已落地，本地实测 0 漏洞** |

## 7. 风险与边界

1. **真实 API 成本**：real 套件每次跑产生真实费用 → 仅在用户批准时手动触发；用例用最小输入（1 张图/1 次 chat）
2. **时序脆弱**：E2E 断言等终态（completed/failed），不测瞬时 running；超时预算宽裕（60s+）
3. **前端测试学习曲线**：jsdom 无法覆盖 WS/布局 → WS 用 mock hook，布局交给 E2E/手动
4. **Mock 的盲区**：Mock Provider 返回模板，无法验证真实协议细节 → 由 L6 真实套件兜底
5. **OpenAPI 快照的维护成本**：端点变更需同步快照，作为"契约变更显式化"手段（利大于弊）

---

## 附：本计划与现有资产的关系

- 不重写任何现有测试；P1-P6 全部为**新增**（补盲区）
- 现有 468 用例继续作为回归基线；**P5 后实际为 565 例默认全绿（+4 slow +6 real 按标记运行）**
- 最终测试资产：565 默认后端用例 + 4 slow 负载 + 6 real 真实 API + 63 前端用例 + 7 场景 E2E
