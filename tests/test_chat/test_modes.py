"""多协作模式测试"""

import pytest
from src.agents.registry import AgentRegistry
from src.chat.session import SessionManager
from src.chat.engine import ChatEngine
from src.providers import get_provider_registry


@pytest.fixture
async def setup():
    provider_registry = get_provider_registry()
    agent_registry = AgentRegistry()
    await agent_registry.load_from_config(provider_registry)
    session_mgr = SessionManager()
    engine = ChatEngine(registry=agent_registry, session_manager=session_mgr)
    return engine, session_mgr


@pytest.mark.asyncio
async def test_serial_mode_completes(setup, sample_image_base64):
    """串行模式正常完成"""
    engine, session_mgr = setup
    session = session_mgr.create(
        product_images=[sample_image_base64],
        platform="taobao",
        collaboration_mode="serial",
    )
    result = await engine.run(session)
    assert result["status"] == "completed"
    assert "analysis" in result["artifacts"]
    assert "review" in result["artifacts"]


@pytest.mark.asyncio
async def test_ab_generate_mode_completes(setup, sample_image_base64):
    """A/B 生成模式正常完成"""
    engine, session_mgr = setup
    session = session_mgr.create(
        product_images=[sample_image_base64],
        platform="taobao",
        collaboration_mode="ab_generate",
    )
    result = await engine.run(session)
    assert result["status"] == "completed"
    assert "review" in result["artifacts"]


@pytest.mark.asyncio
async def test_debate_mode_completes(setup, sample_image_base64):
    """辩论模式正常完成"""
    engine, session_mgr = setup
    session = session_mgr.create(
        product_images=[sample_image_base64],
        platform="taobao",
        collaboration_mode="debate",
    )
    result = await engine.run(session)
    assert result["status"] in ("completed", "failed")
    review = result["artifacts"].get("review", {})
    if review:
        # 辩论模式：两次审查聚合
        assert "overall_score" in review


@pytest.mark.asyncio
async def test_vote_mode_completes(setup, sample_image_base64):
    """投票模式正常完成"""
    engine, session_mgr = setup
    session = session_mgr.create(
        product_images=[sample_image_base64],
        platform="taobao",
        collaboration_mode="vote",
    )
    result = await engine.run(session)
    assert result["status"] in ("completed", "failed")
    review = result["artifacts"].get("review", {})
    if review:
        assert "overall_score" in review


@pytest.mark.asyncio
async def test_mode_set_in_coordinator(setup):
    """Coordinator 正确设置模式"""
    engine, session_mgr = setup
    from src.agents.registry import get_agent_registry
    provider_registry = get_provider_registry()
    agent_registry = get_agent_registry()
    # 确保已加载
    await agent_registry.load_from_config(provider_registry)

    coordinator = agent_registry.get("中心决策者")
    assert coordinator is not None
    coordinator.set_mode("debate")
    assert coordinator._current_mode == "debate"
    coordinator.reset()
    assert coordinator._current_mode == "serial"  # reset 后回默认
    coordinator.set_mode("vote")
    assert coordinator._current_mode == "vote"
