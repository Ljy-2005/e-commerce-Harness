"""ChatEngine 端到端测试（Mock Mode）"""

import pytest
from src.agents.registry import AgentRegistry
from src.chat.session import SessionManager
from src.chat.engine import ChatEngine
from src.providers import get_provider_registry


@pytest.fixture
async def engine():
    """创建 Mock 模式的 ChatEngine"""
    provider_registry = get_provider_registry()
    agent_registry = AgentRegistry()
    await agent_registry.load_from_config(provider_registry)

    session_mgr = SessionManager()
    engine = ChatEngine(
        registry=agent_registry,
        session_manager=session_mgr,
    )
    return engine, session_mgr, agent_registry


@pytest.mark.asyncio
async def test_engine_completes_pipeline(engine, sample_image_base64):
    """完整流水线：提交保健品图片 → 群聊完成 → 返回所有产出物"""
    eng, session_mgr, registry = engine

    session = session_mgr.create(
        product_images=[sample_image_base64],
        product_info="护肝胶囊，水飞蓟提取物",
        platform="taobao",
        category_hint="保健品",
    )

    result = await eng.run(session)

    assert result["status"] == "completed"
    assert result["turn_count"] > 0
    assert len(result["messages"]) > 0

    artifacts = result["artifacts"]
    assert "analysis" in artifacts
    assert "prompts" in artifacts
    assert "images" in artifacts
    assert "review" in artifacts
    assert "compliance" in artifacts

    # 验证分析结果
    analysis = artifacts["analysis"]
    assert analysis.get("category") == "保健品"

    # 验证审查结果
    review = artifacts["review"]
    assert review.get("verdict") == "pass"

    # 验证合规结果
    compliance = artifacts["compliance"]
    assert compliance.get("passed") is True


@pytest.mark.asyncio
async def test_engine_registers_all_agents(engine):
    """验证所有 Agent 都已注册"""
    eng, session_mgr, registry = engine

    agent_names = registry.list_agent_names()
    assert "中心决策者" in agent_names
    assert "商品分析员" in agent_names
    assert "品类专项分析员" in agent_names
    assert "提示词生成员" in agent_names
    assert "生图员" in agent_names
    assert "审查员" in agent_names
    assert "合规审查员" in agent_names
    assert "图像后处理员" in agent_names
    assert len(agent_names) >= 8


@pytest.mark.asyncio
async def test_engine_messages_have_correct_structure(engine, sample_image_base64):
    """验证消息格式正确"""
    eng, session_mgr, registry = engine

    session = session_mgr.create(
        product_images=[sample_image_base64],
        platform="taobao",
    )

    result = await eng.run(session)

    for msg in result["messages"]:
        assert "id" in msg
        assert "turn" in msg
        assert "role" in msg
        assert "sender" in msg
        assert "action" in msg
        assert "content" in msg
        assert msg["role"] in ("coordinator", "agent", "system")
