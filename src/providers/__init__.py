"""ProviderRegistry — 服务商路由表驱动，解析能力→模型映射

路由表见 `src/providers/routes.py`：内置 7 条（openai/deepseek/anthropic/qwen/
**ark 火山引擎方舟**/seedream 旧版视觉接口/flux）+ 用户在设置页添加的自定义服务商
（OpenAI / Anthropic 兼容）。此前可用性是逐个 `if os.getenv("OPENAI_API_KEY")`
硬编码的，用户无法新增服务商。
"""

import os
from typing import Optional

from src.core.config import is_mock_mode, load_models_config
from src.providers.base import BaseImageProvider, BaseLLMProvider
from src.providers.mock import MockImageProvider, MockLLMProvider
from src.providers.routes import (
    BUILTIN_BY_ROUTE,
    RouteSpec,
    spec_from_custom,
)

# 已告警过的「覆盖键与 requires 不匹配」组合（避免每次 resolve 刷屏）
_WARNED_OVERRIDE_MISMATCH: set[tuple[str, str]] = set()


class _ProviderMeta:
    """Provider 元信息"""

    def __init__(self, name: str, available: bool, capabilities: list[str]):
        self.name = name
        self.available = available
        self.capabilities = capabilities


def custom_route_specs(existing: set[str] | None = None) -> list[RouteSpec]:
    """读取用户自定义服务商（config/custom_providers.yaml），非法条目跳过并告警

    放在 providers 层（而非 api 层）是因为注册表与 API 都要用它；
    配置读写本身仍在 `src/core/config.py`。
    """
    from src.core.config import load_custom_providers
    from src.core.logging_config import get_logger

    occupied = set(BUILTIN_BY_ROUTE) if existing is None else set(existing)
    specs: list[RouteSpec] = []
    for raw in load_custom_providers():
        spec, errors = spec_from_custom(raw, occupied)
        if spec is None:
            get_logger(__name__).warning(
                "跳过非法的自定义服务商定义: %s", "; ".join(errors))
            continue
        specs.append(spec)
        occupied.add(spec.route)
    return specs


def all_route_specs() -> dict[str, RouteSpec]:
    """内置 + 自定义（自定义不得与内置重名，见 validate_spec）"""
    merged: dict[str, RouteSpec] = dict(BUILTIN_BY_ROUTE)
    for spec in custom_route_specs(set(merged)):
        merged[spec.route] = spec
    return merged


class ProviderRegistry:
    """Provider 注册中心

    负责：
    1. 按路由表检测可用性（凭据环境变量是否存在）
    2. 根据 config/models.yaml 解析能力→模型映射
    3. 为 Agent 提供 resolve(requires) 方法
    """

    def __init__(self):
        self._llm: dict[str, BaseLLMProvider] = {}
        self._image: dict[str, BaseImageProvider] = {}
        self._meta: dict[str, _ProviderMeta] = {}
        self._specs: dict[str, RouteSpec] = all_route_specs()
        self._models_config = load_models_config()

        # 注册 Mock（始终可用）
        self._register_llm("mock", MockLLMProvider())
        self._register_image("mock", MockImageProvider())

        # 内置 + 自定义：任一凭据环境变量存在即视为可用；
        # `all_key_envs`（火山 AK/SK 签名）要求成对存在，缺一半不算可用
        for route, spec in self._specs.items():
            if self._spec_available(spec):
                self._meta[route] = _ProviderMeta(route, True, list(spec.capabilities))

    @staticmethod
    def _spec_available(spec: RouteSpec) -> bool:
        """该路由此刻是否可用（凭据就位）"""
        if any(os.getenv(env) for env in spec.key_envs):
            return True
        return bool(spec.all_key_envs) and all(os.getenv(env) for env in spec.all_key_envs)

    # ── 注册方法 ──

    def _register_llm(self, name: str, provider: BaseLLMProvider):
        self._llm[name] = provider
        if name not in self._meta:
            self._meta[name] = _ProviderMeta(name, True, provider.capabilities)

    def _register_image(self, name: str, provider: BaseImageProvider):
        self._image[name] = provider
        if name not in self._meta:
            self._meta[name] = _ProviderMeta(name, True, provider.capabilities)

    # ── 查询方法 ──

    def spec(self, provider_name: str) -> Optional[RouteSpec]:
        """路由定义（内置或自定义）；未知返回 None"""
        return self._specs.get(provider_name)

    def _key_for(self, spec: RouteSpec) -> str:
        """该路由的凭据：按 key_envs 顺序取第一个非空环境变量"""
        for env in spec.key_envs:
            value = os.getenv(env, "")
            if value:
                return value
        return ""

    def _resolve_spec_base_url(self, spec: RouteSpec) -> str:
        """端点：该路由的 BASE_URL 环境变量 → config/providers.yaml → 表里的默认端点"""
        from src.core.config import resolve_base_url
        if not spec.default_base_url:
            return ""
        return resolve_base_url(spec.route, spec.default_base_url,
                                env_names=spec.base_url_envs or None)

    def _resolve_max_tokens(self, spec: RouteSpec) -> int:
        """单次输出预算：config/providers.yaml 覆盖 → 路由表默认

        推理模型的思考 token 也计入预算，方舟/DeepSeek 这类路由必须给足，
        否则"调用成功但 content 为空"（实测 4096 被 reasoning_tokens 吃满）。
        """
        from src.core.config import clean_max_tokens, load_provider_config
        override = clean_max_tokens((load_provider_config().get(spec.route) or {}).get("max_tokens"))
        return override or spec.max_tokens

    def _build_openai_compatible(self, spec: RouteSpec, capability: str):
        """按 kind 构造 Provider 实例（openai / anthropic 兼容）"""
        api_key = self._key_for(spec)
        base_url = self._resolve_spec_base_url(spec)
        if capability == "image":
            from src.providers.openai import OpenAIImageProvider
            if spec.kind != "openai" or "image" not in spec.capabilities:
                return None
            # 非官方端点剔除 DALL-E 专有参数，并显式要 URL 结果（火山方舟等）
            drop = () if spec.route == "openai" else ("quality",)
            extra = {} if spec.route == "openai" else {"response_format": "url"}
            provider = OpenAIImageProvider(api_key=api_key, base_url=base_url, name=spec.route,
                                           label=spec.label, extra_body=extra, drop_params=drop,
                                           default_size=spec.image_size,
                                           # 文+图双条件与平台参数能力来自路由表
                                           supports_reference=spec.supports_reference,
                                           supported_options=spec.image_options,
                                           supports_negative_prompt=spec.supports_negative_prompt)
            provider.route = spec.route   # 定价按「路由/模型」查（方舟价 ≠ DALL·E 价）
            return provider
        caps = [c for c in spec.capabilities if c in ("text", "vision")]
        if spec.kind == "anthropic":
            from src.providers.anthropic import AnthropicLLMProvider
            provider = AnthropicLLMProvider(api_key=api_key, base_url=base_url, name=spec.route,
                                            capabilities=caps, label=spec.label,
                                            max_tokens=self._resolve_max_tokens(spec))
            provider.route = spec.route
            return provider
        from src.providers.openai import OpenAILLMProvider
        provider = OpenAILLMProvider(api_key=api_key, base_url=base_url, name=spec.route,
                                     capabilities=caps, label=spec.label,
                                     max_tokens=self._resolve_max_tokens(spec))
        provider.route = spec.route
        return provider

    def get_llm(self, provider_name: str, model_name: str = "") -> Optional[BaseLLMProvider]:
        """获取 LLM Provider 实例（延迟实例化真实 Provider）

        内置路由保留各自的专用实现（定价/用量口径不同）；**ark 与自定义服务商**
        走 OpenAI / Anthropic 兼容实现（可注入端点与密钥）。
        """
        if provider_name in self._llm:
            return self._llm[provider_name]

        spec = self._specs.get(provider_name)
        if spec is None or "image" in spec.capabilities and spec.is_image_only():
            return None

        # 内置专用实现（保持既有行为与定价口径）
        legacy = {
            "openai": ("src.providers.openai", "OpenAILLMProvider"),
            "deepseek": ("src.providers.deepseek", "DeepSeekLLMProvider"),
            "anthropic": ("src.providers.anthropic", "AnthropicLLMProvider"),
            "qwen": ("src.providers.qwen", "QwenLLMProvider"),
        }
        try:
            if provider_name in legacy and not spec.custom:
                module_name, class_name = legacy[provider_name]
                module = __import__(module_name, fromlist=[class_name])
                provider = getattr(module, class_name)(max_tokens=self._resolve_max_tokens(spec))
            else:
                provider = self._build_openai_compatible(spec, "text")
            if provider is None:
                return None
            # 路由 id 注入：定价按「路由/模型」查（同一模型在不同服务商价格不同）
            provider.route = spec.route
            self._llm[provider_name] = provider
            return provider
        except Exception:
            if provider_name in self._meta:
                self._meta[provider_name].available = False
            return None

    def get_image(self, provider_name: str, model_name: str = "") -> Optional[BaseImageProvider]:
        """获取 Image Provider 实例"""
        if provider_name in self._image:
            return self._image[provider_name]

        spec = self._specs.get(provider_name)
        if spec is None or "image" not in spec.capabilities:
            return None

        try:
            if spec.kind == "volc_cv" and not spec.custom:
                from src.providers.seedream import SeedreamImageProvider
                provider = SeedreamImageProvider()
            elif spec.kind == "flux" and not spec.custom:
                from src.providers.flux import FluxImageProvider
                provider = FluxImageProvider()
            else:
                provider = self._build_openai_compatible(spec, "image")
            if provider is None:
                return None
            provider.route = spec.route
            self._image[provider_name] = provider
            return provider
        except Exception:
            if provider_name in self._meta:
                self._meta[provider_name].available = False
            return None

    # ── 核心：能力解析 ──

    def resolve(self, requires: list[str], agent_name: str = "") -> tuple:
        """根据 Agent 的能力需求，解析出 (provider, model_name)

        解析顺序：
        1. 查 agent_overrides 是否有覆盖
        2. 取 capabilities.{cap}.default
        3. 检查 Provider 是否可用，且能力是 requires 的超集
        4. 否 → 尝试 alternatives → fallback → mock

        Returns:
            (provider_instance, model_name) 或 (None, "")
        """
        requires = requires or ["text"]
        primary_capability = requires[0]
        models = self._models_config

        # 候选列表: (provider_name, model)
        candidates: list[tuple[str, str]] = []

        # 1. 查 agent_overrides（键必须与该 Agent 的 requires 对得上，否则覆盖永不生效）
        overrides = models.get("agent_overrides", {})
        if agent_name in overrides and isinstance(overrides[agent_name], dict):
            cap_map = overrides[agent_name]
            if primary_capability in cap_map:
                spec = cap_map[primary_capability]
                p_name, model = self._parse_model_spec(spec)
                candidates.append((p_name, model))
            else:
                self._warn_override_mismatch(agent_name, cap_map, requires)

        # 2. 取 capabilities.default → alternatives → fallback
        caps = models.get("capabilities", {})
        if primary_capability in caps:
            cap_cfg = caps[primary_capability]
            spec = cap_cfg.get("default", "")
            if spec:
                p_name, model = self._parse_model_spec(spec)
                candidates.append((p_name, model))
            for alt in cap_cfg.get("alternatives", []):
                p_name, model = self._parse_model_spec(alt)
                candidates.append((p_name, model))
            for fb in cap_cfg.get("fallback", []):
                p_name, model = self._parse_model_spec(fb)
                candidates.append((p_name, model))

        # 3. 按顺序匹配：Provider 必须覆盖所有 requires 能力
        for provider_name, model in candidates:
            meta = self._meta.get(provider_name)
            if not meta or not meta.available:
                continue
            # 检查 Provider 的能力是否是 requires 的超集
            if not set(requires).issubset(set(meta.capabilities)):
                continue
            p = self._get_provider_for_capability(provider_name, primary_capability)
            if p:
                return (p, model)

        # 4. 最终兜底：Mock（满足所有能力声明）
        if "image" in requires:
            return (self._image.get("mock"), "mock")
        return (self._llm.get("mock"), "mock")

    def _warn_override_mismatch(self, agent_name: str, cap_map: dict, requires: list[str]) -> None:
        """覆盖键与 Agent 的 requires 不匹配 → 永远不生效（A41）

        实测：models.yaml 给「审查员」写了 `text: deepseek/...`，但审查员 requires=vision，
        覆盖被静默忽略，用户以为"改成便宜模型了"其实没改。这里留一条日志（同一组合只报一次），
        界面侧由 `/api/settings` 的 `agent_overrides_issues` 提示。
        """
        from src.core.logging_config import get_logger
        for capability in cap_map:
            if capability in requires:
                continue
            key = (agent_name, capability)
            if key in _WARNED_OVERRIDE_MISMATCH:
                continue
            _WARNED_OVERRIDE_MISMATCH.add(key)
            get_logger(__name__).warning(
                "agent_overrides 覆盖不生效：%s 需要 %s，但配置的是 %s",
                agent_name, "/".join(requires) or "（无）", capability)

    def _parse_model_spec(self, spec: str) -> tuple[str, str]:
        """解析 "openai/gpt-4o" → ("openai", "gpt-4o")"""
        if "/" in spec:
            return spec.split("/", 1)[0], spec.split("/", 1)[1]
        return spec, ""

    def _get_provider_for_capability(self, provider_name: str, capability: str):
        """根据能力获取对应的 Provider 实例"""
        if capability == "image":
            return self.get_image(provider_name)
        return self.get_llm(provider_name)

    def list_available(self) -> list[dict]:
        """列出所有可用 Provider"""
        return [
            {"name": m.name, "capabilities": m.capabilities}
            for m in self._meta.values() if m.available
        ]


# ── 全局单例 ──

_registry: Optional[ProviderRegistry] = None


def get_provider_registry() -> ProviderRegistry:
    global _registry
    if _registry is None:
        _registry = ProviderRegistry()
    return _registry


def reset_provider_registry() -> ProviderRegistry:
    """重建 Provider 注册表（设置页更新 API Key 后调用）"""
    global _registry
    _registry = ProviderRegistry()
    return _registry
