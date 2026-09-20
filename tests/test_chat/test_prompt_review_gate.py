"""提示词阶段门禁（引擎接线）：体检 → 审美审核 → 有界重写

用户 2026-09-18 的两条反馈催生了这条链路：
- "未有明确约束每一张该有的提示词" → 逐张契约 + 体检（零成本、确定性）；
- "让提示词更接近大众的商品图审美" → 提示词审核优化员按分改写画面描述。

门禁语义（本文件逐条回归）：
- 关掉开关 → 只记体检结果，不花钱；
- 审核员给的低分改写稿落地前必须过体检；被拦下就保留原稿并记账；
- 同一版提示词（digest）已审过且通过 → 不重复花钱；
- 审核员报错/缺失 → 记 `skipped|error` 并继续出图（**不静默当通过**）；
- 体检仍有硬伤 → 打回提示词生成员**至多一轮**；
- `require_prompt_confirm=true` 且仍不达标 → 暂停等人工。
"""

import pytest

from src.agents.registry import AgentRegistry
from src.chat.engine import ChatEngine
from src.chat.session import SessionManager
from src.harness.set_plan import finalize_prompts, platform_slot_plan
from src.providers import get_provider_registry

CLEAN_SCENE = ("品牌浅色底背景 #EEF2F7 无缝，纸盒居中偏左占画面 60%，左上留白 45%，"
               "顶部柔光 + 右侧补光，投影短而干净")
PLATFORM = "pinduoduo"


def _prompts(platform: str = PLATFORM, *, scene: str = CLEAN_SCENE) -> dict:
    slots = [{"number": index, "slot_id": slot_id, "role": slot_id, "prompt": scene,
              "composition": "居中", "background": "#FFFFFF", "aspect": "1:1"}
             for index, slot_id in enumerate(platform_slot_plan(platform), start=1)]
    return finalize_prompts({"set_plan": {"platform": platform, "slots": slots}}, platform)


def _session(prompts: dict | None = None) -> dict:
    return {
        "session_id": "s-gate", "tenant_id": "default", "status": "running",
        "turn_count": 2, "messages": [], "cost_budget_usd": 10.0,
        "task": {"platform": PLATFORM},
        "artifacts": {"prompts": prompts if prompts is not None else _prompts()},
    }


class _StubProvider:
    """占位 Provider：引擎用 `provider is None` 判"审核员是否可用"，替身必须给一个非空 provider"""

    name = "stub"
    capabilities = ["text"]


class _StubAgent:
    """按序返回预置结果的 Agent 替身（记录调用次数与最后一次任务描述）"""

    def __init__(self, results=None, default=None):
        self.provider = _StubProvider()
        self._results = list(results or [])
        self._default = default or {}
        self.calls = 0
        self.last_brief = ""

    async def execute(self, task_brief, session, **kwargs):
        self.calls += 1
        self.last_brief = task_brief
        result = self._results.pop(0) if self._results else self._default
        return dict(result)

    def _get_model(self):
        return "stub"


async def _engine(prompt_gen=None, reviewer=None):
    registry = AgentRegistry()
    await registry.load_from_config(get_provider_registry())
    if prompt_gen is not None:
        registry._agents["提示词生成员"] = prompt_gen
    if reviewer is not None:
        registry._agents["提示词审核优化员"] = reviewer
    engine = ChatEngine(registry=registry, session_manager=SessionManager())
    return engine, registry


@pytest.fixture
def settings(monkeypatch):
    def _set(**overrides):
        base = {"require_prompt_review": True, "prompt_aesthetic_threshold": 85.0,
                "prompt_review_max_rounds": 1, "require_prompt_confirm": False,
                "max_consecutive_review_failures": 2, "max_turns": 15,
                "session_ttl_hours": 24, "require_identity_confirm": True}
        base.update(overrides)
        monkeypatch.setattr("src.core.config.chat_settings", lambda: base)
    return _set


class TestReviewGate:
    @pytest.mark.asyncio
    async def test_disabled_switch_records_lint_only(self, settings):
        settings(require_prompt_review=False)
        reviewer = _StubAgent(default={"verdict": "pass"})
        engine, _ = await _engine(reviewer=reviewer)
        session = _session()
        halted = await engine._review_prompts(session, turn=2)
        assert halted is False
        assert session["artifacts"]["prompt_review"]["status"] == "disabled"
        assert session["artifacts"]["prompt_lint"]["checked"] == len(platform_slot_plan(PLATFORM))
        assert reviewer.calls == 0

    @pytest.mark.asyncio
    async def test_low_score_patch_is_applied(self, settings):
        settings()
        patch = "品牌深蓝 #1B3F94 背景无缝，纸盒居中占画面 88%，四周留白与短投影，柔光精修"
        reviewer = _StubAgent(default={
            "verdict": "revise", "scores": {"main_white": 60},
            "slots": [{"slot_id": "main_white", "score": 60,
                       "defects": ["缺少光位与投影"], "scene": patch}]})
        engine, _ = await _engine(reviewer=reviewer)
        session = _session()
        await engine._review_prompts(session, turn=2)
        review = session["artifacts"]["prompt_review"]
        assert review["status"] == "reviewed"
        assert review["verdict"] == "revised"
        assert review["revised_slots"] == ["main_white"]
        slots = {slot["slot_id"]: slot for slot in session["artifacts"]["set_plan"]["slots"]}
        assert slots["main_white"]["prompt"].startswith("品牌深蓝")
        assert slots["main_white"]["revised_by_reviewer"] is True
        # 其他槽位原稿未动
        assert slots["main_scene"]["prompt"] == CLEAN_SCENE
        # main_image 跟随第 1 张（finalize_prompts 的兼容字段）
        assert session["artifacts"]["prompts"]["main_image"]["prompt"].startswith("品牌深蓝")

    @pytest.mark.asyncio
    async def test_patch_that_breaks_lint_is_rejected(self, settings):
        settings()
        reviewer = _StubAgent(default={
            "verdict": "revise", "scores": {"main_white": 40},
            "slots": [{"slot_id": "main_white", "score": 40,
                       "scene": CLEAN_SCENE + "，包装上写着 60's"}]})
        engine, _ = await _engine(reviewer=reviewer)
        session = _session()
        await engine._review_prompts(session, turn=2)
        review = session["artifacts"]["prompt_review"]
        assert review["revised_slots"] == []
        assert review["refine_rejected"]
        slots = {slot["slot_id"]: slot
                 for slot in session["artifacts"]["prompts"]["set_plan"]["slots"]}
        assert slots["main_white"]["prompt"] == CLEAN_SCENE

    @pytest.mark.asyncio
    async def test_reviewer_error_is_skipped_not_blocking(self, settings):
        settings()
        reviewer = _StubAgent(default={"error": "boom"})
        engine, _ = await _engine(reviewer=reviewer)
        session = _session()
        halted = await engine._review_prompts(session, turn=2)
        assert halted is False
        review = session["artifacts"]["prompt_review"]
        assert review["status"] == "error"
        assert review["verdict"] == "skipped"
        assert "体检仍有效" in review["message"]

    @pytest.mark.asyncio
    async def test_same_digest_is_not_reviewed_twice(self, settings):
        settings()
        reviewer = _StubAgent(default={"verdict": "pass", "scores": {}})
        engine, _ = await _engine(reviewer=reviewer)
        session = _session()
        await engine._review_prompts(session, turn=2)
        first_calls = reviewer.calls
        await engine._review_prompts(session, turn=3)
        assert reviewer.calls == first_calls   # digest 未变 → 不重复花钱

    @pytest.mark.asyncio
    async def test_hard_lint_error_triggers_single_regeneration(self, settings):
        settings()
        # 原稿里带品牌词（会命中 identity 检查需身份卡；这里用"包装上写着"制造硬伤）
        prompts = _prompts(scene=CLEAN_SCENE + "，包装上写着 60's")
        prompt_gen = _StubAgent(default=_prompts())
        reviewer = _StubAgent(default={"verdict": "pass", "scores": {}})
        engine, _ = await _engine(prompt_gen=prompt_gen, reviewer=reviewer)
        session = _session(prompts)
        await engine._review_prompts(session, turn=2)
        assert prompt_gen.calls == 1                     # 打回重写一次
        assert session[ChatEngine._PROMPT_REVIEW_ROUND_KEY] == 1
        assert any("硬伤" in str(msg.get("content", {}).get("message", ""))
                   for msg in session["messages"])

    @pytest.mark.asyncio
    async def test_confirm_switch_pauses_when_still_below_threshold(self, settings):
        settings(require_prompt_confirm=True)
        reviewer = _StubAgent(default={"verdict": "revise", "scores": {"main_white": 50},
                                       "slots": [{"slot_id": "main_white", "score": 50}]})
        engine, _ = await _engine(reviewer=reviewer)
        session = _session()
        halted = await engine._review_prompts(session, turn=2)
        assert halted is True
        assert session["status"] == "waiting_human"
        hitl = [msg["content"] for msg in session["messages"]
                if isinstance(msg.get("content"), dict) and msg["content"].get("hitl")]
        assert hitl and hitl[-1]["hitl"] == "prompt_review_needed"

    @pytest.mark.asyncio
    async def test_no_plan_does_nothing(self, settings):
        settings()
        reviewer = _StubAgent(default={"verdict": "pass"})
        engine, _ = await _engine(reviewer=reviewer)
        session = _session(prompts={"main_image": {"prompt": "旧路径"}})
        halted = await engine._review_prompts(session, turn=2)
        assert halted is False
        assert "prompt_review" not in session["artifacts"]
        assert reviewer.calls == 0


class TestPromptStage:
    @pytest.mark.asyncio
    async def test_prompt_stage_generates_reviews_and_records(self, settings):
        settings()
        prompt_gen = _StubAgent(default=_prompts())
        reviewer = _StubAgent(default={"verdict": "pass", "scores": {}})
        engine, _ = await _engine(prompt_gen=prompt_gen, reviewer=reviewer)
        session = _session(prompts={})
        result, halted = await engine._prompt_stage(session, "生成提示词", turn=2)
        assert halted is False
        assert result["set_plan"]["slots"]
        assert session["artifacts"]["prompts"]["set_plan"]
        assert session["artifacts"]["prompt_lint"]["checked"] == len(platform_slot_plan(PLATFORM))
        assert any(msg.get("sender") == "提示词生成员" for msg in session["messages"])

    @pytest.mark.asyncio
    async def test_prompt_stage_passes_aesthetic_feedback(self, settings, monkeypatch):
        settings(require_prompt_review=False)
        prompt_gen = _StubAgent(default=_prompts())
        engine, _ = await _engine(prompt_gen=prompt_gen)
        session = _session(prompts={})
        await engine._prompt_stage(session, "重新生成提示词", turn=2,
                                   aesthetic_feedback="上一轮审查得分 70\n问题：偏暖黄")
        assert "偏暖黄" in prompt_gen.last_brief
        assert "审查意见" in prompt_gen.last_brief

    @pytest.mark.asyncio
    async def test_review_feedback_helper(self):
        feedback = ChatEngine._review_feedback({
            "overall_score": 70, "top_issues": ["偏暖黄", "留白不足"],
            "fix_direction": ["中性白平衡", "加大留白"]})
        assert "70" in feedback and "偏暖黄" in feedback and "中性白平衡" in feedback
        assert ChatEngine._review_feedback({}) == ""
