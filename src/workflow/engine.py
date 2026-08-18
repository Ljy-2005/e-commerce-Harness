"""Workflow 编排层 — 节点状态机引擎

自动挡 / 手动挡 / 单步控制 / 节点重试 / 人工审批 / 断点续跑
（详见 docs/workflow-design.md §4）
"""

import asyncio
import time
from datetime import datetime, timezone
from typing import Optional

from src.core.logging_config import get_logger
from src.workflow.models import WorkflowJob, StepRecord, JobStatus, StepStatus
from src.workflow import templates as tpl_mod
from src.workflow import expressions as expr
from src.workflow.tools import run_tool

_engine_logger = get_logger(__name__)

# Agent 产出 → 会话 artifacts 键的映射（与 ChatEngine._update_artifacts 一致）
_ARTIFACT_KEYS = {
    "商品分析员": "analysis",
    "品类专项分析员": "analysis",
    "提示词生成员": "prompts",
    "生图员": "images",
    "图像后处理员": "images",
    "审查员": "review",
    "合规审查员": "compliance",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


class _Runtime:
    """单个 job 的运行期控制状态（单进程内存）"""

    def __init__(self):
        self.advance_event: asyncio.Event | None = None   # 手动挡/暂停等待
        self.wake_pending = False                        # 唤醒信号先于等待就位（竞态防护）
        self.next_action: str = ""                       # step | resume
        self.pause_requested = False
        self.cancelled = False
        self.human_event: asyncio.Event | None = None
        self.human_action: str = ""
        self.step_outputs: dict[str, dict] = {}           # {node: outputs}（scope 用）
        self.artifacts: dict = {}                         # 会话 artifacts 镜像
        self.task: asyncio.Task | None = None


class WorkflowEngine:
    """工作流状态机。单进程假设：控制信号在内存，状态在 SQLite。"""

    def __init__(self, agent_registry, job_store, broadcaster=None):
        self.registry = agent_registry
        self.store = job_store
        self.broadcaster = broadcaster
        self._runtimes: dict[str, _Runtime] = {}

    # ── 事件：落库 + WS 广播 ──

    async def _emit(self, job_id: str, event: str, payload: dict | None = None) -> None:
        payload = payload or {}
        await self.store.append_event(job_id, event, payload)
        if self.broadcaster:
            try:
                await self.broadcaster.broadcast(job_id, {
                    "type": "workflow_event",
                    "event": event,
                    "payload": payload,
                    "timestamp": _now().isoformat(),
                })
            except Exception:
                pass

    # ── 对外入口 ──

    async def start(self, job: WorkflowJob) -> asyncio.Task:
        """创建任务并异步执行（与 ChatEngine.start 模式一致）"""
        runtime = self._runtimes.setdefault(job.job_id, _Runtime())
        if runtime.task and not runtime.task.done():
            return runtime.task
        runtime.task = asyncio.create_task(self.run(job))
        return runtime.task

    async def control(self, job_id: str, action: str, step_node: str = "") -> dict:
        """控制指令：run_next / pause / resume / retry_step / skip_step / cancel"""
        runtime = self._runtimes.setdefault(job_id, _Runtime())
        job = await self.store.get_job(job_id)
        if job is None:
            raise ValueError("job 不存在")

        if action == "run_next":
            if job.status != JobStatus.PAUSED:
                raise ValueError(f"job 未处于暂停状态（当前 {job.status.value}）")
            runtime.next_action = "step"
            self._signal(runtime)
            return {"job_id": job_id, "action": action, "status": "running"}

        if action == "resume":
            runtime.next_action = "resume"
            runtime.pause_requested = False
            self._signal(runtime)
            return {"job_id": job_id, "action": action, "status": "running"}

        if action == "pause":
            runtime.pause_requested = True
            return {"job_id": job_id, "action": action, "status": "pausing"}

        if action == "cancel":
            runtime.cancelled = True
            self._signal(runtime)
            return {"job_id": job_id, "action": action, "status": "cancelling"}

        if action == "retry_step":
            if not step_node:
                raise ValueError("retry_step 需要 step_node 参数")
            await self.retry_step(job_id, step_node)
            return {"job_id": job_id, "action": action, "node": step_node, "status": "retrying"}

        if action == "skip_step":
            if not step_node:
                raise ValueError("skip_step 需要 step_node 参数")
            if job.status != JobStatus.PAUSED:
                raise ValueError(f"跳过仅支持暂停状态（当前 {job.status.value}）")
            await self.skip_step(job_id, step_node)
            runtime.next_action = "step"
            self._signal(runtime)
            return {"job_id": job_id, "action": action, "node": step_node, "status": "skipped"}

        raise ValueError(f"未知控制指令: {action}")

    async def decide_human(self, job_id: str, action: str) -> dict:
        """人工审批节点决策：approve / retry / reject"""
        if action not in ("approve", "retry", "reject"):
            raise ValueError("action 必须是 approve/retry/reject")
        job = await self.store.get_job(job_id)
        if job is None:
            raise ValueError("job 不存在")
        if job.status != JobStatus.WAITING_HUMAN:
            raise ValueError(f"job 未等待人工审批（当前 {job.status.value}）")
        runtime = self._runtimes.setdefault(job_id, _Runtime())
        runtime.human_action = action
        if runtime.human_event:
            runtime.human_event.set()
        await self._emit(job_id, "human_decided", {"action": action})
        return {"job_id": job_id, "action": action}

    # ── 主循环 ──

    async def run(self, job: WorkflowJob) -> WorkflowJob:
        job_id = job.job_id
        runtime = self._runtimes.setdefault(job_id, _Runtime())
        snapshot = tpl_mod.get_snapshot(job)
        nodes = snapshot.get("nodes") or {}
        graph, start = tpl_mod.build_graph(nodes, snapshot.get("edges") or [], snapshot.get("start"))

        # 初始化步骤记录（已存在则复用 → 断点续跑）
        steps = await self.store.get_steps(job_id)
        if not steps:
            steps = [
                StepRecord(job_id=job_id, node=n, type=(nodes[n] or {}).get("type", "tool"), order=i)
                for i, n in enumerate(nodes.keys())
            ]
            await self.store.create_steps(steps)
            await self.store.update_job(job)
            await self._emit(job_id, "job_created", {"template": job.template_name})
        steps_by_node = {s.node: s for s in steps}

        # 恢复运行期镜像（步骤产出/artifacts）
        for s in steps:
            if s.status == StepStatus.SUCCEEDED and s.outputs:
                runtime.step_outputs[s.node] = s.outputs
                self._update_artifacts(runtime, nodes.get(s.node) or {}, s.outputs)

        job.status = JobStatus.RUNNING
        await self.store.update_job(job)
        await self._emit(job_id, "job_started", {"mode": job.mode})

        current = self._resume_node(steps, start)
        try:
            while current is not None:
                if runtime.cancelled:
                    job.status = JobStatus.CANCELLED
                    await self.store.update_job(job)
                    await self._emit(job_id, "job_cancelled")
                    return job

                # 每步前刷新步骤状态（外部控制如 skip_step 可能已修改存储）
                steps = await self.store.get_steps(job_id)
                steps_by_node = {s.node: s for s in steps}

                # 暂停检查（自动挡收到 pause 指令）
                if runtime.pause_requested and job.status == JobStatus.RUNNING:
                    job.status = JobStatus.PAUSED
                    await self.store.update_job(job)
                    await self._emit(job_id, "job_paused", {"reason": "control"})
                    await self._wait_advance(runtime)
                    job.status = JobStatus.RUNNING
                    await self.store.update_job(job)

                node_cfg = nodes.get(current) or {}
                step = steps_by_node[current]
                scope = expr.build_scope(job.inputs, runtime.step_outputs, job.context)

                # 已被外部跳过 → 沿默认边前进
                if step.status == StepStatus.SKIPPED:
                    current = graph.get(current)
                    continue

                # 节点跳过条件
                if node_cfg.get("when") and not expr.eval_expr(node_cfg["when"], scope):
                    step.status = StepStatus.SKIPPED
                    await self.store.update_step(step)
                    await self._emit(job_id, "step_skipped", {"node": current, "reason": "when"})
                    current = graph.get(current)
                    continue

                # 执行节点 → 返回下一节点
                current = await self._execute_node(job, current, node_cfg, step, scope, runtime, graph)

                # 回跳重试：目标节点已执行过 → 重置其声明顺序之后的所有步骤
                if current is not None and self._needs_reset(runtime, current):
                    await self._loop_back(job, runtime, current)

                # 手动挡：每步成功后暂停
                if current is not None and job.mode == "manual" and step.status == StepStatus.SUCCEEDED:
                    job.status = JobStatus.PAUSED
                    await self.store.update_job(job)
                    await self._emit(job_id, "job_paused", {"reason": "manual_mode"})
                    action = await self._wait_advance(runtime)
                    if action == "resume":
                        job.mode = "auto"
                        await self.store.update_job(job)
                    job.status = JobStatus.RUNNING
                    await self.store.update_job(job)

            # current is None → end 节点已写终态
        except asyncio.CancelledError:
            raise
        except Exception as e:
            job.status = JobStatus.FAILED
            await self.store.update_job(job)
            await self._emit(job_id, "job_failed", {"error": str(e)[:300]})
            _engine_logger.warning("workflow job 失败: %s", e, exc_info=True)

        return await self.store.get_job(job_id) or job

    # ── 节点执行 ──

    async def _execute_node(self, job, node, cfg, step, scope, runtime, graph) -> Optional[str]:
        job_id = job.job_id
        ntype = cfg.get("type", "tool")

        if ntype == "end":
            step.status = StepStatus.SUCCEEDED
            step.outputs = {"status": cfg.get("status", "completed")}
            step.finished_at = _now()
            await self.store.update_step(step)
            job.status = JobStatus.COMPLETED if cfg.get("status", "completed") != "failed" else JobStatus.FAILED
            job.updated_at = _now()
            job.cost_so_far = await self._total_cost(job_id)
            await self.store.update_job(job)
            await self._emit(
                job_id,
                "job_completed" if job.status == JobStatus.COMPLETED else "job_failed",
            )
            return None

        if ntype == "condition":
            step.status = StepStatus.RUNNING
            await self.store.update_step(step)
            await self._emit(job_id, "step_started", {"node": node, "attempt": 1})
            matched = self._match_condition(cfg, scope)
            step.outputs = {"matched": matched}
            step.status = StepStatus.SUCCEEDED
            step.finished_at = _now()
            await self.store.update_step(step)
            await self._emit(job_id, "step_succeeded", {"node": node, "matched": matched})
            if not matched:
                raise RuntimeError("条件节点无匹配规则（缺少 default）")
            return matched

        if ntype == "human":
            return await self._run_human(job, cfg, step, graph)

        # agent / tool / group_chat — 带节点级重试
        max_retries = max(1, int(cfg.get("max_retries", 1)))
        for attempt in range(max_retries):
            step.attempt = attempt + 1
            step.status = StepStatus.RUNNING
            step.started_at = _now()
            await self.store.update_step(step)
            await self._emit(job_id, "step_started", {"node": node, "attempt": attempt + 1})

            t0 = time.monotonic()
            try:
                if ntype == "agent":
                    outputs = await self._run_agent(job, cfg, scope, runtime)
                elif ntype == "tool":
                    outputs = await run_tool(cfg.get("tool", ""), self._resolve_inputs(cfg.get("inputs"), scope))
                elif ntype == "group_chat":
                    outputs = await self._run_group_chat(job, cfg)
                else:
                    raise RuntimeError(f"节点类型 '{ntype}' 暂未实现")

                step.outputs = outputs
                step.cost_usd = float(outputs.get("cost_usd", 0) or 0)
                step.status = StepStatus.SUCCEEDED
                step.elapsed_ms = (time.monotonic() - t0) * 1000
                step.finished_at = _now()
                await self.store.update_step(step)
                await self._emit(job_id, "step_succeeded", {"node": node, "attempt": attempt + 1})

                runtime.step_outputs[node] = outputs
                self._update_artifacts(runtime, cfg, outputs)
                job.cost_so_far = await self._total_cost(job_id)
                await self.store.update_job(job)

                return graph.get(node)

            except asyncio.CancelledError:
                raise
            except Exception as e:
                step.error = str(e)[:500]
                if attempt + 1 < max_retries:
                    await self._emit(job_id, "step_retrying", {"node": node, "attempt": attempt + 2})
                    continue
                step.status = StepStatus.FAILED
                step.elapsed_ms = (time.monotonic() - t0) * 1000
                step.finished_at = _now()
                await self.store.update_step(step)
                await self._emit(job_id, "step_failed", {"node": node, "error": step.error})
                if cfg.get("on_error"):
                    return cfg["on_error"]
                raise

        raise RuntimeError(f"节点 {node} 重试耗尽")

    async def _run_agent(self, job, cfg, scope, runtime) -> dict:
        agent = self.registry.get(cfg.get("agent", ""))
        if agent is None:
            raise RuntimeError(f"Agent '{cfg.get('agent')}' 未注册")
        task = expr.render_string(cfg.get("task", ""), scope)
        # M3 一键风格复刻：把已拆解的风格要素注入提示词生成员的任务
        style = job.context.get("_style_breakdown")
        if style and cfg.get("agent") == "提示词生成员":
            style_text = style.get("style_prompt_text") or str(style)
            task = f"{task}\n\n[风格复刻] 必须严格遵循以下风格拆解要素：\n{style_text}"
        session = self._build_session(job, runtime)
        result = await agent.execute(task, session)
        if "error" in result:
            raise RuntimeError(result["error"])
        return result

    async def _run_group_chat(self, job, cfg) -> dict:
        from src.chat.session import SessionManager
        from src.chat.engine import ChatEngine

        mode = cfg.get("collaboration_mode", "serial")
        if isinstance(mode, str) and mode.startswith("$"):
            scope = expr.build_scope(job.inputs, {}, job.context)
            mode = expr.resolve_path(mode, scope) or "serial"

        mgr = SessionManager()
        session = mgr.create(
            product_images=job.inputs.get("product_images", []),
            product_info=job.inputs.get("product_info", ""),
            platform=job.inputs.get("platform", "taobao"),
            category_hint=job.inputs.get("category_hint", ""),
            collaboration_mode=mode,
            tenant_id=job.tenant_id,
        )
        engine = ChatEngine(registry=self.registry, session_manager=mgr, broadcaster=self.broadcaster)
        result = await engine.run(session)
        return {
            "status": result.get("status"),
            "artifacts": result.get("artifacts", {}),
            "messages_count": len(result.get("messages", [])),
            "cost_usd": result.get("cost_so_far", 0.0),
            "session_id": session["session_id"],
        }

    async def _run_human(self, job, cfg, step, graph) -> str:
        job_id = job.job_id
        runtime = self._runtimes[job_id]
        step.status = StepStatus.WAITING_HUMAN
        step.started_at = _now()
        await self.store.update_step(step)
        job.status = JobStatus.WAITING_HUMAN
        await self.store.update_job(job)
        await self._emit(job_id, "human_waiting", {"node": step.node, "prompt": cfg.get("prompt", "")})

        runtime.human_action = ""
        runtime.human_event = asyncio.Event()
        if not runtime.human_action:
            # 决策尚未到达 → 等待（decision API 会 set human_event）
            await runtime.human_event.wait()
        runtime.human_event = None
        action = runtime.human_action

        step.outputs = {"decision": action}
        step.status = StepStatus.SUCCEEDED
        step.finished_at = _now()
        await self.store.update_step(step)
        await self._emit(job_id, "step_succeeded", {"node": step.node, "decision": action})

        if action == "retry":
            job.context["retries"] = int(job.context.get("retries", 0)) + 1
            await self.store.update_job(job)
        return (cfg.get("routes") or {}).get(action, graph.get(step.node))

    # ── 路由与辅助 ──

    def _match_condition(self, cfg, scope) -> Optional[str]:
        for rule in cfg.get("rules") or []:
            if "default" in rule:
                continue
            if expr.eval_expr(rule.get("when", ""), scope):
                return rule.get("goto")
        for rule in cfg.get("rules") or []:
            if "default" in rule:
                return rule.get("default")
        return None

    def _resolve_inputs(self, spec, scope) -> dict:
        if not isinstance(spec, dict):
            return {}
        resolved = {}
        for k, v in spec.items():
            if isinstance(v, str) and v.startswith("$"):
                resolved[k] = expr.resolve_path(v, scope)
            else:
                resolved[k] = v
        return resolved

    def _build_session(self, job, runtime) -> dict:
        # 透传全部输入（product_images / reference_images / platform / …），
        # 各 Agent 按需读取（如风格拆解员读 reference_images）
        task = dict(job.inputs)
        task.setdefault("product_images", [])
        task.setdefault("platform", "taobao")
        task.setdefault("collaboration_mode", "serial")
        return {
            "tenant_id": job.tenant_id,
            "task": task,
            "artifacts": dict(runtime.artifacts),
            "turn_count": 0,
        }

    def _update_artifacts(self, runtime, cfg, outputs: dict):
        """按 Agent 产出键合并到 artifacts 镜像（与 ChatEngine 语义一致）"""
        if cfg.get("type") == "group_chat" and isinstance(outputs.get("artifacts"), dict):
            runtime.artifacts.update(outputs["artifacts"])
            return
        agent_name = cfg.get("agent", "")
        key = _ARTIFACT_KEYS.get(agent_name)
        if key is None:
            return
        if key == "analysis":
            existing = runtime.artifacts.get("analysis", {})
            merged = dict(existing)
            merged.update(outputs)
            runtime.artifacts["analysis"] = merged
        else:
            runtime.artifacts[key] = outputs

    def _needs_reset(self, runtime, target_node) -> bool:
        """目标节点已有成功产出 → 回跳循环，需要重置下游"""
        return target_node in runtime.step_outputs

    async def _loop_back(self, job, runtime, target_node):
        """回跳重试：重置 target 及其声明顺序之后的所有步骤"""
        job_id = job.job_id
        steps = await self.store.get_steps(job_id)
        target = next(s for s in steps if s.node == target_node)
        await self.store.reset_steps_from(job_id, target.order)
        for s in steps:
            if s.order >= target.order:
                runtime.step_outputs.pop(s.node, None)
        job.context["retries"] = int(job.context.get("retries", 0)) + 1
        await self.store.update_job(job)
        await self._emit(job_id, "job_loop", {"node": target_node, "retries": job.context["retries"]})

    def _resume_node(self, steps, start) -> Optional[str]:
        """断点续跑：第一个未成功/未跳过的节点"""
        for s in sorted(steps, key=lambda x: x.order):
            if s.status in (StepStatus.PENDING, StepStatus.RUNNING, StepStatus.FAILED, StepStatus.WAITING_HUMAN):
                return s.node
        return None

    async def _total_cost(self, job_id) -> float:
        steps = await self.store.get_steps(job_id)
        return round(sum(s.cost_usd for s in steps), 6)

    # ── 控制辅助 ──

    def _signal(self, runtime: _Runtime):
        runtime.wake_pending = True
        if runtime.advance_event:
            runtime.advance_event.set()

    async def _wait_advance(self, runtime: _Runtime) -> str:
        """等待控制信号，返回动作（step / resume / ""）。

        竞态防护：信号（或 cancel）可能先于等待就位。
        """
        if runtime.wake_pending:
            runtime.wake_pending = False
            action = runtime.next_action
            runtime.next_action = ""
            return action
        runtime.advance_event = asyncio.Event()
        try:
            await runtime.advance_event.wait()
        finally:
            runtime.advance_event = None
            runtime.wake_pending = False
        action = runtime.next_action
        runtime.next_action = ""
        return action

    async def retry_step(self, job_id: str, step_node: str):
        """重跑指定步骤（用输入快照 + 模板快照，重放语义）"""
        job = await self.store.get_job(job_id)
        if job is None:
            raise ValueError("job 不存在")
        steps = await self.store.get_steps(job_id)
        target = next((s for s in steps if s.node == step_node), None)
        if target is None:
            raise ValueError(f"节点 '{step_node}' 不存在")
        await self.store.reset_steps_from(job_id, target.order)
        job.status = JobStatus.RUNNING
        job.context["retries"] = int(job.context.get("retries", 0)) + 1
        await self.store.update_job(job)
        await self._emit(job_id, "step_retrying", {"node": step_node})
        runtime = self._runtimes.setdefault(job_id, _Runtime())
        for s in steps:
            if s.order >= target.order:
                runtime.step_outputs.pop(s.node, None)
        if runtime.task and not runtime.task.done():
            runtime.task.cancel()
        await self.start(job)

    async def skip_step(self, job_id: str, step_node: str):
        steps = await self.store.get_steps(job_id)
        target = next((s for s in steps if s.node == step_node), None)
        if target is None:
            raise ValueError(f"节点 '{step_node}' 不存在")
        target.status = StepStatus.SKIPPED
        await self.store.update_step(target)
        await self._emit(job_id, "step_skipped", {"node": step_node, "manual": True})

    async def replicate_style(self, job_id: str, reference_images: list[str],
                              target_node: str = "prompt") -> dict:
        """M3 一键风格复刻：拆解参考图风格 → 存入 job 上下文 → 从提示词节点重跑

        reference_images: 参考图的 base64 列表（1-3 张）
        """
        job = await self.store.get_job(job_id)
        if job is None:
            raise ValueError("job 不存在")
        agent = self.registry.get("风格拆解员")
        if agent is None:
            raise ValueError("风格拆解员未注册（检查 config/agents/style_analyst.yaml）")
        if not reference_images:
            raise ValueError("请至少提供 1 张参考图")

        session = {
            "tenant_id": job.tenant_id,
            "task": {"reference_images": reference_images},
            "artifacts": {},
            "turn_count": 0,
        }
        breakdown = await agent.execute("拆解参考图的风格要素", session)
        if "error" in breakdown:
            raise RuntimeError(breakdown["error"])

        job.context["_style_breakdown"] = breakdown
        await self.store.update_job(job)
        await self._emit(job_id, "style_replicated", {
            "style_tags": breakdown.get("style_tags", []),
        })

        # 从提示词节点重跑（引擎会把风格要素注入提示词生成员任务）
        await self.retry_step(job_id, target_node)
        return breakdown
