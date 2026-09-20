"""Session 管理 — 创建/加载/持久化群聊会话"""

import time
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

from src.core.state import SessionState, RunStatus
from src.core.models import Message
from src.core.logging_config import get_logger

_session_logger = get_logger(__name__)

# 活跃状态集合（配额统计用：created/running/waiting_human 都占配额）
_ACTIVE_STATUSES = (RunStatus.CREATED.value, RunStatus.RUNNING.value, "waiting_human")
# 进行中状态（TTL 驱逐豁免：真正在跑的会话不驱逐；
# created 未启动/终态会话超期即驱逐）
_INFLIGHT_STATUSES = (RunStatus.RUNNING.value, "waiting_human")


class SessionManager:
    """管理群聊会话的生命周期"""

    def __init__(self, session_ttl_hours: float = 0):
        self._sessions: dict[str, SessionState] = {}  # 内存存储（后续可换 DB）
        # 审计修复：会话 TTL（config/default.yaml 的 chat.session_ttl_hours，
        # 0 = 永不过期）；此前该配置是死配置，会话/checkpoint 只增不减
        if session_ttl_hours <= 0:
            from src.core.config import load_default_config
            session_ttl_hours = float((load_default_config().get("chat", {}) or {}).get("session_ttl_hours", 24) or 24)
        self._ttl_hours = session_ttl_hours

    @property
    def ttl_hours(self) -> float:
        """会话 TTL（小时，0 = 永不过期）——启动期磁盘 checkpoint 回收用（B1-8）"""
        return self._ttl_hours

    def set_ttl_hours(self, hours: float) -> float:
        """运行时更新 TTL（设置页改「会话策略」后立即生效，无需重启）"""
        try:
            value = float(hours)
        except (TypeError, ValueError):
            return self._ttl_hours
        self._ttl_hours = max(0.0, value)
        return self._ttl_hours

    def _sweep_expired(self):
        """惰性驱逐超期会话（审计修复：session_ttl_hours 此前无人消费）

        P3 补缺（test-plan L2）：仅驱逐**非进行中**会话——running/
        waiting_human 等真正在跑的会话不被 TTL 驱逐（其生命周期由引擎
        管理，崩溃恢复路径会在重启时标记 failed 后再进入可驱逐集合）；
        created（从未启动）与终态会话超期即驱逐。

        第三轮审计：
        - B1-9：`updated_at` 从 checkpoint 恢复时是 ISO **字符串**，此前只认
          datetime（`ts = 0`）→ 恢复出来的会话永不驱逐；改用 `parse_timestamp` 统一解析；
        - B1-8：驱逐时连磁盘 checkpoint 一起回收（此前只清内存，磁盘只增不减）。
        """
        if self._ttl_hours <= 0:
            return
        from src.storage.checkpoint import parse_timestamp
        cutoff = time.time() - self._ttl_hours * 3600
        stale = []
        for sid, s in self._sessions.items():
            if s.get("status") in _INFLIGHT_STATUSES:
                continue  # 进行中会话不驱逐
            ts = parse_timestamp(s.get("updated_at")) or 0
            if ts and ts < cutoff:
                stale.append(sid)
        for sid in stale:
            del self._sessions[sid]
            self._reclaim_checkpoint(sid)

    @staticmethod
    def _reclaim_checkpoint(session_id: str) -> None:
        """同步回收磁盘 checkpoint（B1-8）

        惰性驱逐发生在 get/list/count 请求路径上，这里只删一个几 KB 的小文件，
        同步 unlink 开销可忽略；失败仅告警，不影响请求。
        """
        try:
            from src.storage.checkpoint import delete_checkpoint_sync
            delete_checkpoint_sync(session_id)
        except Exception as e:  # noqa: BLE001 — 回收失败不得影响会话读取
            _session_logger.warning("checkpoint 回收失败 (session=%s): %s", session_id, e)

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
        self._sweep_expired()
        session = self._sessions.get(session_id)
        if session is None:
            return None
        if tenant_id and session.get("tenant_id") != tenant_id:
            return None  # 返回 None 而非 403，避免泄露会话存在性
        return session

    async def update(self, session_id: str, state: SessionState, slim: bool = False):
        """持久化会话；`slim=True` 写轻量快照（剔除上传图 base64，用于每步进度落盘）"""
        state["updated_at"] = datetime.now(timezone.utc)
        self._sessions[session_id] = state
        # 持久化到 checkpoint
        try:
            from src.storage.checkpoint import save_checkpoint
            await save_checkpoint(session_id, dict(state), slim=slim)
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
        self._sweep_expired()
        if tenant_id:
            return [sid for sid, s in self._sessions.items() if s.get("tenant_id") == tenant_id]
        return list(self._sessions.keys())

    def count_by_tenant(self, tenant_id: str) -> int:
        """统计某租户的活跃会话数（审计修复：completed/failed 不再计入，
        否则达到 max_sessions 后该租户被永久 429）"""
        self._sweep_expired()
        return sum(
            1 for s in self._sessions.values()
            if s.get("tenant_id") == tenant_id and s.get("status") in _ACTIVE_STATUSES
        )
