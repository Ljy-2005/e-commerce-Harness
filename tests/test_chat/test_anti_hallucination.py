"""防幻觉 + 上下文压缩验证测试"""

import pytest
from src.agents.registry import AgentRegistry
from src.chat.session import SessionManager
from src.chat.engine import ChatEngine
from src.providers import get_provider_registry
from src.harness.context_manager import ContextManager


@pytest.fixture
async def setup():
    provider_registry = get_provider_registry()
    agent_registry = AgentRegistry()
    await agent_registry.load_from_config(provider_registry)
    session_mgr = SessionManager()
    engine = ChatEngine(registry=agent_registry, session_manager=session_mgr)
    return engine, session_mgr, agent_registry


class TestConfidenceThreshold:
    @pytest.mark.asyncio
    async def test_low_confidence_triggers_warning(self, setup, sample_image_base64, monkeypatch):
        """低置信度分析结果应触发警告

        （演示数据现在的中性置信度是 0，所以这里显式压到 30 来钉住阈值逻辑本身）

        断言**产物里的持久标记**：会话变长后上下文压缩会把中段的群聊消息换成摘要
        （套图从 3 张变 5 张后实测触发），届时消息级断言会随压缩消失；
        而 `artifacts.analysis._low_confidence_warning` 是界面真正读取的信号。
        """
        from src.providers import mock as mock_mod
        monkeypatch.setitem(mock_mod.MOCK_ANALYSIS, "confidence_score", 30.0)
        engine, session_mgr, registry = setup
        session = session_mgr.create(
            product_images=[sample_image_base64],
            platform="taobao",
        )
        result = await engine.run(session)
        assert result["artifacts"]["analysis"].get("_low_confidence_warning") is True
        messages = result.get("messages", [])
        warnings = [m for m in messages if m.get("content", {}).get("hallucination_risk")]
        for warning in warnings:
            assert "置信度" in warning["content"]["hallucination_risk"]

    @pytest.mark.asyncio
    async def test_high_confidence_no_warning(self, setup, sample_image_base64, monkeypatch):
        """高置信度不触发警告"""
        from src.providers import mock as mock_mod
        monkeypatch.setitem(mock_mod.MOCK_ANALYSIS, "confidence_score", 92.0)
        engine, session_mgr, registry = setup
        session = session_mgr.create(
            product_images=[sample_image_base64],
            platform="taobao",
        )
        result = await engine.run(session)
        assert result["status"] == "completed"
        assert not result["artifacts"]["analysis"].get("_low_confidence_warning")
        warnings = [m for m in result.get("messages", [])
                    if m.get("content", {}).get("hallucination_risk")]
        assert warnings == []


class TestCrossValidation:
    @pytest.mark.asyncio
    async def test_engine_has_cross_validation_logic(self, setup, sample_image_base64):
        """交叉验证逻辑存在且不崩"""
        engine, session_mgr, registry = setup
        session = session_mgr.create(
            product_images=[sample_image_base64],
            platform="taobao",
        )
        result = await engine.run(session)
        # 验证流程正常完成
        assert "review" in result["artifacts"]
        assert result["status"] == "completed"

    @pytest.mark.asyncio
    async def test_analysis_and_review_both_present(self, setup, sample_image_base64):
        """分析和审查结果同时存在，可交叉比对"""
        engine, session_mgr, registry = setup
        session = session_mgr.create(
            product_images=[sample_image_base64],
            platform="taobao",
        )
        result = await engine.run(session)
        analysis = result["artifacts"].get("analysis", {})
        review = result["artifacts"].get("review", {})
        assert "category" in analysis
        assert "overall_score" in review


class TestBase64Trimming:
    def test_strip_base64_from_content(self):
        """裁剪 base64 数据"""
        cm = ContextManager()
        msg = {
            "role": "agent",
            "sender": "生图员",
            "content": {
                "images": [
                    {"prompt_name": "v1", "base64_data": "a" * 5000, "image_url": "http://..."},
                    {"prompt_name": "v2", "base64_data": "b" * 3000},
                ]
            },
        }
        stripped = cm._strip_base64(msg)
        images = stripped["content"]["images"]
        assert "[已裁剪" in images[0]["base64_data"]
        assert "[已裁剪" in images[0]["image_url"]
        assert images[0]["prompt_name"] == "v1"  # 文本字段保留

    def test_compact_trims_base64_in_tail(self):
        """压缩后尾部的 base64 被裁剪"""
        cm = ContextManager()
        messages = [
            {"role": "system", "content": "system prompt"},
            {"role": "agent", "sender": "A", "content": {"text": "a"}},
            {"role": "agent", "sender": "B", "content": {"text": "b"}},
            {"role": "agent", "sender": "C", "content": {"images": [{"base64_data": "x"*1000}]}},
            {"role": "agent", "sender": "D", "content": {"images": [{"base64_data": "y"*1000}]}},
        ]
        compacted, summary = cm.compact(messages, keep_first=1, keep_last=2)
        assert len(compacted) < len(messages)
        # tail 中的 base64 应被裁剪
        for m in compacted[-2:]:
            content = m.get("content", {})
            if isinstance(content, dict) and "images" in content:
                for img in content["images"]:
                    if isinstance(img, dict) and "base64_data" in img:
                        assert "[已裁剪" in img["base64_data"]

    def test_non_dict_content_untouched(self):
        """非 dict 型 content 原样保留"""
        cm = ContextManager()
        msg = {"role": "user", "content": "plain text message"}
        stripped = cm._strip_base64(msg)
        assert stripped["content"] == "plain text message"


class TestNoBoundaryViolations:
    """验证无越界行为"""

    @pytest.mark.asyncio
    async def test_session_isolation(self, setup, sample_image_base64):
        """不同会话不互相泄露数据"""
        engine, session_mgr, registry = setup

        s1 = session_mgr.create(product_images=[sample_image_base64], platform="taobao")
        s2 = session_mgr.create(product_images=[sample_image_base64], platform="amazon")

        r1 = await engine.run(s1)
        r2 = await engine.run(s2)

        assert r1["session_id"] != r2["session_id"]
        # 验证消息中的 session_id 不交叉
        r1_ids = {m.get("id") for m in r1.get("messages", [])}
        r2_ids = {m.get("id") for m in r2.get("messages", [])}
        assert r1_ids.isdisjoint(r2_ids), "不同会话的消息 ID 不应重叠"

    @pytest.mark.asyncio
    async def test_no_raw_api_key_leak_in_messages(self, setup, sample_image_base64):
        """消息中不应泄露 API Key"""
        engine, session_mgr, registry = setup
        session = session_mgr.create(product_images=[sample_image_base64])
        result = await engine.run(session)

        all_text = str(result.get("messages", []))
        assert "sk-" not in all_text
        assert "sk-ant-" not in all_text

    def test_config_never_exposes_keys(self):
        """配置文件不应包含硬编码 Key"""
        from src.core.config import load_default_config, load_models_config
        default = str(load_default_config())
        models = str(load_models_config())
        assert "sk-" not in default
        assert "sk-" not in models
