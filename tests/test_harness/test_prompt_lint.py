"""提示词体检（`harness/prompt_lint.py`）—— 零成本确定性护栏的规则回归

背景（用户 2026-09-18）：真实会话 `ee9a3b80e19b4010` 里提示词把包装版式逐字写进画面描述，
模型据此"重画小字"产生 `Sadle Ligement`/`Schneiski` 乱码；6 张画面高度同质；卖点图里没有商品；
单条 586 字且近半是否定词。这些都属于"能过基本检查但结果不可用"，必须**出图前**就能查出来。
"""

import pytest

from src.core.platforms import (
    slot_forbid,
    slot_intent,
    slot_keep_clear,
    slot_kind,
    slot_must,
)
from src.harness.prompt_lint import lint_prompts, prompt_digest, summarize_lint
from src.harness.set_plan import platform_slot_plan

# 一条"体检干净"的画面描述：版式（占比/居中/留白）+ 背景（底色）+ 光影（柔光/投影）都在
CLEAN_SCENE = ("品牌浅色底背景 #EEF2F7 无缝渐层，纸盒居中偏左、占画面 60%，左上留白 45%，"
               "顶部柔光 + 右侧补光，投影短而干净")


def _slot(slot_id: str, prompt: str, number: int = 1, **overrides) -> dict:
    slot = {
        "number": number,
        "slot_id": slot_id,
        "prompt": prompt,
        "kind": slot_kind(slot_id),
        "usage": "main",
        "intent": slot_intent(slot_id),
        "must": slot_must(slot_id),
        "avoid": slot_forbid(slot_id),
        "keep_clear": slot_keep_clear(slot_id),
        "palette": {"primary": "#1B3F94", "secondary": "#7CBF4A", "background": "#EEF2F7"},
    }
    slot.update(overrides)
    return slot


def _plan(slots, platform: str = "pinduoduo", policy: str = "design_allowed") -> dict:
    return {"platform": platform, "background_policy": policy, "slots": slots}


def _rules(report) -> set[str]:
    return {item["rule"] for item in report["findings"]}


# 各槽位不同角色的画面（体检里的"同质化"规则要求它们真的不同）
SCENES = {
    "photo": "纯白背景 #FFFFFF 无缝，纸盒居中、占画面 88%，四周留窄白边，"
             "顶部柔光 + 双侧补光，投影短而干净，哑光纸纹可见",
    "scene": "浅色原木桌面，纸盒置于右三分线、占画面 50%，背景柔和虚化，"
             "晨光自然侧光方向明确，道具仅一只玻璃杯",
    "info": "纯白背景 + 一条品牌色带，纸盒 3/4 视角置于右下角、占画面 55%，"
            "左上与上方留白 45% 供文字排版，柔光均匀，投影极淡",
}


def _full_plan(platform: str = "pinduoduo", policy: str = "design_allowed") -> dict:
    """覆盖平台全部槽位，且各槽位画面互不相同（避免 missing_slot / duplicate 淹没结论）"""
    slots = []
    for index, slot_id in enumerate(platform_slot_plan(platform), start=1):
        kind = slot_kind(slot_id)
        base = SCENES["scene" if slot_id == "main_scene" else kind]
        slots.append(_slot(slot_id, f"{base}（这是 {slot_id} 的第 {index} 张画面）", number=index))
    return _plan(slots, platform=platform, policy=policy)


class TestCleanPlan:
    def test_clean_plan_has_no_errors(self):
        report = lint_prompts(_full_plan())
        assert report["errors"] == [], report["errors"]
        assert report["checked"] == len(platform_slot_plan("pinduoduo"))
        assert report["expected"] == report["checked"]

    def test_summary_shape(self):
        assert "✅" in summarize_lint(lint_prompts(_full_plan()))
        assert "硬伤" in summarize_lint(lint_prompts(_plan([])))

    def test_digest_is_stable_and_prompt_sensitive(self):
        first = prompt_digest(_full_plan())
        assert first == prompt_digest(_full_plan())
        changed = _full_plan()
        changed["slots"][0]["prompt"] = CLEAN_SCENE + "，换成完全不同的画面"
        assert first != prompt_digest(changed)


class TestStructuralRules:
    def test_missing_slot_is_error(self):
        report = lint_prompts(_plan([_slot("main_white", CLEAN_SCENE)]))
        assert "missing_slot" in _rules(report)
        assert any("main_selling_point" in item for item in report["errors"])

    def test_missing_intent_is_warning(self):
        report = lint_prompts(_plan([_slot("main_white", CLEAN_SCENE, intent="")]))
        assert "missing_intent" in _rules(report)

    def test_missing_palette_is_warning(self):
        slots = [_slot("main_white", CLEAN_SCENE, palette={})]
        report = lint_prompts(_plan(slots))
        assert "missing_palette" in _rules(report)


class TestContentRules:
    IDENTITY = {
        "brand": "德國 樂美寶® / DEFOEBUENA",
        "product_name": "金裝 強力 肝迅康（升級版）",
        "spec": "60's",
        "certifications": ["德國GMP優質產品"],
        "status": "confirmed", "source": "vision",
    }

    def test_identity_terms_in_prompt_is_error(self):
        report = lint_prompts(
            _plan([_slot("main_white", CLEAN_SCENE + "，盒面印有 DEFOEBUENA")]),
            identity=self.IDENTITY)
        assert "identity_in_prompt" in _rules(report)
        assert any("DEFOEBUENA" in item for item in report["errors"])

    def test_packaging_prose_is_warning(self):
        report = lint_prompts(_plan([_slot("main_white", CLEAN_SCENE + "，左上藍色品牌區塊金色燙印")]))
        assert "packaging_prose" in _rules(report)

    def test_fake_text_request_is_error(self):
        report = lint_prompts(_plan([_slot("main_white", CLEAN_SCENE + "，包装上写着 60's")]))
        assert "fake_text_request" in _rules(report)

    def test_info_slot_without_keep_clear_is_error(self):
        report = lint_prompts(_plan([
            _slot("main_selling_point", "纯白背景，纸盒居中占画面 60%，顶部柔光投影短而干净")]))
        assert "missing_keep_clear" in _rules(report)

    def test_missing_design_terms_is_error(self):
        report = lint_prompts(_plan([_slot("main_white", "商品盒子放在画面里，干净利落")]))
        assert "missing_design_terms" in _rules(report)

    @pytest.mark.parametrize("snippet,rule", [
        ("禁止文字、不要水印、不得杂乱、避免阴影、不出现道具、不含灰底、杜绝拼接、no props", "negation_pile"),
        ("8K 超高解析度大师作品", "magic_words"),
        ("高饱和强对比但又要高级感克制", "contradiction"),
        ("本品可根治肝病", "forbidden_claim"),
    ])
    def test_antipattern_rules(self, snippet, rule):
        report = lint_prompts(_plan([_slot("main_white", f"{CLEAN_SCENE}，{snippet}")]))
        assert rule in _rules(report)

    def test_too_short_prompt_is_warning(self):
        report = lint_prompts(_plan([_slot("main_white", "纯白背景")]))
        assert "prompt_too_short" in _rules(report)

    def test_white_required_without_white_bg_is_error(self):
        report = lint_prompts(_plan([_slot("main_white", CLEAN_SCENE)], platform="amazon",
                                    policy="white_required"))
        assert "white_required_bg" in _rules(report)

    def test_white_required_with_white_bg_passes_that_rule(self):
        prompt = ("纯白背景 #FFFFFF 无缝，纸盒居中占画面 88%，四周留白，顶部柔光 + 补光，"
                  "投影短而干净")
        report = lint_prompts(_plan([_slot("main_white", prompt)], platform="amazon",
                                    policy="white_required"))
        assert "white_required_bg" not in _rules(report)

    def test_duplicate_slots_are_warned(self):
        slots = [_slot("main_white", CLEAN_SCENE, number=1),
                 _slot("main_scene", CLEAN_SCENE, number=2)]
        report = lint_prompts(_plan(slots))
        assert "duplicate_prompts" in _rules(report)

    def test_distinct_scenes_are_not_flagged_duplicate(self):
        slots = [
            _slot("main_white", CLEAN_SCENE, number=1),
            _slot("main_scene", "浅色原木桌面场景，纸盒置于右侧三分线占画面 50%，"
                                "背景柔和虚化，晨光自然光方向明确", number=2),
        ]
        report = lint_prompts(_plan(slots))
        assert "duplicate_prompts" not in _rules(report)

    # ── 风格档案：逐字照抄检测（A79-A96）──

    def test_style_template_copy_is_flagged(self):
        """画面描述与所注入档案连续重合 ≥12 字 → 说明在搬模板而不是在设计画面"""
        entry = {"id": "e1", "name": "净白硬照",
                 "background": "纯白无缝底，主体居中、四周留窄白边，投影短椭圆。"}
        copied = (CLEAN_SCENE + "；" + "纯白无缝底，主体居中、四周留窄白边")
        report = lint_prompts(_plan([_slot("main_white", copied)]),
                              style_entries={"main_white": [entry]})
        assert "style_template_copy" in _rules(report)

    def test_own_wording_is_not_flagged(self):
        entry = {"id": "e1", "name": "净白硬照",
                 "background": "纯白无缝底，主体居中、四周留窄白边，投影短椭圆。"}
        report = lint_prompts(_plan([_slot("main_white", CLEAN_SCENE)]),
                              style_entries={"main_white": [entry]})
        assert "style_template_copy" not in _rules(report)
        # 没给档案时这条规则根本不参与（老调用点零影响）
        assert "style_template_copy" not in _rules(
            lint_prompts(_plan([_slot("main_white", copied_guard())])))

    def test_style_entries_accepts_full_selection(self):
        from src.harness.style_library import select_by_slot

        entry = {"id": "e1", "name": "净白硬照", "applies_to": {"kinds": ["photo"]},
                 "background": "纯白无缝底，主体居中、四周留窄白边，投影短椭圆。"}
        selection = select_by_slot([{"slot_id": "main_white", "kind": "photo"}],
                                   platform="taobao",
                                   library={"entries": [entry], "anchors": [], "dropped": [],
                                            "defaults": {}, "exists": True})
        copied = CLEAN_SCENE + "；纯白无缝底，主体居中、四周留窄白边"
        report = lint_prompts(_plan([_slot("main_white", copied)]), style_entries=selection)
        assert "style_template_copy" in _rules(report)


def copied_guard() -> str:
    return "纯白无缝底，主体居中、四周留窄白边，投影短椭圆。顶部柔光 + 右侧补光，占画面 88%"
