"""第三轮审计 B2-18：测试运行期目录隔离守卫。

没有 `_isolate_runtime_dirs`（tests/conftest.py）时，后端测试会直写真实
`data/{checkpoints,audit,memory}` 与真实 `config/*.yaml`（实测 checkpoint 累积
1600+ 文件）。本文件断言隔离生效，防止 fixture 被误删/改坏。
"""

from pathlib import Path

import pytest


def test_data_root_is_redirected_to_tmp(_isolate_runtime_dirs):
    from src.core.config import data_root

    assert data_root() == _isolate_runtime_dirs / "data"
    assert "Temp" in str(data_root()) or "tmp" in str(data_root()).lower()


def test_config_root_is_tmp_copy(_isolate_runtime_dirs):
    """配置根指向 tmp 副本：写 config/*.yaml 不再污染仓库"""
    from src.core.config import _project_root

    real_root = Path(__file__).resolve().parents[2]
    assert _project_root() == _isolate_runtime_dirs
    assert _project_root() != real_root
    assert (_project_root() / "config" / "models.yaml").exists(), "副本需含配置（否则读不到）"
    assert ((_project_root() / "config" / "agents").glob("*.yaml")), "副本需含 Agent 配置"


def test_template_dir_uses_project_root(_isolate_runtime_dirs):
    """模板目录与写入侧同基准（此前 file-relative 硬编码会在重定向后分叉）"""
    from src.core.config import _project_root
    from src.workflow.templates import _workflows_dir

    assert _workflows_dir() == _project_root() / "config" / "workflows"


def test_all_data_paths_derive_from_data_root(_isolate_runtime_dirs):
    """四个落盘点都要走 data_root()（防回归成模块级常量）"""
    from src.core.config import data_root
    from src.harness.agent_memory import AgentMemory
    from src.harness.audit_logger import AuditLogger
    from src.storage.checkpoint import _checkpoint_dir
    from src.workflow.job_store import JobStore

    root = data_root()
    assert _checkpoint_dir() == root / "checkpoints"
    assert AgentMemory()._dir == root / "memory"
    assert AuditLogger()._dir == root / "audit"
    assert JobStore().db_path == root / "workflow.db"


@pytest.mark.asyncio
async def test_checkpoint_write_lands_in_tmp(_isolate_runtime_dirs):
    from src.core.config import data_root
    from src.storage.checkpoint import _checkpoint_dir, load_checkpoint, save_checkpoint

    await save_checkpoint("isolation-probe", {"session_id": "isolation-probe", "status": "created"})

    path = _checkpoint_dir() / "isolation-probe.json"
    assert path.exists(), "checkpoint 未落在 data_root() 下"
    assert path.parent.parent == data_root()
    assert (await load_checkpoint("isolation-probe"))["status"] == "created"
    path.unlink()


@pytest.mark.asyncio
async def test_audit_and_memory_write_land_in_tmp(_isolate_runtime_dirs):
    from src.core.config import data_root
    from src.harness.agent_memory import AgentMemory
    from src.harness.audit_logger import AuditLogger

    audit = AuditLogger()
    await audit.log(
        session_id="isolation-probe", agent_name="探针", action="probe",
        provider_name="mock", model="mock", duration_ms=1.0, tokens_used=0, cost_usd=0.0,
        status="success",
    )
    memory = AgentMemory()
    await memory.remember("isolation-probe", "探针品类", {"a": 1}, {"p": 1},
                          {"overall_score": 90, "verdict": "pass"})

    assert list((data_root() / "audit").glob("audit-*.jsonl")), "审计日志未落在 tmp"
    assert list((data_root() / "memory").glob("*.jsonl")), "记忆文件未落在 tmp"
    assert not list((Path.cwd() / "data" / "audit").glob("audit-*isolation*"))
