"""商品身份卡在引擎里的合并规则

实测踩坑（本轮）：商品分析员给出带溯源的身份卡后，**品类专项分析员**的结果也会合并进
`artifacts["analysis"]`，它带的是一份没有 `status`/`source` 的裸 `product_identity`
—— 合并进来就把身份卡"降级"了（`status` 键消失，下游无法判断能不能出现品牌文字）。

规则：身份卡只由商品分析员（vision）或用户确认产生，其他 Agent 不得覆盖。
"""

import pytest

from src.chat.engine import ChatEngine, _has_identity_card
from src.chat.session import SessionManager

IDENTITY = {
    "brand": "DEFOEBUENA®", "product_name": "金裝強力肝迅康", "spec": "60's",
    "certifications": [], "package_form": "", "confidence": 0.93, "evidence": "",
    "source": "vision", "derived_from": [], "status": "confirmed", "missing": [],
    "visible_text": {"lines": [], "language": "zh-Hant", "has_illegible": False},
}


@pytest.fixture
def engine():
    return ChatEngine(registry=None, session_manager=SessionManager())


@pytest.fixture
def session():
    session = SessionManager().create(product_images=["x"], platform="taobao")
    session["artifacts"]["analysis"] = {"category": "保健品",
                                        "product_identity": dict(IDENTITY),
                                        "identity_status": "confirmed"}
    return session


class TestIdentityProtection:
    def test_has_identity_card_requires_status(self):
        assert _has_identity_card({"product_identity": {"status": "confirmed"}})
        assert not _has_identity_card({"product_identity": {"brand": "X"}})
        assert not _has_identity_card({})
        assert not _has_identity_card(None)

    async def test_category_specialist_cannot_overwrite_identity(self, engine, session):
        await engine._update_artifacts(session, "品类专项分析员", {
            "category": "保健品",
            "product_identity": {"brand": "", "product_name": "", "source": "mock"},
            "ingredients": ["维生素C"],          # 其他维度照常合并
        })
        analysis = session["artifacts"]["analysis"]
        assert analysis["product_identity"]["brand"] == "DEFOEBUENA®"
        assert analysis["product_identity"]["status"] == "confirmed"
        assert analysis["identity_status"] == "confirmed"
        assert analysis["ingredients"] == ["维生素C"], "非身份字段仍应正常合并"

    async def test_first_identity_wins_when_none_exists(self, engine):
        session = SessionManager().create(product_images=["x"], platform="taobao")
        await engine._update_artifacts(session, "商品分析员", {
            "category": "保健品", "product_identity": dict(IDENTITY),
            "identity_status": "confirmed",
        })
        assert session["artifacts"]["analysis"]["product_identity"]["status"] == "confirmed"

    async def test_failed_result_does_not_touch_analysis(self, engine, session):
        await engine._update_artifacts(session, "品类专项分析员", {"error": "429"})
        assert session["artifacts"]["analysis"]["identity_status"] == "confirmed"

    async def test_category_specialist_does_not_clear_low_confidence_warning(self, engine, session):
        """低置信度告警由分析员产生：品类专项分析员只是补充维度，不该把它清掉

        （实测：它合并 analysis 时会把 `_low_confidence_warning` 一并 pop 掉，
        界面因此完全不提示"分析置信度偏低"）
        """
        session["artifacts"]["analysis"]["_low_confidence_warning"] = True
        await engine._update_artifacts(session, "品类专项分析员",
                                       {"marketing_angles": {"selling_points": ["演示卖点"]}})
        assert session["artifacts"]["analysis"]["_low_confidence_warning"] is True

    async def test_analyst_rerun_clears_stale_warning(self, engine, session):
        """分析员本人重跑且置信度正常 → 陈旧告警应被清掉"""
        session["artifacts"]["analysis"]["_low_confidence_warning"] = True
        await engine._update_artifacts(session, "商品分析员",
                                       {"category": "保健品", "confidence_score": 90})
        assert "_low_confidence_warning" not in session["artifacts"]["analysis"]
