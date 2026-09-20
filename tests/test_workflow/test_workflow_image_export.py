"""工作流引擎的生成图落盘接线（与聊天引擎同一套导出服务）。

工作流里图属于 job（没有 chat session_id），故目录第二段用 job_id：
`{输出根}/{租户}/{job_id}/{平台}_{品类}_{序号}.ext`
"""

import asyncio

import pytest

from src.storage import image_export as ex
from src.workflow import templates


@pytest.fixture(autouse=True)
def _out_root(tmp_path, monkeypatch):
    root = tmp_path / "output"
    monkeypatch.setenv("ECOMM_OUTPUT_DIR", str(root))
    return root


@pytest.mark.asyncio
async def test_workflow_run_saves_generated_images(engine, store, job_inputs, _out_root):
    job = templates.instantiate("scene_suite", job_inputs, mode="auto")
    await store.create_job(job)

    task = await engine.start(job)
    await asyncio.wait_for(task, timeout=90)

    files = ex.list_session_files(job.tenant_id, job.job_id)
    assert files, "工作流生成的图也要落盘（此前只有内存里的 base64）"
    # 张数由 Mock 套图编排决定（2 摄影槽位 + 3 信息图），不写死数字
    assert len(files) == 5, [f.name for f in files]
    assert any(f.name.startswith("taobao_") for f in files), [f.name for f in files]
    assert ex.find_session_file(job.tenant_id, job.job_id, 1) is not None


@pytest.mark.asyncio
async def test_workflow_zip_export(engine, store, job_inputs, _out_root):
    job = templates.instantiate("scene_suite", job_inputs, mode="auto")
    await store.create_job(job)
    await asyncio.wait_for(await engine.start(job), timeout=90)

    payload = ex.build_session_zip(job.tenant_id, job.job_id)

    assert payload and len(payload) > 0
