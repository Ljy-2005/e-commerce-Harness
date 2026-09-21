"""AgentRegistry — 扫描 config/agents/，自动注册 Agent"""

from typing import TYPE_CHECKING

from src.core.config import list_agent_configs, load_agent_config
from src.core.models import AgentMeta
from src.harness.retry import RetryConfig

# 仅用于类型注解（注解写成字符串，真正的 Agent 实现在 _create_agent 里按需导入，
# 避免注册中心 ↔ 各 Agent 的循环导入）
if TYPE_CHECKING:
    from src.agents.base import BaseAgent

# YAML `retry:` 里允许覆盖的字段（`backoff: exponential` 这类描述性字段忽略）
_RETRY_KEYS = ("max_retries", "base_delay_ms", "max_delay_ms", "backoff_multiplier", "jitter")


def _retry_config_from(raw, default: RetryConfig) -> RetryConfig:
    """把 YAML 的 retry 段合成为 RetryConfig（非法值静默保留默认，不影响启动）"""
    if not isinstance(raw, dict):
        return default
    overrides = {k: raw[k] for k in _RETRY_KEYS if k in raw}
    if not overrides:
        return default
    merged = {k: getattr(default, k) for k in _RETRY_KEYS}
    merged.update(overrides)
    try:
        return RetryConfig(**merged)
    except (TypeError, ValueError):
        return default


class AgentRegistry:
    """Agent 注册中心

    启动时扫描 config/agents/*.yaml → 创建 AgentMeta → 注册 Agent 实例
    """

    def __init__(self):
        self._agents: dict[str, "BaseAgent"] = {}   # name → instance
        self._meta: dict[str, AgentMeta] = {}        # name → meta

    def register(self, agent: "BaseAgent", meta: AgentMeta):
        self._agents[meta.name] = agent
        self._meta[meta.name] = meta

    def get(self, name: str) -> "BaseAgent | None":
        return self._agents.get(name)

    def get_invitable(self, name: str) -> "BaseAgent | None":
        """**群聊可邀请**的 Agent（名单外的名字返回 None）

        引擎的邀请路径用它：`invitable: false` 的后台 Agent（如「风格档案员」）
        即使被模型幻觉邀请，也不会被真正执行（**不产生任何 Provider 调用**）。
        """
        meta = self._meta.get(name)
        if meta is not None and not getattr(meta, "invitable", True):
            return None
        return self._agents.get(name)

    def get_meta(self, name: str) -> AgentMeta | None:
        return self._meta.get(name)

    def list_all(self, *, invitable_only: bool = False) -> list[AgentMeta]:
        """全部 Agent 元信息；`invitable_only=True` 只给群聊名单用"""
        metas = list(self._meta.values())
        if invitable_only:
            metas = [meta for meta in metas if getattr(meta, "invitable", True)]
        return metas

    def list_capable(self, capability: str) -> list[AgentMeta]:
        return [m for m in self._meta.values() if capability in m.requires]

    def list_agent_names(self) -> list[str]:
        return list(self._agents.keys())

    async def load_from_config(self, provider_registry):
        """从 config/agents/*.yaml 自动加载并注册所有 Agent"""
        self._agents.clear()
        self._meta.clear()
        agent_names = list_agent_configs()
        for name in agent_names:
            cfg = load_agent_config(name)
            if not cfg or not cfg.get("name"):
                continue

            meta = AgentMeta(
                name=cfg["name"],
                description=cfg.get("description", ""),
                version=cfg.get("version", "1.0.0"),
                requires=cfg.get("requires", []),
                timeout_ms=cfg.get("timeout_ms", 30_000),
                retry=cfg.get("retry", {}),
                prompt=cfg.get("prompt", ""),
                params=cfg.get("params", []),
                class_name=cfg.get("class", ""),
                # 必须显式取出：AgentMeta 是**逐字段构造**的（不传 cfg），
                # 漏掉这一行 YAML 里的 invitable: false 会被静默忽略 ——
                # 与 A34/A41 的坑同族（"YAML 的 timeout_ms/retry 只进 AgentMeta、
                # 没落到实例，改配置毫无效果"）。有专门的回归测试钉住。
                invitable=cfg.get("invitable", True),
            )

            # 解析 Provider
            provider, model = provider_registry.resolve(meta.requires, meta.name)

            # 创建 Agent 实例
            agent = self._create_agent(meta, provider, model)
            if agent:
                # YAML 的 timeout_ms / retry 必须落到实例上：此前只进 AgentMeta
                # （还会展示在 API 里），跑的一直是类属性 → 改配置毫无效果（A34/A41）
                if meta.timeout_ms and meta.timeout_ms > 0:
                    agent.timeout_ms = int(meta.timeout_ms)
                agent.retry_config = _retry_config_from(meta.retry, agent.retry_config)
                self.register(agent, meta)

    def _create_agent(self, meta: AgentMeta, provider, model: str) -> "BaseAgent | None":
        """根据 AgentMeta 创建对应的 Agent 实例"""
        from src.agents.analyst import ProductAnalystAgent
        from src.agents.category import CategorySpecialistAgent
        from src.agents.compliance import ComplianceAgent
        from src.agents.coordinator import CoordinatorAgent
        from src.agents.image_gen import ImageGeneratorAgent
        from src.agents.post_process import PostProcessAgent
        from src.agents.prompt_gen import PromptGeneratorAgent
        from src.agents.prompt_reviewer import PromptReviewerAgent
        from src.agents.reviewer import ReviewerAgent

        _MAP = {
            "中心决策者": CoordinatorAgent,
            "商品分析员": ProductAnalystAgent,
            "品类专项分析员": CategorySpecialistAgent,
            "提示词生成员": PromptGeneratorAgent,
            "提示词审核优化员": PromptReviewerAgent,
            "生图员": ImageGeneratorAgent,
            "审查员": ReviewerAgent,
            "合规审查员": ComplianceAgent,
            "图像后处理员": PostProcessAgent,
        }

        cls = _MAP.get(meta.name)
        # 插件化：内置映射未命中时按 config 的 class 字段动态导入（新增 Agent 只需 YAML，零核心改动）
        if cls is None and meta.class_name:
            import importlib
            try:
                mod_path, cls_name = meta.class_name.rsplit(".", 1)
                cls = getattr(importlib.import_module(mod_path), cls_name)
            except Exception:
                cls = None
        if cls is None:
            return None

        # 特殊处理：Coordinator 和 PostProcessor 不需要 Provider
        if meta.name == "中心决策者":
            agent = cls(provider=provider, registry=self)  # Coordinator 可在无 Provider 时 Mock 运行
        elif meta.name == "图像后处理员":
            agent = cls()
        else:
            agent = cls(provider=provider) if provider else None

        # 把 config/models.yaml 解析出的模型名注入 Agent（Agent 调用 Provider 时显式传递）
        if agent and model:
            agent.model_name = model
        return agent


# 全局单例
_registry: AgentRegistry | None = None


def get_agent_registry() -> AgentRegistry:
    global _registry
    if _registry is None:
        _registry = AgentRegistry()
    return _registry
