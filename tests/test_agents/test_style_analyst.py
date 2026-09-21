"""风格拆解员 Agent 单元测试（M3 插件化注册 + 一键风格复刻）"""

import pytest

from src.agents.registry import AgentRegistry
from src.agents.style_analyst import StyleAnalystAgent
from src.providers import get_provider_registry
from tests.test_workflow.conftest import _run_async


class TestStyleAnalyst:
    def test_mock_breakdown_structure(self):
        """Mock 拆解包含全部风格要素 + 可直接拼入提示词的文本"""
        agent = StyleAnalystAgent()
        result = agent._mock_breakdown()
        for key in ("composition", "lighting", "color_palette", "elements",
                    "style_tags", "style_prompt_text"):
            assert key in result, f"缺少 {key}"
        assert isinstance(result["color_palette"], list) and len(result["color_palette"]) >= 3
        assert len(result["style_tags"]) >= 2
        assert result["style_prompt_text"]  # 非空

    @pytest.mark.asyncio
    async def test_execute_mock(self, mock_llm):
        agent = StyleAnalystAgent(provider=mock_llm)
        session = {"tenant_id": "default", "task": {"reference_images": ["fake_b64"]},
                   "artifacts": {}, "turn_count": 0}
        result = await agent.execute("拆解参考图风格", session)
        assert "error" not in result
        assert result.get("style_prompt_text")


class TestPluginRegistration:
    """插件化注册：config 的 class 字段动态加载，零核心改动"""

    def test_dynamic_class_loaded(self):
        registry = AgentRegistry()
        _run_async(registry.load_from_config(get_provider_registry()))
        agent = registry.get("风格拆解员")
        assert agent is not None
        assert isinstance(agent, StyleAnalystAgent)
        meta = registry.get_meta("风格拆解员")
        assert meta.class_name == "src.agents.style_analyst.StyleAnalystAgent"

    def test_unknown_class_graceful(self):
        from src.core.models import AgentMeta
        registry = AgentRegistry()
        meta = AgentMeta(name="坏插件", description="x", requires=["text"],
                         class_name="nonexistent.module.Klass")
        assert registry._create_agent(meta, None, "") is None
