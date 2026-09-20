"""记忆库参考注入（A36）

实测事故：引擎把历史最佳提示词**追加在 task_brief 之后**（recency 最强），内容是
别的商品（"护肝胶囊…水飞蓟植物元素环绕"）→ 本次生成的提示词里出现"水飞蓟植物叶片"，
而该商品成分分析结论是"不可见/待确认"，协调者还专门叮嘱"严禁臆造具体成分"。
即：记忆串味 → 无根据的成分宣称进了成图提示词。

修法：参考块**移到 brief 之前**、限定只作风格/构图参考、显式禁止照搬事实、长度受限，
并且可以由 config/default.yaml 的 memory 段整体关闭。
"""

import pytest

from src.chat.engine import _inject_memory_reference


MEMORY = {
    "category": "保健品",
    "recalled_count": 5,
    "best_score": 82.0,
    "best_prompts": {"main": "护肝胶囊产品，白底，水飞蓟植物元素环绕，柔和自然光，高清晰度，商业摄影质感"},
    "common_features": ["高纯度水飞蓟 80%", "每日 2 粒"],
    "common_praises": ["植物元素搭配自然"],
}

BRIEF = "请为「肝迅康」生成 taobao 主图提示词；成分不可见，严禁臆造具体成分。"


class TestMemoryInjection:
    def test_reference_precedes_brief(self):
        """参考必须在 brief 之前（否则模型会拿历史经验覆盖本次约束）"""
        merged = _inject_memory_reference(BRIEF, MEMORY)
        assert merged.index("[记忆库参考") < merged.index("严禁臆造具体成分")
        assert merged.rstrip().endswith("严禁臆造具体成分。")

    def test_reference_is_scoped_to_style_only(self):
        merged = _inject_memory_reference(BRIEF, MEMORY)
        assert "仅" in merged and "风格" in merged
        assert "不得照搬" in merged or "禁止照搬" in merged

    def test_reference_is_truncated(self):
        merged = _inject_memory_reference(BRIEF, MEMORY, max_chars=10)
        assert "水飞蓟植物元素环绕" not in merged.split("严禁臆造具体成分")[0].replace("[记忆库参考", "")

    def test_disabled_by_config(self, monkeypatch):
        import src.core.config as cfg
        monkeypatch.setattr(cfg, "load_default_config",
                            lambda: {"memory": {"inject_prompt_reference": False}})
        assert _inject_memory_reference(BRIEF, MEMORY) == BRIEF

    def test_enabled_by_default(self):
        assert "[记忆库参考" in _inject_memory_reference(BRIEF, MEMORY)

    def test_no_memory_context_is_noop(self):
        assert _inject_memory_reference(BRIEF, None) == BRIEF
        assert _inject_memory_reference(BRIEF, {}) == BRIEF

    def test_missing_best_prompt_is_noop(self):
        assert _inject_memory_reference(BRIEF, {"category": "保健品"}) == BRIEF
