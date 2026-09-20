"""第三轮审计 B1-6：AgentMemory 并发追加不得丢条目。

`_append_entry` 无锁（`audit_logger` 已有模块级 `_WRITE_LOCK` 的同类修复），
实测 400 并发 `remember()` 只落盘 376 行（丢 24）。
"""

import asyncio
import json

import pytest

from src.harness.agent_memory import AgentMemory


def _lines(path) -> list[str]:
    if not path.exists():
        return []
    return [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


@pytest.mark.asyncio
async def test_concurrent_remember_keeps_every_entry(tmp_path):
    memory = AgentMemory(storage_dir=str(tmp_path / "memory"))
    n = 200
    await asyncio.gather(*[
        memory.remember(
            f"s{i}", "并发品类",
            {"features": ["特征" * 100]},
            {"main_image": {"prompt": "提示词" * 200}, "scene_images": []},
            {"overall_score": 90, "verdict": "pass"},
        )
        for i in range(n)
    ])

    files = list((tmp_path / "memory").glob("*.jsonl"))
    assert len(files) == 1, "同一品类应写同一文件"
    lines = _lines(files[0])
    assert len(lines) == n, f"并发写入丢条目：{len(lines)}/{n}"


@pytest.mark.asyncio
async def test_concurrent_entries_are_valid_json_lines(tmp_path):
    """交错写会产生半行 → 读取侧 JSONDecodeError"""
    memory = AgentMemory(storage_dir=str(tmp_path / "memory"))
    await asyncio.gather(*[
        memory.remember(f"s{i}", "并发品类", {"features": ["x" * 500]},
                        {"main_image": {"prompt": "y" * 500}, "scene_images": []},
                        {"overall_score": 90, "verdict": "pass"})
        for i in range(120)
    ])

    path = next((tmp_path / "memory").glob("*.jsonl"))
    for i, line in enumerate(_lines(path)):
        json.loads(line)   # 半行/交错 → 抛 JSONDecodeError
