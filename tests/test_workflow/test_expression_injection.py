"""L7 安全补缺（test-plan P5）— workflow 表达式注入测试

极简表达式求值器是严格 tokenizer/parser（无 eval/无属性访问/无函数调用），
本测试锁定该安全边界：注入形态必须被拒绝或原样保留，绝不执行。
"""

import pytest

from src.workflow import expressions as expr

SCOPE = expr.build_scope(
    {"platform": "taobao", "category_hint": "保健品", "product_images": ["a", "b"]},
    {"review": {"overall_score": 82, "verdict": "pass"}},   # 裸 outputs（决策 22 教训）
    {"retries": 1},
)

# 注入形态：Python 内建/系统调用/属性链/语句拼接/算术/文件路径/切片
ATTACKS = [
    "__import__('os').system('id')",
    "os.system('rm -rf /')",
    "eval('__import__(\"os\")')",
    "exec('import os')",
    "$ctx.__class__.__mro__[1].__subclasses__()",
    "1; import os",
    "$steps.review.outputs.overall_score + 1",
    "cat /etc/passwd",
    "$inputs.product_images[0:2]",
    "True if $steps.review.outputs.overall_score > 75 else False",
]


class TestExpressionInjection:
    def test_attack_shapes_rejected(self):
        """全部注入形态 → ValueError（解析失败或多余内容），不执行任何代码"""
        for attack in ATTACKS:
            try:
                expr.eval_expr(attack, SCOPE)
            except ValueError:
                continue
            pytest.fail(f"注入未被拒绝: {attack}")

    def test_attribute_chain_neutralized_by_dict_lookup(self):
        """属性链形态（$inputs.__class__）：白名单根 + dict.get 语义 →
        安全求值为 None/False，不会触达 Python 对象属性"""
        assert expr.eval_expr("$inputs.__class__", SCOPE) is None
        assert expr.eval_expr("$ctx.__class__ == 1", SCOPE) is False

    def test_forbidden_path_root_rejected(self):
        """$os / $__builtins__ 等非白名单根 → 明确拒绝"""
        for attack in ("$os.system('x')", "$__builtins__.open"):
            with pytest.raises(ValueError, match="不允许的路径根"):
                expr.eval_expr(attack, SCOPE)
        with pytest.raises(ValueError):
            expr.eval_expr("$__import__('os')", SCOPE)  # 无点号 → tokenize 拒绝

    def test_render_string_attack_left_unchanged(self):
        """{...} 插值注入：未知引用原样保留，不执行"""
        out = expr.render_string("命令 {__import__('os').system('id')}", SCOPE)
        assert "__import__" in out
        assert out == "命令 {__import__('os').system('id')}"

    def test_render_string_unknown_key_unchanged(self):
        out = expr.render_string("平台 {inputs.unknown_key} 继续", SCOPE)
        assert "{inputs.unknown_key}" in out

    def test_legit_expressions_still_work(self):
        """对照：合法表达式不受影响（边界收紧 ≠ 功能破坏）"""
        assert expr.eval_expr("$steps.review.outputs.overall_score >= 75", SCOPE) is True
        assert expr.eval_expr("$steps.review.outputs.verdict == pass", SCOPE) is True
        assert expr.eval_expr("$inputs.category_hint in [保健品, 化妆品]", SCOPE) is True
        assert expr.eval_expr(
            "$steps.review.outputs.overall_score >= 75 and $ctx.retries < 2", SCOPE
        ) is True
