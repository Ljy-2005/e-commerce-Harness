"""ChatEngine 端到端测试（Mock Mode）"""

import pytest

from src.core.state import RunStatus
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

    # 验证分析结果：演示数据是中性的（不含具体商品事实），但必须被显式标记
    analysis = artifacts["analysis"]
    assert analysis.get("is_mock") is True
    assert analysis["product_identity"]["status"] == "uncertain"

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


class _StubReviewer:
    """把审查员换成受控替身（gate 逻辑测试）"""

    provider = None

    def __init__(self, result):
        self._result = dict(result)

    async def execute(self, task_brief, session, **kwargs):
        return dict(self._result)

    def _get_model(self):
        return "stub-reviewer"


async def _engine_with_reviewer(reviewer_result):
    provider_registry = get_provider_registry()
    registry = AgentRegistry()
    await registry.load_from_config(provider_registry)
    registry._agents["审查员"] = _StubReviewer(reviewer_result)
    engine = ChatEngine(registry=registry, session_manager=SessionManager())
    session = SessionManager().create(product_images=["x"], platform="taobao",
                                      category_hint="保健品")
    return engine, session


def _hitl_message(session) -> dict:
    for msg in session.get("messages", []):
        content = msg.get("content")
        if isinstance(content, dict) and content.get("hitl") == "human_review_needed":
            return content
    return {}


class TestReviewGate:
    """A33/A37：审查门禁不得把"无法解析/未完成"当成通过

    实测事故：审查员把结论写在 ```json 围栏里 → 解析失败 → `verdict` 丢失 →
    `result.get("verdict", "pass")` 默认放行，既不重试也不转人工，会话继续跑；
    另一次 `overall_score=None` 被拼成"审查评分 None/100"。
    """

    @pytest.mark.asyncio
    async def test_unparseable_review_does_not_pass(self):
        result = {"text": "```json\n{\"verdict\": \"retry\"}\n```",
                  "_output_issues": ["审查员 缺少必要字段: overall_score"]}
        engine, session = await _engine_with_reviewer(result)

        out = await engine.run(session, start_index=5)

        assert out["status"] != RunStatus.COMPLETED.value, "解析失败的审查不得当作通过"
        assert out["status"] == "waiting_human"
        assert out["error_history"], "必须留下失败原因"
        assert "verdict" in out["error_history"][-1]["error"] or "解析" in out["error_history"][-1]["error"]

    @pytest.mark.asyncio
    async def test_reviewer_error_blocks_instead_of_passing(self):
        engine, session = await _engine_with_reviewer(
            {"error": "NO_IMAGE_ACCESSIBLE: 未找到任何可用的生成图"})

        out = await engine.run(session, start_index=5)

        assert out["status"] == "waiting_human"
        assert any("NO_IMAGE_ACCESSIBLE" in e["error"] for e in out["error_history"])

    @pytest.mark.asyncio
    async def test_none_score_does_not_crash_or_print_none(self):
        engine, session = await _engine_with_reviewer({
            "overall_score": None, "verdict": "retry", "needs_human_review": False,
            "dimension_scores": {"texture": None}, "top_issues": ["无法核验"],
        })

        out = await engine.run(session, start_index=5)

        assert out["status"] == "waiting_human"
        message = _hitl_message(out).get("message", "")
        assert "None" not in message and "nan" not in message.lower(), message

    @pytest.mark.asyncio
    async def test_valid_pass_still_completes(self):
        """不要矫枉过正：正常 pass 必须照常放行"""
        engine, session = await _engine_with_reviewer({
            "overall_score": 88, "verdict": "pass", "needs_human_review": False,
            "dimension_scores": {"texture": 88},
        })

        out = await engine.run(session, start_index=5)

        assert out["status"] == RunStatus.COMPLETED.value


class TestMultiVariantReviewGate:
    """A43：审查员返回逐变体结果时必须汇总后判定（真实会话实测）

    真实会话里审查员给了很扎实的逐张评分（`{"results": [...]}`），但顶层没有
    `overall_score`/`verdict` → 门禁判为"无法解析"转人工；内容本身是有效的，
    缺的只是汇总。另：`verdict="fail"` 也不该被静默放过。
    """

    CAPTURED = {
        "results": [
            {"variant": "variant_1", "overall_score": 62.4, "verdict": "fail",
             "dimension_scores": {"texture": 74, "product_fidelity": 22},
             "top_issues": ["品牌英文被臆造为『NUTRIVA®』"], "needs_human_review": True},
            {"variant": "variant_2", "overall_score": 64.0, "verdict": "fail",
             "dimension_scores": {"texture": 70, "product_fidelity": 42},
             "top_issues": ["® 符号脱离品牌名单独漂浮"], "needs_human_review": False},
        ]
    }

    @pytest.mark.asyncio
    async def test_multi_variant_review_is_aggregated(self):
        engine, session = await _engine_with_reviewer(self.CAPTURED)

        out = await engine.run(session, start_index=5)

        review = out["artifacts"].get("review") or {}
        assert review.get("verdict") == "fail", "最严重判定必须冒泡到顶层"
        assert review.get("overall_score") == 63.2
        assert not any("无法解析" in e["error"] for e in out["error_history"]), \
            "汇总成功后不应再报『结果无法解析』"

    @pytest.mark.asyncio
    async def test_fail_verdict_goes_to_human_not_silent_continue(self):
        engine, session = await _engine_with_reviewer({
            "overall_score": 42, "verdict": "fail", "needs_human_review": False,
            "dimension_scores": {"product_fidelity": 20},
        })

        out = await engine.run(session, start_index=5)

        assert out["status"] == "waiting_human", "判定 fail 不得静默继续跑完"
        assert out["status"] != RunStatus.COMPLETED.value
