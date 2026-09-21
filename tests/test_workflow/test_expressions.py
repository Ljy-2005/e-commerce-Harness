"""极简表达式引擎测试"""

import pytest

from src.workflow.expressions import (
    build_scope,
    eval_expr,
    render_string,
    resolve_path,
)


def _scope():
    return build_scope(
        job_inputs={"platform": "taobao", "count": 3},
        step_outputs={"review": {"overall_score": 82, "verdict": "retry"},
                      "analyze": {"category": "保健品"}},
        ctx={"retries": 1},
    )


class TestResolvePath:
    def test_inputs(self):
        scope = _scope()
        assert resolve_path("$inputs.platform", scope) == "taobao"

    def test_steps_outputs(self):
        scope = _scope()
        assert resolve_path("$steps.review.outputs.overall_score", scope) == 82

    def test_ctx(self):
        assert resolve_path("$ctx.retries", _scope()) == 1

    def test_missing_returns_none(self):
        assert resolve_path("$steps.nope.outputs.x", _scope()) is None

    def test_illegal_root(self):
        with pytest.raises(ValueError):
            resolve_path("$os.environ.PATH", _scope())


class TestEvalExpr:
    def test_number_compare(self):
        scope = _scope()
        assert eval_expr("$steps.review.outputs.overall_score >= 75", scope) is True
        assert eval_expr("$steps.review.outputs.overall_score < 60", scope) is False

    def test_string_eq_bareword(self):
        assert eval_expr("$steps.review.outputs.verdict == retry", _scope()) is True
        assert eval_expr("$steps.review.outputs.verdict == pass", _scope()) is False

    def test_string_eq_quoted(self):
        assert eval_expr('$inputs.platform == "taobao"', _scope()) is True

    def test_in_list(self):
        scope = _scope()
        assert eval_expr("$steps.analyze.outputs.category in [保健品, 化妆品]", scope) is True
        assert eval_expr("$steps.analyze.outputs.category in [食品]", scope) is False

    def test_not_in(self):
        scope = _scope()
        assert eval_expr("$steps.analyze.outputs.category not in [食品]", scope) is True

    def test_and_or_not(self):
        scope = _scope()
        assert eval_expr("$ctx.retries < 2 and $steps.review.outputs.verdict == retry", scope) is True
        assert eval_expr("$ctx.retries > 2 or $inputs.platform == amazon", scope) is False
        assert eval_expr("not ($inputs.count > 5)", scope) is True

    def test_missing_key_compare_safe(self):
        scope = _scope()
        assert eval_expr("$steps.nope.outputs.x >= 70", scope) is False

    def test_empty_expr_true(self):
        assert eval_expr("", _scope()) is True
        assert eval_expr(None, _scope()) is True

    def test_parentheses(self):
        scope = _scope()
        assert eval_expr("($inputs.count == 3) and ($ctx.retries == 1)", scope) is True

    def test_parse_error(self):
        with pytest.raises(ValueError):
            eval_expr("$inputs.platform ==", _scope())


class TestRenderString:
    def test_inputs_interpolation(self):
        scope = _scope()
        assert render_string("生成 {inputs.platform} 平台提示词", scope) == "生成 taobao 平台提示词"

    def test_steps_interpolation(self):
        scope = _scope()
        assert render_string("品类: {steps.analyze.outputs.category}", scope) == "品类: 保健品"

    def test_unresolved_keeps_placeholder(self):
        assert render_string("未知 {inputs.missing}", _scope()) == "未知 {inputs.missing}"
