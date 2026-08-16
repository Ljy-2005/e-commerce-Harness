"""ProviderRegistry — 自动检测环境变量，解析能力→模型映射"""

import os
from typing import Optional

from src.providers.base import BaseLLMProvider, BaseImageProvider
from src.providers.mock import MockLLMProvider, MockImageProvider
from src.core.config import load_models_config, is_mock_mode


class _ProviderMeta:
    """Provider 元信息"""
    def __init__(self, name: str, available: bool, capabilities: list[str]):
        self.name = name
        self.available = available
        self.capabilities = capabilities


class ProviderRegistry:
    """Provider 注册中心

    负责：
    1. 自动检测环境变量，标记 Provider 可用性
    2. 根据 config/models.yaml 解析能力→模型映射
    3. 为 Agent 提供 resolve_provider(requires) 方法
    """

    def __init__(self):
        self._llm: dict[str, BaseLLMProvider] = {}
        self._image: dict[str, BaseImageProvider] = {}
        self._meta: dict[str, _ProviderMeta] = {}
        self._models_config = load_models_config()

        # 注册 Mock（始终可用）
        self._register_llm("mock", MockLLMProvider())
        self._register_image("mock", MockImageProvider())

        # 检测 OpenAI
        if os.getenv("OPENAI_API_KEY"):
            self._meta["openai"] = _ProviderMeta("openai", True, ["vision", "text", "image"])

        # 检测 DeepSeek
        if os.getenv("DEEPSEEK_API_KEY"):
            self._meta["deepseek"] = _ProviderMeta("deepseek", True, ["text"])

        # 检测 Anthropic
        if os.getenv("ANTHROPIC_API_KEY"):
            self._meta["anthropic"] = _ProviderMeta("anthropic", True, ["vision", "text"])

        # 检测 即梦AI (Seedream)
        if os.getenv("SEEDREAM_API_KEY") or (os.getenv("VOLCANO_ACCESS_KEY") and os.getenv("VOLCANO_SECRET_KEY")):
            self._meta["seedream"] = _ProviderMeta("seedream", True, ["image"])

        # 检测 通义千问 (Qwen)
        if os.getenv("DASHSCOPE_API_KEY"):
            self._meta["qwen"] = _ProviderMeta("qwen", True, ["vision", "text"])

        # 检测 FLUX
        if os.getenv("BFL_API_KEY") or os.getenv("FAL_KEY") or os.getenv("REPLICATE_API_KEY"):
            self._meta["flux"] = _ProviderMeta("flux", True, ["image"])

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

    def get_llm(self, provider_name: str, model_name: str = "") -> Optional[BaseLLMProvider]:
        """获取 LLM Provider 实例（延迟实例化真实 Provider）"""
        if provider_name in self._llm:
            return self._llm[provider_name]

        # 延迟实例化
        if provider_name == "openai" and "openai" not in self._llm:
            try:
                from src.providers.openai import OpenAILLMProvider
                p = OpenAILLMProvider()
                self._llm["openai"] = p
                return p
            except Exception:
                if provider_name in self._meta:
                    self._meta[provider_name].available = False
                return None

        if provider_name == "deepseek" and "deepseek" not in self._llm:
            try:
                from src.providers.deepseek import DeepSeekLLMProvider
                p = DeepSeekLLMProvider()
                self._llm["deepseek"] = p
                return p
            except Exception:
                if provider_name in self._meta:
                    self._meta[provider_name].available = False
                return None

        if provider_name == "anthropic" and "anthropic" not in self._llm:
            try:
                from src.providers.anthropic import AnthropicLLMProvider
                p = AnthropicLLMProvider()
                self._llm["anthropic"] = p
                return p
            except Exception:
                if provider_name in self._meta:
                    self._meta[provider_name].available = False
                return None

        if provider_name == "qwen" and "qwen" not in self._llm:
            try:
                from src.providers.qwen import QwenLLMProvider
                p = QwenLLMProvider()
                self._llm["qwen"] = p
                return p
            except Exception:
                if provider_name in self._meta:
                    self._meta[provider_name].available = False
                return None

        return None

    def get_image(self, provider_name: str, model_name: str = "") -> Optional[BaseImageProvider]:
        """获取 Image Provider 实例"""
        if provider_name in self._image:
            return self._image[provider_name]

        if provider_name == "openai" and "openai" not in self._image:
            try:
                from src.providers.openai import OpenAIImageProvider
                p = OpenAIImageProvider()
                self._image["openai"] = p
                return p
            except Exception:
                if provider_name in self._meta:
                    self._meta[provider_name].available = False
                return None

        if provider_name == "seedream" and "seedream" not in self._image:
            try:
                from src.providers.seedream import SeedreamImageProvider
                p = SeedreamImageProvider()
                self._image["seedream"] = p
                return p
            except Exception:
                if provider_name in self._meta:
                    self._meta[provider_name].available = False
                return None

        if provider_name == "flux" and "flux" not in self._image:
            try:
                from src.providers.flux import FluxImageProvider
                p = FluxImageProvider()
                self._image["flux"] = p
                return p
            except Exception:
                if provider_name in self._meta:
                    self._meta[provider_name].available = False
                return None

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

        # 1. 查 agent_overrides
        overrides = models.get("agent_overrides", {})
        if agent_name in overrides and primary_capability in overrides[agent_name]:
            spec = overrides[agent_name][primary_capability]
            p_name, model = self._parse_model_spec(spec)
            candidates.append((p_name, model))

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
