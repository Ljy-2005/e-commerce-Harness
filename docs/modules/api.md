# API — FastAPI 接入层

> 覆盖: `src/main.py`

## 功能

FastAPI 服务，提供前后端分离架构的后端接口。

- **REST API** — 6 个端点（创建会话/查询状态/拉取消息/删除/列出 Agent/健康检查）
- **WebSocket** — 实时群聊消息流推送
- **CORS** — 白名单配置，支持跨域前端调用
- **启动加载** — 扫描 `config/agents/` 自动注册所有 Agent

## API 端点

### `GET /health`
健康检查。返回所有已注册 Agent 和可用 Provider。

```
→ {status, mock_mode, agents: [{name, requires}], providers: [{name, capabilities}]}
```

### `GET /api/agents`
列出所有 Agent 及其可配置参数。**前端据此渲染配置面板。**

```
→ {agents: [{name, description, version, requires, params: [{key, label, type, options, default}], timeout_ms}]}
```

### `POST /api/sessions`
创建会话，上传商品图片，异步启动 ChatEngine。

```
Form: product_info, platform (default: taobao), category_hint, files[] (1-10 张)
→ {session_id, status: "created", message}
```

### `GET /api/sessions/{session_id}`
获取会话完整状态（状态 + 全部消息 + 产出物）。

```
→ {session_id, status, task, messages: [], artifacts: {analysis, prompts, images, review, compliance}, turn_count, cost_so_far, created_at, updated_at}
```

### `GET /api/sessions/{session_id}/messages?since=N`
增量拉取消息。`since` 为消息序号（turn 级别）。

```
→ {messages: [msg, ...], total: N}
```

### `DELETE /api/sessions/{session_id}`
删除会话及产出物。

```
→ {status: "deleted", session_id}
```

### `WS /ws/sessions/{session_id}`
WebSocket 实时群聊流。连接后：

1. 先发送全部历史消息
2. 保持连接，有新消息时自动推送
3. 支持 `ping/pong` 心跳

消息格式：
```json
{"id": "...", "turn": 1, "role": "coordinator", "sender": "中心决策者", "action": "invite", "content": {...}}
```

## 全局组件

启动时初始化：

```
ProviderRegistry          # 检测环境变量 → 标记可用 Provider
AgentRegistry             # 扫描 config/agents/ → 注册 Agent + resolve Provider
SessionManager            # 内存会话存储
Broadcaster               # WebSocket 连接管理
```

## CORS 配置

通过环境变量 `ECOMM_CORS_ORIGINS` 配置白名单：

```
ECOMM_CORS_ORIGINS=http://localhost:5173,http://localhost:3000
```

默认允许 `localhost:5173` (Vite) 和 `localhost:3000` (Next.js)。

## 修改指南

- **新增端点** → 在 `src/main.py` 添加路由函数
- **修改 WebSocket 消息格式** → 统一修改 `chat/broadcaster.py` 和 `main.py` 的 ws 端点
- **调整 CORS** → 设置环境变量 `ECOMM_CORS_ORIGINS`
- **替换存储后端** → 修改 `main.py` 中 `SessionManager` 实例化方式
