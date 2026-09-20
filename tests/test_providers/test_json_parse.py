"""宽松 JSON 解析（A33）

实测事故：模型把 JSON 放在 ```json 围栏里返回 → `json.loads` 直接失败 →
- 商品分析员输出被当成纯文本 → `confidence_score` 读成 0 → 系统播报"置信度偏低"、白跑一轮；
- 审查员输出被当成纯文本 → `verdict` 丢失 → 引擎 `result.get("verdict", "pass")`
  **静默按通过放行**，质量门禁失效。
"""

import pytest

from src.providers.json_parse import parse_json_loose, strip_code_fence


class TestStripCodeFence:
    def test_json_fence(self):
        assert strip_code_fence('```json\n{"a": 1}\n```') == '{"a": 1}'

    def test_bare_fence(self):
        assert strip_code_fence('```\n{"a": 1}\n```') == '{"a": 1}'

    def test_no_fence_unchanged(self):
        assert strip_code_fence('{"a": 1}') == '{"a": 1}'

    def test_text_around_fence_kept_together(self):
        text = '说明如下：\n```json\n{"a": 1}\n```\n以上。'
        assert '{"a": 1}' in strip_code_fence(text)


class TestParseJsonLoose:
    def test_plain_object(self):
        assert parse_json_loose('{"a": 1}') == {"a": 1}

    def test_fenced_object(self):
        assert parse_json_loose('```json\n{"verdict": "retry"}\n```') == {"verdict": "retry"}

    def test_prose_wrapped_object(self):
        text = '注意：本次未附图。\n\n```json\n{"overall_score": null, "verdict": "retry"}\n```\n补充说明。'
        assert parse_json_loose(text) == {"overall_score": None, "verdict": "retry"}

    def test_nested_and_braces_in_strings(self):
        raw = '前缀 {"a": {"b": [1, 2]}, "note": "含 } 与 { 的字符串"} 后缀'
        assert parse_json_loose(raw) == {"a": {"b": [1, 2]}, "note": "含 } 与 { 的字符串"}

    def test_escaped_quote_inside_string(self):
        assert parse_json_loose(r'{"q": "he said \"hi\" }"}') == {"q": 'he said "hi" }'}

    @pytest.mark.parametrize("raw", [
        "", "   ", "纯文本没有 JSON", '{"a": 1', '{"a": 1,}', "```json\n{未闭合\n```",
        "[1, 2, 3]", "null", "42",
    ])
    def test_unparseable_returns_none(self, raw):
        assert parse_json_loose(raw) is None

    def test_first_object_wins_when_multiple(self):
        assert parse_json_loose('{"a": 1}\n{"b": 2}') == {"a": 1}
