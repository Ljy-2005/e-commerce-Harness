"""配置加载器 — YAML + 环境变量（ECOMM_ 前缀）"""

import os
from pathlib import Path
from typing import Any

import yaml


def _project_root() -> Path:
    """返回项目根目录（pyproject.toml 所在）"""
    return Path(__file__).parent.parent.parent


def load_yaml(rel_path: str) -> dict[str, Any]:
    """加载 YAML 文件，相对于项目根目录"""
    path = _project_root() / rel_path
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_default_config() -> dict[str, Any]:
    return load_yaml("config/default.yaml")


def load_models_config() -> dict[str, Any]:
    return load_yaml("config/models.yaml")


def load_agent_config(agent_name: str) -> dict[str, Any]:
    """加载单个 Agent 的配置文件"""
    return load_yaml(f"config/agents/{agent_name}.yaml")


def list_agent_configs() -> list[str]:
    """扫描 config/agents/ 目录，返回所有 Agent 名称列表"""
    agents_dir = _project_root() / "config" / "agents"
    if not agents_dir.exists():
        return []
    return sorted([
        f.stem for f in agents_dir.glob("*.yaml")
        if not f.stem.startswith("_")
    ])


def get_env(key: str, default: str = "") -> str:
    """获取环境变量（ECOMM_ 前缀），自动回退到原始键名"""
    val = os.getenv(f"ECOMM_{key}")
    if val is not None:
        return val
    return os.getenv(key, default)


# ── CORS 白名单 ──

DEFAULT_CORS_ORIGINS = ["http://localhost:5173", "http://localhost:3000"]


def get_cors_origins() -> list[str]:
    """CORS 允许来源列表。

    优先级：ECOMM_CORS_ORIGINS（逗号分隔）→ config/default.yaml 的
    app.cors_origins → 内置默认值（本地开发前端端口）。
    """
    raw = get_env("CORS_ORIGINS")
    if raw:
        origins = [o.strip() for o in raw.split(",") if o.strip()]
        if origins:
            return origins
    cfg = load_default_config().get("app", {}) or {}
    origins = cfg.get("cors_origins")
    if isinstance(origins, list):
        cleaned = [str(o).strip() for o in origins if str(o).strip()]
        if cleaned:
            return cleaned
    return list(DEFAULT_CORS_ORIGINS)


# ── 运行时密钥持久化（Web 设置页写入的 API Key） ──

RUNTIME_SECRETS_REL = "config/secrets.yaml"


def runtime_secrets_path() -> Path:
    return _project_root() / RUNTIME_SECRETS_REL


def load_runtime_secrets() -> dict[str, str]:
    """读取 Web 设置页持久化的密钥文件"""
    data = load_yaml(RUNTIME_SECRETS_REL)
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items() if v}


def save_runtime_secrets(secrets: dict[str, str]) -> None:
    """把密钥写入 config/secrets.yaml（空值表示删除该键）"""
    path = runtime_secrets_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = load_runtime_secrets()
    merged = dict(existing)
    for k, v in secrets.items():
        if v:
            merged[k] = v
        else:
            merged.pop(k, None)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(merged, f, allow_unicode=True, sort_keys=False)
    # 审计修复：密钥文件收紧权限（POSIX 下 0644 → 0600，防本机其他用户读取）
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass  # Windows 无 POSIX 权限语义，忽略


def apply_runtime_secrets_to_env() -> int:
    """把持久化密钥注入 os.environ（环境变量显式设置时优先），返回注入数量。

    必须在 Provider 注册表构建前调用（本模块 import 时自动执行一次）。
    """
    applied = 0
    for k, v in load_runtime_secrets().items():
        if os.environ.get(k) is None:
            os.environ[k] = v
            applied += 1
    return applied


# ── 租户独立 API Key 持久化（C2 方案①：每租户一把钥匙） ──

TENANT_KEYS_REL = "config/tenant_keys.yaml"


def tenant_keys_path() -> Path:
    """租户 Key 文件路径（tenant_id → key，与 secrets.yaml 同目录，已 gitignore）"""
    return _project_root() / TENANT_KEYS_REL


def load_tenant_keys_file() -> dict[str, str]:
    """读取持久化的租户 Key（缺失文件返回空字典）"""
    data = load_yaml(TENANT_KEYS_REL)
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items() if v}


def save_tenant_keys_file(keys: dict[str, str]) -> None:
    """写入租户 Key 文件（空值表示删除该租户的 Key），POSIX 下 0600"""
    path = tenant_keys_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    merged = load_tenant_keys_file()
    for k, v in keys.items():
        if v:
            merged[k] = v
        else:
            merged.pop(k, None)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(merged, f, allow_unicode=True, sort_keys=False)
    # 审计修复：密钥文件收紧权限（POSIX 0644 → 0600，防本机其他用户读取）
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass  # Windows 无 POSIX 权限语义，忽略


# 模块导入时自动应用（Provider 检测依赖 os.environ）
apply_runtime_secrets_to_env()


def is_mock_mode() -> bool:
    """检测是否应启用 Mock 模式"""
    explicit = get_env("MOCK_MODE")
    if explicit.lower() in ("true", "1", "yes"):
        return True
    if explicit.lower() in ("false", "0", "no"):
        return False
    # 自动检测：无任何 API Key 时自动 Mock
    key_vars = [
        "OPENAI_API_KEY", "DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY",
        "SEEDREAM_API_KEY", "VOLCANO_ACCESS_KEY", "DASHSCOPE_API_KEY",
        "BFL_API_KEY", "FAL_KEY", "REPLICATE_API_KEY",
    ]
    return not any(os.getenv(k) for k in key_vars)
