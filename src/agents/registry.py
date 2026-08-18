"""AgentRegistry — 扫描 config/agents/，自动注册 Agent"""

from src.core.config import load_agent_config, list_agent_configs
from src.core.models import AgentMeta


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

    def get_meta(self, name: str) -> AgentMeta | None:
        return self._meta.get(name)

    def list_all(self) -> list[AgentMeta]:
        return list(self._meta.values())

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
            )

            # 解析 Provider
            provider, model = provider_registry.resolve(meta.requires, meta.name)

            # 创建 Agent 实例
            agent = self._create_agent(meta, provider, model)
            if agent:
                self.register(agent, meta)

    def _create_agent(self, meta: AgentMeta, provider, model: str) -> "BaseAgent | None":
        """根据 AgentMeta 创建对应的 Agent 实例"""
        from src.agents.coordinator import CoordinatorAgent
        from src.agents.analyst import ProductAnalystAgent
        from src.agents.category import CategorySpecialistAgent
        from src.agents.prompt_gen import PromptGeneratorAgent
        from src.agents.image_gen import ImageGeneratorAgent
        from src.agents.reviewer import ReviewerAgent
        from src.agents.compliance import ComplianceAgent
        from src.agents.post_process import PostProcessAgent

        _MAP = {
            "中心决策者": CoordinatorAgent,
            "商品分析员": ProductAnalystAgent,
            "品类专项分析员": CategorySpecialistAgent,
            "提示词生成员": PromptGeneratorAgent,
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
