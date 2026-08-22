# Auth — API 鉴权

> 覆盖: `src/api/auth.py`（原 `src/core/auth.py`，2026-08-16 按 PRD D4 迁移至 API 层，修复"core 依赖 FastAPI"的依赖方向问题）

## 功能

API Key 鉴权中间件 + 暴力破解 IP 限流，支持两类凭据（C2 方案①「每租户独立 Key」，2026-08-22）：

- **开箱即用**：全局 Key 与租户 Key 都未配置时，全部请求放行（开发模式）
- **配置后全局保护**：任一 Key 配置即启用强制鉴权，所有非公开路径必须携带有效 Key
- **两种传 Key 方式**：`X-API-Key: <key>` 或 `Authorization: Bearer <key>`
- **公开路径**：`/health`、`/docs`、`/redoc`、`/openapi.json` 始终放行
- **WebSocket**：`/ws/*` 端点用查询参数 `?api_key=` 鉴权（在端点内检查，中间件不拦 WS 升级）

## 两类凭据

| 凭据 | 身份 | 租户判定 | 管理端点 |
|------|------|----------|----------|
| 全局 Key（`ECOMM_API_KEY`） | `admin` | 由 `X-Tenant-ID` 声明（原语义） | ✅ 放行 |
| 租户 Key（`config/tenant_keys.yaml` / `ECOMM_TENANT_KEYS`） | `tenant` | **密钥即身份**：`X-Tenant-ID` 声明被忽略，中间件重写为该 Key 绑定的租户（防冒充） | ❌ 403 |

## 关键类

### AuthMiddleware (`starlette BaseHTTPMiddleware`)

```
dispatch(request, call_next)
  1. 公开路径前缀 → 放行
  2. 全局 Key 与租户 Key 均未配置 → 放行（开发模式）
  3. 提取 Key（X-API-Key / Bearer）→ authenticate_api_key() 无匹配 → 401
  4. 通过 → request.state.auth_role = "admin" | "tenant"
     tenant 身份时重写 scope 头 X-Tenant-ID = 绑定租户（密钥即身份，防伪造）
  5. 同一 IP 每分钟失败 > 10 次 → 429（防暴力破解）
```

失败响应：

```
401: {"error": "Invalid API Key", "hint": "Set X-API-Key header or Authorization: Bearer <key>"}
429: {"error": "Too many authentication attempts. Retry later."}
```

IP 失败字典带膨胀清扫（`_AUTH_MAX_IP_ENTRIES=10000`，审计修复）。

### authenticate_api_key(api_key) → (role, tenant_id)

- 先与全局 `ECOMM_API_KEY` 比较（`hmac.compare_digest` 时序安全）→ `("admin", "")`
- 再遍历租户 Key 表 → `("tenant", tenant_id)`
- 无匹配 → `("none", "")`

### 租户 Key 注册表（`get_tenant_keys` / `reload_tenant_keys`）

- 文件 `config/tenant_keys.yaml`（设置页写入，0600 权限、已 gitignore）+ 环境变量
  `ECOMM_TENANT_KEYS`（格式 `tenant1:key1,tenant2:key2`，部署引导用）合并，环境变量优先
- 模块级缓存，管理端点保存后调用 `reload_tenant_keys()` 热生效

## 管理面

管理端点（`/api/settings/*` 与 `/api/admin/*`）由 `_require_admin_access`（见 `src/api/main.py`）保护：

- `auth_role == "admin"`（全局 Key）→ 放行
- `auth_role == "tenant"`（租户 Key）→ 403（租户不得改系统配置/分发租户 Key）
- 未配置任何 Key（开发模式）→ 仅本机（127.0.0.1/::1/localhost/testclient）

租户 Key 的管理端点（仅 admin）：

- `GET /api/settings/tenant-keys` — 状态列表（仅 configured 布尔，不返回密钥内容）
- `POST /api/settings/tenant-keys` — 创建/轮换/删除（body: `{"tenant_id": "...", "api_key": "..."}`，空值删除；租户须已在 `ECOMM_TENANTS` 注册，Key 长度 16-256）

## 配置

| 环境变量 | 说明 |
|----------|------|
| `ECOMM_API_KEY` | 全局管理 Key，配置后启用全局鉴权（见 `.env.example`） |
| `ECOMM_TENANT_KEYS` | 租户 Key 引导（`tenant1:key1,tenant2:key2`，逗号分隔）；设置页管理的 Key 持久化在 `config/tenant_keys.yaml` |
| `ECOMM_WEBHOOK_TOKEN` | 工作流入站回调端点 `/api/webhooks/workflows/{id}/decision` 的独立 token（M4） |

## 修改指南

- **新增公开路径** → `auth.py` 顶部 `_PUBLIC_PREFIXES`
- **调整限流阈值** → `_AUTH_MAX_FAILURES_PER_MINUTE`
- **调整租户 Key 长度限制** → `main.py` 顶部 `TENANT_KEY_MIN_LEN` / `TENANT_KEY_MAX_LEN`
- **接入 OAuth/JWT** → 扩展 `authenticate_api_key()` / `dispatch()`（本模块位于 `src/api/`，与 FastAPI/Starlette 同层，依赖方向符合 PRD D4：core ← providers ← agents ← chat ← api/cli）
