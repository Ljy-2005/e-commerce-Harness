"""Workflow 编排层 — SQLite 持久化（标准库 + asyncio.to_thread，WAL 模式）

表：jobs / steps / events（事件溯源，见 docs/workflow-design.md §6）
"""

import asyncio
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.workflow.models import WorkflowJob, StepRecord, JobStatus, StepStatus

_DB_PATH = Path(__file__).parent.parent.parent / "data" / "workflow.db"

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


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    return conn


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
        cost_usd=row["cost_usd"] or 0.0,
        started_at=_parse_dt(row["started_at"]),
        finished_at=_parse_dt(row["finished_at"]),
    )


class JobStore:
    """Workflow 持久化：job / step / event"""

    def __init__(self, db_path: Path | None = None):
        self.db_path = Path(db_path) if db_path else _DB_PATH

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
        def _do():
            with _connect(self.db_path) as conn:
                conn.execute(
                    """UPDATE batches SET status=?, done=?, failed=?, updated_at=?
                       WHERE batch_id=?""",
                    (status, done, failed, datetime.now(timezone.utc).isoformat(), batch_id),
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
