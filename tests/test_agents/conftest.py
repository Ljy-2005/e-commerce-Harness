"""Agent 单元测试共享 fixtures"""

import pytest
from src.providers.mock import MockLLMProvider, MockImageProvider
from src.agents.registry import AgentRegistry
from src.chat.session import SessionManager


@pytest.fixture
def mock_llm():
    return MockLLMProvider()


@pytest.fixture
def mock_image():
    return MockImageProvider()


@pytest.fixture
def empty_session():
    """空会话（无任何上下文）"""
    mgr = SessionManager()
    return mgr.create(
        product_images=[],
        product_info="测试商品",
        platform="taobao",
        category_hint="保健品",
        collaboration_mode="serial",
    )


@pytest.fixture
def session_with_analysis():
    """含分析结果的会话"""
    mgr = SessionManager()
    session = mgr.create(
        product_images=["fake_base64_data_for_test"],
        product_info="测试保健品 - 维生素C片",
        platform="taobao",
        category_hint="保健品",
        collaboration_mode="serial",
    )
    session["artifacts"]["analysis"] = {
        "category": "保健品",
        "confidence_score": 85,
        "features": ["维生素C", "增强免疫力", "咀嚼片"],
        "target_audience": {"age": "25-50", "gender": "不限"},
        "style_constraints": {"colors": ["白色", "绿色"], "mood": "健康活力"},
    }
    return session


@pytest.fixture
def session_with_prompts():
    """含提示词的会话"""
    mgr = SessionManager()
    session = mgr.create(
        product_images=["fake_b64"],
        platform="taobao",
        collaboration_mode="serial",
    )
    session["artifacts"]["prompts"] = {
        "main_image": {"prompt": "E-commerce product on white background, studio lighting", "platform": "taobao"},
        "scene_images": [
            {"prompt": "Product in natural light kitchen scene", "platform": "taobao"},
        ],
    }
    return session
