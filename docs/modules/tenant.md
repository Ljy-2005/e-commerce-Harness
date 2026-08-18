# Tenant — 多租户隔离

> 覆盖: `src/core/tenant.py`

## 功能

租户上下文 + 配额管理，实现多租户资源隔离。

- **ContextVar 协程安全** — 租户上下文随请求生命周期流转，不跨协程泄漏
- **配额体系** — rpm / tpm / 月度预算 / 并发会话数 / 存储上限
- **默认租户兜底** — 未知租户 ID 回退到 `default` 租户（不拒绝请求）
- **环境变量扩展** — `ECOMM_TENANTS` 逗号分隔声明额外租户

## 关键类

### TenantContext

```
tenant_id / name / tier (free|pro|enterprise) / quota
storage_prefix  # property，用于隔离 checkpoint/audit/memory
```

### TenantQuota

```
rpm=60, tpm=100_000, budget_usd=10.0, max_sessions=50, storage_mb=500
```

### TenantRegistry

```
get(tenant_id) → TenantContext     # 未知租户返回 default
list_ids() / all_quotas()
```

单例：`get_tenant_registry()`。

### 上下文函数

```
get_current_tenant() / set_current_tenant(ctx)
```

## 配置

| 环境变量 | 格式 | 示例 |
|----------|------|------|
| `ECOMM_TENANTS` | `tenant:层级[:rpm]` 逗号分隔 | `tenant1:pro:120,tenant2:free` |

层级配额表（代码内置）：

| 层级 | rpm | tpm | 预算 | 会话数 |
|------|-----|-----|------|--------|
| free | 30 | 50K | $5 | 10 |
| pro | 120 | 200K | $50 | 100 |
| enterprise | 600 | 1M | $500 | 500 |

## 集成点

- `POST /api/sessions`：校验租户会话数 + 预算（超限 429）
- 会话/工作流 job/batch：`tenant_id` 字段隔离查询
- 限流器：`set_tenant_limit()` 按租户注入独立配额

## 修改指南

- **新增层级** → `tenant.py` 的 `tier_quotas` 表
- **接入真实租户系统** → 替换 `TenantRegistry._load_from_env`（生产建议 DB/配置中心）
- **新增配额维度** → 扩展 `TenantQuota` 并在对应消费点（session 创建/限流/存储）加检查
