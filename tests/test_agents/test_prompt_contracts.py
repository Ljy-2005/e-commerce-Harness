"""提示词契约测试 —— **防止"改了代码没改提示词"**

为什么需要这一组（都是实测踩到的）：
- 会话 `bb6cc0fa56a54921` 里 `main_usage` 被判"缺素材"，根因是**下游在等 `analysis["usage"]`
  （`slot_copy.py:119`），而上游 schema 里根本没有这个字段** —— 两边各自都有测试，接缝没人测；
- A97 把风格单位改成"一轮会话一套"之后，`prompt_gen.yaml` 里还留着
  「同一套里不同槽位要用**不同档案**」这条**过期指令**，会诱导模型逐张换风格；
- `prompt_reviewer.yaml` 要求 `scene ≤180 字`，而系统对【画面】段的上限是
  `image_prompt.MAX_SCENE_CHARS`（260）且还要追加构图/背景 → 10/10 张触发静默裁剪。

这些契约一旦漂移，功能会**安静地**退化，所以用断言钉住。
"""

import re

import pytest

from src.core.config import load_yaml
from src.harness import image_prompt


def _system_text(path: str) -> str:
    cfg = load_yaml(path)
    return cfg.get("system", "")


class TestAnalystSchema:
    """分析员必须产出下游真正要的事实"""

    def test_schema_has_usage_field(self):
        system = _system_text("config/prompts/analyst.yaml")
        assert '"usage"' in system, (
            "分析员 schema 必须显式给出 usage 字段——下游 build_slot_copy('main_usage') "
            "读的就是 analysis['usage']，schema 里没有它模型就不会产出"
        )

    def test_transcription_covers_all_faces(self):
        system = _system_text("config/prompts/analyst.yaml")
        assert "逐面覆盖" in system
        for face in ("侧面", "背面"):
            assert face in system, f"转录要求里要写明去找{face}（食用方法常印在侧面/背面）"


class TestPromptGenStyleUnit:
    """一轮会话一套风格词（用户 2026-09-20 更正口径：是"一套"，不是"一条/一个"）"""

    def test_no_stale_multi_archive_instruction(self):
        system = _system_text("config/prompts/prompt_gen.yaml")
        assert "不同档案" not in system, (
            "A97 之后只有一套生效风格，'不同槽位要用不同档案' 这条会诱导逐张换风格"
        )

    def test_states_one_style_per_session(self):
        system = _system_text("config/prompts/prompt_gen.yaml")
        assert "一套风格词" in system or "一套风格" in system


class TestPromptReviewerLength:
    """审核员的长度上限必须与系统裁剪口径一致"""

    def test_scene_limit_matches_system_cap(self):
        system = _system_text("config/prompts/prompt_reviewer.yaml")
        numbers = [int(n) for n in re.findall(r"(\d+)\s*字以内", system)]
        assert numbers, "没有找到「N 字以内」的上限声明"
        assert image_prompt.MAX_SCENE_CHARS in numbers, (
            f"审核员声明的上限 {numbers} 与系统 MAX_SCENE_CHARS="
            f"{image_prompt.MAX_SCENE_CHARS} 不一致——超出部分会在出图前被静默裁掉"
        )


class TestCoordinatorRoutingRules:
    """协调者的两条路由纪律（实录：连跑两次品类分析、跳过图像后处理）"""

    def test_forbids_reinviting_ready_stages(self):
        system = _system_text("config/prompts/coordinator.yaml")
        assert "不要再邀请对应 Agent" in system
        assert "不要重复邀请" in system

    def test_names_post_processing_as_in_flow(self):
        system = _system_text("config/prompts/coordinator.yaml")
        assert "图像后处理" in system

    def test_artifact_status_is_authoritative(self):
        system = _system_text("config/prompts/coordinator.yaml")
        assert "当前产物状态" in system
        assert "唯一权威" in system


class TestStyleUnitWording:
    """术语统一：面向用户与文档都写"一套风格词"（不是"一个/一条"）"""

    @pytest.mark.parametrize("path", [
        "config/style_library.yaml",
        "config/default.yaml",
    ])
    def test_config_says_set_not_single(self, path):
        assert isinstance(load_yaml(path), dict)      # 顺手校验仍能解析
        with open(_project_path(path), encoding="utf-8") as handle:
            raw = handle.read()
        # 引文里允许出现用户原话（"只用一个风格词"），但**叙述句**必须写"一套"。
        # 注意 markdown 粗体会把词切开（`那**一套**风格词` → 实际含"套**风格词"），
        # 所以匹配要按"套…风格"这种不依赖装饰符的形态，不能写死 `一套风格`。
        narrative = "\n".join(line for line in raw.splitlines()
                              if "原话" not in line and "更正" not in line)
        assert re.search(r"套\**风格", narrative), "叙述句里要写「一套风格词」"
        assert "只用一条风格词" not in narrative
        assert "一个风格词" not in narrative


def _project_path(rel: str) -> str:
    from src.core.config import _project_root
    return str(_project_root() / rel)
