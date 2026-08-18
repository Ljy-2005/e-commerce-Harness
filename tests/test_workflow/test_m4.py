"""M4 测试 — 审批 SLA 超时自动决策 / webhook 工具 / 模板导入导出"""

import asyncio
import io

import pytest

from src.workflow import templates
from src.workflow.engine import _Runtime
from src.workflow.models import StepRecord, StepStatus
from src.workflow.tools import run_tool, list_tools


class TestSlaTimeout:
    """human 节点 SLA 超时自动决策"""

    def _make_step(self, job):
        return StepRecord(job_id=job.job_id, node="h", type="human", order=0)

    @pytest.mark.asyncio
    async def test_auto_approve_on_timeout(self, engine, store, job_inputs):
        job = templates.instantiate("scene_suite", job_inputs, mode="auto")
        await store.create_job(job)
        engine._runtimes[job.job_id] = _Runtime()
        step = self._make_step(job)
        cfg = {"type": "human", "prompt": "x",
               "sla_minutes": 0.001, "on_sla_timeout": "auto_approve",  # 0.06 秒
               "routes": {"approve": "notify", "retry": "retry_node", "reject": "failed_end"}}
        next_node = await engine._run_human(job, cfg, step, {"h": None})
        assert next_node == "notify"
        assert step.status == StepStatus.SUCCEEDED
        assert step.outputs["decision"] == "auto_approve"
        assert step.outputs["sla_timeout"] is True
        # SLA 超时事件已记录
        events = await store.get_events(job.job_id)
        assert any(e["event"] == "human_sla_timeout" and e["payload"]["action"] == "auto_approve"
                   for e in events)

    @pytest.mark.asyncio
    async def test_auto_reject_on_timeout(self, engine, store, job_inputs):
        job = templates.instantiate("scene_suite", job_inputs, mode="auto")
        await store.create_job(job)
        engine._runtimes[job.job_id] = _Runtime()
        step = self._make_step(job)
        cfg = {"type": "human", "prompt": "x",
               "sla_minutes": 0.001, "on_sla_timeout": "auto_reject",
               "routes": {"approve": "a", "retry": "r", "reject": "failed_end"}}
        next_node = await engine._run_human(job, cfg, step, {"h": None})
        assert next_node == "failed_end"
        assert step.outputs["decision"] == "auto_reject"

    @pytest.mark.asyncio
    async def test_decision_before_timeout_wins(self, engine, store, job_inputs):
        """决策先于 SLA 到期到达 → 以人工决策为准"""
        job = templates.instantiate("scene_suite", job_inputs, mode="auto")
        await store.create_job(job)
        engine._runtimes[job.job_id] = _Runtime()
        step = self._make_step(job)
        cfg = {"type": "human", "prompt": "x",
               "sla_minutes": 60, "on_sla_timeout": "auto_approve",
               "routes": {"approve": "a", "retry": "r", "reject": "rej"}}
        task = asyncio.create_task(engine._run_human(job, cfg, step, {"h": None}))
        await asyncio.sleep(0.2)
        await engine.decide_human(job.job_id, "reject")
        next_node = await asyncio.wait_for(task, timeout=10)
        assert next_node == "rej"
        assert step.outputs["decision"] == "reject"

    @pytest.mark.asyncio
    async def test_keep_waiting_never_auto_decides(self, engine, store, job_inputs):
        """keep_waiting（默认）+ SLA 配置 → 不会自动决策，等人工"""
        job = templates.instantiate("scene_suite", job_inputs, mode="auto")
        await store.create_job(job)
        engine._runtimes[job.job_id] = _Runtime()
        step = self._make_step(job)
        cfg = {"type": "human", "prompt": "x",
               "sla_minutes": 0.001, "on_sla_timeout": "keep_waiting",
               "routes": {"approve": "a", "retry": "r", "reject": "rej"}}
        task = asyncio.create_task(engine._run_human(job, cfg, step, {"h": None}))
        await asyncio.sleep(0.2)  # 远超 SLA，仍未决策
        assert not task.done()
        await engine.decide_human(job.job_id, "approve")
        next_node = await asyncio.wait_for(task, timeout=10)
        assert next_node == "a"


class TestWebhookNotifyTool:
    @pytest.mark.asyncio
    async def test_empty_url_skips(self):
        result = await run_tool("webhook_notify", {"url": "", "event": "x", "payload": {}})
        assert result["ok"] is True
        assert result["status_code"] == 0

    @pytest.mark.asyncio
    async def test_success_post(self, monkeypatch):
        import httpx as _httpx

        class _Resp:
            status_code = 200

        class _FakeClient:
            def __init__(self, *a, **kw):
                self.posts = []
            async def __aenter__(self):
                return self
            async def __aexit__(self, *a):
                return False
            async def post(self, url, json=None):
                self.posts.append({"url": url, "json": json})
                return _Resp()

        state = {"client": None}
        def _make(*a, **kw):
            state["client"] = _FakeClient()
            return state["client"]
        monkeypatch.setattr(_httpx, "AsyncClient", _make)

        result = await run_tool("webhook_notify", {
            "url": "http://example.com/hook", "event": "job_completed", "payload": {"a": 1},
        })
        assert result["ok"] is True
        assert result["status_code"] == 200
        sent = state["client"].posts[0]
        assert sent["url"] == "http://example.com/hook"
        assert sent["json"]["event"] == "job_completed"

    @pytest.mark.asyncio
    async def test_failure_returns_error_dict(self, monkeypatch):
        import httpx as _httpx

        class _FakeClient:
            def __init__(self, *a, **kw):
                pass
            async def __aenter__(self):
                return self
            async def __aexit__(self, *a):
                return False
            async def post(self, url, json=None):
                raise RuntimeError("connection refused")

        monkeypatch.setattr(_httpx, "AsyncClient", _FakeClient)
        result = await run_tool("webhook_notify", {"url": "http://nope", "event": "x"})
        assert result["ok"] is False
        assert "connection refused" in result["error"]

    def test_tool_registered(self):
        assert "webhook_notify" in list_tools()
