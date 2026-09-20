"""提示词审核优化员（`agents/prompt_reviewer.py`）

用户 2026-09-18："我加这个的初衷是为了让生成图提示词更接近大众的商品图审美，
免得跑出一些符合基本要求但是图片审美完全不合格的图片。"

设计分工：**审美判断交给 LLM**（本 Agent），**可枚举的错交给零成本体检**。
因此测试要盯住三件事：输入里带齐契约与体检结论、输出可被规范化、失败不阻塞出图。
"""

import pytest

from src.agents.prompt_reviewer import PromptReviewerAgent
from src.harness.set_plan import finalize_prompts, platform_slot_plan
from src.providers.mock import MockLLMProvider

CLEAN_SCENE = ("品牌浅色底背景 #EEF2F7 无缝，纸盒居中偏左占画面 60%，左上留白 45%，"
               "顶部柔光 + 右侧补光，投影短而干净")


class _FakeTextProvider:
    """简单的文本 Provider 替身：按预置结果返回 `content`"""

    name = "fake"
    capabilities = ["text"]

    def __init__(self, payload):
        self.payload = payload
        self.calls = 0
        self.last_messages = []

    async def chat(self, messages=None, **kwargs):
        self.calls += 1
        self.last_messages = messages or []
        return dict(self.payload)


def _prompts(platform: str = "pinduoduo") -> dict:
    slots = [{"number": index, "slot_id": slot_id, "role": slot_id, "prompt": CLEAN_SCENE,
              "composition": "居中", "background": "#FFFFFF", "aspect": "1:1"}
             for index, slot_id in enumerate(platform_slot_plan(platform), start=1)]
    return finalize_prompts({"set_plan": {"platform": platform, "slots": slots}}, platform)


def _session(prompts: dict | None = None) -> dict:
    return {
        "session_id": "s-review", "tenant_id": "default", "turn_count": 3,
        "task": {"platform": "pinduoduo"},
        "artifacts": {
            "prompts": prompts if prompts is not None else _prompts(),
            "product_identity": {
                "brand": "德國 樂美寶®", "product_name": "金裝 肝迅康", "spec": "60's",
                "certifications": [], "status": "confirmed", "source": "vision",
                "brand_palette": {"primary": "#1B3F94", "secondary": "#7CBF4A",
                                  "background": "#EEF2F7", "evidence": "正面品牌区"},
            },
        },
        "messages": [],
    }


class TestPromptReviewerAgent:
    @pytest.mark.asyncio
    async def test_mock_provider_is_marked_demo(self):
        agent = PromptReviewerAgent(provider=MockLLMProvider())
        result = await agent.execute("审核提示词", _session())
        assert result["status"] == "mock"
        assert result["verdict"] == "pass"
        assert "演示数据" in result["message"]

    @pytest.mark.asyncio
    async def test_without_plan_is_skipped_not_error(self):
        agent = PromptReviewerAgent(provider=_FakeTextProvider({"content": {}}))
        session = _session(prompts={"main_image": {"prompt": "x"}})
        result = await agent.execute("审核提示词", session)
        assert result["status"] == "skipped"
        assert "error" not in result

    @pytest.mark.asyncio
    async def test_reviews_and_normalizes_output(self):
        provider = _FakeTextProvider({"content": {
            "verdict": "revise", "overall_score": 71,
            "aesthetic_scores": {"main_white": 60, "main_scene": 88},
            "slots": [{"slot_id": "main_white", "score": 60,
                       "defects": ["缺少投影描述"],
                       "scene": "品牌深蓝 #1B3F94 底，纸盒居中占 88%，四周留白"}],
            "notes": ["整体偏平淡"],
        }})
        agent = PromptReviewerAgent(provider=provider)
        result = await agent.execute("审核提示词", _session())
        assert provider.calls == 1
        assert result["status"] == "reviewed"
        assert result["verdict"] == "revise"
        assert result["scores"]["main_white"] == 60.0
        assert result["lint_digest"]
        assert "审美分" in result["message"]

    @pytest.mark.asyncio
    async def test_provider_error_propagates(self):
        agent = PromptReviewerAgent(provider=_FakeTextProvider({"error": "boom"}))
        result = await agent.execute("审核提示词", _session())
        assert result["error"] == "boom"

    def test_brief_carries_contract_and_lint(self):
        agent = PromptReviewerAgent()
        plan = _prompts()["set_plan"]
        from src.harness.product_identity import normalize_identity

        identity = normalize_identity({
            "product_identity": {"brand": "德國 樂美寶®", "product_name": "金裝 肝迅康",
                                 "confidence": 0.9, "evidence": "正面",
                                 "brand_palette": {"primary": "#1B3F94", "secondary": "#7CBF4A",
                                                   "background": "#EEF2F7",
                                                   "evidence": "正面品牌区"}}}, source="vision")
        from src.harness.prompt_lint import lint_prompts

        lint = lint_prompts(plan, platform="pinduoduo", identity=identity)
        brief = agent._build_brief(plan, identity, identity["brand_palette"], lint,
                                   "pinduoduo", "额外要求：上一轮成图偏暗")
        # 逐张契约（目的/设计要点/必须）与体检结论都要在提示里
        assert "第1张" in brief
        assert "设计要点" in brief
        assert "必须" in brief
        assert "系统体检已发现的问题" in brief
        assert "上一轮成图偏暗" in brief
        assert "#1B3F94" in brief

    # ── 风格档案与用户锚点（A79-A96）──

    def test_brief_carries_style_archive_block(self):
        """逐槽位档案块要在审核员的输入里（它据此逐条对照打分）"""
        from src.harness.product_identity import normalize_identity
        from src.harness.prompt_lint import lint_prompts
        from src.harness.style_library import select_by_slot

        agent = PromptReviewerAgent()
        plan = _prompts()["set_plan"]
        identity = normalize_identity({"product_identity": {"brand": "X", "product_name": "Y"}},
                                      source="vision")
        selection = select_by_slot(plan["slots"], analysis={"category": "保健品"},
                                   platform="pinduoduo")
        lint = lint_prompts(plan, platform="pinduoduo", identity=identity,
                            style_entries=selection)
        brief = agent._build_brief(plan, identity, identity["brand_palette"], lint,
                                   "pinduoduo", "", selection)
        assert "适用风格档案" in brief
        assert "档案定义" in brief and "逐张对应" in brief
        assert "锚点优先" in brief

    def test_review_payload_keeps_style_ids(self):
        from src.harness.style_library import select_by_slot

        selection = select_by_slot([{"slot_id": "main_white", "kind": "photo"}],
                                   platform="pinduoduo")
        refs = PromptReviewerAgent._style_refs(selection)
        assert refs["entries"], refs
        assert isinstance(refs["notes"], list)
