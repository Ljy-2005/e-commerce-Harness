"""Session 管理 — 创建/加载/持久化群聊会话"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from src.core.state import SessionState, RunStatus
from src.core.models import Message
from src.core.logging_config import get_logger

_session_logger = get_logger(__name__)


class SessionManager:
    """管理群聊会话的生命周期"""

    def __init__(self):
        self._sessions: dict[str, SessionState] = {}  # 内存存储（后续可换 DB）

    def create(
        self,
        product_images: list[str],
        product_info: str = "",
        platform: str = "taobao",
        category_hint: str = "",
        max_turns: int = 15,
        collaboration_mode: str = "serial",
        tenant_id: str = "default",
    ) -> SessionState:
        session_id = uuid.uuid4().hex[:16]
        now = datetime.now(timezone.utc)

        state: SessionState = {
            "session_id": session_id,
            "tenant_id": tenant_id,
            "status": RunStatus.CREATED.value,
            "created_at": now,
            "updated_at": now,
            "task": {
                "product_images": product_images,
                "product_info": product_info,
                "platform": platform,
                "category_hint": category_hint,
                "collaboration_mode": collaboration_mode,
            },
            "messages": [],
            "artifacts": {},
            "turn_count": 0,
            "max_turns": max_turns,
            "cost_so_far": 0.0,
            "error_history": [],
        }
        self._sessions[session_id] = state
        return state

    def get(self, session_id: str, tenant_id: str = "") -> Optional[SessionState]:
        """获取会话。若指定 tenant_id，则校验租户归属（不匹配返回 None）"""
        session = self._sessions.get(session_id)
        if session is None:
            return None
        if tenant_id and session.get("tenant_id") != tenant_id:
            return None  # 返回 None 而非 403，避免泄露会话存在性
        return session

    async def update(self, session_id: str, state: SessionState):
        state["updated_at"] = datetime.now(timezone.utc)
        self._sessions[session_id] = state
        # 持久化到 checkpoint
        try:
            from src.storage.checkpoint import save_checkpoint
            await save_checkpoint(session_id, dict(state))
        except Exception as e:
            _session_logger.error("checkpoint 保存失败 (session=%s): %s", session_id, e, exc_info=True)

    async def delete(self, session_id: str):
        self._sessions.pop(session_id, None)
        try:
            from src.storage.checkpoint import delete_checkpoint
            await delete_checkpoint(session_id)
        except Exception as e:
            _session_logger.error("checkpoint 保存失败 (session=%s): %s", session_id, e, exc_info=True)

    def list_ids(self, tenant_id: str = "") -> list[str]:
        if tenant_id:
            return [sid for sid, s in self._sessions.items() if s.get("tenant_id") == tenant_id]
        return list(self._sessions.keys())

    def count_by_tenant(self, tenant_id: str) -> int:
        """统计某租户的活跃会话数"""
        return sum(1 for s in self._sessions.values() if s.get("tenant_id") == tenant_id)
