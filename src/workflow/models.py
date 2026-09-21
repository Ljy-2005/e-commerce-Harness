"""Workflow 编排层 — 数据模型"""

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


def _uid() -> str:
    return uuid.uuid4().hex[:16]


def _now() -> datetime:
    return datetime.now(timezone.utc)


class JobStatus(str, Enum):
    CREATED = "created"            # 已创建（尚未开始执行）
    QUEUED = "queued"              # 排队中（批量场景，Phase 2）
    RUNNING = "running"            # 执行中
    PAUSED = "paused"              # 手动挡暂停 / 显式暂停
    WAITING_HUMAN = "waiting_human"  # 等待人工审批
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    WAITING_HUMAN = "waiting_human"


class WorkflowJob(BaseModel):
    """一次工作流实例的执行状态"""
    job_id: str = Field(default_factory=_uid)
    template_name: str
    version: str = "1.0.0"
    tenant_id: str = "default"
    status: JobStatus = JobStatus.CREATED
    mode: str = "auto"            # auto | manual
    inputs: dict = Field(default_factory=dict)
    context: dict = Field(default_factory=dict)   # $ctx（retries 等）
    cost_so_far: float = 0.0
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    def to_store(self) -> dict:
        d = self.model_dump()
        d["status"] = self.status.value
        return d


class StepRecord(BaseModel):
    """单个节点的执行记录"""
    step_id: str = Field(default_factory=_uid)
    job_id: str
    node: str                       # 节点名
    type: str                       # tool/agent/condition/human/group_chat/end
    order: int = 0                  # 模板声明顺序（回跳重置用）
    status: StepStatus = StepStatus.PENDING
    attempt: int = 0
    inputs: dict = Field(default_factory=dict)
    outputs: dict = Field(default_factory=dict)
    error: str = ""
    elapsed_ms: float = 0.0
    cost_usd: float = 0.0
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

    def to_store(self) -> dict:
        d = self.model_dump()
        d["status"] = self.status.value
        d["started_at"] = self.started_at.isoformat() if self.started_at else None
        d["finished_at"] = self.finished_at.isoformat() if self.finished_at else None
        return d
