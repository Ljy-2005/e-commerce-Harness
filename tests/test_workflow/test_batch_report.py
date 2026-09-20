"""批量报表聚合测试（M5）— JobStore.get_batch_report"""

from datetime import datetime, timedelta, timezone

import pytest

from src.workflow.job_store import JobStore
from src.workflow.models import WorkflowJob, JobStatus


@pytest.fixture
def store(tmp_path):
    return JobStore(db_path=tmp_path / "report.db")


def _now():
    return datetime.now(timezone.utc)


async def _seed_batch(store, batch_id, template, tenant, status, done, failed,
                      items, jobs):
    """items = [(status, error, job_id), ...]；jobs = {job_id: (duration_s, cost)}"""
    now = _now()
    await store.create_batch({
        "batch_id": batch_id, "template_name": template, "tenant_id": tenant,
        "status": status, "mode": "auto", "max_concurrency": 2,
        "total": len(items), "done": done, "failed": failed,
        "created_at": now, "updated_at": now,
    })
    await store.create_batch_items([
        {"batch_id": batch_id, "seq": i, "inputs": {"product_info": f"商品{i}"},
         "job_id": job_id or "", "status": st, "attempts": 1, "error": err,
         "updated_at": now}
        for i, (st, err, job_id) in enumerate(items)
    ])
    for jid, (secs, cost) in jobs.items():
        await store.create_job(WorkflowJob(
            job_id=jid, template_name=template, tenant_id=tenant,
            status=JobStatus.COMPLETED, cost_so_far=cost,
            created_at=now, updated_at=now + timedelta(seconds=secs),
        ))


@pytest.mark.asyncio
async def test_report_aggregates_overview_and_templates(store):
    await _seed_batch(
        store, "b1", "scene_suite", "default", "partial", 2, 1,
        items=[
            ("succeeded", "", "j1"),
            ("succeeded", "", "j2"),
            ("failed", "必填输入缺失: product_images", ""),
        ],
        jobs={"j1": (2, 0.1), "j2": (8, 0.2)},
    )
    await _seed_batch(
        store, "b2", "white_bg_suite", "default", "completed", 2, 0,
        items=[("succeeded", "", "j3"), ("succeeded", "", "j4")],
        jobs={"j3": (60, 0.5), "j4": (150, 0.6)},
    )

    report = await store.get_batch_report(tenant_id="default")

    ov = report["overview"]
    assert ov["batch_count"] == 2
    assert ov["item_count"] == 5
    assert ov["succeeded"] == 4
    assert ov["failed"] == 1
    assert ov["success_rate"] == pytest.approx(0.8)
    assert ov["total_cost_usd"] == pytest.approx(1.4)
    # 4 个有 job 的条目：(2+8+60+150)/4 = 55s
    assert ov["avg_item_duration_ms"] == pytest.approx(55_000.0)

    assert report["batch_status"] == {"partial": 1, "completed": 1}

    by_tpl = {t["template_name"]: t for t in report["by_template"]}
    scene = by_tpl["scene_suite"]
    assert scene["item_count"] == 3
    assert scene["success_rate"] == pytest.approx(0.6667)
    assert scene["avg_duration_ms"] == pytest.approx(5_000.0)   # (2+8)/2
    assert by_tpl["white_bg_suite"]["success_rate"] == 1.0


@pytest.mark.asyncio
async def test_report_counts_unpriced_steps_separately(store):
    """未标定价格：金额只算已标定部分，另有 N 次未标定单独计数

    **不加数据库列**（本库只有 CREATE TABLE IF NOT EXISTS，没有迁移机制，老库不会加列）：
    步骤 `cost_usd` 保持 REAL，`cost_unknown`/`usage` 存在既有的 `outputs_json` 里，
    报表把两者分开呈现（"已标定 $x ｜ 另有 N 次未标定"）。
    """
    from src.workflow.models import StepRecord, StepStatus

    now = _now()
    await _seed_batch(
        store, "b1", "scene_suite", "default", "completed", 1, 0,
        items=[("succeeded", "", "j1")], jobs={"j1": (3, 0.2)},
    )
    await store.create_steps([
        StepRecord(job_id="j1", node="生图", type="agent", order=0,
                   status=StepStatus.SUCCEEDED, cost_usd=0.0,
                   outputs={"images": [{"slot_id": "main_white"}], "cost_usd": None,
                            "cost_unknown": True, "usage": {"images": 3}},
                   started_at=now, finished_at=now),
        StepRecord(job_id="j1", node="提示词", type="agent", order=1,
                   status=StepStatus.SUCCEEDED, cost_usd=0.2,
                   outputs={"cost_usd": 0.2, "cost_unknown": False},
                   started_at=now, finished_at=now),
    ])

    report = await store.get_batch_report(tenant_id="default")
    assert report["overview"]["total_cost_usd"] == pytest.approx(0.2)
    assert report["overview"]["unknown_cost_calls"] == 1
    assert report["by_template"][0]["unknown_cost_calls"] == 1

    # 事实（张数）留在既有列里，没有新增数据库列
    steps = await store.get_steps("j1")
    img_step = next(s for s in steps if s.node == "生图")
    assert img_step.outputs["usage"]["images"] == 3
    assert img_step.outputs["cost_unknown"] is True
    assert img_step.cost_usd == 0.0, "REAL 列存不了 NULL 语义，未知即 0，真实口径在 outputs_json"


@pytest.mark.asyncio
async def test_report_histogram_and_failure_reasons(store):
    await _seed_batch(
        store, "b1", "scene_suite", "default", "partial", 2, 2,
        items=[
            ("succeeded", "", "j1"),                       # 2s → 1-5s
            ("succeeded", "", "j2"),                       # 8s → 5-30s
            ("failed", "必填输入缺失: product_images", ""),
            ("failed", "job 终态: failed", "j3"),          # 有 job 但失败 → 计入耗时
        ],
        jobs={"j1": (2, 0.1), "j2": (8, 0.2), "j3": (200, 0.3)},
    )

    report = await store.get_batch_report(tenant_id="default")

    hist = {h["bucket"]: h["count"] for h in report["duration_histogram"]}
    assert hist == {"0-1s": 0, "1-5s": 1, "5-30s": 1, "30s-2min": 0, "2min+": 1}

    reasons = report["failure_reasons"]
    assert [r["reason"] for r in reasons] == ["job 终态", "必填输入缺失"]  # 按出现序稳定排序
    assert all(r["count"] == 1 for r in reasons)


@pytest.mark.asyncio
async def test_report_tenant_isolation_and_empty(store):
    await _seed_batch(
        store, "b1", "scene_suite", "default", "completed", 1, 0,
        items=[("succeeded", "", "j1")], jobs={"j1": (1, 0.1)},
    )
    await _seed_batch(
        store, "b2", "free_chat", "other_tenant", "completed", 1, 0,
        items=[("succeeded", "", "j2")], jobs={"j2": (1, 0.2)},
    )

    # 租户隔离：default 只看自己的
    report = await store.get_batch_report(tenant_id="default")
    assert report["overview"]["batch_count"] == 1
    assert [t["template_name"] for t in report["by_template"]] == ["scene_suite"]

    # 无数据租户 → 空报表
    empty = await store.get_batch_report(tenant_id="ghost")
    assert empty["overview"]["batch_count"] == 0
    assert empty["overview"]["success_rate"] == 0.0
    assert empty["failure_reasons"] == []

    # 不传 tenant → 全量（管理视角）
    all_report = await store.get_batch_report()
    assert all_report["overview"]["batch_count"] == 2


@pytest.mark.asyncio
async def test_report_zero_item_batch_no_phantom(store):
    """审计修复：0 条目批次不应被 LEFT JOIN 的 NULL 行虚增为 1 条 pending"""
    now = _now()
    await store.create_batch({
        "batch_id": "empty1", "template_name": "scene_suite", "tenant_id": "default",
        "status": "completed", "mode": "auto", "max_concurrency": 2,
        "total": 0, "done": 0, "failed": 0,
        "created_at": now, "updated_at": now,
    })
    report = await store.get_batch_report(tenant_id="default")
    assert report["overview"]["batch_count"] == 1
    assert report["overview"]["item_count"] == 0
    assert report["overview"]["pending_or_running"] == 0
    assert report["by_template"] == []


@pytest.mark.asyncio
async def test_report_running_item_duration_excluded(store):
    """审计修复：running 条目的部分耗时不计入分布（仅终态条目）"""
    now = _now()
    await store.create_batch({
        "batch_id": "b-run", "template_name": "scene_suite", "tenant_id": "default",
        "status": "running", "mode": "auto", "max_concurrency": 2,
        "total": 2, "done": 1, "failed": 0,
        "created_at": now, "updated_at": now,
    })
    await store.create_batch_items([
        {"batch_id": "b-run", "seq": 0, "inputs": {}, "job_id": "j-done",
         "status": "succeeded", "updated_at": now},
        {"batch_id": "b-run", "seq": 1, "inputs": {}, "job_id": "j-running",
         "status": "running", "updated_at": now},
    ])
    for jid, secs in (("j-done", 5), ("j-running", 999)):
        await store.create_job(WorkflowJob(
            job_id=jid, template_name="scene_suite", tenant_id="default",
            status=JobStatus.COMPLETED if jid == "j-done" else JobStatus.RUNNING,
            cost_so_far=0.1,
            created_at=now, updated_at=now + timedelta(seconds=secs),
        ))
    report = await store.get_batch_report(tenant_id="default")
    assert report["overview"]["avg_item_duration_ms"] == pytest.approx(5_000.0)
    hist = {h["bucket"]: h["count"] for h in report["duration_histogram"]}
    assert hist["5-30s"] == 1 and sum(hist.values()) == 1
