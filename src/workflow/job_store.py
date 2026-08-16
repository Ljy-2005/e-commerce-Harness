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
