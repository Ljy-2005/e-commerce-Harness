"""Workflow 编排层 — SQLite 持久化（标准库 + asyncio.to_thread，WAL 模式）

表：jobs / steps / events（事件溯源，见 docs/workflow-design.md §6）
"""

import asyncio
import contextlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.workflow.models import JobStatus, StepRecord, StepStatus, WorkflowJob

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    template_name TEXT NOT NULL,
    version TEXT,
    tenant_id TEXT NOT NULL,
    status TEXT NOT NULL,
    mode TEXT NOT NULL,
    inputs_json TEXT,
    context_json TEXT,
    cost_so_far REAL DEFAULT 0,
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS steps (
    step_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    node TEXT NOT NULL,
    type TEXT NOT NULL,
    order_idx INTEGER DEFAULT 0,
    status TEXT NOT NULL,
    attempt INTEGER DEFAULT 0,
    inputs_json TEXT,
    outputs_json TEXT,
    error TEXT DEFAULT '',
    elapsed_ms REAL DEFAULT 0,
    cost_usd REAL DEFAULT 0,
    started_at TEXT,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_steps_job ON steps(job_id, order_idx);
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    event TEXT NOT NULL,
    payload_json TEXT,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_job ON events(job_id, seq);
CREATE TABLE IF NOT EXISTS batches (
    batch_id TEXT PRIMARY KEY,
    template_name TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    status TEXT NOT NULL,
    mode TEXT NOT NULL,
    max_concurrency INTEGER DEFAULT 3,
    total INTEGER DEFAULT 0,
    done INTEGER DEFAULT 0,
    failed INTEGER DEFAULT 0,
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS batch_items (
    batch_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    inputs_json TEXT,
    job_id TEXT DEFAULT '',
    status TEXT NOT NULL,
    attempts INTEGER DEFAULT 0,
    error TEXT DEFAULT '',
    updated_at TEXT,
    PRIMARY KEY (batch_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_batch_items ON batch_items(batch_id, status);
"""


@contextlib.contextmanager
def _connect(db_path: Path):
    """打开 SQLite 连接：commit on success + 显式 close（审计修复：
    此前只 commit 不 close，连接依赖 GC 回收，负载下会有 ResourceWarning）"""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _iso(dt) -> str:
    if dt is None:
        return ""
    if isinstance(dt, str):
        return dt
    return dt.isoformat()


def _parse_dt(s: str) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _job_from_row(row) -> WorkflowJob:
    return WorkflowJob(
        job_id=row["job_id"],
        template_name=row["template_name"],
        version=row["version"] or "1.0.0",
        tenant_id=row["tenant_id"],
        status=JobStatus(row["status"]),
        mode=row["mode"],
        inputs=json.loads(row["inputs_json"] or "{}"),
        context=json.loads(row["context_json"] or "{}"),
        cost_so_far=row["cost_so_far"] or 0.0,
        created_at=_parse_dt(row["created_at"]) or datetime.now(timezone.utc),
        updated_at=_parse_dt(row["updated_at"]) or datetime.now(timezone.utc),
    )


def _step_from_row(row) -> StepRecord:
    return StepRecord(
        step_id=row["step_id"],
        job_id=row["job_id"],
        node=row["node"],
        type=row["type"],
        order=row["order_idx"],
        status=StepStatus(row["status"]),
        attempt=row["attempt"] or 0,
        inputs=json.loads(row["inputs_json"] or "{}"),
        outputs=json.loads(row["outputs_json"] or "{}"),
        error=row["error"] or "",
        elapsed_ms=row["elapsed_ms"] or 0.0,
        # 步骤 cost_usd 仍是 REAL 列（**不加列**：本库只有 CREATE TABLE IF NOT EXISTS，
        # 没有迁移机制，老库不会加列）。"价格未标定"的事实存在 outputs_json 的
        # `cost_unknown`/`usage` 里，报表与界面据此显示"另有 N 次未标定"。
        cost_usd=row["cost_usd"] if row["cost_usd"] is not None else 0.0,
        started_at=_parse_dt(row["started_at"]),
        finished_at=_parse_dt(row["finished_at"]),
    )


class JobStore:
    """Workflow 持久化：job / step / event"""

    def __init__(self, db_path: Path | None = None):
        self._explicit_db_path = Path(db_path) if db_path else None

    @property
    def db_path(self) -> Path:
        """SQLite 路径：显式注入优先；否则 data_root()/workflow.db。

        第三轮审计 B2-18：默认值在使用时解析（此前是模块级常量，import 期就锁定
        了真实 data/ 目录，测试无法重定向）。
        """
        from src.core.config import data_root
        return self._explicit_db_path or (data_root() / "workflow.db")

    # ── job ──

    async def create_job(self, job: WorkflowJob) -> None:
        def _do():
            with _connect(self.db_path) as conn:
                conn.execute(
                    """INSERT OR REPLACE INTO jobs
                       (job_id, template_name, version, tenant_id, status, mode,
                        inputs_json, context_json, cost_so_far, created_at, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (job.job_id, job.template_name, job.version, job.tenant_id,
                     job.status.value, job.mode,
                     json.dumps(job.inputs, ensure_ascii=False),
                     json.dumps(job.context, ensure_ascii=False),
                     job.cost_so_far, _iso(job.created_at), _iso(job.updated_at)),
                )
        await asyncio.to_thread(_do)

    async def get_job(self, job_id: str) -> Optional[WorkflowJob]:
        def _do():
            with _connect(self.db_path) as conn:
                row = conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
                return _job_from_row(row) if row else None
        return await asyncio.to_thread(_do)

    async def list_jobs(self, tenant_id: str = "", limit: int = 50) -> list[WorkflowJob]:
        def _do():
            with _connect(self.db_path) as conn:
                if tenant_id:
                    rows = conn.execute(
                        "SELECT * FROM jobs WHERE tenant_id=? ORDER BY created_at DESC LIMIT ?",
                        (tenant_id, limit),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
                    ).fetchall()
                return [_job_from_row(r) for r in rows]
        return await asyncio.to_thread(_do)

    async def update_job(self, job: WorkflowJob) -> None:
        await self.create_job(job)  # INSERT OR REPLACE

    # ── steps ──

    async def create_steps(self, steps: list[StepRecord]) -> None:
        def _do():
            with _connect(self.db_path) as conn:
                for s in steps:
                    conn.execute(
                        """INSERT OR REPLACE INTO steps
                           (step_id, job_id, node, type, order_idx, status, attempt,
                            inputs_json, outputs_json, error, elapsed_ms, cost_usd,
                            started_at, finished_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (s.step_id, s.job_id, s.node, s.type, s.order,
                         s.status.value, s.attempt,
                         json.dumps(s.inputs, ensure_ascii=False),
                         json.dumps(s.outputs, ensure_ascii=False),
                         s.error, s.elapsed_ms, s.cost_usd,
                         _iso(s.started_at), _iso(s.finished_at)),
                    )
        await asyncio.to_thread(_do)

    async def update_step(self, step: StepRecord) -> None:
        await self.create_steps([step])

    async def get_steps(self, job_id: str) -> list[StepRecord]:
        def _do():
            with _connect(self.db_path) as conn:
                rows = conn.execute(
                    "SELECT * FROM steps WHERE job_id=? ORDER BY order_idx",
                    (job_id,),
                ).fetchall()
                return [_step_from_row(r) for r in rows]
        return await asyncio.to_thread(_do)

    async def reset_steps_from(self, job_id: str, order_idx: int) -> None:
        """把 order >= order_idx 的步骤重置为 PENDING（回跳重试用）"""
        def _do():
            with _connect(self.db_path) as conn:
                conn.execute(
                    """UPDATE steps SET status='pending', outputs_json='{}', error='',
                       elapsed_ms=0, cost_usd=0, started_at=NULL, finished_at=NULL
                       WHERE job_id=? AND order_idx>=?""",
                    (job_id, order_idx),
                )
        await asyncio.to_thread(_do)

    # ── events ──

    async def append_event(self, job_id: str, event: str, payload: dict | None = None) -> None:
        def _do():
            with _connect(self.db_path) as conn:
                conn.execute(
                    "INSERT INTO events (job_id, event, payload_json, created_at) VALUES (?,?,?,?)",
                    (job_id, event, json.dumps(payload or {}, ensure_ascii=False),
                     datetime.now(timezone.utc).isoformat()),
                )
        await asyncio.to_thread(_do)

    async def get_events(self, job_id: str, since_seq: int = 0) -> list[dict]:
        def _do():
            with _connect(self.db_path) as conn:
                rows = conn.execute(
                    "SELECT * FROM events WHERE job_id=? AND seq>? ORDER BY seq",
                    (job_id, since_seq),
                ).fetchall()
                return [
                    {"seq": r["seq"], "event": r["event"],
                     "payload": json.loads(r["payload_json"] or "{}"),
                     "created_at": r["created_at"]}
                    for r in rows
                ]
        return await asyncio.to_thread(_do)

    # ── batches（批量调度，M2） ──

    async def create_batch(self, batch: dict) -> None:
        def _do():
            with _connect(self.db_path) as conn:
                conn.execute(
                    """INSERT OR REPLACE INTO batches
                       (batch_id, template_name, tenant_id, status, mode, max_concurrency,
                        total, done, failed, created_at, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (batch["batch_id"], batch["template_name"], batch["tenant_id"],
                     batch["status"], batch["mode"], batch.get("max_concurrency", 3),
                     batch.get("total", 0), batch.get("done", 0), batch.get("failed", 0),
                     _iso(batch.get("created_at")), _iso(batch.get("updated_at"))),
                )
        await asyncio.to_thread(_do)

    async def get_batch(self, batch_id: str) -> Optional[dict]:
        def _do():
            with _connect(self.db_path) as conn:
                row = conn.execute("SELECT * FROM batches WHERE batch_id=?", (batch_id,)).fetchone()
                if not row:
                    return None
                return {
                    "batch_id": row["batch_id"], "template_name": row["template_name"],
                    "tenant_id": row["tenant_id"], "status": row["status"],
                    "mode": row["mode"], "max_concurrency": row["max_concurrency"],
                    "total": row["total"], "done": row["done"], "failed": row["failed"],
                    "created_at": row["created_at"], "updated_at": row["updated_at"],
                }
        return await asyncio.to_thread(_do)

    async def list_batches(self, tenant_id: str = "", limit: int = 50) -> list[dict]:
        def _do():
            with _connect(self.db_path) as conn:
                if tenant_id:
                    rows = conn.execute(
                        "SELECT * FROM batches WHERE tenant_id=? ORDER BY created_at DESC LIMIT ?",
                        (tenant_id, limit),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM batches ORDER BY created_at DESC LIMIT ?", (limit,)
                    ).fetchall()
                return [
                    {
                        "batch_id": r["batch_id"], "template_name": r["template_name"],
                        "tenant_id": r["tenant_id"], "status": r["status"],
                        "mode": r["mode"], "max_concurrency": r["max_concurrency"],
                        "total": r["total"], "done": r["done"], "failed": r["failed"],
                        "created_at": r["created_at"], "updated_at": r["updated_at"],
                    }
                    for r in rows
                ]
        return await asyncio.to_thread(_do)

    async def update_batch_counts(self, batch_id: str, status: str,
                                  done: int, failed: int) -> None:
        """绝对覆盖计数（审计修复：仅用于最终调和，见 reconcile_batch_counts）"""
        def _do():
            with _connect(self.db_path) as conn:
                conn.execute(
                    """UPDATE batches SET status=?, done=?, failed=?, updated_at=?
                       WHERE batch_id=?""",
                    (status, done, failed, datetime.now(timezone.utc).isoformat(), batch_id),
                )
        await asyncio.to_thread(_do)

    async def reconcile_batch_counts(self, batch_id: str, status: str,
                                     done: int, failed: int) -> None:
        """以 items 表为准做原子调和：取 max(当前计数, 目标值)，只增不减。

        审计修复 #23：此前 _finalize 绝对覆盖 done/failed，与 worker 的
        increment_batch 并存时可能丢计数（并发读改写竞态）。
        """
        def _do():
            with _connect(self.db_path) as conn:
                row = conn.execute(
                    "SELECT done, failed FROM batches WHERE batch_id=?", (batch_id,)
                ).fetchone()
                cur_done = row["done"] if row else 0
                cur_failed = row["failed"] if row else 0
                conn.execute(
                    """UPDATE batches SET status=?, done=?, failed=?, updated_at=?
                       WHERE batch_id=?""",
                    (status, max(cur_done, done), max(cur_failed, failed),
                     datetime.now(timezone.utc).isoformat(), batch_id),
                )
        await asyncio.to_thread(_do)

    async def set_batch_status(self, batch_id: str, status: str) -> None:
        """只更新状态（计数由 increment_batch 原子维护）"""
        def _do():
            with _connect(self.db_path) as conn:
                conn.execute(
                    "UPDATE batches SET status=?, updated_at=? WHERE batch_id=?",
                    (status, datetime.now(timezone.utc).isoformat(), batch_id),
                )
        await asyncio.to_thread(_do)

    async def increment_batch(self, batch_id: str, done_delta: int = 0,
                              failed_delta: int = 0) -> None:
        """原子递增计数（并发 worker 安全）"""
        def _do():
            with _connect(self.db_path) as conn:
                conn.execute(
                    """UPDATE batches SET done=done+?, failed=failed+?, updated_at=?
                       WHERE batch_id=?""",
                    (done_delta, failed_delta, datetime.now(timezone.utc).isoformat(), batch_id),
                )
        await asyncio.to_thread(_do)

    async def create_batch_items(self, items: list[dict]) -> None:
        def _do():
            with _connect(self.db_path) as conn:
                for it in items:
                    conn.execute(
                        """INSERT OR REPLACE INTO batch_items
                           (batch_id, seq, inputs_json, job_id, status, attempts, error, updated_at)
                           VALUES (?,?,?,?,?,?,?,?)""",
                        (it["batch_id"], it["seq"],
                         json.dumps(it.get("inputs", {}), ensure_ascii=False),
                         it.get("job_id", ""), it["status"], it.get("attempts", 0),
                         it.get("error", ""), _iso(it.get("updated_at"))),
                    )
        await asyncio.to_thread(_do)

    async def get_batch_items(self, batch_id: str) -> list[dict]:
        def _do():
            with _connect(self.db_path) as conn:
                rows = conn.execute(
                    "SELECT * FROM batch_items WHERE batch_id=? ORDER BY seq", (batch_id,)
                ).fetchall()
                return [
                    {
                        "batch_id": r["batch_id"], "seq": r["seq"],
                        "inputs": json.loads(r["inputs_json"] or "{}"),
                        "job_id": r["job_id"], "status": r["status"],
                        "attempts": r["attempts"], "error": r["error"],
                        "updated_at": r["updated_at"],
                    }
                    for r in rows
                ]
        return await asyncio.to_thread(_do)

    async def update_batch_item(self, item: dict) -> None:
        await self.create_batch_items([item])

    async def reset_failed_items(self, batch_id: str) -> int:
        """死信项重置为待执行（retry_failed），返回重置数量"""
        def _do():
            with _connect(self.db_path) as conn:
                cur = conn.execute(
                    """UPDATE batch_items SET status='pending', error='', job_id=''
                       WHERE batch_id=? AND status='failed'""",
                    (batch_id,),
                )
                return cur.rowcount
        return await asyncio.to_thread(_do)

    # ── 批量报表（M5） ──

    _DURATION_BUCKETS = (
        ("0-1s", 0, 1_000),
        ("1-5s", 1_000, 5_000),
        ("5-30s", 5_000, 30_000),
        ("30s-2min", 30_000, 120_000),
        ("2min+", 120_000, None),
    )

    async def get_batch_report(self, tenant_id: str = "") -> dict:
        """聚合批量任务数据（总览 / 模板维度 / 耗时直方图 / 失败原因 Top-N）

        - 条目耗时取自关联 job 的 created_at → updated_at（无 job 的死信项不计入）
        - 失败原因归一化：取错误文本首个冒号前的前缀（如"必填输入缺失: xxx" → "必填输入缺失"）
        - tenant_id 为空时统计全部租户（管理视角），否则仅统计该租户
        - **金额口径**：`total_cost_usd` 是"已标定部分"的合计；步骤 `cost_usd` 只存已标定
          金额（NULL/0 无法区分），未标定的次数存在 `outputs_json` 的 `cost_unknown`，
          这里单独汇总成 `unknown_cost_calls` 并在界面分行显示（"已标定 ¥x ｜ 另有 N 次未标定"）
        """
        def _do():
            with _connect(self.db_path) as conn:
                where = "WHERE b.tenant_id=?" if tenant_id else ""
                params = (tenant_id,) if tenant_id else ()
                batches = conn.execute(
                    f"SELECT * FROM batches b {where} ORDER BY created_at DESC", params
                ).fetchall()
                rows = conn.execute(
                    f"""SELECT b.template_name, i.seq, i.status AS i_status, i.error,
                               j.cost_so_far, j.created_at AS j_created, j.updated_at AS j_updated,
                               COALESCE((
                                   SELECT COUNT(*) FROM steps s
                                   WHERE s.job_id = j.job_id
                                     AND s.outputs_json LIKE '%"cost_unknown": true%'
                               ), 0) AS unknown_steps
                        FROM batches b
                        LEFT JOIN batch_items i ON i.batch_id = b.batch_id
                        LEFT JOIN jobs j ON j.job_id = i.job_id AND i.job_id != ''
                        {where}
                        ORDER BY b.created_at DESC, i.seq""",
                    params,
                ).fetchall()

            # ── 聚合 ──
            item_count = succeeded = failed = pending_or_running = 0
            total_cost = 0.0
            unknown_cost_calls = 0
            durations_ms: list[float] = []
            template_map: dict[str, dict] = {}
            reason_map: dict[str, int] = {}
            batch_status: dict[str, int] = {}

            for b in batches:
                st = b["status"] or "created"
                batch_status[st] = batch_status.get(st, 0) + 1

            for r in rows:
                if r["seq"] is None:
                    # 0 条目批次：LEFT JOIN 产生一行全 NULL，不视为条目（审计修复）
                    continue
                tpl = r["template_name"]
                t = template_map.setdefault(tpl, {
                    "item_count": 0, "succeeded": 0, "failed": 0,
                    "total_cost_usd": 0.0, "unknown_cost_calls": 0, "durations_ms": [],
                })
                item_count += 1
                t["item_count"] += 1

                st = r["i_status"] or "pending"
                if st == "succeeded":
                    succeeded += 1
                    t["succeeded"] += 1
                elif st == "failed":
                    failed += 1
                    t["failed"] += 1
                    reason = (r["error"] or "未知错误").split(":")[0].strip()[:60]
                    reason_map[reason] = reason_map.get(reason, 0) + 1
                else:
                    pending_or_running += 1

                cost = r["cost_so_far"] or 0.0
                total_cost += cost
                t["total_cost_usd"] += cost
                unknown_steps = int(r["unknown_steps"] or 0)
                unknown_cost_calls += unknown_steps
                t["unknown_cost_calls"] += unknown_steps

                # 审计修复：仅终态条目计入耗时分布（running 条目的 job 时间戳是部分耗时）
                jc, ju = _parse_dt(r["j_created"]), _parse_dt(r["j_updated"])
                if st in ("succeeded", "failed") and jc and ju and ju >= jc:
                    dms = (ju - jc).total_seconds() * 1000
                    durations_ms.append(dms)
                    t["durations_ms"].append(dms)

            def _avg(xs: list[float]) -> float:
                return round(sum(xs) / len(xs), 1) if xs else 0.0

            def _rate(ok: int, bad: int) -> float:
                return round(ok / (ok + bad), 4) if (ok + bad) else 0.0

            by_template = [
                {
                    "template_name": name,
                    "item_count": t["item_count"],
                    "succeeded": t["succeeded"],
                    "failed": t["failed"],
                    "success_rate": _rate(t["succeeded"], t["failed"]),
                    # 已标定部分与"另有 N 次未标定"分开（用户口径：未标定不显示金额）
                    "total_cost_usd": round(t["total_cost_usd"], 6),
                    "unknown_cost_calls": t["unknown_cost_calls"],
                    "avg_duration_ms": _avg(t["durations_ms"]),
                }
                for name, t in sorted(template_map.items())
            ]

            histogram = [
                {
                    "bucket": label,
                    "count": sum(
                        1 for d in durations_ms
                        if d >= lo and (hi is None or d < hi)
                    ),
                }
                for label, lo, hi in self._DURATION_BUCKETS
            ]

            failure_reasons = [
                {"reason": k, "count": v}
                for k, v in sorted(reason_map.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
            ]

            return {
                "overview": {
                    "batch_count": len(batches),
                    "item_count": item_count,
                    "succeeded": succeeded,
                    "failed": failed,
                    "pending_or_running": pending_or_running,
                    "success_rate": _rate(succeeded, failed),
                    "total_cost_usd": round(total_cost, 6),
                    "unknown_cost_calls": unknown_cost_calls,
                    "avg_item_duration_ms": _avg(durations_ms),
                },
                "batch_status": batch_status,
                "by_template": by_template,
                "duration_histogram": histogram,
                "failure_reasons": failure_reasons,
            }
        return await asyncio.to_thread(_do)
