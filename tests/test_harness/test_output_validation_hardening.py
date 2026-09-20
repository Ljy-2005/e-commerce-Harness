"""第三轮审计 B1-5：输出校验管道对畸形 LLM 输出**不得抛异常**。

`BusinessRuleValidator` 直接比较未做类型校验的值：`{"overall_score": "85"}` →
`TypeError: '<=' not supported between 'int' and 'str'`；调用处（`engine.py`）无 try
→ **整轮群聊失败**（同文件的完整性检查反而做了 isinstance）。
另有两条同类崩溃路径：`category` 非字符串（`.strip()` → AttributeError）、
`main_image.prompt` 为 dict（把 dict 当字符串 `strip()`）。
"""

import pytest

from src.harness.output_pipeline import OutputPipeline

PIPELINE = OutputPipeline()


def _errors_for(agent: str, output: dict) -> list[str]:
    result = PIPELINE.validate(agent, output)
    assert result.passed is False, f"{output} 应判为不合格"
    return result.errors


class TestMalformedOutputNeverRaises:
    @pytest.mark.parametrize("agent,output", [
        ("审查员", {"overall_score": "85", "dimension_scores": {}, "verdict": "pass"}),
        ("审查员", {"overall_score": None, "dimension_scores": {}, "verdict": "pass"}),
        ("审查员", {"overall_score": [85], "dimension_scores": {}, "verdict": "pass"}),
        ("商品分析员", {"category": 123, "features": ["a"], "target_audience": {},
                    "confidence_score": "90"}),
        ("商品分析员", {"category": "", "features": [], "target_audience": {},
                    "confidence_score": None}),
        ("提示词生成员", {"main_image": {"prompt": {"nested": "x"}}, "scene_images": []}),
        ("提示词生成员", {"main_image": {"prompt": 42}, "scene_images": []}),
        ("合规审查员", {"passed": "yes", "risk_level": 3}),
    ])
    def test_validate_returns_result_instead_of_raising(self, agent, output):
        result = PIPELINE.validate(agent, output)   # 关键：不抛异常
        assert isinstance(result.passed, bool)
        assert result.errors, "畸形输出必须有可读的失败原因"

    def test_string_score_reports_range_error(self):
        errors = _errors_for("审查员", {"overall_score": "85", "dimension_scores": {},
                                    "verdict": "pass"})
        assert any("overall_score" in e for e in errors)

    def test_non_string_category_reports_error(self):
        errors = _errors_for("商品分析员", {"category": 123, "features": ["a"],
                                       "target_audience": {}, "confidence_score": 90})
        assert any("category" in e for e in errors)

    def test_dict_prompt_reports_error(self):
        errors = _errors_for("提示词生成员", {"main_image": {"prompt": {"x": 1}},
                                        "scene_images": []})
        assert any("main_image.prompt" in e for e in errors)


class TestValidOutputStillPasses:
    def test_reviewer_valid(self):
        result = PIPELINE.validate("审查员", {"overall_score": 88, "dimension_scores": {"a": 1},
                                           "verdict": "pass"})
        assert result.passed is True, result.errors

    def test_analyst_valid(self):
        result = PIPELINE.validate("商品分析员", {
            "category": "保健品", "features": ["补肾"], "target_audience": {"age": "30+"},
            "confidence_score": 90,
        })
        assert result.passed is True, result.errors

    def test_prompt_gen_valid(self):
        result = PIPELINE.validate("提示词生成员", {
            "main_image": {"prompt": "白色背景产品图"}, "scene_images": [{"prompt": "场景"}],
        })
        assert result.passed is True, result.errors
