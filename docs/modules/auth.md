# Auth — API 鉴权

> 覆盖: `src/api/auth.py`（原 `src/core/auth.py`，2026-08-16 按 PRD D4 迁移至 API 层，修复"core 依赖 FastAPI"的依赖方向问题）

## 功能

API Key 鉴权中间件 + 暴力破解 IP 限流。

- **开箱即用**：`ECOMM_API_KEY` 环境变量未配置时，全部请求放行（开发模式）
- **配置后全局保护**：所有非公开路径必须携带有效 Key
- **两种传 Key 方式**：`X-API-Key: <key>` 或 `Authorization: Bearer <key>`
- **公开路径**：`/health`、`/docs`、`/redoc`、`/openapi.json` 始终放行
- **WebSocket**：`/ws/*` 端点用查询参数 `?api_key=` 鉴权（在端点内检查，中间件不拦 WS 升级）

## 关键类

### AuthMiddleware (`starlette BaseHTTPMiddleware`)

```
dispatch(request, call_next)
  1. 公开路径前缀 → 放行
  2. ECOMM_API_KEY 未配置 → 放行（开发模式）
  3. 提取 Key（X-API-Key / Bearer）→ 不匹配 → 401
  4. 同一 IP 每分钟失败 > 10 次 → 429（防暴力破解）
```

失败响应：

```
401: {"error": "Invalid API Key", "hint": "Set X-API-Key header or Authorization: Bearer <key>"}
429: {"error": "Too many authentication attempts. Retry later."}
```

IP 失败字典带膨胀清扫（`_AUTH_MAX_IP_ENTRIES=10000`，审计修复）。

管理面（`/api/settings/*` 与 `/api/admin/*`）在未配置 Key 时由 `_require_admin_access`
限制为仅本机访问（见 `src/api/main.py`；`require_api_key` 死代码已删除）。

## 配置

| 环境变量 | 说明 |
|----------|------|
| `ECOMM_API_KEY` | 配置后启用全局鉴权（见 `.env.example`） |
| `ECOMM_WEBHOOK_TOKEN` | 工作流入站回调端点 `/api/webhooks/workflows/{id}/decision` 的独立 token（M4） |

## 修改指南

- **新增公开路径** → `auth.py` 顶部 `_PUBLIC_PREFIXES`
- **调整限流阈值** → `_AUTH_MAX_FAILURES_PER_MINUTE`
- **接入 OAuth/JWT** → 扩展 `dispatch()`（本模块位于 `src/api/`，与 FastAPI/Starlette 同层，依赖方向符合 PRD D4：core ← providers ← agents ← chat ← api/cli）
