"""第三轮审计 B1-7/B1-8：checkpoint 的写入原子性与磁盘回收。

B1-7：`_save_sync` 用 `open("w")` 截断 + 流式 dump，无 temp+rename
      → 并发/中断写实测 4/40 次 JSONDecodeError，旧 checkpoint 被半截文件顶掉。
B1-8：TTL 只清内存不删磁盘，启动仍要 glob 全目录（实测真实 data/checkpoints
      累积 1698 个文件 / 53.8MB）。
"""

import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from src.storage import checkpoint as cp_mod


@pytest.fixture
def cp_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(cp_mod, "_checkpoint_dir", lambda: tmp_path)
    return tmp_path


def _write_raw(cp_dir, session_id: str, status: str, updated: datetime | str, *, corrupt=False):
    path = cp_dir / f"{session_id}.json"
    if corrupt:
        path.write_text('{"session_id": "partial", ', encoding="utf-8")
    else:
        iso = updated if isinstance(updated, str) else updated.isoformat()
        path.write_text(json.dumps({"session_id": session_id, "status": status,
                                    "created_at": iso, "updated_at": iso}, ensure_ascii=False),
                        encoding="utf-8")
    return path


class TestAtomicWrite:
    @pytest.mark.asyncio
    async def test_failed_write_keeps_previous_checkpoint(self, cp_dir, monkeypatch):
        """写入中途失败（磁盘满/进程被杀）不得毁掉上一个有效 checkpoint"""
        await cp_mod.save_checkpoint("s1", {"session_id": "s1", "status": "created",
                                            "messages": []})

        def _partial_dump(obj, fp, **kwargs):
            fp.write('{"session_id": "s1", "status": ')   # 半截后失败
            raise OSError("disk full")

        monkeypatch.setattr(cp_mod.json, "dump", _partial_dump)
        with pytest.raises(OSError):
            await cp_mod.save_checkpoint("s1", {"session_id": "s1", "status": "completed"})

        state = await cp_mod.load_checkpoint("s1")
        assert state is not None, "旧 checkpoint 被半截文件顶掉"
        assert state["status"] == "created"
        assert list(cp_dir.glob("*.tmp*")) == [], "失败写入应清理临时文件"

    @pytest.mark.asyncio
    async def test_concurrent_saves_never_corrupt(self, cp_dir):
        states = [{"session_id": "s1", "status": "running", "n": i, "payload": "x" * 20000}
                  for i in range(30)]

        import asyncio
        await asyncio.gather(*[cp_mod.save_checkpoint("s1", s) for s in states])

        state = await cp_mod.load_checkpoint("s1")
        assert state is not None, "并发写产生了损坏 JSON"
        assert state["n"] in range(30)


class TestDiskReclamation:
    @pytest.mark.asyncio
    async def test_cleanup_removes_expired_terminal_checkpoints(self, cp_dir):
        now = datetime.now(timezone.utc)
        old, fresh = now - timedelta(hours=48), now
        _write_raw(cp_dir, "old_done", "completed", old)
        _write_raw(cp_dir, "old_failed", "failed", old)
        _write_raw(cp_dir, "old_running", "running", old)       # 进行中 → 保留
        _write_raw(cp_dir, "fresh_done", "completed", fresh)    # 未超期 → 保留
        corrupt = _write_raw(cp_dir, "corrupt_old", "", old, corrupt=True)
        stale_ts = old.timestamp()
        os.utime(corrupt, (stale_ts, stale_ts))

        stats = await cp_mod.cleanup_checkpoints(ttl_hours=24)

        remaining = {p.stem for p in cp_dir.glob("*.json")}
        assert remaining == {"old_running", "fresh_done"}
        assert stats["removed"] == 3
        assert stats["scanned"] == 5

    @pytest.mark.asyncio
    async def test_cleanup_removes_expired_never_started(self, cp_dir):
        """口径与内存 TTL 一致：created（从未启动）超期也要回收，否则永久堆积"""
        now = datetime.now(timezone.utc)
        old = now - timedelta(hours=48)
        _write_raw(cp_dir, "old_created", "created", old)
        _write_raw(cp_dir, "old_waiting", "waiting_human", old)   # 进行中 → 保留
        _write_raw(cp_dir, "fresh_created", "created", now)

        stats = await cp_mod.cleanup_checkpoints(ttl_hours=24)

        remaining = {p.stem for p in cp_dir.glob("*.json")}
        assert remaining == {"old_waiting", "fresh_created"}
        assert stats["removed"] == 1

    @pytest.mark.asyncio
    async def test_cleanup_is_noop_when_ttl_disabled(self, cp_dir):
        _write_raw(cp_dir, "old_done", "completed",
                   datetime.now(timezone.utc) - timedelta(days=30))

        stats = await cp_mod.cleanup_checkpoints(ttl_hours=0)

        assert stats["removed"] == 0
        assert (cp_dir / "old_done.json").exists()

    @pytest.mark.asyncio
    async def test_cleanup_uses_mtime_when_timestamp_unparsable(self, cp_dir):
        """updated_at 缺失/非法时按文件 mtime 判定年龄（否则永远清不掉）"""
        path = cp_dir / "no_timestamp.json"
        path.write_text(json.dumps({"session_id": "no_timestamp", "status": "completed"}),
                        encoding="utf-8")
        old_ts = (datetime.now(timezone.utc) - timedelta(days=10)).timestamp()
        os.utime(path, (old_ts, old_ts))

        stats = await cp_mod.cleanup_checkpoints(ttl_hours=24)

        assert stats["removed"] == 1
        assert not path.exists()

    @pytest.mark.asyncio
    async def test_cleanup_removes_stale_temp_files(self, cp_dir):
        """失败写入遗留的 *.json.tmp 也要回收（超期才删，新临时文件留给并发写）"""
        stale = cp_dir / "s1-abc.json.tmp"
        fresh = cp_dir / "s2-def.json.tmp"
        for path in (stale, fresh):
            path.write_text("{", encoding="utf-8")
        old_ts = (datetime.now(timezone.utc) - timedelta(days=10)).timestamp()
        os.utime(stale, (old_ts, old_ts))

        stats = await cp_mod.cleanup_checkpoints(ttl_hours=24)

        assert not stale.exists()
        assert fresh.exists()


class TestStartupReclamation:
    def test_lifespan_reclaims_expired_terminal_checkpoints(self, tmp_path, monkeypatch):
        """B1-8 接线：启动（lifespan）即回收超期终态 checkpoint"""
        from fastapi.testclient import TestClient

        import src.core.config as config_mod
        from src.main import app

        monkeypatch.setattr(config_mod, "data_root", lambda: tmp_path)
        monkeypatch.setattr(cp_mod, "_checkpoint_dir", lambda: tmp_path)

        old = datetime.now(timezone.utc) - timedelta(days=30)
        stale = _write_raw(tmp_path, "startup_stale", "completed", old)
        fresh = _write_raw(tmp_path, "startup_fresh", "completed", datetime.now(timezone.utc))

        with TestClient(app):   # 上下文管理器 → 触发 startup/shutdown
            pass

        assert not stale.exists(), "启动未回收超期终态 checkpoint"
        assert fresh.exists(), "未超期 checkpoint 被误删"


class TestStartupRestoreVisibility:
    """A45（真实使用中踩到）：重启后历史会话必须还在列表里

    会话列表来自内存（`SessionManager._sessions`），而启动恢复此前**只恢复非终态** →
    每次重启，completed/failed 的会话就从 UI 上消失（磁盘 checkpoint 还在，只是不列）。
    """

    def test_terminal_checkpoints_are_restored_too(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient

        import src.core.config as config_mod
        from src.main import _session_manager, app

        # 恢复路径枚举的是 data_root()/"checkpoints"，而 load_checkpoint 用 _checkpoint_dir()
        # ——两者必须指向同一目录，否则测试写到了别处（踩过一次）
        monkeypatch.setattr(config_mod, "data_root", lambda: tmp_path)
        cp_dir = tmp_path / "checkpoints"
        cp_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(cp_mod, "_checkpoint_dir", lambda: cp_dir)

        _write_raw(cp_dir, "old_completed", "completed", datetime.now(timezone.utc))
        _write_raw(cp_dir, "old_failed", "failed", datetime.now(timezone.utc))
        _write_raw(cp_dir, "mid_running", "running", datetime.now(timezone.utc))

        with TestClient(app):
            ids = set(_session_manager.list_ids())

        assert "old_completed" in ids, "终态会话重启后必须仍可浏览"
        assert "old_failed" in ids
        assert "mid_running" in ids
        assert _session_manager.get("mid_running")["status"] == "failed", "崩溃中的会话标记为非正常结束"
        assert _session_manager.get("old_completed")["status"] == "completed", "终态不应被改写"

        for sid in ("old_completed", "old_failed", "mid_running"):
            _session_manager._sessions.pop(sid, None)
