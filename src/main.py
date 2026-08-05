"""E-Commerce Harness — FastAPI 入口"""

import os
import base64
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, WebSocket, WebSocketDisconnect, Header, Request
from fastapi.middleware.cors import CORSMiddleware

from src.core.state import SessionState
from src.core.config import is_mock_mode
from src.core.tenant import TenantContext, get_tenant_registry, set_current_tenant
from src.providers import get_provider_registry
from src.agents.registry import get_agent_registry, AgentRegistry
from src.chat.session import SessionManager
from src.chat.engine import ChatEngine
from src.chat.broadcaster import Broadcaster

# ── 全局组件 ──
_provider_registry = get_provider_registry()
_agent_registry = get_agent_registry()
_session_manager = SessionManager()
_broadcaster = Broadcaster()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时加载 Agent 配置"""
    await _agent_registry.load_from_config(_provider_registry)
    names = _agent_registry.list_agent_names()
    print(f"[Harness] 已注册 {len(names)} 个 Agent: {', '.join(names)}")
    print(f"[Harness] Mock Mode: {is_mock_mode()}")
    yield


app = FastAPI(
    title="E-Commerce Harness",
    description="群聊式多智能体电商商品图生成系统",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS
_origins = os.getenv("ECOMM_CORS_ORIGINS", "http://localhost:5173,http://localhost:3000").split(",")
app.add_middleware(CORSMiddleware, allow_origins=_origins, allow_methods=["*"], allow_headers=["*"])


# ── REST API ──

@app.get("/health")
async def health():
    from src.agents.base import get_circuit_breaker, get_rate_limiter

    # 熔断器 + 速率限制器状态（单次遍历）
    circuits = {}
    rate_status = {}
    limiter = get_rate_limiter()
    for p in _provider_registry.list_available():
        p_name = p["name"]
        cb = get_circuit_breaker(p_name)
        circuits[p_name] = {
            "state": cb.state.value,
            "allow_request": cb.allow_request(),
        }
        rate_status[p_name] = limiter.remaining(p_name)

    return {
        "status": "healthy",
        "mock_mode": is_mock_mode(),
        "harness": {
            "circuits": circuits,
            "rate_limiter": rate_status,
            "rate_limiter_rpm": limiter.default_rpm,
        },
        "agents": [
            {"name": m.name, "requires": m.requires}
            for m in _agent_registry.list_all()
        ],
        "providers": _provider_registry.list_available(),
        "tenants": {
            "total": len(get_tenant_registry().list_ids()),
            "active_sessions": {
                tid: _session_manager.count_by_tenant(tid)
                for tid in get_tenant_registry().list_ids()
                if _session_manager.count_by_tenant(tid) > 0
            },
        },
    }


@app.get("/api/agents")
async def list_agents():
    """列出所有已注册 Agent 及其可配置参数（前端据此渲染配置面板）"""
    agents = []
    for meta in _agent_registry.list_all():
        agents.append({
            "name": meta.name,
            "description": meta.description,
            "version": meta.version,
            "requires": meta.requires,
            "params": meta.params,
            "timeout_ms": meta.timeout_ms,
        })
    return {"agents": agents}


@app.post("/api/sessions")
async def create_session(
    product_info: str = Form(""),
    platform: str = Form("taobao"),
    category_hint: str = Form(""),
    mode: str = Form("serial"),
    files: list[UploadFile] = File(...),
    x_tenant_id: str = Header("default", alias="X-Tenant-ID"),
):
    """创建会话，上传商品图片，返回 session_id

    mode: serial | ab_generate | debate | vote
    """
    # 租户校验
    tenant_registry = get_tenant_registry()
    tenant = tenant_registry.get(x_tenant_id)
    set_current_tenant(tenant)

    # 租户会话数限制
    active_count = _session_manager.count_by_tenant(tenant.tenant_id)
    if active_count >= tenant.quota.max_sessions:
        raise HTTPException(429, f"租户 '{tenant.tenant_id}' 活跃会话数已达上限 ({tenant.quota.max_sessions})")

    # 租户预算检查
    if tenant.quota.budget_usd > 0:
        # 简单检查：统计该租户已有会话的总成本
        tenant_cost = sum(
            s.get("cost_so_far", 0)
            for s_id in _session_manager.list_ids(tenant.tenant_id)
            if (s := _session_manager.get(s_id))
        )
        if tenant_cost >= tenant.quota.budget_usd:
            raise HTTPException(429, f"租户 '{tenant.tenant_id}' 月度预算已用完 (${tenant.quota.budget_usd:.2f})")

    if not files:
        raise HTTPException(400, "请至少上传 1 张图片")

    from src.harness.input_pipeline import ImageValidator
    validator = ImageValidator()

    images = []
    validation_errors = []
    for f in files[:10]:
        content = await f.read()

        # 输入验证：格式/大小/非空
        class _FakeImage:
            def __init__(self):
                self.source_path = f.filename or "upload"
                self.base64_data = ""
                self.media_type = f.content_type or "image/octet-stream"
                self.file_size = len(content)
        fi = _FakeImage()
        result = validator.validate(fi)
        if not result.passed:
            validation_errors.extend([f"[{f.filename}] {e}" for e in result.errors])
            continue

        b64 = base64.b64encode(content).decode("utf-8")
        images.append(b64)

    if not images and validation_errors:
        raise HTTPException(400, f"图片验证失败: {'; '.join(validation_errors[:3])}")

    session = _session_manager.create(
        product_images=images,
        product_info=product_info,
        platform=platform,
        category_hint=category_hint,
        collaboration_mode=mode,
        tenant_id=tenant.tenant_id,
    )

    # 异步启动 ChatEngine
    engine = ChatEngine(
        registry=_agent_registry,
        session_manager=_session_manager,
        broadcaster=_broadcaster,
    )
    import asyncio
    asyncio.create_task(engine.run(session))

    return {
        "session_id": session["session_id"],
        "status": session["status"],
        "message": f"会话已创建，{len(images)} 张图片已上传",
    }


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str):
    """获取会话完整状态"""
    session = _session_manager.get(session_id)
    if session is None:
        raise HTTPException(404, "会话不存在")
    return {
        "session_id": session["session_id"],
        "status": session["status"],
        "task": session.get("task", {}),
        "messages": session.get("messages", []),
        "artifacts": session.get("artifacts", {}),
        "turn_count": session.get("turn_count", 0),
        "cost_so_far": session.get("cost_so_far", 0.0),
        "created_at": str(session.get("created_at", "")),
        "updated_at": str(session.get("updated_at", "")),
    }


@app.get("/api/sessions/{session_id}/messages")
async def get_messages(session_id: str, since: int = 0):
    """增量拉取消息（since=turn 序号）"""
    session = _session_manager.get(session_id)
    if session is None:
        raise HTTPException(404, "会话不存在")
    messages = session.get("messages", [])
    return {"messages": messages[since:], "total": len(messages)}


@app.post("/api/sessions/{session_id}/decision")
async def human_decision(session_id: str, action: str = Form(...)):
    """人工审查决策

    action: "approve" (接受当前结果) | "retry" (重新生成) | "reject" (拒绝并终止)
    """
    if action not in ("approve", "retry", "reject"):
        raise HTTPException(400, "action 必须是 approve / retry / reject")

    session = _session_manager.get(session_id)
    if session is None:
        raise HTTPException(404, "会话不存在")

    if session.get("status") != "waiting_human":
        raise HTTPException(400, f"会话未处于等待人工审查状态 (当前: {session.get('status')})")

    # 恢复执行
    import asyncio
    engine = ChatEngine(
        registry=_agent_registry,
        session_manager=_session_manager,
        broadcaster=_broadcaster,
    )

    result = await engine.resume_after_hitl(session, action)

    return {
        "session_id": session_id,
        "status": result.get("status"),
        "decision": action,
        "message": f"人工决策 '{action}' 已执行",
    }


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    """删除会话及产出物"""
    session = _session_manager.get(session_id)
    if session is None:
        raise HTTPException(404, "会话不存在")
    await _session_manager.delete(session_id)
    return {"status": "deleted", "session_id": session_id}


@app.get("/api/memory/stats")
async def memory_stats():
    """Agent 记忆库统计"""
    from src.harness.agent_memory import AgentMemory
    memory = AgentMemory()
    return await memory.stats()


@app.get("/api/memory/recall")
async def memory_recall(category: str = "", limit: int = 5):
    """召回某品类历史成功经验"""
    from src.harness.agent_memory import AgentMemory
    memory = AgentMemory()
    entries = await memory.recall(category, limit=limit)
    return {"category": category, "total": len(entries), "entries": entries}


@app.get("/api/audit")
async def audit_log(session_id: str = "", agent: str = "", date: str = ""):
    """查询审计日志"""
    from src.harness.audit_logger import AuditLogger
    logger = AuditLogger()
    entries = await logger.query(session_id=session_id, agent_name=agent, date=date)
    stats = await logger.stats(date=date) if not session_id and not agent else {}
    return {"total": len(entries), "entries": entries[-50:], "stats": stats}


# ── WebSocket ──

@app.websocket("/ws/sessions/{session_id}")
async def ws_session(websocket: WebSocket, session_id: str):
    """实时群聊消息流"""
    session = _session_manager.get(session_id)
    if session is None:
        await websocket.close(code=4004, reason="会话不存在")
        return

    await _broadcaster.connect(session_id, websocket)

    # 先发送历史消息
    for msg in session.get("messages", []):
        await websocket.send_json(msg)

    # 保持连接，等待新消息（Broadcaster 会主动推送）
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        pass
    finally:
        _broadcaster.disconnect(session_id, websocket)
