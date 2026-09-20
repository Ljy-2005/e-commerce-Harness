"""API 鉴权 — 全局管理 Key + 每租户独立 Key 验证（API 层，PRD D4）

依赖 FastAPI/Starlette，故位于 src/api/ 而非 core/（保持 core 不依赖上层的方向）。

两类凭据（C2 方案①「每租户独立 Key」，2026-08-22）：
- 全局管理 Key（ECOMM_API_KEY）：管理员身份，租户由 X-Tenant-ID 声明（沿用原语义）；
- 租户 Key（config/tenant_keys.yaml 或 ECOMM_TENANT_KEYS 环境变量）：身份即租户，
  中间件把 X-Tenant-ID 重写为该 Key 绑定的租户——密钥持有者无法伪造租户头。

执行规则：
- 两者都未配置 → 开发模式，全部放行（管理面由 _require_admin_access 仅限本机）；
- 任一配置 → 强制鉴权：无 Key / 错 Key → 401（限流同前）。
- /health 和 /docs 路径始终放行。
"""

import hmac
import os
import time
from collections import defaultdict
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse


# 白名单路径（无需鉴权；精确前缀匹配，防 /healthX 类假想路径放行）
_PUBLIC_PREFIXES = ("/health", "/docs", "/redoc", "/openapi.json")

# 简易 IP 限流：每分钟最多 N 次失败尝试
_AUTH_FAILURES: dict[str, list[float]] = defaultdict(list)
_AUTH_MAX_FAILURES_PER_MINUTE = 10
_AUTH_MAX_IP_ENTRIES = 10_000     # IP 字典膨胀阈值（超过则清扫过期条目）

# ── 租户 Key 注册表（缓存；经 reload_tenant_keys() 刷新） ──

_tenant_keys_cache: dict[str, str] | None = None


def _parse_tenant_keys_env() -> dict[str, str]:
    """解析 ECOMM_TENANT_KEYS 环境变量（格式: tenant1:key1,tenant2:key2）"""
    raw = os.getenv("ECOMM_TENANT_KEYS", "")
    out: dict[str, str] = {}
    if not raw:
        return out
    for item in raw.split(","):
        item = item.strip()
        if not item or ":" not in item:
            continue
        tid, key = item.split(":", 1)
        tid, key = tid.strip(), key.strip()
        if tid and key:
            out[tid] = key
    return out


def env_tenant_keys() -> dict[str, str]:
    """仅来自环境变量引导的租户 Key（ECOMM_TENANT_KEYS，部署层注入，优先级最高）

    设置页不可管理 env 供给的 Key（见 POST /api/settings/tenant-keys 的 403），
    避免"UI 改了但 env 覆盖不生效"的困惑。
    """
    return _parse_tenant_keys_env()


def reload_tenant_keys() -> dict[str, str]:
    """重新加载租户 Key（文件 + 环境变量合并，环境变量优先；环境变量用于部署引导）"""
    global _tenant_keys_cache
    from src.core.config import load_tenant_keys_file
    merged = load_tenant_keys_file()
    merged.update(env_tenant_keys())
    _tenant_keys_cache = {k: v for k, v in merged.items() if v}
    return _tenant_keys_cache


def get_tenant_keys() -> dict[str, str]:
    """当前生效的租户 Key 映射（tenant_id → key）"""
    if _tenant_keys_cache is None:
        reload_tenant_keys()
    return _tenant_keys_cache


def auth_enabled() -> bool:
    """是否启用鉴权：全局 Key 或任一租户 Key 配置即启用"""
    return bool(os.getenv("ECOMM_API_KEY") or get_tenant_keys())


def authenticate_api_key(api_key: str) -> tuple[str, str]:
    """验证 Key，返回 (role, tenant_id)。

    role:
    - "none"   无效/缺失（含未配置任何 Key 时——由调用方决定是否放行开发模式）
    - "admin"  全局管理 Key（租户仍由 X-Tenant-ID 声明）
    - "tenant" 租户 Key（tenant_id 为该 Key 绑定的租户）
    """
    if not api_key:
        return ("none", "")
    expected = os.getenv("ECOMM_API_KEY", "")
    if expected and _safe_compare(api_key, expected):  # 审计修复：时序安全比较
        return ("admin", "")
    for tid, key in get_tenant_keys().items():
        if _safe_compare(api_key, key):
            return ("tenant", tid)
    return ("none", "")


def _safe_compare(candidate: str, expected: str) -> bool:
    """hmac.compare_digest 的 ASCII 安全包装。

    第二/三轮审计修复：`hmac.compare_digest` 对含非 ASCII 的 str 直接抛 TypeError
    （HTTP 头只能承载 ASCII，这类 Key 永远无法认证）——此前一旦 `ECOMM_TENANT_KEYS`
    或 tenant_keys.yaml 里写入非 ASCII Key，**所有**未匹配该条目的请求都会 500。
    非 ASCII 的候选/期望值一律判为不匹配。
    """
    try:
        return hmac.compare_digest(candidate, expected)
    except TypeError:
        return False


def _set_scope_header(scope: dict, name: str, value: str) -> None:
    """重写 ASGI scope 中的请求头（下游 FastAPI 按 scope 重建 Request 时生效）"""
    lname = name.lower().encode("latin-1")
    headers = [(h0, h1) for h0, h1 in scope["headers"] if h0 != lname]
    headers.append((lname, value.encode("latin-1")))
    scope["headers"] = headers


class AuthMiddleware(BaseHTTPMiddleware):
    """API Key 验证中间件（全局 Key + 租户 Key，带暴力破解防护）

    支持两种传 Key 方式：
    - Header: X-API-Key: <key>
    - Header: Authorization: Bearer <key>

    通过 request.state.auth_role / auth_tenant_id 向下游传递身份：
    - "admin"：全局 Key（管理面端点据此放行）
    - "tenant"：租户 Key，X-Tenant-ID 已被重写为绑定租户（密钥即身份）
    """

    async def dispatch(self, request: Request, call_next):
        # 公开路径放行（精确路径或子路径，审计修复：startswith 会放行 /healthX 等假想路径）
        path = request.url.path
        if any(path == p or path.startswith(p + "/") for p in _PUBLIC_PREFIXES):
            return await call_next(request)

        # 未配置任何 Key → 开发模式，放行（管理面由 _require_admin_access 仅限本机）
        if not auth_enabled():
            return await call_next(request)

        # 提取 Key
        api_key = request.headers.get("X-API-Key", "")
        if not api_key:
            auth_header = request.headers.get("Authorization", "")
            if auth_header.strip().lower().startswith("bearer "):
                api_key = auth_header[7:].strip()

        role, tenant_id = authenticate_api_key(api_key)
        if role == "none":
            return self._fail(request, "Missing API Key" if not api_key else "Invalid API Key")

        request.state.auth_role = role
        request.state.auth_tenant_id = tenant_id
        if role == "tenant":
            # C2 方案①：密钥即身份，X-Tenant-ID 声明被覆盖（防租户伪造）
            _set_scope_header(request.scope, "X-Tenant-ID", tenant_id)

        return await call_next(request)

    def _fail(self, request: Request, message: str) -> JSONResponse:
        """记录失败并检查限流"""
        ip = request.client.host if request.client else "unknown"
        now = time.monotonic()

        # 清理旧记录（审计修复：防 IP 字典无限增长——过期条目即移除，
        # 并在字典膨胀时做一次全局清扫）
        cutoff = now - 60
        remaining = [t for t in _AUTH_FAILURES[ip] if t > cutoff]
        remaining.append(now)
        _AUTH_FAILURES[ip] = remaining
        if len(_AUTH_FAILURES) > _AUTH_MAX_IP_ENTRIES:
            stale = [k for k, v in _AUTH_FAILURES.items() if not any(t > cutoff for t in v)]
            for k in stale:
                del _AUTH_FAILURES[k]

        if len(_AUTH_FAILURES[ip]) > _AUTH_MAX_FAILURES_PER_MINUTE:
            return JSONResponse(
                status_code=429,
                # P3 补缺（test-plan L3）：全端点错误统一 {"detail": ...} 形态
                content={"detail": "Too many authentication attempts. Retry later."},
            )

        return JSONResponse(
            status_code=401,
            content={
                "detail": f"{message} — Set X-API-Key header or Authorization: Bearer <key>",
            },
        )
