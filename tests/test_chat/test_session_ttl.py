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
    running = _create(mgr, status="running")
    waiting = _create(mgr, status="waiting_human")
    failed = _create(mgr, status="failed")

    assert mgr.count_by_tenant("t1") == 2  # running + waiting_human
    # 列表仍能看到全部（TTL 之外的会话）
    assert len(mgr.list_ids("t1")) == 4
    assert done["session_id"] in mgr.list_ids("t1")
    assert failed["session_id"] in mgr.list_ids("t1")
    assert waiting["session_id"] in mgr.list_ids("t1")
