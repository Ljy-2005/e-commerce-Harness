"""配置加载器 — YAML + 环境变量（ECOMM_ 前缀）"""

import os
import re
from pathlib import Path
from typing import Any

import yaml


def _project_root() -> Path:
    """返回项目根目录（pyproject.toml 所在，配置文件基准）。

    第三轮审计 B2-18：支持 `ECOMM_PROJECT_ROOT` 重定向——测试把它指向 tmp 副本，
    避免 API 测试写坏真实 `config/*.yaml`；部署时也可指向挂载卷。
    """
    override = os.getenv("ECOMM_PROJECT_ROOT", "").strip()
    return Path(override) if override else Path(__file__).parent.parent.parent


def data_root() -> Path:
    """运行期数据根目录（checkpoint / audit / memory / workflow.db）。

    第三轮审计 B2-18：支持 `ECOMM_DATA_DIR` 重定向——测试指向 tmp（此前每跑一轮
    测试就往真实 `data/` 里灌 checkpoint / 审计 / 记忆），部署时可指向容器挂载卷。

    一律**在使用时调用**（不要在模块级固化成常量），否则 import 期就锁定了真实路径。
    """
    override = os.getenv("ECOMM_DATA_DIR", "").strip()
    return Path(override) if override else _project_root() / "data"


def load_yaml(rel_path: str) -> dict[str, Any]:
    """加载 YAML 文件，相对于项目根目录"""
    path = _project_root() / rel_path
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_default_config() -> dict[str, Any]:
    return load_yaml("config/default.yaml")


# ── 会话策略（config/default.yaml 的 chat 段；设置页可改） ──
#
# 「审查/合规连续失败 N 次即停」（用户反馈）：真实会话里协调者会因为合规不过而**反复重新
# 生成图片**（每轮约 ¥1），而会话预算只告警不拦截、max_turns=15 上限又太远 → 需要一条
# 明确的止损线。0 = 关闭该保护。

CHAT_CONFIG_REL = "config/chat.yaml"        # 设置页写入的会话策略（实例级配置，已 gitignore）
CHAT_DEFAULTS_REL = "config/default.yaml"   # 项目默认值（**不被程序改写**，注释保留）
DEFAULT_MAX_CONSECUTIVE_FAILURES = 2
MAX_CONSECUTIVE_FAILURES_CEILING = 20
# 提示词阶段（A70-A78，用户 2026-09-18 指定）：
#   require_prompt_review        出图前是否跑"提示词审核优化员"（审美审核 + 改写）
#   prompt_aesthetic_threshold   审美分低于该阈值才改写画面描述（默认 85）
#   prompt_review_max_rounds     体检仍有硬伤时打回提示词生成员重写的轮数（默认 1）
#   require_prompt_confirm       仍不达标时是否暂停等人工确认（默认关，不制造摩擦）
# 风格档案库（A79-A96，用户 2026-09-18 指定「风格词库」）：
#   style_library_enabled        是否把「逐槽位风格档案」注入提示词生成员与提示词审核优化员
#   style_library_max            **整套最多用几套风格**（0 = 不注入；上限见 STYLE_LIBRARY_MAX_CEILING）
#                                1（默认）= 只用词库里启用的那**一套**风格词（一轮会话一套风格词）；
#                                ≥2 = 允许内置原型补"参考套图没覆盖的槽位"（显式叠加，风格会混）
# 用户 2026-09-20 原话："应该是一组生成图用一种风格，或者说是一轮会话里只用一个风格词"；
# 同日更正口径：单位是**一套**风格词（一条记录携带的一组字段），不是"一个词"。
DEFAULT_AESTHETIC_THRESHOLD = 85.0
PROMPT_REVIEW_ROUNDS_CEILING = 3
DEFAULT_STYLE_LIBRARY_MAX = 1
STYLE_LIBRARY_MAX_CEILING = 4
_CHAT_POLICY_KEYS = ("max_turns", "session_ttl_hours", "max_consecutive_review_failures",
                     "require_identity_confirm", "require_prompt_review",
                     "prompt_aesthetic_threshold", "prompt_review_max_rounds",
                     "require_prompt_confirm", "style_library_enabled",
                     "style_library_max")


def normalize_max_consecutive_failures(raw) -> int:
    """规范化「连续失败即停」阈值：非法/负数 → 默认值；0 表示关闭"""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_MAX_CONSECUTIVE_FAILURES
    if value < 0:
        return DEFAULT_MAX_CONSECUTIVE_FAILURES
    return min(value, MAX_CONSECUTIVE_FAILURES_CEILING)


def _normalize_chat_policy(chat: dict) -> dict[str, Any]:
    if not isinstance(chat, dict):
        chat = {}
    try:
        max_turns = int(chat.get("max_turns", 15) or 15)
    except (TypeError, ValueError):
        max_turns = 15
    try:
        ttl = float(chat.get("session_ttl_hours", 24) or 24)
    except (TypeError, ValueError):
        ttl = 24.0
    return {
        "max_turns": max(1, min(50, max_turns)),
        "session_ttl_hours": max(0.0, ttl),
        "max_consecutive_review_failures": normalize_max_consecutive_failures(
            chat.get("max_consecutive_review_failures", DEFAULT_MAX_CONSECUTIVE_FAILURES)),
        # 商品身份未确认时是否暂停等用户确认（用户反馈：分析员连品牌都没识别出来就往下跑）
        # 注意：`chat.yaml` 里没有这一项、`default.yaml` 也没写时，`merged` 里的值是 None
        # —— 必须按"未设置"处理，否则 bool(None) 会把开关**静默关掉**（实测踩到）
        "require_identity_confirm": _as_switch(chat.get("require_identity_confirm"), True),
        # 出图前的提示词把关（用户的初衷是审美：让提示词接近大众商品图审美）
        "require_prompt_review": _as_switch(chat.get("require_prompt_review"), True),
        "prompt_aesthetic_threshold": _aesthetic_threshold(chat.get("prompt_aesthetic_threshold")),
        "prompt_review_max_rounds": _bounded_int(
            chat.get("prompt_review_max_rounds"), 1, 0, PROMPT_REVIEW_ROUNDS_CEILING),
        "require_prompt_confirm": _as_switch(chat.get("require_prompt_confirm"), False),
        # 风格档案库（逐槽位设计档案；用户指定的「风格词库」）
        "style_library_enabled": _as_switch(chat.get("style_library_enabled"), True),
        "style_library_max": _bounded_int(
            chat.get("style_library_max"), DEFAULT_STYLE_LIBRARY_MAX, 0,
            STYLE_LIBRARY_MAX_CEILING),
    }


def _aesthetic_threshold(raw) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_AESTHETIC_THRESHOLD
    return round(max(0.0, min(100.0, value)), 1)


def _bounded_int(raw, default: int, low: int, high: int) -> int:
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, value))


def _as_switch(value, default: bool) -> bool:
    """三态布尔：None/缺失 → 默认值；字符串按 true/false 解析"""
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "on")
    return bool(value)


def chat_settings() -> dict[str, Any]:
    """生效的会话策略：`config/chat.yaml`（设置页写入）→ `config/default.yaml` 的 chat 段

    用独立文件承载 UI 写入，是为了**不改写 default.yaml**——`yaml.safe_dump` 会丢掉文件里的
    全部注释（output.yaml 早就用同样的做法）。
    """
    overrides = load_yaml(CHAT_CONFIG_REL)
    merged: dict[str, Any] = {}
    if isinstance(overrides, dict):
        merged.update({k: v for k, v in overrides.items() if k in _CHAT_POLICY_KEYS})
    defaults = load_default_config().get("chat", {})
    if isinstance(defaults, dict):
        for key in _CHAT_POLICY_KEYS:
            # 只在默认值**存在**时兜底：`defaults.get(key)` 对缺省键返回 None，
            # 而 None 会让 `_normalize_chat_policy` 无法区分"没设置"和"显式关闭"
            if key not in merged and defaults.get(key) is not None:
                merged[key] = defaults[key]
    return _normalize_chat_policy(merged)


def save_chat_settings(updates: dict[str, Any]) -> dict[str, Any]:
    """把会话策略写入 `config/chat.yaml`（只动白名单字段；非法值抛 ValueError）"""
    import yaml as _yaml

    if not isinstance(updates, dict):
        raise ValueError("会话策略必须是对象")
    unknown = sorted(set(updates) - set(_CHAT_POLICY_KEYS))
    if unknown:
        raise ValueError(f"不支持的字段: {', '.join(unknown)}")

    current = load_yaml(CHAT_CONFIG_REL)
    stored = dict(current) if isinstance(current, dict) else {}
    effective = chat_settings()

    for key, value in updates.items():
        if key == "max_consecutive_review_failures":
            try:
                number = int(value)
            except (TypeError, ValueError):
                raise ValueError("max_consecutive_review_failures 必须是整数")
            if number < 0 or number > MAX_CONSECUTIVE_FAILURES_CEILING:
                raise ValueError(
                    f"max_consecutive_review_failures 必须在 0–{MAX_CONSECUTIVE_FAILURES_CEILING} 之间"
                    "（0 = 关闭该保护）")
            stored[key] = number
        elif key == "max_turns":
            try:
                number = int(value)
            except (TypeError, ValueError):
                raise ValueError("max_turns 必须是整数")
            if not (1 <= number <= 50):
                raise ValueError("max_turns 必须在 1–50 之间")
            stored[key] = number
        elif key == "require_identity_confirm":
            if not isinstance(value, bool):
                raise ValueError("require_identity_confirm 必须是 true/false")
            stored[key] = value
        elif key == "require_prompt_review":
            if not isinstance(value, bool):
                raise ValueError("require_prompt_review 必须是 true/false")
            stored[key] = value
        elif key == "require_prompt_confirm":
            if not isinstance(value, bool):
                raise ValueError("require_prompt_confirm 必须是 true/false")
            stored[key] = value
        elif key == "prompt_aesthetic_threshold":
            try:
                score = float(value)
            except (TypeError, ValueError):
                raise ValueError("prompt_aesthetic_threshold 必须是数字")
            if not (0 <= score <= 100):
                raise ValueError("prompt_aesthetic_threshold 必须在 0–100 之间")
            stored[key] = _aesthetic_threshold(score)
        elif key == "prompt_review_max_rounds":
            try:
                rounds = int(value)
            except (TypeError, ValueError):
                raise ValueError("prompt_review_max_rounds 必须是整数")
            if not (0 <= rounds <= PROMPT_REVIEW_ROUNDS_CEILING):
                raise ValueError(
                    f"prompt_review_max_rounds 必须在 0–{PROMPT_REVIEW_ROUNDS_CEILING} 之间（0 = 不打回重写）")
            stored[key] = rounds
        elif key == "style_library_enabled":
            if not isinstance(value, bool):
                raise ValueError("style_library_enabled 必须是 true/false")
            stored[key] = value
        elif key == "style_library_max":
            try:
                count = int(value)
            except (TypeError, ValueError):
                raise ValueError("style_library_max 必须是整数")
            if not (0 <= count <= STYLE_LIBRARY_MAX_CEILING):
                raise ValueError(
                    f"style_library_max 必须在 0–{STYLE_LIBRARY_MAX_CEILING} 之间（0 = 不注入风格档案）")
            stored[key] = count
        else:  # session_ttl_hours
            try:
                hours = float(value)
            except (TypeError, ValueError):
                raise ValueError("session_ttl_hours 必须是数字")
            if hours < 0:
                raise ValueError("session_ttl_hours 不能为负数")
            stored[key] = hours

    path = _project_root() / CHAT_CONFIG_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_yaml.safe_dump(stored, allow_unicode=True, sort_keys=False),
                    encoding="utf-8")
    _ = effective  # 供调试时对比（保存前后）
    return chat_settings()


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


# 由进程环境变量（而非 secrets.yaml 注入）提供的密钥变量名
_ENV_PROVIDED_SECRETS: set[str] = set()


def apply_runtime_secrets_to_env() -> int:
    """把持久化密钥注入 os.environ（环境变量显式设置时优先），返回注入数量。

    必须在 Provider 注册表构建前调用（本模块 import 时自动执行一次）。

    同时记录"由进程环境变量直接供给"的变量名（`_ENV_PROVIDED_SECRETS`）——
    设置页据此把这些 Key 标为只读（环境变量优先，经设置页保存不会生效）。
    """
    secrets = load_runtime_secrets()
    for k in secrets:
        if os.environ.get(k) is not None:
            _ENV_PROVIDED_SECRETS.add(k)
    applied = 0
    for k, v in secrets.items():
        if os.environ.get(k) is None:
            os.environ[k] = v
            applied += 1
    return applied


def env_provided_secret_keys() -> set[str]:
    """进程环境显式供给的密钥变量名（secrets.yaml 注入的不计）"""
    return set(_ENV_PROVIDED_SECRETS)


def secret_key_source(env_name: str) -> str:
    """密钥来源：'' 未配置 / 'env' 环境变量供给 / 'file' 由 secrets.yaml 持久化。

    'env' 意味着设置页保存的值不会生效（`apply_runtime_secrets_to_env` 只在
    env 缺失时注入），UI 据此禁止编辑并提示改环境变量。
    """
    value = os.getenv(env_name)
    if not value:
        return ""
    if env_name in _ENV_PROVIDED_SECRETS:
        return "env"
    try:
        if load_runtime_secrets().get(env_name) == value:
            return "file"
    except Exception:
        pass
    return "env"  # 进程启动后由外部写入的 env（部署脚本/测试注入）


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


# ── Provider 端点与模型覆盖（第三方 coding plan / 代理 / 自建网关） ──
#
# 场景：使用第三方 coding plan（OpenAI/Anthropic 兼容中转）或自建网关时，
# 官方 base_url 不可用；同时其模型 id 往往不在内置目录中。
# 优先级：环境变量（ECOMM_<ROUTE>_BASE_URL 或 <ROUTE>_BASE_URL）→
#        config/providers.yaml → Provider 内置官方端点。
# 该文件含私有端点，与 secrets.yaml 同样加入 .gitignore。

PROVIDER_CONFIG_REL = "config/providers.yaml"


def provider_config_path() -> Path:
    return _project_root() / PROVIDER_CONFIG_REL


def load_provider_config() -> dict[str, dict]:
    """读取 provider 覆盖配置：{route: {"base_url": str, "models": [str], "max_tokens": int}}"""
    data = load_yaml(PROVIDER_CONFIG_REL)
    if not isinstance(data, dict):
        return {}
    out: dict[str, dict] = {}
    for route, cfg in data.items():
        if not isinstance(cfg, dict):
            continue
        entry: dict = {}
        base_url = str(cfg.get("base_url") or "").strip().rstrip("/")
        if base_url:
            entry["base_url"] = base_url
        models = cfg.get("models")
        if isinstance(models, list):
            cleaned = [str(m).strip() for m in models if str(m).strip()]
            if cleaned:
                entry["models"] = cleaned
        max_tokens = clean_max_tokens(cfg.get("max_tokens"))
        if max_tokens:
            entry["max_tokens"] = max_tokens
        if entry:
            out[str(route)] = entry
    return out


MAX_TOKENS_MIN, MAX_TOKENS_MAX = 256, 200_000


def clean_max_tokens(raw) -> int:
    """规范化 LLM 单次输出预算；非法返回 0（表示未配置）

    实测事故：推理模型（DeepSeek V4）的思考 token 也计入该预算，4096 会被吃光 →
    `content=""` 却被当成成功。用户可在 config/providers.yaml 按路由调大。
    """
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 0
    if not (MAX_TOKENS_MIN <= value <= MAX_TOKENS_MAX):
        return 0
    return value


def save_provider_config(updates: dict[str, dict]) -> dict[str, dict]:
    """合并写入 provider 覆盖配置（空 base_url / 空 models 列表表示清除该项）"""
    path = provider_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    merged = load_provider_config()
    for route, cfg in updates.items():
        entry = dict(merged.get(route, {}))
        if "base_url" in cfg:
            base_url = str(cfg.get("base_url") or "").strip().rstrip("/")
            if base_url:
                entry["base_url"] = base_url
            else:
                entry.pop("base_url", None)
        if "models" in cfg:
            models = [str(m).strip() for m in (cfg.get("models") or []) if str(m).strip()]
            if models:
                entry["models"] = models
            else:
                entry.pop("models", None)
        if "max_tokens" in cfg:
            max_tokens = clean_max_tokens(cfg.get("max_tokens"))
            if max_tokens:
                entry["max_tokens"] = max_tokens
            else:
                entry.pop("max_tokens", None)
        if entry:
            merged[route] = entry
        else:
            merged.pop(route, None)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(merged, f, allow_unicode=True, sort_keys=False)
    return merged


def resolve_base_url(route: str, default: str, env_names: tuple[str, ...] | None = None) -> str:
    """解析 Provider 生效端点：env 覆盖 → providers.yaml → 内置官方端点

    `env_names` 用于自定义服务商（`<ROUTE>_BASE_URL`）；缺省用内置路由的环境变量表。
    """
    if env_names:
        for name in env_names:
            val = get_env(name).strip()
            if val:
                return val.rstrip("/")
        from_env = ""
    else:
        from_env = base_url_from_env(route)
    return from_env or (
        load_provider_config().get(route) or {}
    ).get("base_url", "") or default


# 端点环境变量名（默认 <ROUTE>_BASE_URL；qwen 兼容 DashScope 官方命名）
_BASE_URL_ENVS = {
    "qwen": ("DASHSCOPE_BASE_URL", "QWEN_BASE_URL"),
}


def base_url_env_names(route: str) -> tuple[str, ...]:
    return _BASE_URL_ENVS.get(route, (f"{route.upper()}_BASE_URL",))


def base_url_from_env(route: str) -> str:
    """进程环境变量提供的端点（ECOMM_ 前缀或原始名，均支持）；未提供返回 ''"""
    for name in base_url_env_names(route):
        val = get_env(name).strip()
        if val:
            return val.rstrip("/")
    return ""


def base_url_source(route: str, env_names: tuple[str, ...] | None = None) -> str:
    """端点来源：'env' / 'file' / ''（未覆盖，用官方端点）"""
    if env_names:
        if any(get_env(name).strip() for name in env_names):
            return "env"
    elif base_url_from_env(route):
        return "env"
    if (load_provider_config().get(route) or {}).get("base_url"):
        return "file"
    return ""


def provider_models_override(route: str) -> list[str]:
    """该 Provider 的自定义模型列表（未配置返回空列表）"""
    return list((load_provider_config().get(route) or {}).get("models", []))


# ── 自定义模型服务商（用户反馈："我无法自己添加模型服务商"） ──
#
# 此前路由/凭据槽/模型目录/可用密钥全部硬编码在 API 与注册表里，用户一个都改不了。
# 现在用户可在设置页添加 OpenAI / Anthropic 兼容的任意服务商，落盘到本文件。
# 该文件可能含私有端点，与 providers.yaml 一样加入 .gitignore。

CUSTOM_PROVIDERS_REL = "config/custom_providers.yaml"


def custom_providers_path() -> Path:
    return _project_root() / CUSTOM_PROVIDERS_REL


def load_custom_providers() -> list[dict]:
    """读取自定义服务商定义（结构非法/文件损坏时返回空列表，不影响启动）"""
    try:
        data = load_yaml(CUSTOM_PROVIDERS_REL)
    except yaml.YAMLError:
        return []
    if not isinstance(data, dict):
        return []
    providers = data.get("providers")
    if not isinstance(providers, list):
        return []
    return [p for p in providers if isinstance(p, dict)]


def save_custom_providers(providers: list[dict]) -> list[dict]:
    """覆盖写入自定义服务商定义"""
    path = custom_providers_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"providers": list(providers)} if providers else {}
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, allow_unicode=True, sort_keys=False)
    return list(providers)


def upsert_custom_provider(spec: dict) -> list[dict]:
    """新增或按 route 覆盖一条自定义服务商"""
    from src.providers.routes import validate_spec  # 局部导入避免循环依赖

    route = str(spec.get("route") or "").strip().lower()
    others = [p for p in load_custom_providers()
              if str(p.get("route") or "").strip().lower() != route]
    normalized, errors = validate_spec(spec, existing_routes=set(), allow_builtin_override=True)
    if errors:
        raise ValueError("; ".join(errors))
    return save_custom_providers(others + [normalized])


def remove_custom_provider(route: str) -> bool:
    """删除一条自定义服务商；返回是否真的删掉了"""
    route = str(route or "").strip().lower()
    current = load_custom_providers()
    remain = [p for p in current if str(p.get("route") or "").strip().lower() != route]
    if len(remain) == len(current):
        return False
    save_custom_providers(remain)
    return True


# ── 生图参数（尺寸 / 张数）──
#
# 用户实测事故：方舟 Seedream 5.0 要求图像 ≥ 3,686,400 像素，而 Agent 写死
# `1024x1024`（104 万像素）→ 上游 400 InvalidParameter，且错误被静默吞掉。
# 尺寸由此变成**可配置**：config/models.yaml 的 `capabilities.image.size`
# （用户可改）→ 路由表里的默认值（如 ark 的 2048x2048）→ 1024x1024。

DEFAULT_IMAGE_SIZE = "1024x1024"
DEFAULT_IMAGE_VARIANTS = 3
IMAGE_VARIANTS_MIN, IMAGE_VARIANTS_MAX = 1, 6
_IMAGE_SIDE_MIN, _IMAGE_SIDE_MAX = 64, 8192
IMAGE_SIZE_RE = re.compile(r"^(\d{2,5})x(\d{2,5})$")
# 方舟 Seedream 支持 2K/3K/4K 档位（比写死像素更省心，且换模型不用改数字）
IMAGE_SIZE_PRESETS = ("1K", "2K", "3K", "4K")
_IMAGE_PRESET_RE = re.compile(r"^([1-4])k$")


def clean_image_size(raw) -> str:
    """把尺寸字符串规范化；非法（含注入字符/越界/负数）返回 ''

    支持两种写法：`2048x2048`（精确像素）与 `2K/3K/4K`（平台档位预设）。
    """
    text = str(raw or "").strip().lower().replace(" ", "")
    preset = _IMAGE_PRESET_RE.match(text)
    if preset:
        return f"{preset.group(1)}K"
    match = IMAGE_SIZE_RE.match(text)
    if not match:
        return ""
    width, height = int(match.group(1)), int(match.group(2))
    if not (_IMAGE_SIDE_MIN <= width <= _IMAGE_SIDE_MAX
            and _IMAGE_SIDE_MIN <= height <= _IMAGE_SIDE_MAX):
        return ""
    return f"{width}x{height}"


# ── 生图质量策略（config/image.yaml；设置页可改）──
#
# 用户三点反馈直接催生了这些开关：
#   ① "必须文+图生图"     → reference_mode（auto=有原图就用作参考条件）
#   ② "文字要么逐字还原要么干净虚化，不能让模型编" → text_strategy
#   ③ "交付物要成套图"    → slot_candidates（每个槽位的候选数）
# 另加 watermark（消掉平台默认的"AI生成"水印）与本地体检阈值。

IMAGE_CONFIG_REL = "config/image.yaml"      # 设置页写入（实例级配置，已 gitignore）
DEFAULT_TEXT_STRATEGY = "preserve"
TEXT_STRATEGIES = ("preserve", "blur", "none")     # 逐字还原 / 干净虚化 / 不出现文字
DEFAULT_REFERENCE_MODE = "auto"
REFERENCE_MODES = ("auto", "off")                  # auto=有原图就用 / off=强制文生图（对照实验）
DEFAULT_MAX_REFERENCES = 4
MAX_REFERENCES_LIMIT = 8
DEFAULT_SLOT_CANDIDATES = 1                        # 套图每个槽位出几张（1=一套每槽一张）
SLOT_CANDIDATES_MAX = 4
DEFAULT_QUALITY_THRESHOLDS: dict[str, float] = {
    "edge_whiteness": 250.0,        # 白底图边缘平均亮度下限（#FFFFFF=255）
    "watermark_zone_delta": 3.0,    # 右下角与边缘的均值差上限（超过即疑似水印/叠加物）
    "identity_similarity_min": 0.45,  # 与参考图的身份相似度下限（低于即商品身份丢失）
    "near_copy_max": 0.92,          # 与参考图的相似度上限（高于即"复制原图没重绘"）
}
_IMAGE_SETTING_KEYS = ("size", "variants", "slot_candidates", "text_strategy",
                       "reference_mode", "max_references", "watermark", "quality", "platforms",
                       "typography")
_QUALITY_KEYS = tuple(DEFAULT_QUALITY_THRESHOLDS)

# 信息图的排版参数（本地绘制文字层的样式；用户看完成图后会想调字号/配色/条目数）
DEFAULT_TYPOGRAPHY: dict[str, Any] = {
    "font_scale": 1.0,          # 字号倍率（0.6–1.8）
    "max_items": 6,             # 一张图最多画几条（1–8）
    "brand_color": "#1860AC",   # 标题条/主色块
    "accent_color": "#24945F",  # 次色块/编号方块
    "show_footer": True,        # 页脚是否画品牌｜规格｜认证
}
_TYPOGRAPHY_KEYS = tuple(DEFAULT_TYPOGRAPHY)
_FONT_SCALE_MIN, _FONT_SCALE_MAX = 0.6, 1.8
_TYPO_MAX_ITEMS_MIN, _TYPO_MAX_ITEMS_MAX = 1, 8
_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def clean_hex_color(raw, default: str) -> str:
    """十六进制颜色（`#RRGGBB`）；非法回落默认值"""
    text = str(raw or "").strip()
    return text.upper() if _HEX_COLOR_RE.match(text) else default


def clean_typography(raw) -> dict[str, Any]:
    """排版参数：非法/缺省回落默认；数值越界收敛到合理区间"""
    cfg = raw if isinstance(raw, dict) else {}
    try:
        scale = float(cfg.get("font_scale", DEFAULT_TYPOGRAPHY["font_scale"]))
    except (TypeError, ValueError):
        scale = DEFAULT_TYPOGRAPHY["font_scale"]
    try:
        items = int(cfg.get("max_items", DEFAULT_TYPOGRAPHY["max_items"]))
    except (TypeError, ValueError):
        items = DEFAULT_TYPOGRAPHY["max_items"]
    footer = cfg.get("show_footer", DEFAULT_TYPOGRAPHY["show_footer"])
    return {
        "font_scale": round(max(_FONT_SCALE_MIN, min(_FONT_SCALE_MAX, scale)), 2),
        "max_items": max(_TYPO_MAX_ITEMS_MIN, min(_TYPO_MAX_ITEMS_MAX, items)),
        "brand_color": clean_hex_color(cfg.get("brand_color"), DEFAULT_TYPOGRAPHY["brand_color"]),
        "accent_color": clean_hex_color(cfg.get("accent_color"), DEFAULT_TYPOGRAPHY["accent_color"]),
        "show_footer": bool(footer) if not isinstance(footer, str)
        else footer.strip().lower() in ("true", "1", "yes", "on"),
    }


def clean_text_strategy(raw) -> str:
    """文字策略：preserve（逐字还原）/ blur（干净虚化交后期贴图）/ none（不出现文字）"""
    text = str(raw or "").strip().lower()
    return text if text in TEXT_STRATEGIES else DEFAULT_TEXT_STRATEGY


def clean_reference_mode(raw) -> str:
    """参考图模式：auto（有原图就用）/ off（强制文生图，做对照实验用）"""
    text = str(raw or "").strip().lower()
    return text if text in REFERENCE_MODES else DEFAULT_REFERENCE_MODE


def _clamp_int(raw, default: int, low: int, high: int) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, value))


def clean_quality_thresholds(raw) -> dict[str, float]:
    """体检阈值：非法/缺省回落默认；越界收敛到合理区间"""
    cfg = raw if isinstance(raw, dict) else {}
    result: dict[str, float] = {}
    for key, default in DEFAULT_QUALITY_THRESHOLDS.items():
        try:
            value = float(cfg.get(key, default))
        except (TypeError, ValueError):
            value = default
        if key in ("edge_whiteness", "watermark_zone_delta"):
            value = max(0.0, value)
        else:
            value = max(0.0, min(1.0, value))
        result[key] = value
    return result


def _clean_platform_overrides(raw) -> dict[str, dict]:
    """平台槽位覆盖：`{平台: {"slots": [...]}}`；非法形状整体丢弃（配置错了不该炸会话）"""
    if not isinstance(raw, dict):
        return {}
    cleaned: dict[str, dict] = {}
    for slug, value in raw.items():
        if not isinstance(value, dict):
            continue
        entry: dict[str, Any] = {}
        slots = value.get("slots")
        if isinstance(slots, list):
            names = [str(s).strip() for s in slots if str(s).strip()]
            if names:
                entry["slots"] = names
        if entry:
            cleaned[str(slug)] = entry
    return cleaned


def image_settings() -> dict[str, Any]:
    """生效的生图质量策略：`config/image.yaml`（设置页）→ `config/models.yaml` → 默认值"""
    overrides = load_yaml(IMAGE_CONFIG_REL)
    caps = load_models_config().get("capabilities", {}) or {}
    models_cfg = caps.get("image", {}) or {}
    if not isinstance(models_cfg, dict):
        models_cfg = {}
    if not isinstance(overrides, dict):
        overrides = {}

    def pick(key, default):
        """覆盖文件优先；`None`/缺失视为"没设"，继续往下找"""
        for source in (overrides, models_cfg):
            value = source.get(key)
            if value is not None:
                return value
        return default

    return {
        "size": clean_image_size(pick("size", "")),
        "variants": _clamp_int(pick("variants", DEFAULT_IMAGE_VARIANTS),
                               DEFAULT_IMAGE_VARIANTS, IMAGE_VARIANTS_MIN, IMAGE_VARIANTS_MAX),
        "slot_candidates": _clamp_int(pick("slot_candidates", DEFAULT_SLOT_CANDIDATES),
                                      DEFAULT_SLOT_CANDIDATES, 1, SLOT_CANDIDATES_MAX),
        "text_strategy": clean_text_strategy(pick("text_strategy", DEFAULT_TEXT_STRATEGY)),
        "reference_mode": clean_reference_mode(pick("reference_mode", DEFAULT_REFERENCE_MODE)),
        "max_references": _clamp_int(pick("max_references", DEFAULT_MAX_REFERENCES),
                                     DEFAULT_MAX_REFERENCES, 1, MAX_REFERENCES_LIMIT),
        "watermark": bool(pick("watermark", False)),
        "quality": clean_quality_thresholds(
            overrides.get("quality") if overrides.get("quality") is not None
            else models_cfg.get("quality")),
        "typography": clean_typography(
            overrides.get("typography") if overrides.get("typography") is not None
            else models_cfg.get("typography")),
        "platforms": _clean_platform_overrides(
            overrides.get("platforms") if overrides.get("platforms") is not None
            else models_cfg.get("platforms")),
    }


def save_image_settings(updates: dict[str, Any]) -> dict[str, Any]:
    """把生图质量策略写入 `config/image.yaml`（只动白名单字段；非法值抛 ValueError）"""
    import yaml as _yaml

    if not isinstance(updates, dict):
        raise ValueError("生图策略必须是对象")
    unknown = sorted(set(updates) - set(_IMAGE_SETTING_KEYS))
    if unknown:
        raise ValueError(f"不支持的字段: {', '.join(unknown)}")

    current = load_yaml(IMAGE_CONFIG_REL)
    stored = dict(current) if isinstance(current, dict) else {}

    for key, value in updates.items():
        if key == "size":
            size = clean_image_size(value)
            if str(value or "").strip() and not size:
                raise ValueError("size 必须是 WxH（如 2048x2048）或 1K/2K/3K/4K")
            stored[key] = size
        elif key == "variants":
            stored[key] = _bounded_int(value, "variants", IMAGE_VARIANTS_MIN, IMAGE_VARIANTS_MAX)
        elif key == "slot_candidates":
            stored[key] = _bounded_int(value, "slot_candidates", 1, SLOT_CANDIDATES_MAX)
        elif key == "text_strategy":
            if str(value or "").strip().lower() not in TEXT_STRATEGIES:
                raise ValueError(f"text_strategy 必须是 {'/'.join(TEXT_STRATEGIES)} 之一")
            stored[key] = clean_text_strategy(value)
        elif key == "reference_mode":
            if str(value or "").strip().lower() not in REFERENCE_MODES:
                raise ValueError(f"reference_mode 必须是 {'/'.join(REFERENCE_MODES)} 之一")
            stored[key] = clean_reference_mode(value)
        elif key == "max_references":
            stored[key] = _bounded_int(value, "max_references", 1, MAX_REFERENCES_LIMIT)
        elif key == "watermark":
            if not isinstance(value, bool):
                raise ValueError("watermark 必须是 true/false")
            stored[key] = value
        elif key == "quality":
            if not isinstance(value, dict):
                raise ValueError("quality 必须是对象")
            bad = sorted(set(value) - set(_QUALITY_KEYS))
            if bad:
                raise ValueError(f"quality 不支持的字段: {', '.join(bad)}")
            # 先与已存值合并、再规范化：`clean_*` 会给缺失键填默认值，
            # 直接 update 会把没提交的字段**重置成默认值**（部分保存语义被破坏，实测抓到）
            stored["quality"] = clean_quality_thresholds({**(stored.get("quality") or {}), **value})
        elif key == "typography":
            if not isinstance(value, dict):
                raise ValueError("typography 必须是对象")
            bad = sorted(set(value) - set(_TYPOGRAPHY_KEYS))
            if bad:
                raise ValueError(f"typography 不支持的字段: {', '.join(bad)}")
            for name, raw_value in value.items():
                if name == "font_scale":
                    try:
                        number = float(raw_value)
                    except (TypeError, ValueError):
                        raise ValueError("typography.font_scale 必须是数字")
                    if not (_FONT_SCALE_MIN <= number <= _FONT_SCALE_MAX):
                        raise ValueError(f"typography.font_scale 必须在 "
                                         f"{_FONT_SCALE_MIN}–{_FONT_SCALE_MAX} 之间")
                elif name == "max_items":
                    if isinstance(raw_value, bool) or not str(raw_value).lstrip("-").isdigit() \
                            or not (_TYPO_MAX_ITEMS_MIN <= int(raw_value) <= _TYPO_MAX_ITEMS_MAX):
                        raise ValueError(f"typography.max_items 必须在 "
                                         f"{_TYPO_MAX_ITEMS_MIN}–{_TYPO_MAX_ITEMS_MAX} 之间")
                elif name in ("brand_color", "accent_color"):
                    if not _HEX_COLOR_RE.match(str(raw_value or "").strip()):
                        raise ValueError(f"typography.{name} 必须是 #RRGGBB")
                elif name == "show_footer" and not isinstance(raw_value, bool):
                    raise ValueError("typography.show_footer 必须是 true/false")
            merged_typo = dict(stored.get("typography") or {})
            merged_typo.update(value)
            stored["typography"] = clean_typography(merged_typo)
        else:  # platforms
            if not isinstance(value, dict):
                raise ValueError("platforms 必须是对象（{平台: {slots: [...]}}）")
            for slug, entry in value.items():
                if not isinstance(entry, dict):
                    raise ValueError(f"platforms.{slug} 必须是对象")
                slots = entry.get("slots")
                if slots is not None and not (isinstance(slots, list)
                                              and all(str(s).strip() for s in slots)):
                    raise ValueError(f"platforms.{slug}.slots 必须是非空字符串数组")
            merged = dict(stored.get("platforms") or {})
            for slug, entry in value.items():
                merged[str(slug)] = {**(merged.get(str(slug)) or {}),
                                     **({"slots": [str(s).strip() for s in entry["slots"]]}
                                        if entry.get("slots") is not None else {})}
            stored["platforms"] = merged

    path = _project_root() / IMAGE_CONFIG_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_yaml.safe_dump(stored, allow_unicode=True, sort_keys=False),
                    encoding="utf-8")
    return image_settings()


def _bounded_int(raw, name: str, low: int, high: int) -> int:
    """保存时的整数校验：非数字或越界直接报错（**不静默收敛**——用户填错要立刻知道）"""
    if isinstance(raw, bool) or not str(raw).lstrip("-").isdigit():
        raise ValueError(f"{name} 必须是整数")
    value = int(raw)
    if not (low <= value <= high):
        raise ValueError(f"{name} 必须在 {low}–{high} 之间")
    return value


def image_options() -> dict[str, Any]:
    """生效的生图参数（`config/image.yaml` → `models.yaml` → 默认值）

    返回值同时覆盖新旧两条出图路径：
    - `size` / `variants`：**旧路径**（无套图编排时，同一提示词出 N 张候选）
    - `slot_candidates`：**套图路径**（每个槽位出几张，默认 1）
    - `text_strategy` / `reference_mode` / `max_references` / `watermark`：文+图双条件的开关
    - `quality`：本地体检阈值（白度 / 水印区 / 身份相似度）
    - `platforms`：按平台覆盖套图槽位
    """
    return image_settings()


def resolve_image_size(route_default: str = "") -> str:
    """生效的生图尺寸：config/image.yaml → models.yaml → 路由默认 → 1024x1024"""
    return image_options()["size"] or clean_image_size(route_default) or DEFAULT_IMAGE_SIZE


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


# ── 生成图片输出目录（用户反馈：没法自己设置导出路径） ──
#
# 背景：`deploy/Dockerfile` 早就 `mkdir output` 并挂了 `harness_output` 卷，
# 但没有任何代码往里写——生成图只以 base64 存内存/checkpoint，前端用 <img> 显示。
# 优先级：ECOMM_OUTPUT_DIR 环境变量 → config/output.yaml 的 dir → <项目根>/output。
# 相对路径按项目根解析（可预测）；该文件是机器相关配置，加入 .gitignore。

OUTPUT_CONFIG_REL = "config/output.yaml"
OUTPUT_DIR_ENV = "ECOMM_OUTPUT_DIR"


def output_config_path() -> Path:
    return _project_root() / OUTPUT_CONFIG_REL


def output_dir_from_env() -> str:
    """环境变量提供的输出根（未提供返回 ''）"""
    return os.getenv(OUTPUT_DIR_ENV, "").strip()


def output_dir_from_file() -> str:
    """config/output.yaml 里配置的输出根（未配置返回 ''）"""
    data = load_yaml(OUTPUT_CONFIG_REL)
    if not isinstance(data, dict):
        return ""
    return str(data.get("dir") or "").strip()


def output_dir_source() -> str:
    """输出根来源：'env' / 'file' / 'default'"""
    if output_dir_from_env():
        return "env"
    if output_dir_from_file():
        return "file"
    return "default"


def output_root() -> Path:
    """生成图输出根目录（使用时解析，勿在模块级固化为常量）

    - 相对路径按项目根解析（`exports` → `<项目根>/exports`）；
    - `~` 展开为用户主目录；
    - 默认 `<项目根>/output`（与 deploy/ 的卷挂载一致）。
    """
    raw = output_dir_from_env() or output_dir_from_file()
    if not raw:
        return _project_root() / "output"
    path = Path(os.path.expanduser(raw))
    return path if path.is_absolute() else (_project_root() / path)


def save_output_dir(directory: str) -> str:
    """写入输出根配置（空字符串 = 清除，回落默认目录）；返回生效的配置值"""
    path = output_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    value = str(directory or "").strip()
    data = {"dir": value} if value else {}
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
    return value


def output_status() -> dict:
    """输出目录状态（设置页展示 + 保存前校验）：确保可创建、可写，失败给出原因

    路径配错（盘符不存在 / 无权限 / 只读挂载）是用户最常踩的坑，
    所以这里做一次真实的 mkdir + 探针写入，而不是只看配置字符串。
    """
    import tempfile

    root = output_root()
    status = {
        "dir": output_dir_from_file(),
        "effective_dir": str(root),
        "source": output_dir_source(),
        "env_locked": bool(output_dir_from_env()),
        "writable": False,
        "error": "",
    }
    try:
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=str(root), prefix=".write-probe-", delete=True):
            pass
        status["writable"] = True
    except Exception as e:  # noqa: BLE001 — 状态查询不得抛异常
        status["error"] = f"{type(e).__name__}: {e}"
    return status
