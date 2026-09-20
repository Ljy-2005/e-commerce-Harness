"""第三轮审计 B3-25：会话失败原因可见（`error_history` 此前是**没人写过**的死字段）。

背景：Agent 报错时引擎只往群聊消息与审计日志里写，会话本身不带原因——
`SessionState.error_history` 定义了却只在创建时初始化为 `[]`，
前端"会话失败"页面因此无原因可显示、也无处跳转。
"""

import pytest

from src.agents.base import BaseAgent
from src.agents.registry import AgentRegistry
from src.chat.engine import ChatEngine
from src.chat.session import SessionManager
from src.harness.rate_limiter import RateLimiter
import src.agents.base as agents_base
from src.providers import get_provider_registry


@pytest.fixture(autouse=True)
def _fast_limiter(monkeypatch):
    monkeypatch.setattr(agents_base, "_rate_limiter", RateLimiter(default_rpm=100_000))


@pytest.fixture
async def engine():
    agent_registry = AgentRegistry()
    await agent_registry.load_from_config(get_provider_registry())
    session_mgr = SessionManager()
    return ChatEngine(registry=agent_registry, session_manager=session_mgr), session_mgr, agent_registry


class _FailingAnalyst(BaseAgent):
    """替换商品分析员：始终返回 Provider 错误（模拟配错 Key / 端点）"""

    meta_name = "商品分析员"

    async def _execute_impl(self, task_brief, session):
        return {"error": "DeepSeek API error: 401 @ https://api.deepseek.com/v1 — invalid api key"}


@pytest.mark.asyncio
async def test_agent_error_recorded_in_error_history(engine, sample_image_base64):
    eng, session_mgr, registry = engine
    registry._agents["商品分析员"] = _FailingAnalyst(provider=None)

    session = session_mgr.create(product_images=[sample_image_base64], product_info="测试商品")
    result = await eng.run(session)

    history = result.get("error_history") or []
    assert history, "Agent 报错必须记入 error_history（否则界面无原因可显示）"
    entry = history[0]
    assert entry["agent"] == "商品分析员"
    assert "401" in entry["error"]
    assert entry["kind"] == "agent"
    assert entry["timestamp"]


@pytest.mark.asyncio
async def test_max_turns_failure_recorded(engine, sample_image_base64):
    """超过最大轮次中止也要留原因（kind=abort）"""
    eng, session_mgr, _registry = engine
    session = session_mgr.create(product_images=[sample_image_base64], product_info="测试商品",
                                 max_turns=1)

    result = await eng.run(session)

    assert result["status"] == "failed"
    history = result.get("error_history") or []
    assert any("最大轮次" in e["error"] and e["kind"] == "abort" for e in history), history


@pytest.mark.asyncio
async def test_error_history_is_capped(engine, sample_image_base64):
    """历史上限：只保留最近 20 条（长跑会话不无限膨胀）"""
    eng, session_mgr, _registry = engine
    session = session_mgr.create(product_images=[sample_image_base64], product_info="测试商品")
    session["error_history"] = [
        {"agent": f"a{i}", "error": "e", "kind": "agent", "timestamp": "t", "turn": i}
        for i in range(25)
    ]

    eng._record_error(session, "探针", "新错误")

    assert len(session["error_history"]) <= 20
    assert session["error_history"][-1]["error"] == "新错误"


def test_record_error_helper_shape():
    """helper 结构契约：agent/error/kind/turn/timestamp 五字段，error 截断到 500"""
    eng = ChatEngine(registry=None, session_manager=SessionManager())
    session = {"session_id": "s", "turn_count": 3}

    eng._record_error(session, "审查员", "x" * 900, kind="agent")

    entry = session["error_history"][0]
    assert set(entry) == {"agent", "error", "kind", "turn", "timestamp"}
    assert entry["turn"] == 3
    assert len(entry["error"]) == 500


def test_api_exposes_error_history():
    """接缝回归：引擎写入的 error_history 必须经 `GET /api/sessions/{id}` 返回

    此前前端读 `session.error_history`、引擎也写，但**接口没返回这个字段**——
    两边各自有测试，接缝没人测（新增字段时的经典漏点）。
    """
    from fastapi.testclient import TestClient

    import src.api.main as main_mod
    from src.main import app

    state = main_mod._session_manager.create([], product_info="测试", tenant_id="default")
    state["status"] = "failed"
    state["error_history"] = [{"agent": "商品分析员", "error": "401 invalid api key",
                               "kind": "agent", "turn": 1, "timestamp": "t"}]

    resp = TestClient(app).get(f"/api/sessions/{state['session_id']}",
                               headers={"X-Tenant-ID": "default"})

    assert resp.status_code == 200
    assert resp.json()["error_history"][0]["error"] == "401 invalid api key"
