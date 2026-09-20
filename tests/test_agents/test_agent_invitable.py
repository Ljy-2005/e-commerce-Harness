"""后台 Agent（`invitable: false`）—— 不进群聊名单、不被执行、且 YAML 真的生效

用户 2026-09-18 的质疑（这一整份测试就是为它写的）：

> "为什么不跟中心决策者说明风格分析员不参与会话，而是在风格分析员里写入，
> 这不会导致中心决策者还会调用风格分析员，只是会被拒绝而已吧？这样不会导致消耗额外的 token 吗"

结论：**对"永不邀请"的 Agent，正确做法是让它不出现在名单里**（而不是在协调者的提示词里
写一句"不要邀请它"——那句提示每次决策都要付 token，还把名字塞进上下文、反而可能诱导模型去提）。
名单由 `coordinator._build_agent_list()` 从注册表生成，所以：

1. `AgentMeta.invitable` 必须在 `registry.load_from_config()` 里**显式取出**——`AgentMeta` 是
   逐字段构造的（不传 cfg），漏掉就静默失效（与 A34/A41"YAML 的 timeout/retry 只进 meta"
   同族的坑，这条是专门钉它的回归测试）；
2. 协调者名单里**看不到**它；
3. 万一是模型幻觉出来的名字，引擎的邀请路径**不会调用任何 Provider**（不产生费用）；
4. 但 `/api/agents`、`/api/capabilities`、工作流模板校验等**仍然看得到全部 Agent**（过滤只作用于群聊名单）。
"""

import pytest

from src.agents.coordinator import CoordinatorAgent
from src.agents.registry import AgentRegistry
from src.core.models import AgentMeta
from src.providers import get_provider_registry


def _load_registry() -> AgentRegistry:
    import asyncio

    registry = AgentRegistry()
    asyncio.run(registry.load_from_config(get_provider_registry()))
    return registry


async def _load_registry_async() -> AgentRegistry:
    """异步测试里不能用 `asyncio.run`（会撞上正在运行的事件循环）"""
    registry = AgentRegistry()
    await registry.load_from_config(get_provider_registry())
    return registry


class TestInvitableFlag:
    def test_yaml_flag_reaches_meta(self):
        """**专钉 A34/A41 同族坑**：YAML 写了 invitable: false 就必须落到 AgentMeta"""
        registry = _load_registry()
        meta = registry.get_meta("风格档案员")
        assert meta is not None, "风格档案员未注册（检查 config/agents/style_archivist.yaml）"
        assert meta.invitable is False, "YAML 的 invitable: false 没有生效（逐字段构造漏了这一项）"

    def test_chat_agents_default_invitable(self):
        registry = _load_registry()
        assert registry.get_meta("生图员").invitable is True
        assert registry.get_meta("审查员").invitable is True

    def test_get_invitable_refuses_background_agent(self):
        registry = _load_registry()
        assert registry.get("风格档案员") is not None, "后台 Agent 仍应可被界面/脚本直接取用"
        assert registry.get_invitable("风格档案员") is None
        assert registry.get_invitable("生图员") is not None
        assert registry.get_invitable("不存在的 Agent") is None

    def test_list_all_scopes(self):
        registry = _load_registry()
        everything = {meta.name for meta in registry.list_all()}
        invitable = {meta.name for meta in registry.list_all(invitable_only=True)}
        assert "风格档案员" in everything
        assert "风格档案员" not in invitable


class TestCoordinatorRoster:
    def test_background_agent_not_in_roster(self):
        registry = _load_registry()
        coordinator = CoordinatorAgent(registry=registry)
        roster = coordinator._build_agent_list()
        assert "风格档案员" not in roster
        assert "生图员" in roster and "审查员" in roster

    def test_roster_is_not_longer_than_before(self):
        """加一个后台 Agent **不该**让名单变长（每一轮决策都付这份 token）"""
        registry = _load_registry()
        coordinator = CoordinatorAgent(registry=registry)
        lines = [line for line in coordinator._build_agent_list().splitlines() if line.strip()]
        names = {meta.name for meta in registry.list_all(invitable_only=True)}
        assert len(lines) == len(names)


class TestEngineRefusesBackgroundInvite:
    @pytest.mark.asyncio
    async def test_invite_is_refused_without_provider_call(self, monkeypatch):
        """幻觉邀请后台 Agent：**不调用任何 Provider**，只在群聊里记一条可读错误"""
        from src.chat.engine import ChatEngine
        from src.chat.session import SessionManager
        from src.core.state import RunStatus

        registry = await _load_registry_async()
        archivist = registry.get("风格档案员")
        calls = {"n": 0}

        class _Spy:
            name = "spy"
            capabilities = ["vision"]

            async def chat_with_vision(self, messages=None, **kwargs):
                calls["n"] += 1
                return {"content": {}}

        archivist.provider = _Spy()

        coordinator = registry.get("中心决策者")
        decisions = [{"action": "invite", "agent_name": "风格档案员", "task_brief": "分析我的风格"},
                     {"action": "done", "agent_name": "", "task_brief": "结束"}]

        async def _decide(session):
            return decisions.pop(0)

        monkeypatch.setattr(coordinator, "decide", _decide)

        manager = SessionManager()
        session = manager.create(product_images=[], product_info="x", platform="taobao")
        session["status"] = RunStatus.RUNNING.value
        engine = ChatEngine(registry=registry, session_manager=manager)

        await engine.run(session)

        assert calls["n"] == 0, "后台 Agent 被真的执行了（会产生费用）"
        errors = [message for message in session["messages"]
                  if str((message.get("content") or {}).get("error", "")).find("后台 Agent") >= 0]
        assert errors, "拒绝邀请时应给出可读原因，供协调者自我纠正"
        assert "不参与群聊" in errors[0]["content"]["error"]


def test_meta_default_is_invitable():
    """纯增量字段：不写就是可邀请（老 YAML 零影响）"""
    assert AgentMeta(name="x", description="y").invitable is True
