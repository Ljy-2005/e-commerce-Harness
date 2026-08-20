"""E-Commerce Harness — FastAPI 入口（PRD D4：API 层）"""

import os
import asyncio
import base64
import json
import uuid
import yaml
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, WebSocket, WebSocketDisconnect, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

from src.core.state import SessionState
from src.core.config import is_mock_mode, load_models_config, list_agent_configs, load_agent_config, _project_root, get_cors_origins
from src.core.tenant import TenantContext, get_tenant_registry, set_current_tenant
from src.api.auth import AuthMiddleware
from src.core.logging_config import get_logger
from src.providers import get_provider_registry
from src.agents.registry import get_agent_registry, AgentRegistry
from src.chat.session import SessionManager
from src.chat.engine import ChatEngine
from src.chat.broadcaster import Broadcaster
from src.workflow.job_store import JobStore
from src.workflow.engine import WorkflowEngine
from src.workflow.batch import BatchScheduler
from src.workflow import templates as wf_templates
from src.workflow.models import JobStatus, StepStatus

logger = get_logger(__name__)

# ── 全局常量（魔法数字收敛）──
MAX_UPLOAD_IMAGES = 10          # 单次上传/实例化的最大图片数
MAX_REPLICATE_IMAGES = 3        # 风格复刻参考图上限
MAX_INTERJECTION_CHARS = 1000   # 群聊插话最大字符数
MAX_CHECKPOINT_RESTORE = 100    # 启动时最多恢复的 checkpoint 数
PREPROCESS_MAX_PIXELS = 2048    # 图片预处理最长边（像素）
PREPROCESS_JPEG_QUALITY = 85    # 图片预处理 JPEG 质量
AUDIT_TAIL = 50                 # 审计查询返回条数上限
EVENT_TAIL = 200                # 作业事件流返回条数上限
JOB_LIST_LIMIT = 100            # 作业/批次列表分页上限
MAX_BATCH_ITEMS = 100           # 单批次商品项上限（防无界 job/费用 DoS）
UPLOAD_READ_CHUNK = 1024 * 1024  # 上传分块读取大小（1MB）

# ── 全局组件 ──
_provider_registry = get_provider_registry()
_agent_registry = get_agent_registry()
_session_manager = SessionManager()
_broadcaster = Broadcaster()
_workflow_store = JobStore()
_workflow_engine = WorkflowEngine(_agent_registry, _workflow_store, _broadcaster)
_batch_scheduler = BatchScheduler(_workflow_engine, _workflow_store)

# ── API Key 元信息（设置页用） ──
_API_KEY_META = [
    {"name": "OpenAI", "env": "OPENAI_API_KEY", "capabilities": "vision / text / image"},
    {"name": "DeepSeek", "env": "DEEPSEEK_API_KEY", "capabilities": "text"},
    {"name": "Anthropic", "env": "ANTHROPIC_API_KEY", "capabilities": "vision / text"},
    {"name": "Seedream（即梦）", "env": "SEEDREAM_API_KEY", "capabilities": "image"},
    {"name": "火山引擎 AccessKey", "env": "VOLCANO_ACCESS_KEY", "capabilities": "image（签名）"},
    {"name": "火山引擎 SecretKey", "env": "VOLCANO_SECRET_KEY", "capabilities": "image（签名）"},
    {"name": "通义千问（Qwen）", "env": "DASHSCOPE_API_KEY", "capabilities": "vision / text"},
    {"name": "FLUX（BFL）", "env": "BFL_API_KEY", "capabilities": "image"},
    {"name": "FLUX（Fal.ai）", "env": "FAL_KEY", "capabilities": "image"},
    {"name": "Replicate", "env": "REPLICATE_API_KEY", "capabilities": "image"},
]

_ALLOWED_SECRET_KEYS = {m["env"] for m in _API_KEY_META} | {"MOCK_MODE"}


async def _read_upload_limited(f, limit: int = 20 * 1024 * 1024, name: str = "file") -> bytes:
    """分块读取上传文件，超过 limit 立即 413 中止（审计修复：防整体读入内存 DoS）"""
    chunks = []
    total = 0
    while True:
        chunk = await f.read(UPLOAD_READ_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise HTTPException(413, f"文件过大（>{limit // (1024 * 1024)}MB）：{name}")
        chunks.append(chunk)
    return b"".join(chunks)


def _resolve_tenant(x_tenant_id: str) -> TenantContext:
    """解析租户头：未知租户直接 403（审计修复：不再回退 default）"""
    tenant = get_tenant_registry().get(x_tenant_id)
    if tenant is None:
        raise HTTPException(403, f"未知租户: '{x_tenant_id}'（请检查 X-Tenant-ID 或配置 ECOMM_TENANTS）")
    set_current_tenant(tenant)
    return tenant


async def _append_interjection(session_id: str, content: str, tenant_id: str = "") -> dict:
    """M3 群聊插话：把用户指令追加到会话消息并广播（REST/WS 共用）

    tenant_id 非空时校验会话租户归属（审计修复：防跨租户插话）。
    """
    session = _session_manager.get(session_id, tenant_id=tenant_id)
    if session is None:
        raise HTTPException(404, "会话不存在")
    content = content.strip()[:MAX_INTERJECTION_CHARS]
    if not content:
        raise HTTPException(400, "插话内容不能为空")
    msg = {
        "id": uuid.uuid4().hex[:12],
        "turn": session.get("turn_count", 0),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "role": "system",
        "sender": "用户插话",
        "action": "respond",
        "content": {"interjection": content},
    }
    session["messages"].append(msg)
    await _broadcaster.broadcast(session_id, msg)
    await _session_manager.update(session_id, session)
    return msg


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
            for cp in checkpoints[:MAX_CHECKPOINT_RESTORE]:  # 最多恢复 MAX_CHECKPOINT_RESTORE 个
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

# CORS（默认白名单来自 config/default.yaml，ECOMM_CORS_ORIGINS 环境变量可覆盖）
_origins = get_cors_origins()
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


_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}


def _require_admin_access(request: Request):
    """管理面保护（审计修复）：未配置 ECOMM_API_KEY（开发模式）时仅允许本机访问。

    已配置 Key 时由 AuthMiddleware 全局保护；开发模式下远程调用管理端点一律 403，
    杜绝未授权者改写 API Key / 模型映射 / Agent 参数。
    """
    if os.getenv("ECOMM_API_KEY"):
        return
    if request.client and request.client.host in _LOCAL_HOSTS:
        return
    raise HTTPException(403, "开发模式管理面仅允许本机访问（请配置 ECOMM_API_KEY 后远程管理）")


@app.get("/api/admin/status")
async def admin_status(request: Request, x_tenant_id: str = Header("default", alias="X-Tenant-ID")):
    """管理员端点：完整系统状态（需鉴权；dev 模式仅本机）"""
    _require_admin_access(request)
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
    # 租户校验（未知租户 403）
    tenant = _resolve_tenant(x_tenant_id)

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

    from src.harness.input_pipeline import ImageValidator
    from src.harness.image_preprocessor import ImagePreprocessor
    validator = ImageValidator()
    preprocessor = ImagePreprocessor(max_pixels=PREPROCESS_MAX_PIXELS, quality=PREPROCESS_JPEG_QUALITY)

    images = []
    validation_errors = []
    preprocess_stats = {"total": 0, "resized": 0, "compressed": 0}

    for f in files[:MAX_UPLOAD_IMAGES]:
        content = await _read_upload_limited(f, name=f.filename or "upload")

        # 1. 格式验证（ImageValidator 校验格式/大小/base64 有效性）
        class _FakeImage:
            def __init__(self):
                self.source_path = f.filename or "upload"
                self.base64_data = base64.b64encode(content).decode("utf-8")
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
                "timestamp": datetime.now(timezone.utc).isoformat(),
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


@app.post("/api/sessions/{session_id}/interject")
async def interject_session(session_id: str, request: Request, x_tenant_id: str = Header("default", alias="X-Tenant-ID")):
    """M3 群聊插话：向运行中的会话插入用户指令（Coordinator 下一轮决策会读到）

    Body: {"content": "换个更简约的风格"}
    """
    session = _session_manager.get(session_id, tenant_id=x_tenant_id)
    if session is None:
        raise HTTPException(404, "会话不存在")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "请求体必须是 JSON")
    content = str(body.get("content", "")).strip()
    if not content:
        raise HTTPException(400, "content 不能为空")
    msg = await _append_interjection(session_id, content, tenant_id=x_tenant_id)
    return {"status": "appended", "session_id": session_id, "message": msg}


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
async def memory_stats(x_tenant_id: str = Header("default", alias="X-Tenant-ID")):
    """Agent 记忆库统计（审计修复：按租户隔离）"""
    from src.harness.agent_memory import AgentMemory
    memory = AgentMemory()
    return await memory.stats(tenant_id=x_tenant_id)


@app.get("/api/memory/recall")
async def memory_recall(category: str = "", limit: int = 5,
                        x_tenant_id: str = Header("default", alias="X-Tenant-ID")):
    """召回某品类历史成功经验（审计修复：按租户隔离）"""
    from src.harness.agent_memory import AgentMemory
    memory = AgentMemory()
    entries = await memory.recall(category, limit=limit, tenant_id=x_tenant_id)
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

    tenant = _resolve_tenant(x_tenant_id)
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
        # 审计修复：变体/评审数上限，防并行 LLM 费用 DoS
        if not 1 <= len(variants) <= 8:
            raise HTTPException(400, f"变体数量须在 1-8 之间（当前 {len(variants)}）")
        review_count = int(ab_config.get("review_count", 3) or 3)
        if not 1 <= review_count <= 5:
            raise HTTPException(400, f"review_count 须在 1-5 之间（当前 {review_count}）")
        config = ABTestConfig(
            agent_name=ab_config.get("agent_name", "提示词生成员"),
            variants=[ABVariant(**v) for v in variants],
            task_brief=ab_config.get("task_brief", "基于分析结果生成提示词"),
            review_count=review_count,
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
async def audit_log(session_id: str = "", agent: str = "", date: str = "",
                     x_tenant_id: str = Header("default", alias="X-Tenant-ID")):
    """查询审计日志（审计修复：按租户隔离，X-Tenant-ID 缺省 default）"""
    from src.harness.audit_logger import AuditLogger
    logger = AuditLogger()
    entries = await logger.query(session_id=session_id, agent_name=agent, date=date, tenant_id=x_tenant_id)
    stats = await logger.stats(date=date, tenant_id=x_tenant_id) if not session_id and not agent else {}
    return {"total": len(entries), "entries": entries[-AUDIT_TAIL:], "stats": stats}


# ── 会话列表 ──

@app.get("/api/sessions")
async def list_sessions(x_tenant_id: str = Header("default", alias="X-Tenant-ID")):
    """列出当前租户的全部会话（按创建时间倒序）"""
    sessions = []
    for sid in _session_manager.list_ids(x_tenant_id):
        s = _session_manager.get(sid, tenant_id=x_tenant_id)
        if not s:
            continue
        task = s.get("task", {})
        sessions.append({
            "session_id": sid,
            "status": s.get("status"),
            "turn_count": s.get("turn_count", 0),
            "cost_so_far": s.get("cost_so_far", 0.0),
            "platform": task.get("platform", ""),
            "product_info": task.get("product_info", "")[:80],
            "created_at": str(s.get("created_at", "")),
            "updated_at": str(s.get("updated_at", "")),
        })
    sessions.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return {"sessions": sessions, "total": len(sessions)}


# ── 设置：API Key / Provider / Agent / 模型配置 ──

# 各 Provider 可选模型目录（设置页下拉提示用）
_MODEL_CATALOG = {
    "openai": ["gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini", "o3", "o4-mini", "dall-e-3"],
    "deepseek": ["deepseek-chat", "deepseek-reasoner"],
    "anthropic": ["claude-sonnet-4-20250514", "claude-3-5-haiku-20241022"],
    "qwen": ["qwen-max", "qwen-vl-max", "qwen-plus"],
    "seedream": ["seedream-5.0", "seedream-4.0"],
    "flux": ["flux.1-dev", "flux.1-schnell", "flux-pro"],
    "mock": ["mock"],
}


def _settings_payload() -> dict:
    """汇总当前设置状态（含脱敏密钥、Provider、Agent、模型配置）"""
    agents = []
    for stem in list_agent_configs():
        cfg = load_agent_config(stem)
        if not cfg.get("name"):
            continue
        # 解析该 Agent 当前生效的模型（能力声明 → config/models.yaml）
        meta = _agent_registry.get_meta(cfg["name"])
        resolved_model = ""
        if meta:
            try:
                p, m = _provider_registry.resolve(meta.requires, meta.name)
                resolved_model = m or (getattr(p, "name", "mock") if p else "mock")
            except Exception:
                resolved_model = "mock"
        agents.append({
            "name": cfg["name"],
            "config_file": stem,
            "description": cfg.get("description", ""),
            "version": cfg.get("version", "1.0.0"),
            "requires": cfg.get("requires", []),
            "timeout_ms": cfg.get("timeout_ms", 30_000),
            "retry": cfg.get("retry", {}),
            "prompt": cfg.get("prompt", ""),
            "params": cfg.get("params", []),
            "resolved_model": resolved_model,
        })
    return {
        "mock_mode": is_mock_mode(),
        "api_keys": [
            {
                **m,
                "configured": bool(os.getenv(m["env"])),
                # 审计修复：不再返回任何密钥片段（此前泄漏前 4 后 4 共 8 字符）
            }
            for m in _API_KEY_META
        ],
        "providers": _provider_registry.list_available(),
        "agents": agents,
        "models_config": load_models_config(),
        "model_catalog": _MODEL_CATALOG,
        "cors_origins": _origins,
    }


@app.get("/api/settings")
async def get_settings():
    """获取当前系统设置（密钥脱敏）"""
    return _settings_payload()


@app.post("/api/settings/api-keys")
async def update_api_keys(request: Request):
    """保存 API Key（写入 config/secrets.yaml 并立即生效）

    Body: {"api_keys": {"OPENAI_API_KEY": "sk-...", "DEEPSEEK_API_KEY": ""}}
    空值表示删除该 Key。
    """
    _require_admin_access(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "请求体必须是 JSON")

    api_keys = body.get("api_keys", {})
    if not isinstance(api_keys, dict):
        raise HTTPException(400, "api_keys 必须是对象")

    unknown = set(api_keys) - _ALLOWED_SECRET_KEYS
    if unknown:
        raise HTTPException(400, f"不支持的密钥变量: {', '.join(sorted(unknown))}")

    # 1. 应用到当前进程
    for k, v in api_keys.items():
        if v:
            os.environ[k] = str(v)
        else:
            os.environ.pop(k, None)

    # 2. 持久化（重启后仍生效）
    from src.core.config import save_runtime_secrets
    save_runtime_secrets({k: str(v) for k, v in api_keys.items()})

    # 3. 重建 Provider 注册表 + 重载 Agent（新会话即用新配置）
    global _provider_registry
    from src.providers import reset_provider_registry
    _provider_registry = reset_provider_registry()
    await _agent_registry.load_from_config(_provider_registry)
    logger.info("API Key 已更新，Provider/Agent 配置已重载",
                extra={"updated": [k for k, v in api_keys.items() if v]})
    return _settings_payload()


@app.post("/api/settings/agents/{agent_name}")
async def update_agent_params(agent_name: str, request: Request):
    """更新 Agent 可配置参数（写入 config/agents/{file}.yaml 并重载注册表）

    Body: {"defaults": {"detail_level": "detailed", "focus_areas": [...]}}
    """
    _require_admin_access(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "请求体必须是 JSON")
    defaults = body.get("defaults", {})
    if not isinstance(defaults, dict):
        raise HTTPException(400, "defaults 必须是对象")

    # 找到该 Agent 对应的配置文件
    target_stem = None
    target_cfg = None
    for stem in list_agent_configs():
        cfg = load_agent_config(stem)
        if cfg.get("name") == agent_name:
            target_stem, target_cfg = stem, cfg
            break
    if not target_stem:
        raise HTTPException(404, f"Agent '{agent_name}' 的配置文件不存在")

    known_keys = {p.get("key") for p in (target_cfg.get("params") or [])}
    unknown = set(defaults) - known_keys
    if unknown:
        raise HTTPException(400, f"未知参数: {', '.join(sorted(unknown))}")

    # 写入 YAML
    path = _project_root() / "config" / "agents" / f"{target_stem}.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    for p in raw.get("params") or []:
        key = p.get("key")
        if key in defaults:
            p["default"] = defaults[key]
    path.write_text(
        yaml.safe_dump(raw, allow_unicode=True, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )

    # 重载注册表（Coordinator 自动感知新参数）
    await _agent_registry.load_from_config(_provider_registry)
    meta = _agent_registry.get_meta(agent_name)
    return {"updated": agent_name, "params": meta.params if meta else []}


@app.post("/api/settings/models")
async def update_models_config(request: Request):
    """更新能力→模型映射（写入 config/models.yaml 并立即生效）

    Body（部分更新，未提供的键保持不变）:
    {
      "capabilities": {
        "text": {"default": "qwen/qwen-max", "alternatives": ["deepseek/deepseek-chat"], "fallback": ["mock"]}
      },
      "agent_overrides": {
        "提示词生成员": {"text": "deepseek/deepseek-chat"}
      }
    }
    """
    _require_admin_access(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "请求体必须是 JSON")

    def _norm_spec_list(v):
        if v is None:
            return []
        if isinstance(v, str):
            return [s.strip() for s in v.split(",") if s.strip()]
        if isinstance(v, list):
            return [str(s).strip() for s in v if str(s).strip()]
        raise HTTPException(400, "模型列表必须是字符串或数组")

    current = load_models_config()

    # 1. capabilities 部分更新
    capabilities = body.get("capabilities", {})
    if not isinstance(capabilities, dict):
        raise HTTPException(400, "capabilities 必须是对象")
    merged_caps = dict(current.get("capabilities", {}))
    for cap, spec in capabilities.items():
        if not isinstance(spec, dict):
            raise HTTPException(400, f"capabilities.{cap} 必须是对象")
        entry = dict(merged_caps.get(cap, {}))
        if "default" in spec:
            val = str(spec["default"]).strip()
            if not val:
                raise HTTPException(400, f"capabilities.{cap}.default 不能为空")
            entry["default"] = val
        for key in ("alternatives", "fallback"):
            if key in spec:
                entry[key] = _norm_spec_list(spec[key])
        merged_caps[cap] = entry

    # 2. agent_overrides 部分更新
    agent_overrides = body.get("agent_overrides", {})
    if not isinstance(agent_overrides, dict):
        raise HTTPException(400, "agent_overrides 必须是对象")
    merged_overrides = dict(current.get("agent_overrides", {}))
    for agent, caps in agent_overrides.items():
        if not isinstance(caps, dict):
            raise HTTPException(400, f"agent_overrides.{agent} 必须是对象")
        merged_overrides[agent] = {
            str(k): str(v).strip() for k, v in caps.items() if str(v).strip()
        }

    # 3. 持久化
    new_config = {"capabilities": merged_caps, "agent_overrides": merged_overrides}
    path = _project_root() / "config" / "models.yaml"
    path.write_text(
        yaml.safe_dump(new_config, allow_unicode=True, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )

    # 4. 重建 Provider 注册表（重新解析映射）+ 重载 Agent（注入新模型名）
    global _provider_registry
    from src.providers import reset_provider_registry
    _provider_registry = reset_provider_registry()
    await _agent_registry.load_from_config(_provider_registry)
    logger.info("模型映射已更新并重载", extra={})
    return _settings_payload()


# ── Workflow API ──

def _job_payload(job, steps: list | None = None) -> dict:
    def _mask_images(v):
        # 审计修复：product_images 非 list/str 时不调 len 崩溃
        return f"<{len(v)} 张图片>" if isinstance(v, (list, str)) else "<隐藏>"
    return {
        "job_id": job.job_id,
        "template_name": job.template_name,
        "version": job.version,
        "status": job.status.value,
        "mode": job.mode,
        "inputs": {k: (_mask_images(v) if k == "product_images" else v) for k, v in job.inputs.items()},
        "retries": job.context.get("retries", 0) if isinstance(job.context, dict) else 0,
        "cost_so_far": job.cost_so_far,
        "created_at": str(job.created_at),
        "updated_at": str(job.updated_at),
        "steps": steps,
    }


@app.get("/api/workflows/templates")
async def workflow_templates():
    """Skill 库列表"""
    return {"templates": wf_templates.list_templates()}


@app.get("/api/workflows/templates/{name}/export")
async def export_template(name: str):
    """M4 模板导出：返回原始 YAML 文本（name 经安全路径校验，防路径穿越）"""
    tpl = wf_templates.load_template(name)
    if not tpl:
        raise HTTPException(404, f"模板 '{name}' 不存在")
    path = wf_templates.resolve_template_path(name)
    if path is None:
        raise HTTPException(404, f"模板 '{name}' 不存在")
    return PlainTextResponse(path.read_text(encoding="utf-8"), media_type="application/yaml")


@app.post("/api/workflows/templates/import")
async def import_template(request: Request):
    """M4 模板导入：body {yaml: str, template_name?: str, force?: bool}

    校验结构/Agent/工具后写入 config/workflows/{template_name}.yaml；已存在需 force=true。
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "请求体必须是 JSON")
    raw = str(body.get("yaml", ""))
    if not raw.strip():
        raise HTTPException(400, "yaml 不能为空")
    try:
        tpl = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        raise HTTPException(400, f"YAML 解析失败: {str(e)[:200]}")
    if not isinstance(tpl, dict) or not tpl.get("name"):
        raise HTTPException(400, "模板缺少 name")

    template_name = str(body.get("template_name") or tpl.get("template_name") or "").strip()
    if (not template_name
            or any(ch in template_name for ch in ("/", "\\", ":", "\x00"))
            or ".." in template_name
            or template_name.startswith("_")):
        raise HTTPException(400, "template_name 非法（需提供且不能含路径字符）")

    agent_names = {m.name for m in _agent_registry.list_all()}
    errors = wf_templates.validate_template(tpl, agent_names)
    if errors:
        raise HTTPException(400, f"模板校验失败: {'; '.join(errors)}")
    # 工具节点校验
    from src.workflow.tools import get_tool
    for node, cfg in (tpl.get("nodes") or {}).items():
        if cfg.get("type") == "tool" and not get_tool(cfg.get("tool", "")):
            raise HTTPException(400, f"节点 '{node}' 引用的工具 '{cfg.get('tool')}' 未注册")

    path = _project_root() / "config" / "workflows" / f"{template_name}.yaml"
    if path.exists() and not bool(body.get("force")):
        raise HTTPException(400, f"模板 '{template_name}' 已存在（force=true 可覆盖）")
    path.write_text(raw, encoding="utf-8")

    imported = wf_templates.load_template(template_name)
    return {"imported": template_name, "name": imported.get("name", template_name),
            "node_count": len(imported.get("nodes") or {})}


@app.post("/api/webhooks/workflows/{job_id}/decision")
async def webhook_decision(job_id: str, request: Request):
    """M4 入站连接器：外部系统回调触发工作流状态流转（人工审批决策）

    鉴权：X-Webhook-Token 头必须等于 ECOMM_WEBHOOK_TOKEN（未配置则回调停用）。
    Body: {"action": "approve" | "retry" | "reject"}
    """
    expected_token = os.getenv("ECOMM_WEBHOOK_TOKEN", "")
    if not expected_token:
        raise HTTPException(503, "Webhook 回调未启用（需配置 ECOMM_WEBHOOK_TOKEN）")
    if request.headers.get("X-Webhook-Token", "") != expected_token:
        raise HTTPException(401, "无效的 Webhook Token")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "请求体必须是 JSON")
    try:
        result = await _workflow_engine.decide_human(job_id, body.get("action", ""))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"status": "accepted", **result}


@app.post("/api/workflows/templates/{name}/instantiate")
async def workflow_instantiate(
    name: str,
    request: Request,
    platform: str = Form("taobao"),
    category_hint: str = Form(""),
    product_info: str = Form(""),
    scene: str = Form(""),
    collaboration_mode: str = Form("serial"),
    mode: str = Form("auto"),
    x_tenant_id: str = Header("default", alias="X-Tenant-ID"),
):
    """实例化模板 → 创建 job（multipart）

    图片类输入按字段名分发：模板每个 type: images 的输入接受同名文件字段
    （如 reference_images=参考图、product_images=商品图）；
    兼容旧约定：files 字段回退映射到 product_images。
    """
    _resolve_tenant(x_tenant_id)  # 未知租户 403（审计修复）
    from src.harness.image_preprocessor import ImagePreprocessor

    # 收集 multipart 中的文件字段（兼容 FastAPI 已解析的 Form 参数）
    # 注意：FastAPI/Starlette 的 UploadFile 类型不统一，用鸭子类型识别
    form = await request.form()
    file_fields: dict[str, list] = {}
    for key, value in form.multi_items():
        items = value if isinstance(value, list) else [value]
        for v in items:
            if hasattr(v, "filename") and hasattr(v, "read"):
                file_fields.setdefault(key, []).append(v)

    template = wf_templates.load_template(name)
    if not template:
        raise HTTPException(404, f"模板 '{name}' 不存在")
    images_input_keys = [
        i["key"] for i in template.get("inputs", [])
        if i.get("type") == "images"
    ]

    preprocessor = ImagePreprocessor(max_pixels=PREPROCESS_MAX_PIXELS, quality=PREPROCESS_JPEG_QUALITY)

    async def _process_files(key: str) -> list[str]:
        if key in file_fields:
            uploads = file_fields[key]
        elif key == "product_images" and "files" in file_fields:
            uploads = file_fields["files"]  # 旧约定回退
        else:
            uploads = []
        result = []
        for f in uploads[:MAX_UPLOAD_IMAGES]:
            content = await _read_upload_limited(f, name=f.filename or "upload")
            pre = preprocessor.process(content, source_name=f.filename or "upload")
            if pre.error:
                raise HTTPException(400, f"[{f.filename}] {pre.error}")
            result.append(base64.b64encode(pre.data).decode("utf-8"))
        return result

    inputs = {
        "platform": platform,
        "category_hint": category_hint,
        "product_info": product_info,
        "scene": scene,
        "collaboration_mode": collaboration_mode,
    }
    for key in images_input_keys:
        inputs[key] = await _process_files(key)

    try:
        job = wf_templates.instantiate(name, inputs, tenant_id=x_tenant_id, mode=mode)
    except ValueError as e:
        raise HTTPException(400, str(e))

    await _workflow_store.create_job(job)
    task = await _workflow_engine.start(job)

    def _on_done(t: asyncio.Task):
        try:
            t.result()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("workflow 执行异常", extra={"job_id": job.job_id, "error": str(e)})
    task.add_done_callback(_on_done)

    return {"job_id": job.job_id, "template": name, "status": job.status.value, "mode": job.mode}


@app.get("/api/workflows/jobs")
async def workflow_jobs(x_tenant_id: str = Header("default", alias="X-Tenant-ID")):
    """作业列表（租户隔离）"""
    jobs = await _workflow_store.list_jobs(tenant_id=x_tenant_id, limit=JOB_LIST_LIMIT)
    return {"jobs": [_job_payload(j) for j in jobs], "total": len(jobs)}


@app.get("/api/workflows/jobs/{job_id}")
async def workflow_job(job_id: str, x_tenant_id: str = Header("default", alias="X-Tenant-ID")):
    """作业详情（含步骤 + 事件流）"""
    job = await _workflow_store.get_job(job_id)
    if job is None or (x_tenant_id and job.tenant_id != x_tenant_id):
        raise HTTPException(404, "作业不存在")
    steps = await _workflow_store.get_steps(job_id)
    events = await _workflow_store.get_events(job_id)
    return {
        **_job_payload(job, steps=[
            {
                "step_id": s.step_id, "node": s.node, "type": s.type,
                "status": s.status.value, "attempt": s.attempt,
                "outputs": s.outputs, "error": s.error,
                "elapsed_ms": s.elapsed_ms, "cost_usd": s.cost_usd,
            }
            for s in steps
        ]),
        "events": events[-EVENT_TAIL:],
    }


@app.post("/api/workflows/jobs/{job_id}/control")
async def workflow_control(job_id: str, request: Request, x_tenant_id: str = Header("default", alias="X-Tenant-ID")):
    """手动挡控制：{action: run_next|pause|resume|retry_step|skip_step|cancel, step_node?}"""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "请求体必须是 JSON")
    action = body.get("action", "")
    step_node = body.get("step_node", "")
    # 租户校验（审计修复：防跨租户 IDOR）
    job = await _workflow_store.get_job(job_id)
    if job is None or (x_tenant_id and job.tenant_id != x_tenant_id):
        raise HTTPException(404, "作业不存在")
    try:
        result = await _workflow_engine.control(job_id, action, step_node)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return result


@app.post("/api/workflows/jobs/{job_id}/decision")
async def workflow_decision(job_id: str, request: Request, x_tenant_id: str = Header("default", alias="X-Tenant-ID")):
    """人工审批决策：{action: approve|retry|reject}"""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "请求体必须是 JSON")
    # 租户校验（审计修复：防跨租户 IDOR）
    job = await _workflow_store.get_job(job_id)
    if job is None or (x_tenant_id and job.tenant_id != x_tenant_id):
        raise HTTPException(404, "作业不存在")
    try:
        result = await _workflow_engine.decide_human(job_id, body.get("action", ""))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return result


# ── 批量任务（M2） ──

_CSV_HEADERS = ("product_info", "platform", "category_hint", "scene", "collaboration_mode")


def _parse_batch_csv(raw: bytes) -> list[dict]:
    """解析 CSV：product_info,platform,category_hint,scene,collaboration_mode"""
    import csv
    import io as _io

    text = None
    for enc in ("utf-8-sig", "gbk", "utf-8"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ValueError("CSV 编码无法识别（支持 utf-8/gbk）")

    rows = list(csv.reader(_io.StringIO(text)))
    if not rows:
        raise ValueError("CSV 为空")
    # 表头识别（可选）
    start = 1 if rows[0][:2] == ["product_info", "platform"] else 0
    items = []
    for row in rows[start:]:
        if not row or not any(c.strip() for c in row):
            continue
        item = {"product_info": (row[0] if len(row) > 0 else "").strip(),
                "platform": (row[1] if len(row) > 1 else "").strip() or "taobao"}
        if len(row) > 2 and row[2].strip():
            item["category_hint"] = row[2].strip()
        if len(row) > 3 and row[3].strip():
            item["scene"] = row[3].strip()
        if len(row) > 4 and row[4].strip():
            item["collaboration_mode"] = row[4].strip()
        # CSV 无图片列：注入合法 base64 占位符（Mock 模式完整可用；真实模式请用 JSON 传真实 base64）
        item.setdefault("product_images", ["Y3N2"])  # b64("csv")
        items.append(item)
    if not items:
        raise ValueError("CSV 没有有效数据行")
    return items


@app.post("/api/workflows/batches")
async def create_batch(request: Request, x_tenant_id: str = Header("default", alias="X-Tenant-ID")):
    """创建批量任务。

    JSON 方式（application/json）:
        {"template_name": "scene_suite", "mode": "auto", "max_concurrency": 3,
         "items": [{"platform": "taobao", "product_info": "商品A"}, ...]}
    CSV 方式（multipart/form-data）:
        template_name + file（列: product_info,platform,category_hint,scene,collaboration_mode）
    """
    content_type = request.headers.get("content-type", "")
    _resolve_tenant(x_tenant_id)  # 未知租户 403（审计修复）
    if "application/json" in content_type:
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "请求体必须是 JSON")
        template_name = body.get("template_name", "")
        items = body.get("items", [])
        mode = body.get("mode", "auto")
        max_concurrency = body.get("max_concurrency", 3)
        if not isinstance(items, list):
            raise HTTPException(400, "items 必须是数组")
    else:
        try:
            form = await request.form()
        except Exception:
            raise HTTPException(400, "仅支持 application/json 或 multipart/form-data（CSV）")
        template_name = str(form.get("template_name", ""))
        mode = str(form.get("mode", "auto"))
        max_concurrency = form.get("max_concurrency", 3)
        csv_file = form.get("file")
        if csv_file is None:
            raise HTTPException(400, "缺少 CSV 文件（file 字段）")
        try:
            items = _parse_batch_csv(await _read_upload_limited(csv_file, name="batch.csv"))
        except ValueError as e:
            raise HTTPException(400, str(e))

    # 防无界 job/费用 DoS（审计修复）
    if len(items) > MAX_BATCH_ITEMS:
        raise HTTPException(400, f"单批次最多 {MAX_BATCH_ITEMS} 个商品项（当前 {len(items)}）")

    try:
        batch = await _batch_scheduler.submit(
            template_name, items, tenant_id=x_tenant_id,
            mode=mode, max_concurrency=max_concurrency,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return batch


@app.get("/api/workflows/batches")
async def list_batches(x_tenant_id: str = Header("default", alias="X-Tenant-ID")):
    """批量任务列表"""
    batches = await _workflow_store.list_batches(tenant_id=x_tenant_id, limit=JOB_LIST_LIMIT)
    return {"batches": batches, "total": len(batches)}


@app.get("/api/workflows/batches/report")
async def batch_report(x_tenant_id: str = Header("default", alias="X-Tenant-ID")):
    """M5 批量任务报表：总览 / 模板成功率 / 耗时分布 / 失败原因 Top-5（租户隔离）

    注意：本路由须声明在 /batches/{batch_id} 之前，避免 report 被当作 batch_id。
    """
    return await _workflow_store.get_batch_report(tenant_id=x_tenant_id)


@app.get("/api/workflows/batches/{batch_id}")
async def get_batch(batch_id: str, x_tenant_id: str = Header("default", alias="X-Tenant-ID")):
    """批量任务详情（含逐项状态）"""
    batch = await _workflow_store.get_batch(batch_id)
    if batch is None or (x_tenant_id and batch["tenant_id"] != x_tenant_id):
        raise HTTPException(404, "批次不存在")
    items = await _workflow_store.get_batch_items(batch_id)
    return {**batch, "items": items}


@app.post("/api/workflows/batches/{batch_id}/control")
async def control_batch(batch_id: str, request: Request, x_tenant_id: str = Header("default", alias="X-Tenant-ID")):
    """批量控制：{action: pause|resume|cancel|retry_failed}"""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "请求体必须是 JSON")
    # 租户校验（审计修复：防跨租户批次控制）
    batch = await _workflow_store.get_batch(batch_id)
    if batch is None or (x_tenant_id and batch["tenant_id"] != x_tenant_id):
        raise HTTPException(404, "批次不存在")
    try:
        result = await _batch_scheduler.control(batch_id, body.get("action", ""))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return result


@app.post("/api/workflows/jobs/{job_id}/replicate")
async def workflow_replicate(
    job_id: str,
    files: list[UploadFile] = File(...),
    x_tenant_id: str = Header("default", alias="X-Tenant-ID"),
):
    """M3 一键风格复刻：上传参考图 → 风格拆解员拆解 → 融合进提示词并重跑

    multipart: files = 参考风格图（1-3 张）
    """
    # 租户校验（审计修复：防跨租户 IDOR）
    job = await _workflow_store.get_job(job_id)
    if job is None or (x_tenant_id and job.tenant_id != x_tenant_id):
        raise HTTPException(404, "作业不存在")
    from src.harness.image_preprocessor import ImagePreprocessor

    preprocessor = ImagePreprocessor(max_pixels=PREPROCESS_MAX_PIXELS, quality=PREPROCESS_JPEG_QUALITY)
    images = []
    for f in files[:MAX_REPLICATE_IMAGES]:
        content = await _read_upload_limited(f, name=f.filename or "upload")
        pre = preprocessor.process(content, source_name=f.filename or "upload")
        if pre.error:
            raise HTTPException(400, f"[{f.filename}] {pre.error}")
        images.append(base64.b64encode(pre.data).decode("utf-8"))
    if not images:
        raise HTTPException(400, "请至少上传 1 张参考风格图")

    try:
        breakdown = await _workflow_engine.replicate_style(job_id, images)
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e))
    return {
        "job_id": job_id,
        "status": "replicating",
        "style": breakdown,
    }


@app.websocket("/ws/workflows/jobs/{job_id}")
async def ws_workflow_job(websocket: WebSocket, job_id: str):
    """工作流实时事件流"""
    expected_key = os.getenv("ECOMM_API_KEY", "")
    if expected_key:
        api_key = websocket.query_params.get("api_key", "")
        if api_key != expected_key:
            await websocket.close(code=4001, reason="Missing or invalid API Key")
            return

    job = await _workflow_store.get_job(job_id)
    if job is None:
        await websocket.close(code=4004, reason="作业不存在")
        return
    # 租户校验（审计修复：WS 也按租户隔离；tenant 查询参数可选）
    tenant = websocket.query_params.get("tenant", "")
    if tenant and job.tenant_id != tenant:
        await websocket.close(code=4004, reason="作业不存在")
        return

    await _broadcaster.connect(job_id, websocket)  # connect 内部会 accept

    # 回放历史事件
    try:
        for ev in await _workflow_store.get_events(job_id):
            await websocket.send_json({"type": "workflow_event", "event": ev["event"], "payload": ev["payload"], "timestamp": ev["created_at"]})
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        pass
    finally:
        _broadcaster.disconnect(job_id, websocket)


# ── WebSocket ──

@app.websocket("/ws/sessions/{session_id}")
async def ws_session(websocket: WebSocket, session_id: str):
    """实时群聊消息流"""
    # WebSocket 鉴权：检查查询参数 ?api_key=...（子协议方式未实现，见注释）
    expected_key = os.getenv("ECOMM_API_KEY", "")
    if expected_key:
        api_key = websocket.query_params.get("api_key", "")
        if api_key != expected_key:
            await websocket.close(code=4001, reason="Missing or invalid API Key")
            return

    # 租户校验（审计修复：WS 也按租户隔离；tenant 查询参数可选）
    tenant = websocket.query_params.get("tenant", "")
    session = _session_manager.get(session_id, tenant_id=tenant)
    if session is None:
        await websocket.close(code=4004, reason="会话不存在")
        return

    await _broadcaster.connect(session_id, websocket)

    # 回放历史消息 + 保持连接（回放进 try/finally：断开必清理，防死连接残留）
    try:
        for msg in session.get("messages", []):
            await websocket.send_json(msg)

        # 保持连接：支持 ping / 用户插话（{type: "chat", content: "..."}）
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
                continue
            try:
                payload = json.loads(data)
            except Exception:
                continue
            if isinstance(payload, dict) and payload.get("type") == "chat":
                content = str(payload.get("content", "")).strip()
                if content:
                    await _append_interjection(session_id, content, tenant_id=tenant)
    except WebSocketDisconnect:
        pass
    finally:
        _broadcaster.disconnect(session_id, websocket)
