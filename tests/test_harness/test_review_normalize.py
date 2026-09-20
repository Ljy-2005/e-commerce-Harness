"""多图审查结果归一化（A43）

真实会话实测：审查员按"对每张图输出评分"返回 `{"results": [...]}`，顶层没有
`overall_score`/`verdict` → 门禁（正确地）判为无法解析 → 转人工；而审查内容本身完全有效
（它逐字读出了生成图里被臆造的 `NUTRIVA®` 品牌与缺失的 GMP 条）。这里把逐变体结果汇总成顶层判定。
"""

import pytest

from src.harness.review_normalize import normalize_review_payload


def _captured_review():
    """真实会话 72d5ef86831c4f99 的审查员原始返回（截取关键字段）"""
    return {
        "results": [
            {
                "variant": "variant_1", "overall_score": 62.4,
                "dimension_scores": {"texture": 74, "lighting": 78, "composition": 80,
                                     "product_fidelity": 22, "platform_fit": 58},
                "top_issues": ["金色烫印条带上的品牌英文被臆造为『NUTRIVA®』",
                               "原包装核心视觉全部缺失"],
                "top_praises": ["1:1 方图、产品满幅居中"],
                "verdict": "fail", "needs_human_review": True,
            },
            {
                "variant": "variant_2", "overall_score": 64.0,
                "dimension_scores": {"texture": 70, "lighting": 76, "composition": 77,
                                     "product_fidelity": 42, "platform_fit": 55},
                "top_issues": ["『®』注册商标符号脱离品牌名单独漂浮"],
                "top_praises": ["背景更接近纯白"],
                "verdict": "fail", "needs_human_review": True,
            },
            {
                "variant": "variant_3", "overall_score": 58.0,
                "dimension_scores": {"texture": 68, "lighting": 72, "composition": 75,
                                     "product_fidelity": 30, "platform_fit": 52},
                "top_issues": ["金色烫印条带上的品牌英文被臆造为『NUTRIVA®』"],
                "verdict": "fail", "needs_human_review": False,
            },
        ]
    }


class TestNormalizeReviewPayload:
    def test_captured_payload_gets_top_level_verdict(self):
        merged, detail = normalize_review_payload(_captured_review())

        assert merged["verdict"] == "fail"          # 最严重
        assert merged["overall_score"] == pytest.approx(61.5, abs=0.1)   # 平均
        assert merged["needs_human_review"] is True
        assert merged["results"], "逐变体明细必须保留（界面要逐张显示）"
        assert "汇总" in detail

    def test_issues_and_dimensions_merged(self):
        merged, _ = normalize_review_payload(_captured_review())

        assert len(merged["top_issues"]) == 3, "议题合并去重"
        assert merged["dimension_scores"]["texture"] == pytest.approx(70.7, abs=0.1)

    def test_verdict_takes_worst_case(self):
        merged, _ = normalize_review_payload({"results": [
            {"overall_score": 90, "verdict": "pass"},
            {"overall_score": 80, "verdict": "retry"},
        ]})
        assert merged["verdict"] == "retry"
        assert merged["overall_score"] == 85.0

    def test_already_valid_payload_is_untouched(self):
        original = {"overall_score": 88, "verdict": "pass", "top_issues": []}
        merged, detail = normalize_review_payload(original)
        assert merged is original and detail == ""

    def test_idempotent(self):
        once, _ = normalize_review_payload(_captured_review())
        twice, detail = normalize_review_payload(once)
        assert twice["verdict"] == once["verdict"] == "fail"
        assert detail == "", "已归一化的结果不再重复处理"

    @pytest.mark.parametrize("payload", [
        {}, {"results": []}, {"results": "nope"}, {"results": [1, 2]},
        {"error": "NO_IMAGE_ACCESSIBLE"}, None, "text",
    ])
    def test_unusable_payloads_pass_through(self, payload):
        merged, detail = normalize_review_payload(payload)
        assert merged == payload and detail == ""

    def test_results_without_scores_still_gets_verdict(self):
        merged, _ = normalize_review_payload({"results": [
            {"verdict": "retry", "top_issues": ["构图偏左"]},
        ]})
        assert merged["verdict"] == "retry"
        assert "overall_score" not in merged
