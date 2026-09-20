"""套图编排 —— 提示词生成员必须交付"一套图"，而不是"一张图的多个候选"

用户反馈："我看了提示词生成的内容，我发现生成的不是一套可直接上传的套图，而只是生成了
套图中的一张的多张选择，这不符合生产。"

实测确凿：`image_gen` 只取 `prompts.main_image.prompt` 连打 `variants` 次；`prompt_gen`
辛苦生成的 `scene_images`/`social_images` 全被丢掉。这里钉住新的数据契约与平台风格注入。
"""

import pytest

from src.agents.prompt_gen import PromptGeneratorAgent
from src.harness.set_plan import normalize_set_plan, set_plan_coverage, set_plan_summary


TAOBAO_PLAN = {
    "set_plan": {
        "platform": "taobao",
        "slots": [
            {"slot_id": "main_white", "role": "白底主图", "prompt": "白底正面平视",
             "composition": "主体居中 85%", "background": "#FFFFFF", "text_in_image": False,
             "uses_reference": ["upload_1"], "aspect": "1:1"},
            {"slot_id": "main_selling_point", "role": "核心卖点", "prompt": "留白版面待贴字",
             "text_in_image": False},
            {"slot_id": "main_scene", "role": "使用场景", "prompt": "办公桌场景", "text_in_image": False},
            {"slot_id": "main_detail", "role": "细节特写", "prompt": "瓶身特写", "text_in_image": False},
            {"slot_id": "main_spec", "role": "规格参数", "prompt": "规格版面留白", "text_in_image": False},
        ],
    },
    "main_image": {"prompt": "旧的单图提示词"},
}


class _FakeLLM:
    name = "fake-llm"
    capabilities = ["text"]

    def __init__(self, content=None, error=""):
        self.content = content if content is not None else TAOBAO_PLAN
        self.error = error
        self.seen: list = []

    async def chat(self, messages, **kwargs):
        self.seen = messages
        if self.error:
            return {"error": self.error}
        return {"content": self.content, "tokens_in": 10, "tokens_out": 5,
                "cost_usd": 0.001, "model_used": "fake"}


def _session(platform="taobao", identity=None):
    return {
        "session_id": "s1", "tenant_id": "default", "turn_count": 2,
        "task": {"platform": platform, "product_images": []},
        "artifacts": {
            "analysis": {"category": "保健品"},
            **({"product_identity": identity} if identity else {}),
        },
    }


class TestNormalizeSetPlan:
    def test_orders_by_platform_and_carries_contract(self):
        """顺序 = **平台规范顺序**（用户要的"第几张"必须人机一致），并补齐逐张契约"""
        plan = normalize_set_plan(TAOBAO_PLAN, platform="taobao")
        # 淘宝主图顺序 [white, selling_point, benefits, ingredients, audience] + 详情 [spec, usage, cert]
        # 不在规范里的 main_scene / main_detail 排在规范槽位之后
        assert [s["slot_id"] for s in plan["slots"]] == [
            "main_white", "main_selling_point", "main_spec", "main_scene", "main_detail"]
        assert [s["number"] for s in plan["slots"]] == [1, 2, 6, 9, 10]
        first = plan["slots"][0]
        assert first["role"] == "白底主图"
        assert first["background"] == "#FFFFFF"
        assert first["text_in_image"] is False
        assert first["uses_reference"] == ["upload_1"]
        # 逐张契约（来自 config/platforms.yaml 的槽位目录，模型改不动）
        assert first["intent"] and first["design"] and first["must"] and first["avoid"]
        assert first["bg_policy"] == "design_allowed"

    def test_text_in_image_defaults_to_false(self):
        """默认不生成文字：需要文字的版面一律后期贴图（规避臆造品牌）"""
        plan = normalize_set_plan({"set_plan": {"slots": [{"prompt": "x"}]}}, platform="taobao")
        assert plan["slots"][0]["text_in_image"] is False

    def test_missing_prompt_slot_is_skipped_with_note(self):
        payload = {"set_plan": {"slots": [{"slot_id": "main_white", "prompt": "ok"},
                                          {"slot_id": "main_scene", "prompt": "  "}]}}
        plan = normalize_set_plan(payload, platform="taobao")
        assert [s["slot_id"] for s in plan["slots"]] == ["main_white"]
        assert any("跳过" in note for note in plan["notes"])

    def test_duplicate_slot_ids_deduped(self):
        payload = {"set_plan": {"slots": [{"slot_id": "main_white", "prompt": "a"},
                                          {"slot_id": "main_white", "prompt": "b"}]}}
        plan = normalize_set_plan(payload, platform="taobao")
        assert len(plan["slots"]) == 1
        assert any("重复" in note for note in plan["notes"])

    def test_truncated_at_platform_limit(self):
        """平台主图上限（淘宝 5 张）之外的多余槽位必须截断 —— 生成多了也传不上去"""
        slots = [{"slot_id": f"s{i}", "prompt": f"p{i}"} for i in range(9)]
        plan = normalize_set_plan({"set_plan": {"slots": slots}}, platform="taobao")
        assert len(plan["slots"]) == 5
        assert any("上限" in note for note in plan["notes"])

    def test_missing_slot_id_filled_from_platform_order(self):
        """模型没给 slot_id 时按平台槽位顺序补位，保证落盘文件名可读"""
        plan = normalize_set_plan({"set_plan": {"slots": [{"prompt": "a"}, {"prompt": "b"}]}},
                                  platform="pinduoduo")
        assert [s["slot_id"] for s in plan["slots"]] == ["main_white", "main_selling_point"]

    def test_slot_override_wins(self):
        plan = normalize_set_plan({"set_plan": {"slots": [{"prompt": "a"}]}},
                                  platform="taobao", slot_override=["note_cover"])
        assert plan["slots"][0]["slot_id"] == "note_cover"

    def test_returns_none_without_set_plan(self):
        assert normalize_set_plan({"main_image": {"prompt": "x"}}, platform="taobao") is None
        assert normalize_set_plan({}, platform="taobao") is None
        assert normalize_set_plan(None, platform="taobao") is None

    def test_accepts_bare_list(self):
        plan = normalize_set_plan({"set_plan": [{"prompt": "a"}]}, platform="taobao")
        assert plan["slots"][0]["slot_id"] == "main_white"


class TestCoverage:
    def test_coverage_reports_missing_slots(self):
        plan = normalize_set_plan(TAOBAO_PLAN, platform="taobao")
        images = [{"slot_id": "main_white"}, {"slot_id": "main_scene"}]
        coverage = set_plan_coverage(plan, images)
        assert coverage["expected"] == 5 and coverage["produced"] == 2
        assert coverage["complete"] is False
        assert "main_detail" in coverage["missing_slots"]

    def test_coverage_complete(self):
        plan = normalize_set_plan(TAOBAO_PLAN, platform="taobao")
        images = [{"slot_id": s["slot_id"]} for s in plan["slots"]]
        assert set_plan_coverage(plan, images)["complete"] is True

    def test_summary_mentions_platform_and_slots(self):
        plan = normalize_set_plan(TAOBAO_PLAN, platform="taobao")
        text = set_plan_summary(plan)
        assert "5 张" in text and "main_white" in text


class TestPromptGenerator:
    async def test_outputs_set_plan_and_backfills_main_image(self):
        agent = PromptGeneratorAgent(provider=_FakeLLM())
        result = await agent.execute("生成提示词", _session())
        assert len(result["set_plan"]["slots"]) == 5
        # 向后兼容：老路径读 main_image.prompt
        assert result["main_image"]["prompt"] == "白底正面平视"
        assert result["platform"] == "taobao"
        assert "套图编排" in result["set_plan_summary"]

    async def test_pinduoduo_style_is_injected(self):
        """用户要求：拼多多的风格也要写上（2026-09-18 起改为**设计导向**的措辞）"""
        provider = _FakeLLM()
        agent = PromptGeneratorAgent(provider=provider)
        await agent.execute("生成提示词", _session(platform="拼多多"))
        user_msg = provider.seen[1]["content"]
        assert "拼多多" in user_msg
        assert "高明度大主体" in user_msg            # 设计调性
        assert "不添加任何文字" in user_msg or "不得出现任何文字" in user_msg
        assert "纯白" in user_msg
        # 逐张约束：编号槽位表 + 每张契约都要进模型视野
        assert "第1张" in user_msg and "设计要点" in user_msg
        assert "视觉设计方向" in user_msg
        assert "detail_style" not in user_msg        # 字段名不外泄，渲染成"版式风格"

    async def test_unknown_platform_is_flagged(self):
        provider = _FakeLLM()
        agent = PromptGeneratorAgent(provider=provider)
        await agent.execute("生成提示词", _session(platform="temu"))
        assert "未登记" in provider.seen[1]["content"]

    async def test_identity_card_is_injected(self):
        identity = {"status": "confirmed", "source": "vision", "brand": "DEFOEBUENA®",
                    "product_name": "金裝強力肝迅康", "spec": "60's", "certifications": [],
                    "confidence": 0.9, "evidence": "", "visible_text": {"lines": []},
                    "derived_from": [], "missing": []}
        provider = _FakeLLM()
        agent = PromptGeneratorAgent(provider=provider)
        await agent.execute("生成提示词", _session(identity=identity))
        user_msg = provider.seen[1]["content"]
        assert "DEFOEBUENA®" in user_msg and "金裝強力肝迅康" in user_msg
        assert "逐字" in user_msg

    async def test_unconfirmed_identity_forbids_brand_text(self):
        identity = {"status": "uncertain", "source": "vision", "brand": "",
                    "product_name": "", "certifications": [], "confidence": 0.0,
                    "evidence": "", "visible_text": {"lines": []}, "derived_from": [],
                    "missing": ["品牌", "商品名"]}
        provider = _FakeLLM()
        agent = PromptGeneratorAgent(provider=provider)
        await agent.execute("生成提示词", _session(identity=identity))
        user_msg = provider.seen[1]["content"]
        assert "未确认" in user_msg and "虚化" in user_msg

    async def test_missing_set_plan_is_flagged_not_silent(self):
        """模型没给套图编排时必须显式提示（否则又变成"一张图的多个候选"而无人察觉）"""
        agent = PromptGeneratorAgent(provider=_FakeLLM({"main_image": {"prompt": "单图"}}))
        result = await agent.execute("生成提示词", _session())
        assert "set_plan_note" in result
        assert "set_plan" not in result

    async def test_provider_error_propagates(self):
        agent = PromptGeneratorAgent(provider=_FakeLLM(error="429"))
        result = await agent.execute("生成提示词", _session())
        assert "error" in result


class TestPromptTemplate:
    def test_template_requires_set_plan(self):
        from src.core.config import load_yaml
        system = load_yaml("config/prompts/prompt_gen.yaml").get("system", "")
        assert "set_plan" in system
        assert "slots" in system
        # 文字策略：模型不画字，文字由**系统本地排版**绘制
        assert "文字" in system and "本地排版" in system
        assert "不要往画面里写任何文字" in system

    def test_template_covers_information_slots(self):
        """用户要的是"能实际使用的套图"（含成分图/人群图/功效图），不是几张白底商品照"""
        from src.core.config import load_yaml
        system = load_yaml("config/prompts/prompt_gen.yaml").get("system", "")
        for slot in ("main_selling_point", "main_benefits", "main_ingredients",
                     "main_audience", "main_spec"):
            assert slot in system, f"提示词模板里应说明信息类槽位 {slot} 怎么产出"
        assert "信息图" in system
        assert "留白" in system
