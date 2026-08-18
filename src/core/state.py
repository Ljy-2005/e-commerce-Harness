"""SessionState + RunStatus — 群聊全局状态"""

from typing import TypedDict, Optional
from enum import Enum
from datetime import datetime


class RunStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class SessionState(TypedDict, total=False):
    """群聊会话的全局状态

    在 ChatEngine 主循环中流转，每轮更新。
    前端通过 GET /api/sessions/{id} 获取完整快照。
    """
    session_id: str
    tenant_id: str                  # 租户 ID（多租户隔离）
    status: RunStatus               # 会话运行状态（见 RunStatus 枚举）
    created_at: datetime
    updated_at: datetime

    # 用户提交的任务
    task: dict                      # {product_images, product_info, platform, category_hint}

    # 群聊消息历史
    messages: list[dict]            # List[Message.model_dump()]

    # 各 Agent 产出物
    artifacts: dict                 # {analysis, prompts, images, review, compliance}

    # 控制字段
    turn_count: int
    max_turns: int
    cost_so_far: float

    # 错误历史
    error_history: list[dict]
