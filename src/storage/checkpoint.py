"""Checkpoint 持久化 — JSON 文件存储"""

import json
from pathlib import Path
from typing import Optional


def _checkpoint_dir() -> Path:
    return Path(__file__).parent.parent.parent / "data" / "checkpoints"


async def save_checkpoint(session_id: str, state: dict):
    """保存 SessionState 到 JSON 文件"""
    _checkpoint_dir().mkdir(parents=True, exist_ok=True)
    path = _checkpoint_dir() / f"{session_id}.json"
    # 转换不可序列化的字段
    data = dict(state)
    for key in ("created_at", "updated_at"):
        if key in data and data[key] is not None:
            data[key] = str(data[key])
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)


async def load_checkpoint(session_id: str) -> Optional[dict]:
    """从 JSON 文件恢复 SessionState"""
    path = _checkpoint_dir() / f"{session_id}.json"
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


async def delete_checkpoint(session_id: str):
    path = _checkpoint_dir() / f"{session_id}.json"
    if path.exists():
        path.unlink()
