"""提示词审核优化结果的安全落地（`harness/prompt_review.py`）

用户 2026-09-18 的初衷是**审美**："让生成图提示词更接近大众的商品图审美，免得跑出一些符合
基本要求但是图片审美完全不合格的图片。" 所以审核优化员的主职是**改写画面描述**。

三条安全边界必须有测试兜住：
1. 只能改 `prompt`（硬约束字段来自配置，改不掉）；
2. 改写稿要过体检 —— 产生新硬伤的改写会被拒绝并保留原稿；
3. 接受与被拒都记账（不静默）。
"""

from src.harness.prompt_review import (
    apply_prompt_patches,
    normalize_prompt_review,
    review_summary,
    select_patches,
)

CLEAN_SCENE = ("品牌浅色底背景 #EEF2F7 无缝，纸盒居中偏左占画面 60%，左上留白 45%，"
               "顶部柔光 + 右侧补光，投影短而干净")


def _plan(prompt: str = CLEAN_SCENE, slot_id: str = "main_white") -> dict:
    return {
        "platform": "pinduoduo",
        "background_policy": "design_allowed",
        "slots": [{
            "number": 1, "slot_id": slot_id, "role": "纯商品图", "kind": "photo",
            "usage": "main", "intent": "让买家第一眼看清商品", "prompt": prompt,
            "must": ["纯白背景"], "avoid": ["道具"], "keep_clear": "",
            "palette": {"primary": "#1B3F94"},
        }],
    }


class TestNormalize:
    def test_normalizes_scores_and_scenes(self):
        review = normalize_prompt_review({
            "verdict": "revise", "overall_score": "78",
            "aesthetic_scores": {"main_white": 62, "main_scene": "88"},
            "slots": [{"slot_id": "main_white", "score": 62, "defects": ["缺少光位"],
                       "scene": "改写稿"}],
            "notes": ["整体偏平淡"],
        })
        assert review["verdict"] == "revise"
        assert review["scores"]["main_white"] == 62.0
        assert review["scores"]["main_scene"] == 88.0
        assert review["slots"][0]["defects"] == ["缺少光位"]
        assert review["notes"] == ["整体偏平淡"]

    def test_missing_verdict_inferred_from_patches(self):
        assert normalize_prompt_review(
            {"slots": [{"slot_id": "main_white", "scene": "x"}]})["verdict"] == "revise"
        assert normalize_prompt_review({"slots": []})["verdict"] == "pass"

    def test_garbage_input_is_safe(self):
        review = normalize_prompt_review(None)
        assert review["verdict"] == "pass"
        assert review["scores"] == {}


class TestSelectPatches:
    def test_below_threshold_with_scene_is_patch(self):
        patches, skipped = select_patches(_plan(), {
            "slots": [{"slot_id": "main_white", "score": 60, "scene": "新画面"}]}, threshold=85)
        assert [item["slot_id"] for item in patches] == ["main_white"]
        assert skipped == []

    def test_above_threshold_keeps_draft(self):
        patches, skipped = select_patches(_plan(), {
            "slots": [{"slot_id": "main_white", "score": 92, "scene": "新画面"}]}, threshold=85)
        assert patches == []
        assert "达标" in skipped[0]["reason"]

    def test_low_score_without_scene_is_skipped(self):
        patches, skipped = select_patches(_plan(), {
            "slots": [{"slot_id": "main_white", "score": 50}]}, threshold=85)
        assert patches == []
        assert "未给出改写稿" in skipped[0]["reason"]

    def test_unknown_slot_is_skipped(self):
        patches, skipped = select_patches(_plan(), {
            "slots": [{"slot_id": "main_ghost", "score": 50, "scene": "x"}]}, threshold=85)
        assert patches == []
        assert "不在本次编排" in skipped[0]["reason"]


class TestApplyPatches:
    def test_applies_patch_and_keeps_contract(self):
        outcome = apply_prompt_patches(_plan(), [{
            "slot_id": "main_white", "score": 60,
            "scene": "品牌深蓝 #1B3F94 背景无缝，纸盒居中占画面 88%，四周留白与柔光投影"}])
        assert outcome["applied"], outcome["rejected"]
        slot = outcome["plan"]["slots"][0]
        assert slot["prompt"].startswith("品牌深蓝")
        assert slot["revised_by_reviewer"] is True
        # 硬约束字段来自配置，改写改不动
        assert slot["must"] == ["纯白背景"]
        assert slot["intent"] == "让买家第一眼看清商品"

    def test_patch_producing_new_hard_error_is_rejected(self):
        """改写稿里写了"包装上写着…"（要求渲染文字）→ 体检拦下，保留原稿"""
        bad_scene = CLEAN_SCENE + "，包装上写着 60's 与 GMP 认证"
        outcome = apply_prompt_patches(_plan(), [{
            "slot_id": "main_white", "score": 50, "scene": bad_scene}])
        assert outcome["applied"] == []
        assert outcome["rejected"] and "体检拦下" in outcome["rejected"][0]["reason"]
        assert outcome["plan"]["slots"][0]["prompt"] == CLEAN_SCENE

    def test_no_patches_is_noop(self):
        plan = _plan()
        outcome = apply_prompt_patches(plan, [])
        assert outcome["applied"] == [] and outcome["rejected"] == []


class TestSummary:
    def test_summary_reports_average_and_low_slots(self):
        text = review_summary({"scores": {"a": 60, "b": 90}, "verdict": "revise"}, threshold=85)
        assert "平均 75.0" in text and "a" in text

    def test_summary_without_scores(self):
        assert "未给出" in review_summary({"verdict": "pass"})
