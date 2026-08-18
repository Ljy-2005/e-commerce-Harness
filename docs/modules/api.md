# API — FastAPI 接入层

> 覆盖: `src/api/main.py`, `src/api/auth.py`
> 工作流端点细节: `docs/workflow-design.md` §7
> 兼容入口: `src/main.py` 为 `sys.modules` 别名 shim（PRD D4 分层迁移后保留，旧 `uvicorn src.main:app` 命令与 `import src.main` 依旧可用）

## 功能

FastAPI 服务，提供前后端分离架构的后端接口。共 4 组 REST + 2 条 WebSocket：

- **会话组** — 创建/详情/消息/列表/删除/人工决策/群聊插话/A-B 测试
- **设置组** — API Key 持久化 / Agent 参数 / 能力→模型映射（均落盘 + 热重载）
- **工作流组** — 模板画廊/实例化/作业列表/详情/控制/人工决策/一键复刻/批量/导入导出
- **连接器组** — 入站 Webhook 回调（`X-Webhook-Token` 鉴权）
- **WebSocket** — `/ws/sessions/{id}`（群聊直播 + 双向插话）、`/ws/workflows/jobs/{id}`（工作流事件流）
- **CORS** — 白名单来自 `config/default.yaml`（`ECOMM_CORS_ORIGINS` 可覆盖，见 `core.md` 的 `get_cors_origins`）

## 端点概览

### 健康与状态

```
GET /health            → {status, mock_mode}（最小化，防信息泄露）
GET /api/admin/status  → 熔断/限流/Agent/Provider/租户总览（需鉴权）
GET /api/agents        → Agent 及可配置参数（前端配置面板数据源）
```

### 会话组

```
POST   /api/sessions                          # 上传商品图 → 异步启动群聊（租户配额检查 + 图片验证 + 预处理）
GET    /api/sessions                          # 会话列表（租户隔离，按创建时间倒序）
GET    /api/sessions/{id}                     # 完整状态（消息 + 产出物 + 成本）
GET    /api/sessions/{id}/messages?since=N    # 增量拉取消息
POST   /api/sessions/{id}/decision            # 人工审查 approve/retry/reject → resume_after_hitl
POST   /api/sessions/{id}/interject           # M3 群聊插话（Coordinator 下一轮感知）
POST   /api/sessions/{id}/ab-test             # 同会话 A/B 变体对比
DELETE /api/sessions/{id}                     # 删除会话及产出物
```

### 设置组（写操作落盘 + 热重载）

```
GET  /api/settings                     # 设置汇总（密钥脱敏 + 生效模型）
POST /api/settings/api-keys            # 保存 API Key → config/secrets.yaml → 重建 Provider 注册表
POST /api/settings/agents/{name}       # 更新 Agent 参数 → config/agents/*.yaml → 重载注册表
POST /api/settings/models              # 部分更新能力→模型映射 → config/models.yaml → 重建注册表
```

### 工作流 / 批量 / 记忆 / 审计

```
GET    /api/workflows/templates                     # Skill 库列表
GET    /api/workflows/templates/{name}/export       # M4 导出原始 YAML
POST   /api/workflows/templates/import              # M4 导入（三级校验 + 防覆盖）
POST   /api/workflows/templates/{name}/instantiate  # multipart 多图片输入分发
GET    /api/workflows/jobs                          # 作业列表
GET    /api/workflows/jobs/{id}                     # 详情（步骤 + 事件流）
POST   /api/workflows/jobs/{id}/control             # 手动挡 run_next/pause/resume/retry_step/skip_step/cancel
POST   /api/workflows/jobs/{id}/decision            # 人工审批 approve/retry/reject
POST   /api/workflows/jobs/{id}/replicate           # M3 一键风格复刻
POST   /api/workflows/batches                       # M2 批量（JSON/CSV）
GET    /api/workflows/batches / batches/{id}        # 批量列表/详情
GET    /api/workflows/batches/report                # M5 批量报表（总览/模板成功率/耗时分布/失败原因）
POST   /api/workflows/batches/{id}/control          # pause/resume/cancel/retry_failed
POST   /api/webhooks/workflows/{id}/decision        # M4 入站回调（X-Webhook-Token；未配置 503）

GET    /api/memory/stats / api/memory/recall        # Agent 记忆库
GET    /api/audit                                  # 审计日志查询
```

### WebSocket

```
WS /ws/sessions/{id}        # ?api_key= 鉴权；历史回放 + 实时流 + 用户插话 {type:"chat", content}
WS /ws/workflows/jobs/{id}  # ?api_key= 鉴权；历史事件回放 + 实时事件流 + ping/pong
```

## 鉴权与租户

- **API Key**：`ECOMM_API_KEY` 配置后 AuthMiddleware 全局保护（见 `auth.md`）；WS 用 `?api_key=` 查询参数；**前端自动携带**：`api.js` 统一注入 `X-API-Key` 头 + `wsUrl` 追加 `?api_key=`（Key 在设置页「前端 API Key」处存入 localStorage）
- **租户**：`X-Tenant-ID` 头（默认 `default`）；**未知租户 403**（不再回退 default）；会话/作业/批次/WS（`?tenant=` 参数）均按租户隔离
- **管理面**：`/api/settings/*` 与 `/api/admin/*` 在未配置 `ECOMM_API_KEY` 时仅允许本机访问（`_require_admin_access`）
- **上传防护**：分块读取（超 20MB 即 413）→ `ImageValidator`（格式/大小/base64）→ `ImagePreprocessor`（解压炸弹防护/缩放/压缩），失败即 400

## 全局组件

```
ProviderRegistry    # 环境变量检测 → 可用 Provider + 能力解析
AgentRegistry       # 扫描 config/agents/ → 注册 9 Agent + resolve 模型
SessionManager      # 内存会话存储 + checkpoint 持久化
Broadcaster         # WebSocket 连接管理（会话 + 工作流共用）
JobStore            # 工作流 SQLite（事件溯源）
WorkflowEngine      # 工作流状态机
BatchScheduler      # 批量调度器
```

## 修改指南

- **新增端点** → 在 `src/api/main.py` 添加路由函数（自动被 AuthMiddleware 保护）
- **上传限制调优** → `main.py` 顶部常量（`MAX_UPLOAD_IMAGES` / `PREPROCESS_MAX_PIXELS` 等）
- **调整 CORS** → `config/default.yaml` 的 `app.cors_origins` 或环境变量 `ECOMM_CORS_ORIGINS`
- **WebSocket 消息格式** → `chat/broadcaster.py` + `main.py` 的 ws 端点保持同步
