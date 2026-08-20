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

    @pytest.mark.asyncio
    @pytest.mark.parametrize("url", [
        "http://127.0.0.1:8000/hook",
        "http://localhost/hook",
        "http://169.254.169.254/latest/meta-data",
        "http://10.0.0.5/hook",
        "http://192.168.1.1/hook",
        "ftp://example.com/hook",
        "file:///etc/passwd",
        "http://[::1]/hook",
    ])
    async def test_ssrf_blocked(self, url):
        """审计修复：webhook_notify 拒绝内网/回环/私网/非 http(s) 协议"""
        result = await run_tool("webhook_notify", {"url": url, "event": "x", "payload": {}})
        assert result["ok"] is False
        assert "禁止访问" in result["error"] or "仅支持" in result["error"]

    @pytest.mark.asyncio
    async def test_public_url_still_allowed(self, monkeypatch):
        """公共域名不被误伤（example.com 解析为公网 IP）"""
        import httpx as _httpx

        class _Resp:
            status_code = 200

        class _FakeClient:
            def __init__(self, *a, **kw):
                pass
            async def __aenter__(self):
                return self
            async def __aexit__(self, *a):
                return False
            async def post(self, url, json=None):
                return _Resp()

        monkeypatch.setattr(_httpx, "AsyncClient", _FakeClient)
        result = await run_tool("webhook_notify", {
            "url": "https://example.com/hook", "event": "x", "payload": {},
        })
        assert result["ok"] is True

    def test_tool_registered(self):
        assert "webhook_notify" in list_tools()


class TestCancelWhileWaitingHuman:
    """审计修复：WAITING_HUMAN 下 cancel 必须唤醒 human_event（此前 job 永久卡死）"""

    @pytest.mark.asyncio
    async def test_cancel_wakes_waiting_human(self, engine, store, job_inputs):
        from src.workflow import templates as tpl
        from src.workflow.models import JobStatus
        job = tpl.instantiate("light_approval", job_inputs, mode="auto")
        await store.create_job(job)
        task = await engine.start(job)

        for _ in range(200):
            await asyncio.sleep(0.1)
            j = await store.get_job(job.job_id)
            if j.status.value == "waiting_human":
                break
        assert j.status.value == "waiting_human"

        await engine.control(job.job_id, "cancel")
        await asyncio.wait_for(task, timeout=30)
        j = await store.get_job(job.job_id)
        assert j.status.value == "cancelled"


class TestSlaMatrix:
    """M5b 审批 SLA 业务矩阵：表达式规则顺序匹配 → default → 遗留策略"""

    def _scope(self, score):
        """build_scope 的 step_outputs 参数 = {node: 裸 outputs}（勿再包 outputs 层）"""
        from src.workflow import expressions as expr
        return expr.build_scope(
            {"platform": "taobao"},
            {"review": {"overall_score": score}},
            {"retries": 0},
        )

    def test_first_match_wins(self, engine):
        cfg = {"sla_matrix": [
            {"when": "$steps.review.outputs.overall_score >= 75", "action": "auto_approve"},
            {"when": "$steps.review.outputs.overall_score >= 60", "action": "auto_reject"},
        ]}
        action, policy = engine._resolve_sla_policy(cfg, self._scope(82))
        assert action == "auto_approve"
        assert policy.startswith("matrix:")
        action, _ = engine._resolve_sla_policy(cfg, self._scope(65))
        assert action == "auto_reject"

    def test_default_fallback(self, engine):
        cfg = {"sla_matrix": [
            {"when": "1 == 2", "action": "auto_approve"},
            {"default": "keep_waiting"},
        ]}
        action, policy = engine._resolve_sla_policy(cfg, self._scope(10))
        assert action == "keep_waiting"
        assert policy == "matrix:default"

    def test_legacy_on_sla_timeout_fallback(self, engine):
        cfg = {"on_sla_timeout": "auto_reject"}
        action, policy = engine._resolve_sla_policy(cfg, self._scope(10))
        assert action == "auto_reject"
        assert policy == "legacy"

    def test_no_policy_keep_waiting(self, engine):
        action, _ = engine._resolve_sla_policy({}, self._scope(10))
        assert action == "keep_waiting"

    def test_missing_value_safe_false(self, engine):
        """缺失的 overall_score → 条件恒 False，不抛异常"""
        cfg = {"sla_matrix": [{"when": "$steps.review.outputs.overall_score >= 75",
                               "action": "auto_approve"},
                              {"default": "auto_reject"}]}
        scope = {"inputs": {}, "steps": {}, "ctx": {}}
        action, _ = engine._resolve_sla_policy(cfg, scope)
        assert action == "auto_reject"


class TestApprovalMatrixTemplate:
    """M5b approval_matrix 模板端到端：评分驱动超时自动决策"""

    @pytest.mark.asyncio
    async def test_score_based_auto_approve(self, engine, store, job_inputs):
        from src.workflow.models import JobStatus
        job = templates.instantiate("approval_matrix", job_inputs, mode="auto")
        await store.create_job(job)
        task = await engine.start(job)
        await asyncio.wait_for(task, timeout=60)

        job = await store.get_job(job.job_id)
        assert job.status == JobStatus.COMPLETED
        steps = await store.get_steps(job.job_id)
        human = next(s for s in steps if s.node == "human_approval")
        # Mock 审查员固定 82 分 → 矩阵首条（>=75）命中 → 自动通过
        # （输出保留原始决策 auto_approve，路由键 approve 归一化——M4 决策 14 可审计语义）
        assert human.outputs.get("decision") == "auto_approve"
        assert human.outputs.get("sla_timeout") is True

    @pytest.mark.asyncio
    async def test_auto_reject_via_matrix(self, engine, store, job_inputs):
        """矩阵 default=auto_reject → 超时自动拒绝 → job failed"""
        from src.workflow.models import JobStatus
        job = templates.instantiate("scene_suite", job_inputs, mode="auto")
        await store.create_job(job)
        engine._runtimes[job.job_id] = _Runtime()
        step = StepRecord(job_id=job.job_id, node="h", type="human", order=0)
        cfg = {"type": "human", "prompt": "x",
               "sla_minutes": 0.001,
               "sla_matrix": [
                   {"when": "$steps.review.outputs.overall_score >= 200", "action": "auto_approve"},
                   {"default": "auto_reject"},
               ],
               "routes": {"approve": "ok", "retry": "retry_node", "reject": "failed_end"}}
        next_node = await engine._run_human(job, cfg, step, {"h": None})
        assert next_node == "failed_end"
        assert step.outputs.get("decision") == "auto_reject"  # 原始决策保留（可审计）
        assert step.outputs.get("sla_timeout") is True
