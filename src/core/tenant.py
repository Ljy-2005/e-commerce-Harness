"""多租户隔离 — TenantContext + 租户级资源隔离"""

import os
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Optional

# ── 当前请求的租户上下文（ContextVar，协程安全）──
_current_tenant: ContextVar[Optional["TenantContext"]] = ContextVar("tenant_ctx", default=None)


def get_current_tenant() -> Optional["TenantContext"]:
    """获取当前协程的租户上下文（由中间件设置）"""
    return _current_tenant.get()


def set_current_tenant(ctx: Optional["TenantContext"]):
    _current_tenant.set(ctx)


@dataclass
class TenantQuota:
    """租户配额"""
    rpm: int = 60              # 每分钟请求数
    tpm: int = 100_000         # 每分钟 token 数
    budget_usd: float = 10.0   # 月度预算
    max_sessions: int = 50     # 最大并发会话数
    storage_mb: int = 500      # 存储上限


@dataclass
class TenantContext:
    """租户上下文 — 贯穿整个请求生命周期"""

    tenant_id: str
    name: str = ""
    tier: str = "free"         # free / pro / enterprise
    quota: TenantQuota = field(default_factory=TenantQuota)

    @property
    def storage_prefix(self) -> str:
        """租户的存储前缀，用于隔离 checkpoint/audit/memory"""
        return self.tenant_id


class TenantRegistry:
    """租户注册中心

    管理所有租户的配额和配置。
    生产环境应接入数据库或配置中心。
    """

    def __init__(self):
        self._tenants: dict[str, TenantContext] = {}

        # 默认租户（开发/演示用）
        default_tenant = TenantContext(
            tenant_id="default",
            name="Default Tenant",
            tier="pro",
            quota=TenantQuota(rpm=120, tpm=200_000, budget_usd=50.0, max_sessions=100),
        )
        self._tenants["default"] = default_tenant

        # 从环境变量加载额外租户
        self._load_from_env()

    def _load_from_env(self):
        """从 ECOMM_TENANTS 环境变量加载租户配置

        格式: ECOMM_TENANTS=tenant1:pro:120,tenant2:free:30
        """
        raw = os.getenv("ECOMM_TENANTS", "")
        if not raw:
            return

        tier_quotas = {
            "free": TenantQuota(rpm=30, tpm=50_000, budget_usd=5.0, max_sessions=10),
            "pro": TenantQuota(rpm=120, tpm=200_000, budget_usd=50.0, max_sessions=100),
            "enterprise": TenantQuota(rpm=600, tpm=1_000_000, budget_usd=500.0, max_sessions=500),
        }

        for item in raw.split(","):
            parts = item.strip().split(":")
            if len(parts) >= 2:
                tid = parts[0]
                tier = parts[1] if len(parts) >= 2 else "free"
                quota = tier_quotas.get(tier, TenantQuota())
                self._tenants[tid] = TenantContext(
                    tenant_id=tid,
                    name=tid,
                    tier=tier,
                    quota=quota,
                )

    def get(self, tenant_id: str) -> Optional["TenantContext"]:
        """获取租户上下文。未知租户返回 None（调用方应拒绝，勿静默回退 default）。

        审计修复：此前未知租户静默回退 default 租户，配合自声明的 X-Tenant-ID
        使多租户隔离失效（拼错/伪造租户头即共享 default 的数据与配额）。
        """
        return self._tenants.get(tenant_id)

    def list_ids(self) -> list[str]:
        return list(self._tenants.keys())

    def all_quotas(self) -> dict[str, TenantQuota]:
        return {tid: t.quota for tid, t in self._tenants.items()}


# ── 全局单例 ──

_tenant_registry: Optional[TenantRegistry] = None


def get_tenant_registry() -> TenantRegistry:
    global _tenant_registry
    if _tenant_registry is None:
        _tenant_registry = TenantRegistry()
    return _tenant_registry
