"""Session TTL 与活跃配额测试（审计修复 #7）"""

from datetime import datetime, timedelta, timezone

import pytest

from src.chat.session import SessionManager


def _create(mgr, tenant="t1", status=None):
    s = mgr.create(product_images=["fake"], tenant_id=tenant)
    if status:
        s["status"] = status
    return s


@pytest.mark.asyncio
async def test_expired_session_evicted_on_access():
    """超 TTL 的会话在 get/list 时被惰性驱逐"""
    mgr = SessionManager(session_ttl_hours=1)
    s = _create(mgr)
    s["updated_at"] = datetime.now(timezone.utc) - timedelta(hours=2)

    assert mgr.get(s["session_id"]) is None
    assert mgr.list_ids() == []


@pytest.mark.asyncio
async def test_fresh_session_survives_ttl():
    mgr = SessionManager(session_ttl_hours=1)
    s = _create(mgr)
    assert mgr.get(s["session_id"]) is not None
    assert s["session_id"] in mgr.list_ids()


@pytest.mark.asyncio
async def test_count_by_tenant_only_active():
    """completed/failed 不计入活跃配额（否则达到 max_sessions 后永久 429）"""
    mgr = SessionManager(session_ttl_hours=999_999)
    done = _create(mgr, status="completed")
    _create(mgr, status="running")
    waiting = _create(mgr, status="waiting_human")
    failed = _create(mgr, status="failed")

    assert mgr.count_by_tenant("t1") == 2  # running + waiting_human
    # 列表仍能看到全部（TTL 之外的会话）
    assert len(mgr.list_ids("t1")) == 4
    assert done["session_id"] in mgr.list_ids("t1")
    assert failed["session_id"] in mgr.list_ids("t1")
    assert waiting["session_id"] in mgr.list_ids("t1")


@pytest.mark.asyncio
async def test_inflight_session_not_evicted_by_ttl():
    """P3 回归（test-plan L2）：进行中会话（running/waiting_human）
    即使超 TTL 也不被驱逐；created（未启动）与终态会话超期即驱逐"""
    mgr = SessionManager(session_ttl_hours=1)
    old = datetime.now(timezone.utc) - timedelta(hours=2)
    created = _create(mgr, status="created")
    running = _create(mgr, status="running")
    waiting = _create(mgr, status="waiting_human")
    done = _create(mgr, status="completed")

    for s in (created, running, waiting, done):
        s["updated_at"] = old

    assert mgr.get(running["session_id"]) is not None
    assert mgr.get(waiting["session_id"]) is not None
    assert mgr.get(created["session_id"]) is None  # 从未启动 → 驱逐
    assert mgr.get(done["session_id"]) is None     # 终态超期 → 驱逐
    assert len(mgr.list_ids()) == 2
    assert mgr.count_by_tenant("t1") == 2  # 进行中会话仍计入配额


@pytest.mark.asyncio
async def test_restored_session_string_timestamp_is_evicted(tmp_path, monkeypatch):
    """B1-9：checkpoint 恢复的会话 `updated_at` 是 ISO 字符串（写入侧 str()），
    而驱逐逻辑只认 datetime → `ts = 0` → **永不驱逐**（内存里永久驻留，
    启动恢复路径正是这条）。

    B1-8：驱逐时必须连磁盘 checkpoint 一起回收（此前只清内存）。
    """
    from src.storage import checkpoint as cp_mod

    monkeypatch.setattr(cp_mod, "_checkpoint_dir", lambda: tmp_path)
    mgr = SessionManager(session_ttl_hours=1)
    old = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
    restored = {"session_id": "restored1", "tenant_id": "t1", "status": "failed",
                "created_at": old, "updated_at": old, "messages": [], "artifacts": {}}
    mgr._sessions["restored1"] = restored
    cp_mod._save_sync("restored1", restored)          # 真实落盘（updated_at 会被 str()）
    assert (tmp_path / "restored1.json").exists()

    assert mgr.get("restored1") is None, "字符串时间戳的恢复会话未被 TTL 驱逐"
    assert not (tmp_path / "restored1.json").exists(), "驱逐后磁盘 checkpoint 未回收"


@pytest.mark.asyncio
async def test_eviction_does_not_touch_inflight_or_fresh_restored(tmp_path, monkeypatch):
    """回归：同步回收不得误删进行中/未超期的 checkpoint 文件"""
    from src.storage import checkpoint as cp_mod

    monkeypatch.setattr(cp_mod, "_checkpoint_dir", lambda: tmp_path)
    mgr = SessionManager(session_ttl_hours=24)
    now = datetime.now(timezone.utc)
    cases = {
        "running1": ("running", (now - timedelta(hours=48)).isoformat()),   # 进行中 → 豁免
        "fresh1": ("completed", (now - timedelta(minutes=5)).isoformat()),  # 未超期 → 保留
    }
    for sid, (status, updated) in cases.items():
        state = {"session_id": sid, "tenant_id": "t1", "status": status,
                 "created_at": updated, "updated_at": updated, "messages": []}
        mgr._sessions[sid] = state
        cp_mod._save_sync(sid, state)

    assert mgr.get("running1") is not None
    assert mgr.get("fresh1") is not None
    assert (tmp_path / "running1.json").exists()
    assert (tmp_path / "fresh1.json").exists()
