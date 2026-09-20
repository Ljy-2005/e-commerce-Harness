"""引擎层的商品身份门禁 + 出图后体检播报

用户反馈三点在这里闭环：
1. "根本没识别到我喂的图是什么品牌，商品名是什么都没强调或者提醒"
   → 分析完成后群聊**醒目播报**身份卡；未确认时（默认）暂停等确认，不确认就不出图；
2. "要文+图生图" → 出图后播报"图一（真实商品图）+ 生成图"的双条件结果，
   没有参考图时明确告知"属纯文生图、文字已虚化"；
3. "要一套可直接上传的套图" → 播报套图覆盖度，缺槽位要说清。
"""

import base64
import io

import pytest
from PIL import Image

import src.chat.engine as engine_mod
from src.chat.engine import ChatEngine, _inject_identity_card
from src.chat.session import SessionManager


def _b64(color=(200, 30, 30), size=(64, 64)) -> str:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


CONFIRMED = {"brand": "DEFOEBUENA®", "product_name": "金裝強力肝迅康", "spec": "60's",
             "certifications": ["德國 GMP 優質產品"], "confidence": 0.93, "source": "vision",
             "status": "confirmed", "missing": [], "derived_from": [], "evidence": "",
             "visible_text": {"lines": []}}
UNCERTAIN = {**CONFIRMED, "brand": "", "product_name": "", "status": "uncertain",
             "source": "mock", "missing": ["品牌", "商品名"]}


@pytest.fixture
def engine():
    return ChatEngine(registry=None, session_manager=SessionManager())


def _session(with_image=True):
    session = SessionManager().create(
        product_images=[_b64()] if with_image else [], platform="taobao")
    return session


class TestIdentityGate:
    async def test_confirmed_announces_and_continues(self, engine):
        session = _session()
        halted = await engine._identity_gate(session, {"product_identity": dict(CONFIRMED)}, 1)
        assert halted is False
        assert session["status"] != "waiting_human"
        messages = [m for m in session["messages"] if "product_identity" in m["content"]]
        assert messages and messages[-1]["content"]["message"].startswith("✅")
        assert "DEFOEBUENA®" in messages[-1]["content"]["message"]
        assert session["artifacts"]["product_identity"]["status"] == "confirmed"

    async def test_uncertain_pauses_with_actionable_message(self, engine):
        session = _session()
        uncertain_vision = {**UNCERTAIN, "source": "vision"}
        halted = await engine._identity_gate(session, {"product_identity": uncertain_vision}, 1)
        assert halted is True
        assert session["status"] == "waiting_human"
        hitl = [m for m in session["messages"] if m["content"].get("hitl") == "identity_unconfirmed"]
        assert hitl, "未确认身份必须转人工，而不是继续往下出图"
        text = hitl[-1]["content"]["message"]
        assert "品牌" in text and ("approve" in text and "retry" in text)
        assert any(entry.get("kind") == "identity"
                   for entry in session.get("error_history", []))

    async def test_mock_source_warns_but_does_not_block(self, engine):
        """演示数据不拦流程（否则 Mock 模式每条会话都卡在"确认品牌"），但必须醒目标注"""
        session = _session()
        assert await engine._identity_gate(session, {"product_identity": dict(UNCERTAIN)}, 1) is False
        assert session["status"] != "waiting_human"
        messages = [m for m in session["messages"] if m["content"].get("identity_unconfirmed")]
        assert messages and "演示数据" in messages[-1]["content"]["message"]

    async def test_uncertain_respects_switch(self, engine, monkeypatch):
        """关掉开关就只提醒、不拦（识别失败但用户想先跑完）"""
        monkeypatch.setattr(engine_mod, "chat_settings",
                            lambda: {"require_identity_confirm": False})
        session = _session()
        uncertain_vision = {**UNCERTAIN, "source": "vision"}
        assert await engine._identity_gate(session, {"product_identity": uncertain_vision}, 1) is False
        assert session["status"] != "waiting_human"
        # 但仍然要醒目提醒
        assert any(m["content"].get("identity_unconfirmed") for m in session["messages"])

    async def test_missing_identity_block_treated_as_uncertain(self, engine):
        session = _session()
        assert await engine._identity_gate(session, {"category": "保健品"}, 1) is False
        assert session["artifacts"]["product_identity"]["status"] == "uncertain"
        assert any(m["content"].get("identity_unconfirmed") for m in session["messages"])


class TestIdentityInjection:
    def test_injected_for_prompt_agent(self):
        session = _session()
        session["artifacts"]["product_identity"] = dict(CONFIRMED)
        brief = _inject_identity_card(session, "生成提示词", "提示词生成员")
        assert "DEFOEBUENA®" in brief and "生成提示词" in brief
        assert brief.index("DEFOEBUENA®") < brief.index("生成提示词"), "身份卡应前置（权重最高）"

    def test_skipped_for_unrelated_agent(self):
        session = _session()
        session["artifacts"]["product_identity"] = dict(CONFIRMED)
        assert _inject_identity_card(session, "写个报告", "中心决策者") == "写个报告"

    def test_no_identity_no_change(self):
        assert _inject_identity_card(_session(), "brief", "提示词生成员") == "brief"

    def test_unconfirmed_still_injected(self):
        """未确认的身份卡也要注入 —— 它明确禁止出现任何品牌文字"""
        session = _session()
        session["artifacts"]["product_identity"] = dict(UNCERTAIN)
        brief = _inject_identity_card(session, "生成提示词", "提示词生成员")
        assert "未确认" in brief


class TestImageReports:
    async def test_quality_report_and_coverage_written(self, engine):
        session = _session()
        session["artifacts"]["prompts"] = {"set_plan": {"slots": [
            {"slot_id": "main_white", "prompt": "白底"},
            {"slot_id": "main_scene", "prompt": "场景"},
        ]}}
        result = {"images": [
            {"slot_id": "main_white", "base64_data": _b64(color=(255, 255, 255), size=(200, 200))},
        ]}
        await engine._attach_image_reports(session, result, 3)

        report = session["artifacts"]["quality_report"]
        assert report["count"] == 1
        assert session["artifacts"]["set_plan_coverage"]["missing_slots"] == ["main_scene"]
        assert result["images"][0]["quality"]["is_white_bg"] is True
        # 播报：缺槽位必须说出来（用户要的是"一整套"）
        msg = [m for m in session["messages"] if "set_plan_coverage" in m["content"]]
        assert msg and "套图不完整" in msg[-1]["content"]["message"]

    async def test_near_copy_is_reported(self, engine):
        """退化成"纯图生图"（复制原图没重绘）必须被识别并播报"""
        raw = _b64(color=(180, 180, 180), size=(256, 256))
        session = _session(with_image=False)
        session["task"]["product_images"] = [raw]
        result = {"images": [{"slot_id": "main_white", "base64_data": raw}]}
        await engine._attach_image_reports(session, result, 3)
        report = session["artifacts"]["quality_report"]
        assert report["near_copy"] == ["main_white"]
        msg = [m for m in session["messages"] if "set_plan_coverage" in m["content"]]
        assert "高度相似" in msg[-1]["content"]["message"]

    async def test_no_reference_is_flagged_as_text_only(self, engine):
        session = _session(with_image=False)
        result = {"images": [{"slot_id": "main_white",
                              "base64_data": _b64(color=(255, 255, 255), size=(120, 120))}]}
        await engine._attach_image_reports(session, result, 1)
        msg = [m for m in session["messages"] if "quality_report" in m["content"]]
        assert "纯文生图" in msg[-1]["content"]["message"]

    async def test_inspection_failure_does_not_break_run(self, engine, monkeypatch):
        import src.harness.image_quality as quality_mod

        async def _boom(*args, **kwargs):
            raise RuntimeError("体检炸了")

        monkeypatch.setattr(quality_mod, "inspect_images", _boom)
        session = _session()
        result = {"images": [{"slot_id": "main_white", "base64_data": _b64()}]}
        await engine._attach_image_reports(session, result, 1)   # 不应抛异常
        assert "quality_report" not in session["artifacts"]
