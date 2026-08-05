"""API 鉴权 — API Key / Bearer Token 验证

通过 ECOMM_API_KEY 环境变量配置。
设置后，所有 /api/* 请求必须携带有效 Key。
/health 和 /docs 路径始终放行。
"""

import os
from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse


# 白名单路径（无需鉴权）
_PUBLIC_PREFIXES = ("/health", "/docs", "/redoc", "/openapi.json", "/ws/")


class AuthMiddleware(BaseHTTPMiddleware):
    """API Key 验证中间件

    支持两种传 Key 方式：
    - Header: X-API-Key: <key>
    - Header: Authorization: Bearer <key>
    """

    async def dispatch(self, request: Request, call_next):
        # 公开路径放行
        path = request.url.path
        if any(path.startswith(p) for p in _PUBLIC_PREFIXES):
            return await call_next(request)

        # 检查是否配置了 API Key
        expected_key = os.getenv("ECOMM_API_KEY", "")
        if not expected_key:
            # 未配置 Key → 开发模式，放行
            return await call_next(request)

        # 提取 Key
        api_key = request.headers.get("X-API-Key", "")
        if not api_key:
            auth_header = request.headers.get("Authorization", "")
            if auth_header.strip().lower().startswith("bearer "):
                api_key = auth_header[7:].strip()

        if not api_key:
            return JSONResponse(
                status_code=401,
                content={"error": "Missing API Key", "hint": "Set X-API-Key header or Authorization: Bearer <key>"},
            )

        if api_key != expected_key:
            return JSONResponse(
                status_code=401,
                content={"error": "Invalid API Key"},
            )

        return await call_next(request)


def require_api_key(request: Request):
    """依赖注入：在特定端点强制要求鉴权（即使全局未配置 Key）"""
    expected_key = os.getenv("ECOMM_API_KEY", "")
    if not expected_key:
        return  # 未配置 → 放行

    api_key = request.headers.get("X-API-Key", "")
    if not api_key:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            api_key = auth_header[7:]

    if not api_key:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.strip().lower().startswith("bearer "):
            api_key = auth_header[7:].strip()

    if not api_key or api_key != expected_key:
        raise HTTPException(401, "Missing or invalid API Key")
