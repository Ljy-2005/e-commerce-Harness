"""Workflow 编排层 — 批量调度器（M2）

商品列表 → 逐项实例化 WorkflowJob → 队列 → 并发窗口 → 逐项执行 → 汇总
- 失败隔离：单项失败不影响其他项；每项最多自动重试 2 次 → 死信列表（可重跑）
- 控制：暂停 / 恢复 / 取消 / 重跑死信
- 断点续跑：重启后跳过已成功/已失败的项
（详见 docs/workflow-design.md §5）
"""

import asyncio
import uuid
from datetime import datetime, timezone

from src.core.logging_config import get_logger
from src.workflow import templates as tpl_mod
from src.workflow.engine import WorkflowEngine
from src.workflow.job_store import JobStore

_batch_logger = get_logger(__name__)

ITEM_PENDING = "pending"
ITEM_RUNNING = "running"
ITEM_SUCCEEDED = "succeeded"
ITEM_FAILED = "failed"

BATCH_CREATED = "created"
BATCH_RUNNING = "running"
BATCH_PAUSED = "paused"
BATCH_COMPLETED = "completed"
BATCH_PARTIAL = "partial"      # 有死信项
BATCH_CANCELLED = "cancelled"

# 每项最多自动重试次数（不含首次）
MAX_ITEM_RETRIES = 2

# 单批次商品项上限（审计修复：与 API 端点一致，调度器层兜底防无界 job）
MAX_BATCH_ITEMS = 100


def _make_done_callback(batch_id: str):
    """审计修复 #12：run 任务异常必须被取回并记录，否则批次卡 CREATED/RUNNING"""
    def _on_done(t: asyncio.Task):
        try:
            t.result()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            _batch_logger.error("批次执行异常", extra={"batch_id": batch_id, "error": str(e)[:300]})
    return _on_done


def _now() -> datetime:
    return datetime.now(timezone.utc)


class _BatchRuntime:
    """单个批次的运行期控制（单进程内存）"""

    def __init__(self):
        self.pause_requested = False
        self.cancelled = False
        self.wake_generation = 0            # 每次信号 +1（多 worker 等待用）
        self.wake_event = asyncio.Event()   # 常驻事件，不重建
        self.task: asyncio.Task | None = None


class BatchScheduler:
    """批量调度器：队列 + 并发窗口 + 失败隔离 + 死信"""

    def __init__(self, engine: WorkflowEngine, store: JobStore):
        self.engine = engine
        self.store = store
        self._runtimes: dict[str, _BatchRuntime] = {}

    # ── 对外入口 ──

    async def submit(
        self,
        template_name: str,
        items: list[dict],
        tenant_id: str = "default",
        mode: str = "auto",
        max_concurrency: int = 3,
    ) -> dict:
        """创建批次并异步执行。items = [{输入键值}, ...]"""
        if not tpl_mod.load_template(template_name):
            raise ValueError(f"模板 '{template_name}' 不存在")
        if not items:
            raise ValueError("批次至少需要 1 个商品项")
        if len(items) > MAX_BATCH_ITEMS:
            raise ValueError(f"单批次最多 {MAX_BATCH_ITEMS} 个商品项（当前 {len(items)}）")

        batch = {
            "batch_id": uuid.uuid4().hex[:16],
            "template_name": template_name,
            "tenant_id": tenant_id,
            "status": BATCH_CREATED,
            "mode": mode if mode in ("auto", "manual") else "auto",
            "max_concurrency": max(1, min(int(max_concurrency), 10)),
            "total": len(items),
            "done": 0,
            "failed": 0,
            "created_at": _now(),
            "updated_at": _now(),
        }
        await self.store.create_batch(batch)
        now = _now()
        await self.store.create_batch_items([
            {"batch_id": batch["batch_id"], "seq": i,
             "inputs": dict(item), "status": ITEM_PENDING,
             "updated_at": now}
            for i, item in enumerate(items)
        ])

        runtime = self._runtimes.setdefault(batch["batch_id"], _BatchRuntime())
        runtime.task = asyncio.create_task(self.run(batch["batch_id"]))
        runtime.task.add_done_callback(_make_done_callback(batch["batch_id"]))  # 审计修复 #12
        return batch

    async def control(self, batch_id: str, action: str) -> dict:
        """控制：pause / resume / cancel / retry_failed"""
        batch = await self.store.get_batch(batch_id)
        if batch is None:
            raise ValueError("批次不存在")
        runtime = self._runtimes.setdefault(batch_id, _BatchRuntime())

        if action == "pause":
            runtime.pause_requested = True
            return {"batch_id": batch_id, "action": action, "status": "pausing"}
        if action == "resume":
            runtime.pause_requested = False
            await self.store.set_batch_status(batch_id, BATCH_RUNNING)
            self._signal(runtime)
            return {"batch_id": batch_id, "action": action, "status": "resuming"}
        if action == "cancel":
            runtime.cancelled = True
            runtime.pause_requested = False
            self._signal(runtime)
            return {"batch_id": batch_id, "action": action, "status": "cancelling"}
        if action == "retry_failed":
            count = await self.store.reset_failed_items(batch_id)
            if count > 0:
                await self.store.set_batch_status(batch_id, BATCH_RUNNING)
                runtime.cancelled = False
                runtime.pause_requested = False
                self._signal(runtime)
                if not runtime.task or runtime.task.done():
                    runtime.task = asyncio.create_task(self.run(batch_id))
            return {"batch_id": batch_id, "action": action, "retried": count}
        raise ValueError(f"未知控制指令: {action}")

    # ── 主循环 ──

    async def run(self, batch_id: str) -> dict:
        batch = await self.store.get_batch(batch_id)
        runtime = self._runtimes.setdefault(batch_id, _BatchRuntime())

        items = await self.store.get_batch_items(batch_id)
        # 断点续跑：跳过已成功/已失败的项
        queue: asyncio.Queue = asyncio.Queue()
        pending = 0
        for it in items:
            if it["status"] in (ITEM_SUCCEEDED, ITEM_FAILED):
                continue
            await queue.put(it["seq"])
            pending += 1
        if pending == 0:
            # 无待办（恢复场景）→ 重新汇总终态
            await self._finalize(batch_id)
            self._runtimes.pop(batch_id, None)  # 审计修复：终态清理
            return await self.store.get_batch(batch_id)

        await self.store.set_batch_status(batch_id, BATCH_RUNNING)
        concurrency = min(batch["max_concurrency"], pending)

        async def worker(wid: int):
            while True:
                if runtime.cancelled:
                    return
                if runtime.pause_requested:
                    await self._pause(batch_id, runtime)
                    if runtime.cancelled:
                        return
                try:
                    seq = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                await self._process_item(batch_id, batch, seq)

        workers = [asyncio.create_task(worker(i)) for i in range(concurrency)]
        await asyncio.gather(*workers, return_exceptions=True)

        if runtime.cancelled:
            await self.store.set_batch_status(batch_id, BATCH_CANCELLED)
            self._runtimes.pop(batch_id, None)  # 审计修复：终态清理
            return await self.store.get_batch(batch_id)

        await self._finalize(batch_id)
        self._runtimes.pop(batch_id, None)  # 审计修复：终态清理
        return await self.store.get_batch(batch_id)

    async def _process_item(self, batch_id: str, batch: dict, seq: int):
        items = await self.store.get_batch_items(batch_id)
        item = next(it for it in items if it["seq"] == seq)
        last_error = ""

        for attempt in range(1 + MAX_ITEM_RETRIES):
            item["status"] = ITEM_RUNNING
            item["attempts"] = attempt + 1
            item["updated_at"] = _now()
            await self.store.update_batch_item(item)
            try:
                job = tpl_mod.instantiate(
                    batch["template_name"], item["inputs"],
                    tenant_id=batch["tenant_id"], mode=batch["mode"],
                )
                await self.store.create_job(job)
                item["job_id"] = job.job_id
                await self.store.update_batch_item(item)
                result = await self.engine.run(job)
                if result.status.value == "completed":
                    item["status"] = ITEM_SUCCEEDED
                    item["error"] = ""
                    await self.store.update_batch_item(item)
                    await self.store.increment_batch(batch_id, done_delta=1)
                    return
                last_error = f"job 终态: {result.status.value}"
            except asyncio.CancelledError:
                raise
            except Exception as e:
                last_error = str(e)[:300]

        # 重试耗尽 → 死信
        item["status"] = ITEM_FAILED
        item["error"] = last_error
        item["updated_at"] = _now()
        await self.store.update_batch_item(item)
        await self.store.increment_batch(batch_id, failed_delta=1)

    async def _finalize(self, batch_id: str):
        items = await self.store.get_batch_items(batch_id)
        done = sum(1 for it in items if it["status"] == ITEM_SUCCEEDED)
        failed = sum(1 for it in items if it["status"] == ITEM_FAILED)
        status = BATCH_COMPLETED if failed == 0 else BATCH_PARTIAL
        await self.store.update_batch_counts(batch_id, status, done, failed)

    async def _pause(self, batch_id: str, runtime: _BatchRuntime):
        await self.store.set_batch_status(batch_id, BATCH_PAUSED)
        await self._wait_wake(runtime)

    # ── 控制辅助 ──

    def _signal(self, runtime: _BatchRuntime):
        runtime.wake_generation += 1
        runtime.wake_event.set()

    async def _wait_wake(self, runtime: _BatchRuntime):
        """等待控制信号（多 worker 并发等待安全：世代计数 + 双检防丢失）"""
        gen = runtime.wake_generation
        while runtime.wake_generation == gen and not runtime.cancelled:
            runtime.wake_event.clear()
            if runtime.wake_generation != gen or runtime.cancelled:
                break
            await runtime.wake_event.wait()
