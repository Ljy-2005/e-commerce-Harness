"""Checkpoint 持久化 — JSON 文件存储

审计修复：文件 IO 经 asyncio.to_thread 转线程，避免阻塞事件循环
（save_checkpoint 每次写入含 base64 的大 JSON，此前直接同步写）。

第三轮审计 B1-7/B1-8：
- **原子写**：改为「同目录临时文件 + `os.replace`」，中断/并发不会再让旧 checkpoint
  变成半截 JSON（此前 `open("w")` 截断 + 流式 dump，实测并发保存 4/40 次
  JSONDecodeError，恢复态静默丢失）；
- **磁盘回收**：TTL 此前只清内存不删磁盘，实测 `data/checkpoints` 累积 1698 个文件 /
  53.8MB 且启动仍要 glob 全目录；新增 `cleanup_checkpoints()` 与同步删除入口。
"""

import asyncio
import contextlib
import json
import os
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.core.config import data_root
from src.core.logging_config import get_logger

_checkpoint_logger = get_logger(__name__)

# 可按 TTL 回收的状态：**除进行中以外**全部可回收——与内存侧
# `SessionManager._sweep_expired` 的口径一致（created 从未启动、终态、
# 未知状态都算；running/waiting_human 由引擎管理，绝不按 TTL 删）
INFLIGHT_STATUSES = ("running", "waiting_human")

# 原子写的临时文件后缀（不带前导点：便于 glob 识别，也不会被 `*.json` 误扫）
_TMP_SUFFIX = ".json.tmp"

# 写入串行化（B1-7 落地细节）：Windows 上两个线程同时对同一路径做
# `os.replace` 会撞 sharing violation（实测 WinError 5 ACCESS_DENIED）。
# checkpoint 文件都很小，串行化开销可忽略，换来"每次替换要么完整成功、
# 要么完全不动"的确定性。
_WRITE_LOCK = threading.Lock()

# `os.replace` 对"正被其他句柄打开"的目标会失败（杀毒/索引器/并发读），
# 短暂退避重试即可；超出仍抛错（不吞异常）。
_REPLACE_RETRIES = 5


def _checkpoint_dir() -> Path:
    """checkpoint 目录（第三轮审计 B2-18：经 data_root()，可被 ECOMM_DATA_DIR 重定向）"""
    return data_root() / "checkpoints"


def parse_timestamp(value) -> Optional[float]:
    """把 checkpoint 里的时间戳（ISO 字符串 / datetime）解析为 epoch 秒；失败返回 None

    B1-9：写入侧是 `str(datetime)`，读取侧若只认 datetime，从 checkpoint 恢复的会话
    TTL 永远不生效（内存里永久驻留）。
    """
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value.strip():
        try:
            dt = datetime.fromisoformat(value.strip())
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    try:
        return dt.timestamp()
    except (OverflowError, OSError, ValueError):
        return None


async def save_checkpoint(session_id: str, state: dict, slim: bool = False):
    """保存 SessionState 到 JSON 文件（原子替换，B1-7）

    `slim=True` 写"轻量快照"：剔掉用户上传图等大字段（见 `_serializable_only`），
    用于**每个 Agent 步骤后的进度落盘**——完整快照留给人工暂停/终态/压缩前。
    """
    await asyncio.to_thread(_save_sync, session_id, dict(state), slim)


# 运行时对象：只存在于内存，落盘无意义（恢复后由代码重建）
_RUNTIME_ONLY_KEYS = ("_cost_tracker", "_multi_reviews", "_usage_log", "_warnings")

# 轻量快照剔除的大字段：用户上传图/参考图的 base64（2.6MB 图 → 3.5MB JSON），
# 每轮都写会造成明显写放大；它们只在"重跑分析"时才用得上，而崩溃恢复的会话已被标记失败
_SLIM_DROP_TASK_KEYS = ("product_images", "reference_images")


def _serializable_only(state: dict, slim: bool = False) -> dict:
    """剔除运行时对象与不可序列化的值（A39）；`slim` 时再剔掉上传图等大字段（B3）

    此前靠 `json.dump(default=str)` 兜底，于是 `_cost_tracker`（CostTracker 实例）
    被写成 `"<src.harness.cost_tracker.CostTracker object at 0x…>"`：
    恢复后 `tracker.record(...)` 抛 AttributeError（被上层 try 吞掉）→ 该会话从此
    **不再记账**（成本/预算/审计全失效），文件里还白留一个内存地址。
    """
    cleaned: dict = {}
    for key, value in state.items():
        if key in _RUNTIME_ONLY_KEYS:
            continue
        if slim and key == "task" and isinstance(value, dict):
            value = {k: v for k, v in value.items() if k not in _SLIM_DROP_TASK_KEYS}
        try:
            json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            _checkpoint_logger.warning(
                "checkpoint 字段不可序列化，已丢弃: session=%s key=%s type=%s",
                state.get("session_id", "?"), key, type(value).__name__)
            continue
        cleaned[key] = value
    return cleaned


def _save_sync(session_id: str, state: dict, slim: bool = False):
    directory = _checkpoint_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{session_id}.json"
    # 转换不可序列化的字段
    data = dict(state)
    for key in ("created_at", "updated_at"):
        if key in data and data[key] is not None:
            data[key] = str(data[key])
    data = _serializable_only(data, slim=slim)
    # B1-7：先写临时文件 → fsync → os.replace（同目录替换是原子的）。
    # 任何中断/并发都只影响临时文件，旧 checkpoint 始终完整可读。
    with _WRITE_LOCK:
        fd, tmp_name = tempfile.mkstemp(dir=str(directory), prefix=f"{session_id}-",
                                        suffix=_TMP_SUFFIX)
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2, default=str)
                f.flush()
                os.fsync(f.fileno())
            _replace_with_retry(tmp_path, path)
        except BaseException:
            with contextlib.suppress(OSError):
                tmp_path.unlink()
            raise


def _replace_with_retry(src: Path, dst: Path) -> None:
    """`os.replace` 的短暂退避重试（目标被其他句柄占用时 Windows 会 ACCESS_DENIED）"""
    for attempt in range(_REPLACE_RETRIES):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == _REPLACE_RETRIES - 1:
                raise
            time.sleep(0.02 * (attempt + 1))


async def load_checkpoint(session_id: str) -> Optional[dict]:
    """从 JSON 文件恢复 SessionState"""
    return await asyncio.to_thread(_load_sync, session_id)


def _load_sync(session_id: str) -> Optional[dict]:
    path = _checkpoint_dir() / f"{session_id}.json"
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        # P3 补缺（test-plan L2）：损坏/半写 checkpoint 视为"无 checkpoint"，
        # 返回 None 而非抛异常（启动恢复路径与调用方无需逐层 try/except）
        return None
    return data if isinstance(data, dict) else None


async def delete_checkpoint(session_id: str):
    await asyncio.to_thread(_delete_sync, session_id)


def delete_checkpoint_sync(session_id: str) -> None:
    """同步删除（供 SessionManager 的惰性 TTL 驱逐直接调用，B1-8）"""
    _delete_sync(session_id)


def _delete_sync(session_id: str):
    path = _checkpoint_dir() / f"{session_id}.json"
    if path.exists():
        with contextlib.suppress(OSError):
            path.unlink()


# ── 磁盘回收（B1-8） ──

async def cleanup_checkpoints(ttl_hours: float, now: float | None = None) -> dict:
    """按 TTL 回收磁盘 checkpoint；返回 {"scanned", "removed", "kept"}

    规则（与内存 TTL 驱逐同口径）
    - 超期且**非进行中**（created / completed / failed / cancelled / 未知状态）→ 删
      （内存里这些会话早被驱逐，文件已是死重量）；
    - 进行中（running / waiting_human）→ 保留，绝不误删可能在跑的会话；
    - 损坏/无法解析且 mtime 超期 → 删（恢复路径本来也读不出来）；
    - 失败写入遗留的 `*.json.tmp`：mtime 超期 → 删。

    年龄优先取 `updated_at`，缺失/非法时回落到文件 mtime。
    `ttl_hours <= 0` → 不回收（配置语义：0 = 永不过期）。
    """
    return await asyncio.to_thread(_cleanup_sync, ttl_hours, now)


def _cleanup_sync(ttl_hours: float, now: float | None = None) -> dict:
    stats = {"scanned": 0, "removed": 0, "kept": 0}
    if not ttl_hours or ttl_hours <= 0:
        return stats
    directory = _checkpoint_dir()
    if not directory.exists():
        return stats
    cutoff = (now if now is not None else time.time()) - ttl_hours * 3600

    for path in sorted(directory.iterdir()):
        if not path.is_file():
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue

        if path.name.endswith(_TMP_SUFFIX):
            stats["scanned"] += 1
            if mtime < cutoff:
                _unlink(path)
                stats["removed"] += 1
            else:
                stats["kept"] += 1
            continue

        if path.suffix != ".json":
            continue

        stats["scanned"] += 1
        status, updated_ts = _read_meta(path)
        expired = (updated_ts if updated_ts is not None else mtime) < cutoff
        corrupt = status is None
        # 与内存 TTL 驱逐同口径：超期且非进行中即回收（损坏文件同样回收）
        if expired and (corrupt or status not in INFLIGHT_STATUSES):
            _unlink(path)
            stats["removed"] += 1
        else:
            stats["kept"] += 1
    return stats


def _read_meta(path: Path) -> tuple[Optional[str], Optional[float]]:
    """读取 checkpoint 的 (status, updated_at epoch 秒)；损坏/半写 → (None, None)"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return None, None
    if not isinstance(data, dict):
        return None, None
    return str(data.get("status", "")), parse_timestamp(data.get("updated_at"))


def _unlink(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.unlink()
