# 测试计划（Test Plan）

> 版本：v1.0
> 日期：2026-08-23
> 状态：设计稿，待用户确认后分阶段实施
> 背景：项目已有 44 个测试文件、468 个后端测试全绿；本计划的目标是**系统性地测试项目各方面问题**，
> 补齐现有盲区并建立可持续的质量门槛。

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
| L1 单元（后端） | ✅ 44 文件 / 468 测试：agents 9、harness 11、workflow 9、providers 4、chat 5、api 4、core 1、启动器 1 | 中：**CLI 零测试**、config 边界、覆盖率未量化 |
| L2 集成（后端） | ✅ 引擎/工作流恢复/批量并发/审计/记忆 | 低：SQLite 损坏恢复、并发读写压力 |
| L3 API 契约 | ✅ 鉴权矩阵 13+16、租户隔离、全端点 | 低：契约快照（OpenAPI 漂移检测）缺失 |
| L4 前端 | ❌ **零自动化**（CI 仅 `npm run build`） | **高**：无组件/交互/E2E 测试 |
| L5 E2E 冒烟 | ⚠ 手工做过（决策 7/959e6e3），未脚本化 | **高**：无法防回归 |
| L6 真实 API | ❌ `real` 标记已声明但**无任何用例** | **高**：DeepSeek V4/Seedream 真实调用从未验证 |
| L7 安全 | ✅ 穿越/SSRF/租户/限流矩阵较全 | 低：上传模糊测试、头部伪造变体 |
| L8 性能/负载 | ❌ 无 | 中：并发会话/批量压力、长跑稳定性 |
| L9 部署 | ⚠ CI 无 Docker 构建、无前端 lint | 中 |

---

## 3. 分层测试计划

### L1 — 单元测试补缺（后端）

| 项 | 内容 | 验收 |
|----|------|------|
| **CLI 测试（新增）** | `tests/test_cli.py`：`python -m src.cli run` 参数解析、Mock 模式跑通、错误路径（缺图/坏图/未知平台）、退出码 | ≥10 用例 |
| **config 边界** | `test_models.py` 补：models.yaml 缺文件/坏 YAML/未知 provider/agent_overrides 优先级 | +4 用例 |
| **覆盖率基线** | 用已装的 `pytest-cov` 跑一次基线，输出 `coverage.json`/term 报告，记录各模块覆盖率 | 生成基线报告 |

### L2 — 集成测试补缺

| 项 | 内容 | 验收 |
|----|------|------|
| **SQLite 韧性** | JobStore：损坏 db 文件（半写/空文件）→ 报错而非崩溃；`user_version` 迁移幂等；并发 create_batch 计数正确 | +5 用例 |
| **资源治理回归** | Session TTL 边界（活跃态不驱逐）、`_runtimes` 终态清理、熔断器恢复全流程 | +3 用例 |
| **checkpoint 崩溃恢复** | 会话中途"杀进程"→ 重启恢复标记 failed；工作流断点续跑双保险 | 已有，补 1 例损坏 JSON |

### L3 — API 契约测试补缺

| 项 | 内容 | 验收 |
|----|------|------|
| **OpenAPI 漂移检测** | 导出 `app.openapi()` 快照（JSON）入库，测试断言关键端点/字段存在；改动需同步快照 | +1 用例 |
| **错误响应契约** | 全端点错误路径统一 `{"detail": ...}` 形态断言（抽查 20 个代表性错误路径） | +5 用例 |

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
| **DeepSeek V4** | `tests/test_providers/test_real_deepseek.py`（`@pytest.mark.real`）：chat 返回解析、vision-exp 多模态真实读图（用 fixtures 里的测试小图）、错误 Key → 401 路径 |
| **Seedream 生图** | 真实生成 1 张 → 断言 image_url/base64 有效 + 成本字段 |
| **成本与超时** | 真实调用计入 cost_tracker；超时路径不误熔断 |
| **运行方式** | 本地 `pytest -m real`；CI 提供 manual workflow（用 secrets） |

### L7 — 安全测试补缺

| 项 | 内容 |
|----|------|
| 上传模糊测试 | 截断 JPEG/PNG、EXIF 炸弹、RGBA 奇数宽度、空文件、超长文件名 → 全部 400/413 不崩溃（+8 用例） |
| 头伪造变体 | X-Tenant-ID 大小写/URL 编码/重复头；X-API-Key 前导空格；Bearer 畸形（+5 用例） |
| 表达式注入 | workflow `when` 表达式注入尝试（`__import__`/系统调用形态）→ 全部安全拒绝（+4 用例） |

### L8 — 性能/负载基础测试（新增，标记 slow）

| 项 | 内容 | 门槛 |
|----|------|------|
| 并发会话 | 10 并发创建 + 跑 Mock 群聊，断言全部完成且无共享状态串扰 | 完成率 100% |
| 批量压力 | 100 项批量（MAX_BATCH_ITEMS 边界），断言计数精确、死信隔离 | 计数一致 |
| 长跑稳定性 | 3 个会话 × 15 轮连续跑，断言无内存会话泄漏（`_sessions` 终态清理）+ 无未取回 task 异常 | 0 泄漏/0 异常 |
| 限流有效性 | 高频请求触发 429，恢复后正常 | 429 出现且可恢复 |

### L9 — 部署/CI 补强

| 项 | 内容 |
|----|------|
| Docker CI | ci.yml 加 job：`docker build -f deploy/nginx.Dockerfile` 构建成功（缓存友好） |
| 前端 lint | 加 ESLint（基础规则）或保持现状写进文档（决策点） |
| 依赖安全 | CI 加 `npm audit --audit-level=high`（官方 registry）失败即红 |

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
| **P3 后端补缺** | CLI 测试 + SQLite 韧性 + OpenAPI 快照 + 错误契约 | ~25 | 🟡 中 |
| **P4 真实 API 套件** | real 标记套件（DeepSeek/Seedream）+ CI manual job | ~10 | 🟡 中（需 Key） |
| **P5 安全+性能** | 上传模糊/头伪造/表达式注入 + slow 负载套件 | ~30 | 🟢 中低 |
| **P6 CI 补强** | Docker 构建 job + npm audit 门禁 | 0 | 🟢 低 |

## 6. 质量门槛（实施后生效）

| 门槛 | 值 |
|------|-----|
| 全量测试 | 必须 100% 绿（含新套件） |
| 前端 | `npm test` 全绿 + `npm run build` 零告警 |
| E2E | CI 每次 push 跑 Mock E2E 冒烟 |
| 覆盖率（后端） | 基线测量后设定：core/chat 目标 ≥85%，workflow ≥80%，api ≥75%（首期先记录基线，不设硬门槛） |
| 依赖安全 | `npm audit --audit-level=high` 0 漏洞 |

## 7. 风险与边界

1. **真实 API 成本**：real 套件每次跑产生真实费用 → 仅在用户批准时手动触发；用例用最小输入（1 张图/1 次 chat）
2. **时序脆弱**：E2E 断言等终态（completed/failed），不测瞬时 running；超时预算宽裕（60s+）
3. **前端测试学习曲线**：jsdom 无法覆盖 WS/布局 → WS 用 mock hook，布局交给 E2E/手动
4. **Mock 的盲区**：Mock Provider 返回模板，无法验证真实协议细节 → 由 L6 真实套件兜底
5. **OpenAPI 快照的维护成本**：端点变更需同步快照，作为"契约变更显式化"手段（利大于弊）

---

## 附：本计划与现有资产的关系

- 不重写任何现有测试；P1-P6 全部为**新增**（补盲区）
- 现有 468 用例继续作为回归基线
- 实施后测试资产预期：~530+ 后端用例 + ~45 前端用例 + 7 场景 E2E + 10 real 用例
