"""SQLite 韧性测试（test-plan P3 L2）

损坏 db（半写/垃圾字节）→ 明确报错而非崩溃；空文件按 SQLite 语义
初始化为全新库；schema 重入幂等；并发计数原子正确；库内损坏 JSON
显式抛错（不静默返回脏数据）。
"""

import asyncio
import json
import sqlite3

import pytest

from src.workflow.job_store import JobStore, _connect
from src.workflow.models import WorkflowJob, JobStatus


def _job(job_id: str = "j1") -> WorkflowJob:
    return WorkflowJob(
        job_id=job_id,
        template_name="scene_suite",
        version="1.0.0",
        tenant_id="default",
        status=JobStatus.CREATED,
        mode="auto",
        inputs={"product_images": ["fake_b64_data_12345678"], "platform": "taobao"},
        context={},
    )


def _batch(batch_id: str = "b1", done: int = 0, failed: int = 0) -> dict:
    return {
        "batch_id": batch_id, "template_name": "scene_suite",
        "tenant_id": "default", "status": "running", "mode": "auto",
        "max_concurrency": 3, "total": 10, "done": done, "failed": failed,
    }


class TestCorruptedDatabase:
    @pytest.mark.asyncio
    async def test_empty_file_initialized_cleanly(self, tmp_path):
        """0 字节文件是 SQLite 合法空库：首次写入自动建表成功"""
        db = tmp_path / "empty.db"
        db.write_bytes(b"")
        store = JobStore(db_path=db)
        await store.create_job(_job())
        assert (await store.get_job("j1")) is not None

    @pytest.mark.asyncio
    async def test_garbage_bytes_raise_database_error(self, tmp_path):
        """垃圾字节文件 → 报错而非崩溃/挂死"""
        db = tmp_path / "garbage.db"
        db.write_bytes(b"THIS IS NOT A SQLITE DATABASE" * 16)
        store = JobStore(db_path=db)
        with pytest.raises(sqlite3.DatabaseError):
            await store.create_job(_job())

    @pytest.mark.asyncio
    async def test_truncated_db_raises(self, tmp_path):
        """半写库（有效头 + 截断页）→ 读写时报错而非崩溃"""
        db = tmp_path / "truncated.db"
        store = JobStore(db_path=db)
        await store.create_job(_job())
        # 清掉 WAL/SHM，再把主文件截断到只留头部
        for suffix in ("-wal", "-shm"):
            p = tmp_path / f"truncated.db{suffix}"
            if p.exists():
                p.unlink()
        size = db.stat().st_size
        with open(db, "r+b") as f:
            f.truncate(max(100, size // 3))
        with pytest.raises(sqlite3.DatabaseError):
            await store.get_job("j1")

    @pytest.mark.asyncio
    async def test_db_path_is_directory_raises(self, tmp_path):
        """db_path 指向目录 → 明确报错（OperationalError）而非静默失败"""
        store = JobStore(db_path=tmp_path)
        with pytest.raises(sqlite3.OperationalError):
            await store.create_job(_job())


class TestSchemaIdempotency:
    @pytest.mark.asyncio
    async def test_reopen_existing_db_idempotent(self, tmp_path):
        """schema 重入幂等（CREATE IF NOT EXISTS）：反复打开不报错、数据保留"""
        db = tmp_path / "wf.db"
        store1 = JobStore(db_path=db)
        await store1.create_job(_job("keep"))
        store2 = JobStore(db_path=db)  # 第二次打开（模拟重启）
        job = await store2.get_job("keep")
        assert job is not None
        assert job.job_id == "keep"
        await store2.create_job(_job("other"))  # 继续写入正常
        assert len(await store2.list_jobs()) == 2

    @pytest.mark.asyncio
    async def test_user_version_untouched(self, tmp_path):
        """迁移版本号保持默认 0（v0 起步，未引入迁移时不得漂移）"""
        db = tmp_path / "wf.db"
        await JobStore(db_path=db).create_job(_job())
        with _connect(db) as conn:
            (ver,) = conn.execute("PRAGMA user_version").fetchone()
        assert ver == 0


class TestConcurrentCounters:
    @pytest.mark.asyncio
    async def test_concurrent_increment_batch_counts(self, tmp_path):
        """20 个并发 increment_batch 原子累加 → 计数精确（无读改写竞态）"""
        store = JobStore(db_path=tmp_path / "wf.db")
        await store.create_batch(_batch())
        await asyncio.gather(*[
            store.increment_batch("b1", done_delta=1) for _ in range(20)
        ])
        batch = await store.get_batch("b1")
        assert batch["done"] == 20
        assert batch["failed"] == 0

    @pytest.mark.asyncio
    async def test_reconcile_batch_counts_monotonic(self, tmp_path):
        """reconcile 只增不减：目标值小于当前计数时不回退"""
        store = JobStore(db_path=tmp_path / "wf.db")
        await store.create_batch(_batch(done=3, failed=1))
        await store.reconcile_batch_counts("b1", "completed", 5, 2)
        b = await store.get_batch("b1")
        assert (b["done"], b["failed"]) == (5, 2)
        await store.reconcile_batch_counts("b1", "completed", 3, 4)
        b = await store.get_batch("b1")
        assert (b["done"], b["failed"]) == (5, 4)  # done 不回退、failed 取 max


class TestCorruptJsonInRows:
    @pytest.mark.asyncio
    async def test_bad_inputs_json_raises_explicitly(self, tmp_path):
        """库内损坏 JSON → 显式 JSONDecodeError（报错而非返回脏数据）"""
        db = tmp_path / "wf.db"
        with _connect(db) as conn:
            conn.execute(
                """INSERT INTO jobs (job_id, template_name, tenant_id, status, mode, inputs_json)
                   VALUES (?,?,?,?,?,?)""",
                ("bad", "t", "d", "created", "auto", "{not valid json"),
            )
        store = JobStore(db_path=db)
        with pytest.raises(json.JSONDecodeError):
            await store.get_job("bad")
