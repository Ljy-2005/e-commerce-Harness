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
from src.core.auth import AuthMiddleware
from src.core.logging_config import get_logger
from src.providers import get_provider_registry
from src.agents.registry import get_agent_registry, AgentRegistry
from src.chat.session import SessionManager
from src.chat.engine import ChatEngine
from src.chat.broadcaster import Broadcaster

logger = get_logger(__name__)

# ── 全局组件 ──
_provider_registry = get_provider_registry()
_agent_registry = get_agent_registry()
_session_manager = SessionManager()
_broadcaster = Broadcaster()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时加载 Agent 配置 + 恢复 checkpoint"""
    await _agent_registry.load_from_config(_provider_registry)
    names = _agent_registry.list_agent_names()
    mock_mode = is_mock_mode()
    logger.info("Agent 注册完成", extra={"count": len(names), "names": ", ".join(names)})
    logger.info("运行模式", extra={"mock_mode": mock_mode, "tenants": len(get_tenant_registry().list_ids())})
    logger.info("API Key 鉴权", extra={"enabled": bool(os.getenv("ECOMM_API_KEY"))})

    # P3: 启动时恢复未完成的 checkpoint
    try:
        from src.storage.checkpoint import load_checkpoint
        import glob as _glob
        checkpoint_dir = Path(__file__).parent.parent / "data" / "checkpoints"
        if checkpoint_dir.exists():
            checkpoints = list(checkpoint_dir.glob("*.json"))
            loaded = 0
            for cp in checkpoints[:100]:  # 最多恢复 100 个
                try:
                    state = await load_checkpoint(cp.stem)
                    if state and state.get("status") not in ("completed", "failed"):
                        state["status"] = "failed"  # 标记为非正常结束
                        _session_manager._sessions[state.get("session_id", cp.stem)] = state
                        loaded += 1
                except Exception:
                    pass
            if loaded:
                logger.info("从 checkpoint 恢复会话", extra={"count": loaded})
    except Exception:
        pass  # 恢复失败不影响启动

    yield
    logger.info("Harness 关闭")


app = FastAPI(
    title="E-Commerce Harness",
    description="群聊式多智能体电商商品图生成系统",
    version="0.2.0",
    lifespan=lifespan,
)

# CORS
_origins = os.getenv("ECOMM_CORS_ORIGINS", "http://localhost:5173,http://localhost:3000").split(",")
app.add_middleware(CORSMiddleware, allow_origins=_origins, allow_methods=["*"], allow_headers=["*"])

# API Key 鉴权
app.add_middleware(AuthMiddleware)


# ── REST API ──

@app.get("/health")
async def health():
    """公开健康检查 — 仅返回最小必要信息。详细状态见 /api/admin/status"""
    return {
        "status": "healthy",
        "mock_mode": is_mock_mode(),
    }


@app.get("/api/admin/status")
async def admin_status(x_tenant_id: str = Header("default", alias="X-Tenant-ID")):
    """管理员端点：完整系统状态（需鉴权）"""
    from src.agents.base import get_circuit_breaker, get_rate_limiter

    circuits = {}
    rate_status = {}
    limiter = get_rate_limiter()
    for p in _provider_registry.list_available():
        p_name = p["name"]
        cb = get_circuit_breaker(p_name)
        circuits[p_name] = {"state": cb.state.value, "allow_request": cb.allow_request()}
        rate_status[p_name] = limiter.remaining(p_name)

    return {
        "status": "healthy",
        "mock_mode": is_mock_mode(),
        "harness": {
            "circuits": circuits,
            "rate_limiter": rate_status,
            "rate_limiter_rpm": limiter.default_rpm,
        },
        "agents": [{"name": m.name, "requires": m.requires} for m in _agent_registry.list_all()],
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
    from src.harness.image_preprocessor import ImagePreprocessor
    validator = ImageValidator()
    preprocessor = ImagePreprocessor(max_pixels=2048, quality=85)

    images = []
    validation_errors = []
    preprocess_stats = {"total": 0, "resized": 0, "compressed": 0}

    for f in files[:10]:
        content = await f.read()

        # 1. 格式验证
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

        # 2. 图片预处理（resize/compress/format-normalize）
        pre = preprocessor.process(content, source_name=f.filename or "upload")
        if pre.error:
            validation_errors.append(f"[{f.filename}] {pre.error}")
            continue

        preprocess_stats["total"] += 1
        if pre.resized:
            preprocess_stats["resized"] += 1
        if pre.compressed:
            preprocess_stats["compressed"] += 1

        b64 = base64.b64encode(pre.data).decode("utf-8")
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

    # 异步启动 ChatEngine（带错误回调）
    engine = ChatEngine(
        registry=_agent_registry,
        session_manager=_session_manager,
        broadcaster=_broadcaster,
    )
    import asyncio
    task = asyncio.create_task(engine.run(session))

    def _on_engine_done(t: asyncio.Task):
        try:
            t.result()
        except asyncio.CancelledError:
            logger.info("引擎任务被取消", extra={"session_id": session["session_id"]})
        except Exception as e:
            logger.error("引擎执行异常", extra={"session_id": session["session_id"], "error": str(e)})
            session["status"] = "failed"
            session["messages"].append({
                "id": uuid.uuid4().hex[:12], "turn": 0,
                "timestamp": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
                "role": "system", "sender": "系统", "action": "error",
                "content": {"error": f"引擎异常: {str(e)[:200]}"},
            })
    task.add_done_callback(_on_engine_done)

    return {
        "session_id": session["session_id"],
        "status": session["status"],
        "message": f"会话已创建，{len(images)} 张图片已上传",
        "preprocess": preprocess_stats,
    }


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str, x_tenant_id: str = Header("default", alias="X-Tenant-ID")):
    """获取会话完整状态"""
    session = _session_manager.get(session_id, tenant_id=x_tenant_id)
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
async def get_messages(session_id: str, since: int = 0, x_tenant_id: str = Header("default", alias="X-Tenant-ID")):
    """增量拉取消息（since=turn 序号）"""
    session = _session_manager.get(session_id, tenant_id=x_tenant_id)
    if session is None:
        raise HTTPException(404, "会话不存在")
    messages = session.get("messages", [])
    return {"messages": messages[since:], "total": len(messages)}


@app.post("/api/sessions/{session_id}/decision")
async def human_decision(session_id: str, action: str = Form(...), x_tenant_id: str = Header("default", alias="X-Tenant-ID")):
    """人工审查决策

    action: "approve" (接受当前结果) | "retry" (重新生成) | "reject" (拒绝并终止)
    """
    if action not in ("approve", "retry", "reject"):
        raise HTTPException(400, "action 必须是 approve / retry / reject")

    session = _session_manager.get(session_id, tenant_id=x_tenant_id)
    if session is None:
        raise HTTPException(404, "会话不存在")

    if session.get("status") != "waiting_human":
        raise HTTPException(400, f"会话未处于等待人工审查状态 (当前: {session.get('status')})")

    # 恢复执行
    engine = ChatEngine(
        registry=_agent_registry,
        session_manager=_session_manager,
        broadcaster=_broadcaster,
    )

    try:
        result = await engine.resume_after_hitl(session, action)
    except Exception as e:
        logger.error("HITL 恢复执行异常", extra={"session_id": session_id, "error": str(e)})
        session["status"] = "failed"
        await _session_manager.update(session_id, session)
        raise HTTPException(500, f"恢复执行失败: {str(e)[:200]}")

    return {
        "session_id": session_id,
        "status": result.get("status"),
        "decision": action,
        "message": f"人工决策 '{action}' 已执行",
    }


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str, x_tenant_id: str = Header("default", alias="X-Tenant-ID")):
    """删除会话及产出物"""
    session = _session_manager.get(session_id, tenant_id=x_tenant_id)
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


@app.post("/api/sessions/{session_id}/ab-test")
async def run_ab_test(session_id: str, x_tenant_id: str = Header("default", alias="X-Tenant-ID"), request: Request = None):
    """在已有会话中运行 A/B 测试（同角色多版本并行对比）

    默认对比 3 个模型变体（GPT-4o / DeepSeek / Qwen），
    也可通过 POST body 自定义变体列表。

    Body (可选):
    {
        "agent_name": "提示词生成员",
        "variants": [
            {"variant_id": "v1", "label": "GPT-4o", "model_override": "gpt-4o"},
            {"variant_id": "v2", "label": "DeepSeek", "model_override": "deepseek-chat"}
        ],
        "review_count": 3
    }
    """
    session = _session_manager.get(session_id, tenant_id=x_tenant_id)
    if session is None:
        raise HTTPException(404, "会话不存在")

    tenant = get_tenant_registry().get(x_tenant_id)
    set_current_tenant(tenant)
    session["tenant_id"] = tenant.tenant_id

    from src.harness.ab_testing import ABTestConfig, ABVariant, ABTestRunner, make_model_variants

    # 解析 POST body（优先于 session 中预存的配置）
    ab_config = {}
    if request is not None:
        try:
            body = await request.json()
            if isinstance(body, dict):
                ab_config = body
        except Exception:
            pass  # 无 body 或非 JSON → 使用默认
    if not ab_config:
        ab_config = session.get("task", {}).get("ab_config", {})
    variants = ab_config.get("variants", [])
    if variants:
        config = ABTestConfig(
            agent_name=ab_config.get("agent_name", "提示词生成员"),
            variants=[ABVariant(**v) for v in variants],
            task_brief=ab_config.get("task_brief", "基于分析结果生成提示词"),
            review_count=ab_config.get("review_count", 3),
            scoring_method=ab_config.get("scoring_method", "multi_reviewer"),
        )
    else:
        config = make_model_variants(
            "提示词生成员",
            [
                ("v_gpt4o", "gpt-4o"),
                ("v_deepseek", "deepseek-chat"),
                ("v_qwen", "qwen-max"),
            ],
        )
        config.review_count = 3

    runner = ABTestRunner(_agent_registry, session)
    result = await runner.run(config)
    session["artifacts"]["ab_test"] = {
        "agent_name": config.agent_name,
        "winner": result.winner.variant_id if result.winner else "none",
        "winner_score": result.winner.avg_score if result.winner else 0,
        "ranking": [
            {"id": vr.variant_id, "label": vr.label, "score": vr.avg_score,
             "cost_usd": vr.cost_usd, "elapsed_ms": vr.elapsed_ms}
            for vr in result.ranking
        ],
        "total_cost_usd": result.total_cost_usd,
    }
    return session["artifacts"]["ab_test"]


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
    # WebSocket 鉴权：检查查询参数 ?api_key=... 或子协议
    expected_key = os.getenv("ECOMM_API_KEY", "")
    if expected_key:
        api_key = websocket.query_params.get("api_key", "")
        if api_key != expected_key:
            await websocket.close(code=4001, reason="Missing or invalid API Key")
            return

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
