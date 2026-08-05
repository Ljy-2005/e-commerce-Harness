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
