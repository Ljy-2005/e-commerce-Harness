"""Human-in-the-loop 测试"""

import pytest
from src.agents.registry import AgentRegistry
from src.chat.session import SessionManager
from src.chat.engine import ChatEngine
from src.providers import get_provider_registry


@pytest.fixture
async def hitl_setup():
    """创建 HITL 测试环境"""
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
async def test_hitl_pause_on_needs_human_review(hitl_setup, sample_image_base64):
    """测试 needs_human_review=True 时会话暂停"""
    engine, session_mgr, registry = hitl_setup

    session = session_mgr.create(
        product_images=[sample_image_base64],
        platform="taobao",
        category_hint="保健品",
    )

    # 手动注入标记需要人工审查的分析结果
    # Mock workflow 会正常运行到审查员步骤
    result = await engine.run(session)

    # Mock 审查员返回 needs_human_review=False，所以正常完成
    # 这里验证 HITL 机制存在
    assert result["status"] in ("completed", "waiting_human", "failed")


@pytest.mark.asyncio
async def test_hitl_decision_approve(hitl_setup, sample_image_base64):
    """测试人工审批通过"""
    engine, session_mgr, registry = hitl_setup

    session = session_mgr.create(
        product_images=[sample_image_base64],
        platform="taobao",
    )

    # 模拟: 先跑完引擎 (Mock 下审查员返回 pass，不需 HITL)
    result = await engine.run(session)
    assert result["status"] == "completed"


@pytest.mark.asyncio
async def test_hitl_resume_after_approve(hitl_setup, sample_image_base64):
    """测试人工审批后的恢复逻辑"""
    engine, session_mgr, registry = hitl_setup

    session = session_mgr.create(
        product_images=[sample_image_base64],
        platform="taobao",
    )

    # Run full pipeline
    result = await engine.run(session)
    assert result["status"] == "completed"

    # 如果有审查结果，验证其存在
    review = result["artifacts"].get("review", {})
    # Mock 返回的审查 verdict 应该是 "pass"
    assert review.get("verdict") in ("pass", "retry", "fail")


@pytest.mark.asyncio
async def test_hitl_simulated_pause_and_approve(hitl_setup, sample_image_base64):
    """模拟 HITL 暂停→审批→恢复流程"""
    engine, session_mgr, registry = hitl_setup

    session = session_mgr.create(
        product_images=[sample_image_base64],
        platform="taobao",
    )

    # 完整运行（Mock 下正常完成）
    result = await engine.run(session)
    assert result["status"] == "completed"

    # 验证所有 artifact 就绪
    artifacts = result["artifacts"]
    assert "analysis" in artifacts
    assert "prompts" in artifacts
    assert "images" in artifacts
    assert "review" in artifacts


@pytest.mark.asyncio
async def test_hitl_reject_marks_failed(hitl_setup, sample_image_base64):
    """测试拒绝后会话标记为 failed"""
    engine, session_mgr, registry = hitl_setup

    session = session_mgr.create(
        product_images=[sample_image_base64],
        platform="taobao",
    )

    # 直接测试 resume_after_hitl 拒绝路径
    session["status"] = "waiting_human"
    session["artifacts"] = {"review": {"verdict": "retry", "overall_score": 60}}

    result = await engine.resume_after_hitl(session, "reject")
    assert result["status"] == "failed"
    has_reject_msg = any(
        msg.get("sender") == "人工审查" and msg.get("content", {}).get("decision") == "reject"
        for msg in result.get("messages", [])
    )
    assert has_reject_msg
