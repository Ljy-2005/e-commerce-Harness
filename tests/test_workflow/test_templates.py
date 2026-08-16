"""模板 DSL 加载 / 校验 / 实例化测试"""

import pytest

from src.workflow import templates


class TestListTemplates:
    def test_all_four_templates(self):
        tpls = templates.list_templates()
        names = {t["template_name"] for t in tpls}
        assert {"white_bg_suite", "scene_suite", "compliance_hardened", "free_chat"} <= names
        for t in tpls:
            assert t["name"]
            assert t["inputs"] is not None
            assert t["node_count"] > 0

    def test_validate_all_builtin_templates(self):
        agent_names = {
            "中心决策者", "商品分析员", "品类专项分析员", "提示词生成员",
            "生图员", "审查员", "合规审查员", "图像后处理员",
        }
        for t in templates.list_templates():
            tpl = templates.load_template(t["template_name"])
            errors = templates.validate_template(tpl, agent_names)
            assert errors == [], f"{t['template_name']} 校验失败: {errors}"


class TestInstantiate:
    def test_missing_required_input(self):
        with pytest.raises(ValueError, match="商品图片"):
            templates.instantiate("white_bg_suite", {"platform": "taobao"})

    def test_unknown_template(self):
        with pytest.raises(ValueError, match="不存在"):
            templates.instantiate("nope", {})

    def test_snapshot_stored_in_context(self):
        job = templates.instantiate(
            "scene_suite",
            {"product_images": ["fake_b64_data"], "platform": "taobao"},
        )
        assert job.context["_snapshot"]["name"] == "场景图套装"
        assert templates.get_snapshot(job)["name"] == "场景图套装"

    def test_invalid_mode_falls_back_auto(self):
        job = templates.instantiate(
            "scene_suite",
            {"product_images": ["fake_b64_data"]},
            mode="weird",
        )
        assert job.mode == "auto"


class TestValidate:
    def test_unknown_agent_detected(self):
        tpl = {
            "name": "bad",
            "nodes": {"a": {"type": "agent", "agent": "不存在的Agent"}},
        }
        assert templates.validate_template(tpl, {"商品分析员"})

    def test_unknown_goto_detected(self):
        tpl = {
            "name": "bad",
            "nodes": {
                "c": {"type": "condition", "rules": [{"when": "1 == 1", "goto": "nope"}]},
            },
        }
        assert templates.validate_template(tpl, set())

    def test_invalid_node_type(self):
        tpl = {"name": "bad", "nodes": {"a": {"type": "magic"}}}
        assert templates.validate_template(tpl, set())
