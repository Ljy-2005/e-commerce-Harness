"""API 接入层（PRD D4）

FastAPI 服务与 HTTP 中间件。依赖方向：core ← providers ← agents ← chat ← api/cli。
- `main.py` — FastAPI 入口（REST + WebSocket 端点）
- `auth.py` — API Key 鉴权中间件（依赖 FastAPI/Starlette，故不放在 core/）
"""
