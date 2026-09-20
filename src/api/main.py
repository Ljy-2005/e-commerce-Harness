"""E-Commerce Harness — FastAPI 入口（PRD D4：API 层）"""

import os
import asyncio
import base64
import hmac
import json
import re
import time
import uuid
import yaml
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import (FastAPI, HTTPException, UploadFile, File, Form, WebSocket,
                     WebSocketDisconnect, Header, Request, Response)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse

from src.core.state import SessionState
from src.core.config import is_mock_mode, load_models_config, list_agent_configs, load_agent_config, _project_root, get_cors_origins, chat_settings, save_chat_settings, image_settings, save_image_settings
from src.core.tenant import TenantContext, get_tenant_registry, set_current_tenant
from src.api.auth import AuthMiddleware, authenticate_api_key, auth_enabled, env_tenant_keys, get_tenant_keys, reload_tenant_keys
from src.core.logging_config import get_logger
from src.harness.image_preprocessor import DEFAULT_MAX_BYTES as _PREPROCESS_MAX_BYTES
from src.harness.style_store import MAX_ENTRIES_PER_TENANT as _STYLE_MAX_ENTRIES
from src.harness.style_store import MAX_PHOTOS as _STYLE_MAX_PHOTOS
from src.harness.style_store import VISION_BATCH_SIZE as _STYLE_VISION_BATCH
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
TENANT_KEY_MIN_LEN = 16          # 租户 Key 最短长度（C2：每租户独立 Key）
TENANT_KEY_MAX_LEN = 256         # 租户 Key 最长长度

# ── 风格词库的张数/体积口径（**单一来源**）──
# 数值来自 `harness/style_store.py` 与 `harness/image_preprocessor.py`：此前「6 张」在四处各写
# 一遍，而且前端写"每张不超过 20MB"、后端处理器真实上限却是 10MB（界面在撒谎）。现在全部由
# 这里引用，并经 `GET /api/style-library` 的 `limits` 下发给前端。
MAX_STYLE_IMAGES = _STYLE_MAX_PHOTOS                  # 单条词条的照片上限
STYLE_VISION_BATCH = _STYLE_VISION_BATCH              # 单次视觉调用张数（超出分批）
STYLE_MAX_PHOTO_MB = _PREPROCESS_MAX_BYTES // (1024 * 1024)   # 单张**真实**上限
STYLE_TOTAL_UPLOAD_MB = 100                           # 单次导入的总量闸门（20 张 × 上限）
STYLE_MAX_ENTRIES_PER_TENANT = _STYLE_MAX_ENTRIES

# ── 全局组件 ──
_provider_registry = get_provider_registry()
_agent_registry = get_agent_registry()
_session_manager = SessionManager()
_broadcaster = Broadcaster()
_workflow_store = JobStore()
_workflow_engine = WorkflowEngine(_agent_registry, _workflow_store, _broadcaster)
_batch_scheduler = BatchScheduler(_workflow_engine, _workflow_store)

# ── API Key 元信息（设置页用） ──
# 凭据槽与可用密钥**不再是静态列表**：统一由 `_api_key_meta()` 从路由表
# （内置 7 条 + 用户自定义服务商）生成，用户新增服务商后其凭据槽立刻出现。
# 见下方 `_api_key_meta()` / `_allowed_secret_keys()`。


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


async def _persist_session_inputs(session) -> list[dict]:
    """把上传原图落盘到 `inputs/`，元数据写进 `task.input_files`（尽力而为）

    参考图是"文+图"里的事实来源；不落盘的话，轻量快照（剔除 base64）之后重启就没了，
    生图会静默退化成纯文生图 —— 那正是模型编造包装文字的老路。
    """
    task = session.get("task", {}) if isinstance(session, dict) else {}
    images = task.get("product_images") or []
    if not images:
        return []
    try:
        from src.storage.image_export import save_inputs
        results = await save_inputs(session_id=str(session.get("session_id") or ""),
                                    tenant_id=str(session.get("tenant_id") or "default"),
                                    images=images)
    except Exception as exc:  # noqa: BLE001 — 落盘失败不影响会话创建
        logger.warning("上传图落盘失败 (session=%s): %s", session.get("session_id"), exc)
        return []
    saved = [item for item in results if item.get("ok")]
    if saved:
        task["input_files"] = saved
    return saved


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
    logger.info("API Key 鉴权", extra={"enabled": auth_enabled(), "tenant_keys": len(get_tenant_keys())})

    # P3: 启动时恢复未完成的 checkpoint
    try:
        from src.core.config import data_root
        from src.storage.checkpoint import load_checkpoint
        checkpoint_dir = data_root() / "checkpoints"   # B2-18：可被 ECOMM_DATA_DIR 重定向
        if checkpoint_dir.exists():
            checkpoints = list(checkpoint_dir.glob("*.json"))
            loaded = 0
            for cp in checkpoints[:MAX_CHECKPOINT_RESTORE]:  # 最多恢复 MAX_CHECKPOINT_RESTORE 个
                try:
                    state = await load_checkpoint(cp.stem)
                    if not state:
                        continue
                    # 非终态（崩溃/重启时还在跑）→ 标记为非正常结束
                    if state.get("status") not in ("completed", "failed"):
                        state["status"] = "failed"
                    # A39：checkpoint 里存不了 CostTracker 实例（JSON），
                    # 按 cost_so_far 重建，否则恢复后的会话不再记账
                    from src.harness.cost_tracker import CostTracker
                    CostTracker.from_session(state)
                    # 终态会话也要进内存：此前只恢复非终态，重启后**历史会话从列表里消失**
                    # （会话列表来自内存，UI 上"我的会话不见了"，实测踩到）
                    _session_manager._sessions[state.get("session_id", cp.stem)] = state
                    loaded += 1
                except Exception:
                    pass
            if loaded:
                logger.info("从 checkpoint 恢复会话", extra={"count": loaded})
    except Exception:
        pass  # 恢复失败不影响启动

    # B1-8：启动期回收磁盘上超期的终态 checkpoint（TTL 小时数与会话一致；
    # 此前只清内存，磁盘累积 1600+ 文件/50MB+ 且每次启动都要 glob 全目录）
    try:
        from src.storage.checkpoint import cleanup_checkpoints
        stats = await cleanup_checkpoints(_session_manager.ttl_hours)
        if stats.get("removed"):
            logger.info("回收超期 checkpoint", extra=stats)
    except Exception as e:  # noqa: BLE001 — 回收失败不影响启动
        logger.warning("回收超期 checkpoint 失败: %s", e)

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
    """管理面保护（审计修复 + C2 方案①）：仅全局 admin Key 或（开发模式下）本机可访问。

    - AuthMiddleware 已把身份写入 request.state.auth_role：
      "admin"（全局 ECOMM_API_KEY）→ 放行
      "tenant"（租户 Key）→ 一律 403（租户不得改系统配置/租户 Key）
    - 未配置任何 Key（开发模式）→ 仅允许本机（127.0.0.1/::1/localhost/testclient）
    """
    role = getattr(request.state, "auth_role", "")
    if role == "admin":
        return
    if role == "tenant":
        raise HTTPException(403, "租户 Key 无权访问管理端点（需全局 ECOMM_API_KEY）")
    if os.getenv("ECOMM_API_KEY"):
        raise HTTPException(403, "需要管理员 API Key")
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
    slots: str = Form(""),
    files: list[UploadFile] = File(...),
    x_tenant_id: str = Header("default", alias="X-Tenant-ID"),
):
    """创建会话，上传商品图片，返回 session_id

    mode: serial | ab_generate | debate | vote
    slots: 只出这些槽位（逗号分隔，如 `main_white,main_selling_point`）——
           用于**最小付费冒烟**（`scripts/real_suite_run.py --slots`）与"只重出某几张"。
           留空 = 按平台档案的完整套图。
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

    # 槽位子集（可选）：只出指定的几张（最小付费冒烟 / 只重出某几张）
    slot_subset = _clean_slot_subset(slots)
    if slot_subset:
        session["task"]["slot_override"] = slot_subset

    # 上传原图落盘到 {输出根}/{租户}/{会话}/inputs/：参考图（"文+图"里的"图"）此前只活在
    # 内存与完整 checkpoint 里，轻量快照会剔除 base64 → 重启后 i2i 会静默退化成纯文生图
    await _persist_session_inputs(session)

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
        # B3-25：失败原因（引擎 _record_error 写入）；此前前端读这个字段但接口没返回
        "error_history": session.get("error_history", []),
        "created_at": str(session.get("created_at", "")),
        "updated_at": str(session.get("updated_at", "")),
    }


# ── 会话事实补充（信息图缺素材时，用户手工把事实补进来） ──
#
# 背景（用户追问"为什么全是白底商品图"之后）：信息图（成分/用法/规格/对比）的文案**只取已确认事实**，
# 包装正面看不到的一律标记 blocked 并提示补素材。但如果用户手上就有这些信息（比如知道食用方法、
# 想跟旧包装对比），此前**没有任何入口能把它喂进真实会话**（只有离线渲染脚本支持）——
# 这里补上：写入 `task.product_facts`，生图员下次出图时按来源自动取用。

_FACT_KEYS = ("ingredients", "features", "selling_points", "target_audience",
              "scene_suggestions", "usage", "compare", "spec", "certifications")
MAX_FACT_ITEMS = 8          # 每个来源最多几条
MAX_FACT_CHARS = 60         # 单条长度上限（信息图条目本来就短）
MAX_SLOT_SUBSET = 12        # 槽位子集上限（一次最多点几张）


def _clean_slot_subset(raw: str) -> list[str]:
    """解析 `slots` 表单字段（逗号分隔）→ 槽位 id 列表；空 = 走平台完整套图

    用途：**最小付费冒烟**（只跑 2 张，约 $0.08）与"只重出某几张"。未登记的槽位不在这里拦，
    由 `normalize_set_plan` 的兜底/上限逻辑处理（并记账）。
    """
    text = str(raw or "").strip()
    if not text:
        return []
    items = [piece.strip() for piece in text.replace("，", ",").split(",")]
    cleaned: list[str] = []
    for item in items:
        if not item:
            continue
        if len(item) > 64 or not re.match(r"^[A-Za-z0-9_\-]+$", item):
            raise HTTPException(400, f"槽位 id 非法：{item[:24]}")
        if item not in cleaned:
            cleaned.append(item)
    if len(cleaned) > MAX_SLOT_SUBSET:
        raise HTTPException(400, f"槽位子集最多 {MAX_SLOT_SUBSET} 个（收到 {len(cleaned)}）")
    return cleaned


def _clean_facts(body: dict) -> dict[str, list[str]]:
    """校验并规范化用户补充的事实（未知键/超限/空值一律 400，不静默丢）"""
    unknown = sorted(set(body) - set(_FACT_KEYS))
    if unknown:
        raise HTTPException(400, f"不支持的事实类型: {', '.join(unknown)}"
                                 f"（可用：{', '.join(_FACT_KEYS)}）")
    if not body:
        raise HTTPException(400, "请求体不能为空")
    facts: dict[str, list[str]] = {}
    for key, value in body.items():
        items = [value] if isinstance(value, str) else value
        if not isinstance(items, list):
            raise HTTPException(400, f"{key} 必须是字符串或字符串数组")
        cleaned: list[str] = []
        for raw in items:
            text = str(raw or "").strip()
            if not text:
                continue
            if len(text) > MAX_FACT_CHARS:
                raise HTTPException(400, f"{key} 的条目过长（>{MAX_FACT_CHARS} 字）：{text[:20]}…")
            cleaned.append(text)
        if len(cleaned) > MAX_FACT_ITEMS:
            raise HTTPException(400, f"{key} 最多 {MAX_FACT_ITEMS} 条")
        if cleaned:
            facts[key] = cleaned
    if not facts:
        raise HTTPException(400, "没有有效内容（空字符串不算补充）")
    return facts


@app.post("/api/sessions/{session_id}/facts")
async def update_session_facts(session_id: str, request: Request,
                               x_tenant_id: str = Header("default", alias="X-Tenant-ID")):
    """补充会话事实（成分/用法/规格/对比…），供信息图排版取用

    Body: `{"usage": ["每日 2 粒，飯後服用"], "ingredients": ["水飛薊提取物"]}`
    —— 只接受槽位目录里声明过的来源；写入 `task.product_facts` 后，**重新邀请生图员**即可
    让被拦下的信息图出图（文案里有事实依据，不再 blocked）。
    """
    _resolve_tenant(x_tenant_id)
    session = _session_manager.get(session_id, tenant_id=x_tenant_id)
    if session is None:
        raise HTTPException(404, "会话不存在")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "请求体必须是 JSON")
    if not isinstance(body, dict):
        raise HTTPException(400, "请求体必须是 JSON 对象")

    facts = _clean_facts(body)
    existing = session.setdefault("task", {}).setdefault("product_facts", {})
    existing.update(facts)
    # 必须 await：`SessionManager.update` 是协程，漏 await 时数据只在内存里，
    # 重启/崩溃后补充的事实就丢了（实测 RuntimeWarning 抓到）
    await _session_manager.update(session_id, session, slim=True)
    logger.info("会话补充事实", extra={"session_id": session_id, "keys": sorted(facts)})

    facts_msg = {
        "id": uuid.uuid4().hex[:12],
        "turn": session.get("turn_count", 0),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "role": "system", "sender": "系统", "action": "respond",
        "content": {
            "facts_updated": sorted(facts),
            "message": "已补充事实：" + "、".join(sorted(facts))
                       + "。重新邀请生图员后会用于信息图排版（不再因缺素材被拦下）。",
        },
    }
    session.setdefault("messages", []).append(facts_msg)
    if _broadcaster:
        try:
            await _broadcaster.broadcast(session_id, facts_msg)
        except Exception as exc:  # noqa: BLE001 — 广播失败不影响保存
            logger.warning("补充事实广播失败: %s", exc)
    return {"session_id": session_id, "product_facts": dict(existing),
            "available": list(_FACT_KEYS)}


@app.post("/api/sessions/{session_id}/style")
async def set_session_style(session_id: str, request: Request,
                            x_tenant_id: str = Header("default", alias="X-Tenant-ID")):
    """切换**本次会话的风格词**（一轮会话一套风格词：换风格要显式操作）

    Body: `{"entry_id": "st_xxx"}`（空串 = 清除锁定，回到"词库里启用的那一套"）。

    为什么需要它：会话一旦定下风格就**锁在产物里**（`style_refs.locked_entry_id`），
    提示词生成/审核/体检/重跑全用它 —— 你在词库里临时切换启用项**不会**影响进行中的会话
    （否则同一套图会前几张用 A、后几张用 B）。要换就点这里：写 `task.style_entry_id`
    并清掉旧锁，然后请协调者重新出提示词。
    """
    _resolve_tenant(x_tenant_id)
    session = _session_manager.get(session_id, tenant_id=x_tenant_id)
    if session is None:
        raise HTTPException(404, "会话不存在")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "请求体必须是 JSON")
    if not isinstance(body, dict):
        raise HTTPException(400, "请求体必须是 JSON 对象")
    entry_id = str(body.get("entry_id") or "").strip()

    name = ""
    if entry_id:
        from src.harness.style_store import StyleStore

        entry = StyleStore().get_entry(entry_id, x_tenant_id)
        if entry is None:
            raise HTTPException(404, "风格词条不存在")
        if str(entry.get("status")) != "ready":
            raise HTTPException(400, f"「{entry.get('name')}」还没分析完成（{entry.get('status')}）")
        name = str(entry.get("name") or entry_id)

    session.setdefault("task", {})["style_entry_id"] = entry_id
    # 清掉旧锁：否则 `session_style_lock()` 仍读到上一次的 id（显式指定优先，但清干净更稳）
    prompts = (session.get("artifacts") or {}).get("prompts")
    if isinstance(prompts, dict):
        refs = prompts.get("style_refs")
        if isinstance(refs, dict):
            refs.pop("locked_entry_id", None)
            refs.pop("active_entry", None)
    await _session_manager.update(session_id, session, slim=True)

    message = (f"已把本次会话的风格切到「{name}」（下一次出提示词生效）"
               if name else "已清除本次会话的风格指定：回到词库里启用那一条")
    note = {
        "id": uuid.uuid4().hex[:12], "turn": session.get("turn_count", 0),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "role": "system", "sender": "系统", "action": "respond",
        "content": {"style_switched": entry_id, "message": message
                    + "。请重新出提示词/出图以应用新风格。"},
    }
    session.setdefault("messages", []).append(note)
    if _broadcaster:
        try:
            await _broadcaster.broadcast(session_id, note)
        except Exception as exc:  # noqa: BLE001 — 广播失败不影响保存
            logger.warning("切换风格广播失败: %s", exc)
    return {"session_id": session_id, "entry_id": entry_id, "name": name, "message": message}


# ── 生成图导出（用户反馈：没法自己设置导出路径） ──

@app.get("/api/sessions/{session_id}/images/{index}/download")
async def download_session_image(session_id: str, index: str,
                                 x_tenant_id: str = Header("default", alias="X-Tenant-ID")):
    """下载单张生成图（生成时已按 {租户}/{会话}/{平台}_{品类}_{序号} 落盘）"""
    try:
        seq = int(index)
    except (TypeError, ValueError):
        raise HTTPException(400, "图片序号必须是整数")
    if seq < 1:
        raise HTTPException(400, "图片序号从 1 开始")
    session = _session_manager.get(session_id, tenant_id=x_tenant_id)
    if session is None:
        raise HTTPException(404, "会话不存在")

    from src.storage.image_export import find_session_file
    path = await asyncio.to_thread(
        find_session_file, session.get("tenant_id", "default"), session_id, seq)
    if path is None:
        raise HTTPException(404, f"会话的第 {seq} 张图尚未落盘（生成完成后即可下载）")
    return FileResponse(path, filename=path.name)


@app.get("/api/sessions/{session_id}/export")
async def export_session_images(session_id: str,
                                x_tenant_id: str = Header("default", alias="X-Tenant-ID")):
    """把该会话已落盘的生成图打包成 ZIP 下载"""
    session = _session_manager.get(session_id, tenant_id=x_tenant_id)
    if session is None:
        raise HTTPException(404, "会话不存在")

    from src.storage.image_export import build_session_zip
    payload = await asyncio.to_thread(
        build_session_zip, session.get("tenant_id", "default"), session_id)
    if not payload:
        raise HTTPException(404, "该会话还没有落盘的图片")
    return Response(
        content=payload,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{session_id}.zip"'},
    )


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


# ── 风格词库（用户 2026-09-18 指定：左栏「记忆库」下面加「风格词库」）──
#
# 契约（前端 `frontend/src/api.js` + `pages/Styles.jsx` 按此实现，改动要两处同步）：
#   GET    /api/style-library                列表（内置 + 我的）+ 统计
#   POST   /api/style-library                multipart 建词条 → **后台**跑风格分析（会花钱）
#   GET    /api/style-library/{id}           详情（全字段 + 缩略图 + 用量 + 已剔除项）
#   PATCH  /api/style-library/{id}           编辑（内置档案只允许启停）
#   DELETE /api/style-library/{id}           删除（连照片目录一起清）
#   POST   /api/style-library/{id}/reanalyze 再分析（可带侧重；**会再花钱**）
#   GET    /api/style-library/preview        零成本预览"这条档案注入后的提示词块"
#
# 安全边界（两条，改这块时不要破坏）：
# 1. 照片落在 `data/style_library/<id>/`，**不进 `data/inputs/`**（会话上传目录）——
#    它们只用于风格分析，永远不作为生图参考图（有静态测试钉住 style_store 不 import 图像链路）；
# 2. 金额只显示"有来源的"：provider 回报 → 价格表（用户可标定）→ 未标定时**不显示金额**，
#    只显示用量（张数/调用次数），绝不拿兜底常量编一个数字出来。

STYLE_HINT_CHARS = 200             # "再分析"的侧重说明长度上限
STYLE_TOTAL_UPLOAD_BYTES = STYLE_TOTAL_UPLOAD_MB * 1024 * 1024


def _style_estimate(images: int) -> dict:
    """花钱前的**用量前置**：张数与调用次数是事实；金额只在标定后才有

    「N 张 = M 次调用」= `ceil(N / 单批上限)`（20 张 = 2 次），别再说"1 次"糊弄过去。
    """
    count = max(0, int(images or 0))
    calls = (count + STYLE_VISION_BATCH - 1) // STYLE_VISION_BATCH if count else 0
    note = ("按 token 计费。价格未标定：本次不显示金额；调用后按实际用量显示。"
            "Mock 模式不产生费用。")
    if calls > 1:
        note = (f"{count} 张超过单次上限（{STYLE_VISION_BATCH} 张）→ 会**分批**做 {calls} 次"
                f"视觉调用（分批 = 多次计费）。" + note)
    return {
        "images": count,
        "calls": calls,
        "batch": STYLE_VISION_BATCH,
        "amount": None,
        "currency": "CNY",
        "source": "未标定",
        "note": note,
    }


async def _read_style_uploads(files, *, limit: int) -> list[bytes]:
    """读一批上传照片：单张与**总量**都要设闸门（20 张各 20MB 峰值可达 400MB）

    Raises: HTTPException(413) 单张/总量超限
    """
    payloads: list[bytes] = []
    total = 0
    for upload in files[:limit]:
        content = await _read_upload_limited(upload, limit=20 * 1024 * 1024,
                                             name=upload.filename or "photo")
        total += len(content)
        if total > STYLE_TOTAL_UPLOAD_BYTES:
            raise HTTPException(413, f"这一批照片总量超过 {STYLE_TOTAL_UPLOAD_MB}MB，"
                                     f"请分批导入（单张上限 {STYLE_MAX_PHOTO_MB}MB）")
        payloads.append(content)
    return payloads


def _style_validate_photo(content: bytes, filename: str, content_type: str):
    """单张照片的校验 + 预处理（与新建词条同一套口径）→ `(处理后的字节, 错误说明)`"""
    from src.harness.image_preprocessor import ImagePreprocessor
    from src.harness.input_pipeline import ImageValidator

    class _FakeImage:
        def __init__(self):
            self.source_path = filename
            self.base64_data = base64.b64encode(content).decode("utf-8")
            self.media_type = content_type or "image/jpeg"
            self.file_size = len(content)

    validation = ImageValidator().validate(_FakeImage())
    if not validation.passed:
        return b"", "；".join(validation.errors)
    processed = ImagePreprocessor(max_pixels=PREPROCESS_MAX_PIXELS,
                                  quality=PREPROCESS_JPEG_QUALITY).process(
        content, source_name=filename)
    if processed.error:
        return b"", processed.error
    return processed.data, ""


def _style_limits() -> dict:
    """前端的全部张数/体积口径（**单一来源**：前端不再各写一遍）"""
    return {
        "max_photos": MAX_STYLE_IMAGES,
        "max_entries_per_tenant": STYLE_MAX_ENTRIES_PER_TENANT,
        "max_photo_mb": STYLE_MAX_PHOTO_MB,
        "max_total_mb": STYLE_TOTAL_UPLOAD_MB,
        "vision_batch": STYLE_VISION_BATCH,
    }


def _style_cost(usage: dict) -> dict:
    """金额来源优先级：provider 回报 → 价格表（可标定）→ **未标定（amount=None）**"""
    usage = usage if isinstance(usage, dict) else {}
    calls = int(usage.get("calls") or 0)
    images = int(usage.get("images") or 0)
    basis = f"{calls} 次视觉调用 · {images} 张图"
    amount = usage.get("cost_usd")
    if isinstance(amount, (int, float)):
        return {"amount": round(float(amount), 6), "currency": "CNY", "source": "供应商回报",
                "updated_at": "", "basis": basis, "stale": False}
    try:
        from src.harness.pricing import estimate as _pricing_estimate

        model = str(usage.get("model") or "")
        info = _pricing_estimate(
            str(usage.get("route") or "deepseek"), model, "vision",
            {"images": images, "tokens_in": int(usage.get("tokens_in") or 0),
             "tokens_out": int(usage.get("tokens_out") or 0)})
        if info and info.get("amount") is not None:
            return {**info, "basis": basis}
    except Exception:  # noqa: BLE001 — 价格表不可用不影响词条
        pass
    return {"amount": None, "currency": "CNY", "source": "未标定", "updated_at": "",
            "basis": f"{basis}（价格未标定）", "stale": False,
            "note": "未标定价格：本次不显示金额（可在设置页「💰 计价」填写）"}


async def _analyze_style_entry(entry_id: str, tenant_id: str, *, hint: str = "") -> None:
    """后台任务：把词条的照片交给「风格档案员」，结果清洗后落库

    失败一律写可读原因（含"可能已计费"），绝不静默留在 `analyzing`。
    """
    from src.harness.style_store import StyleStore

    store = StyleStore()
    entry = store.get_entry(entry_id, tenant_id)
    if entry is None:
        return
    agent = _agent_registry.get("风格档案员")
    if agent is None or agent.provider is None:
        store.set_status(entry_id, "failed",
                         error="「风格档案员」不可用（未注册，或没有可用的视觉模型）——"
                               "请在设置页配置视觉模型后点「重试」")
        return
    photos = store.read_photos(entry)
    if not photos:
        store.set_status(entry_id, "failed", error="照片读取失败，请删掉这条重新导入")
        return
    session = {
        "tenant_id": tenant_id, "artifacts": {}, "turn_count": 0,
        "task": {
            "reference_images": [base64.b64encode(data).decode() for data in photos],
            "style_name": entry.get("name") or "",
            "applies_to": entry.get("applies_to") or {},
            "hint": hint,
        },
    }
    try:
        result = await agent.execute("把这组照片拆成风格档案（设计做法 + 审美判词）", session)
    except Exception as exc:  # noqa: BLE001 — 分析异常不能把词条永远留在 analyzing
        logger.warning("风格分析失败 (entry=%s): %s", entry_id, exc, exc_info=True)
        store.set_status(entry_id, "failed", error=f"分析失败：{type(exc).__name__}: {str(exc)[:200]}")
        return

    usage = dict(result.get("usage") or {}) if isinstance(result, dict) else {}
    usage["cost_usd"] = result.get("cost_usd") if isinstance(result, dict) else None
    if isinstance(result, dict) and result.get("error"):
        store.set_status(entry_id, "failed", error=str(result["error"])[:300], analysis={
            "usage": usage, "at": datetime.now(timezone.utc).isoformat()})
        return
    store.apply_analysis(entry_id, result, usage=usage)


def _style_detail(entry: dict, store) -> dict:
    """词条详情（含全部缩略图与"已剔除"说明）"""
    from src.harness.style_store import entry_to_summary

    adopted = store.adopted_counts()
    summary = entry_to_summary(entry, store=store, adopted=adopted,
                               cost=_style_cost((entry.get("analysis") or {}).get("usage") or {}))
    detail = {
        **summary,
        "background": entry.get("background") or "",
        "composition": entry.get("composition") or "",
        "lighting": entry.get("lighting") or "",
        "materials": entry.get("materials") or "",
        "elements": entry.get("elements") or [],
        "palette_roles": entry.get("palette_roles") or {},
        "whitespace": entry.get("whitespace") or "",
        "forbid": entry.get("forbid") or [],
        "keep_clear_hint": entry.get("keep_clear_hint") or "",
        "taste_verdict": entry.get("taste_verdict") or "",
        "reward_points": entry.get("reward_points") or [],
        "avoid_points": entry.get("avoid_points") or [],
        "name_suggestions": entry.get("name_suggestions") or [],
        # 套图结构（用户 2026-09-19："我给的是一套图片……还有套图的制作习惯"）：
        # 界面上要能核对"参考第N张是哪张、被识别成什么角色"，并逐张改
        "shot_flow": entry.get("shot_flow") or "",
        "shot_roles": entry.get("shot_roles") or [],
        "photos": store.photos_data_uri(entry),
        "removed_brand_text": (entry.get("analysis") or {}).get("removed_brand_text")
        or (result_removed(entry)),
        "analysis": {"at": (entry.get("analysis") or {}).get("at", ""),
                     "fallback_note": (entry.get("analysis") or {}).get("fallback_note", "")},
    }
    return detail


def result_removed(entry: dict) -> list:
    """照片上被剔除的文字（存在 analysis.removed_brand_text 或顶层 removed_brand_text）"""
    return entry.get("removed_brand_text") or []


def _builtin_summaries(store) -> list[dict]:
    """内置档案清单（只读）：**包含被停用的**（否则用户没法在界面上把它启用回来）"""
    from src.harness.style_library import load_library
    from src.harness.style_store import entry_to_summary

    library = load_library(include_user=False)
    adopted = store.adopted_counts()
    items: list[dict] = []
    for entry in library["entries"] + (library.get("disabled") or []):
        summary = entry_to_summary(entry, store=store, adopted=adopted)
        summary["source"] = "builtin"
        summary["enabled"] = bool(entry.get("enabled", True))
        items.append(summary)
    return items


@app.get("/api/style-library")
async def style_library_list(x_tenant_id: str = Header("default", alias="X-Tenant-ID")):
    """风格词库列表：内置档案（只读）+ 我的词条（可编辑）+ 统计"""
    tenant = _resolve_tenant(x_tenant_id)
    from src.harness.style_library import library_stats
    from src.harness.style_store import StyleStore, entry_to_summary

    store = StyleStore()
    adopted = store.adopted_counts()
    mine = [entry_to_summary(item, store=store, adopted=adopted,
                             cost=_style_cost((item.get("analysis") or {}).get("usage") or {}))
            for item in store.list_entries(tenant.tenant_id)]
    builtin = _builtin_summaries(store)
    stats = library_stats()
    return {
        "builtin": builtin,
        "mine": mine,
        "stats": {
            "builtin": len(builtin),
            "mine": len(mine),
            "enabled": stats["enabled"],
            "max_entries": stats["max_entries"],
            "anchors": stats["anchors"],
            "dropped": stats["dropped"],
            "limits": _style_limits(),
        },
    }


async def _style_photo_inputs(files, *, limit: int) -> tuple[list[bytes], list[str]]:
    """上传 → `(校验通过的照片, 可读错误清单)`（新建与追加照片共用同一套口径）"""
    payloads = await _read_style_uploads(files, limit=limit)
    photos: list[bytes] = []
    errors: list[str] = []
    for upload, content in zip(files[:limit], payloads):
        name = upload.filename or "photo"
        data, error = _style_validate_photo(content, name, upload.content_type or "image/jpeg")
        if error:
            errors.append(f"[{name}] {error}")
            continue
        photos.append(data)
    return photos, errors


@app.post("/api/style-library")
async def style_library_create(
    name: str = Form(""),
    applies_to: str = Form(""),
    as_anchor: str = Form("true"),
    files: list[UploadFile] = File(default=[]),
    x_tenant_id: str = Header("default", alias="X-Tenant-ID"),
):
    """新建词条：落照片 → 建 `analyzing` 词条 → **后台**跑风格分析（立即返回）

    照片只用于风格分析，**不是生图参考图**（见本区块顶部说明）。
    """
    tenant = _resolve_tenant(x_tenant_id)
    from src.harness.style_store import StyleStore, entry_to_summary

    if not files:
        raise HTTPException(400, "请至少导入 1 张照片")
    if len(files) > MAX_STYLE_IMAGES:
        raise HTTPException(400, f"最多 {MAX_STYLE_IMAGES} 张照片")

    photos, errors = await _style_photo_inputs(files, limit=MAX_STYLE_IMAGES)
    if not photos:
        raise HTTPException(400, "照片验证失败：" + "；".join(errors[:3] or ["没有可用图片"]))

    parsed_applies: dict = {}
    if applies_to.strip():
        try:
            loaded = json.loads(applies_to)
            parsed_applies = loaded if isinstance(loaded, dict) else {}
        except json.JSONDecodeError:
            raise HTTPException(400, "applies_to 必须是 JSON 对象")

    store = StyleStore()
    try:
        entry = store.create_entry(tenant_id=tenant.tenant_id, name=name,
                                   applies_to=parsed_applies,
                                   as_anchor=str(as_anchor).strip().lower() not in ("false", "0", "no"),
                                   photos=photos)
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    asyncio.create_task(_analyze_style_entry(entry["id"], tenant.tenant_id))
    return {
        "entry": entry_to_summary(entry, store=store,
                                  cost=_style_cost({"calls": 0, "images": len(photos)})),
        "estimate": _style_estimate(len(photos)),
        "message": f"已建词条「{entry['name']}」，正在分析这 {len(photos)} 张照片的风格…",
    }


@app.post("/api/style-library/{entry_id}/photos")
async def style_library_add_photos(
    entry_id: str,
    files: list[UploadFile] = File(default=[]),
    x_tenant_id: str = Header("default", alias="X-Tenant-ID"),
):
    """给已有词条**追加照片**（用户 2026-09-19：原来只导入 6 张，补齐整套要重建词条）

    只 append：**序号不变**，因此已识别出的逐张角色仍然对得上（新照片显示"（未识别）"）。
    追加后不会自动重新分析（花钱的事由用户点「再分析」）。整体仍受 `MAX_PHOTOS` 上限约束。
    """
    tenant = _resolve_tenant(x_tenant_id)
    from src.harness.style_store import MAX_PHOTOS, StyleStore, entry_to_summary

    if not files:
        raise HTTPException(400, "请至少选择 1 张照片")
    store = StyleStore()
    entry = store.get_entry(entry_id, tenant.tenant_id)
    if entry is None:
        raise HTTPException(404, "词条不存在")
    room = MAX_PHOTOS - len(entry.get("photos") or [])
    if room <= 0:
        raise HTTPException(400, f"这条词条已经有 {MAX_PHOTOS} 张照片（上限），"
                                 "请先删掉不需要的再追加")
    if len(files) > room:
        raise HTTPException(400, f"还能追加 {room} 张（单条上限 {MAX_PHOTOS} 张）")
    photos, errors = await _style_photo_inputs(files, limit=room)
    if not photos:
        raise HTTPException(400, "照片验证失败：" + "；".join(errors[:3] or ["没有可用图片"]))
    try:
        updated = store.add_photos(entry_id, photos, tenant_id=tenant.tenant_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    total = len(updated.get("photos") or [])
    analyzed = int(((updated.get("analysis") or {}).get("usage") or {}).get("images") or 0)
    return {
        "entry": entry_to_summary(updated, store=store),
        "errors": errors,
        "message": f"已追加 {len(photos)} 张（现共 {total} 张）。"
                   + (f"当前档案是按前 {analyzed} 张分析的，点「再分析」会让它跟上新照片。"
                      if analyzed and analyzed < total else ""),
    }


@app.delete("/api/style-library/{entry_id}/photos/{index}")
async def style_library_remove_photo(
    entry_id: str, index: int,
    x_tenant_id: str = Header("default", alias="X-Tenant-ID"),
):
    """移除第 N 张照片（1 起）。

    ⚠️ 文件会重排（photo_1..N），**序号随之变化** → 一并清空已识别的逐张角色
    （`shot_roles`），共同美术保留。绝不留下"第2张其实是原来第3张"的错误映射。
    """
    tenant = _resolve_tenant(x_tenant_id)
    from src.harness.style_store import StyleStore, entry_to_summary

    store = StyleStore()
    try:
        result = store.remove_photo(entry_id, index, tenant_id=tenant.tenant_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    updated = result["entry"]
    message = f"已移除第 {index} 张（剩 {len(updated.get('photos') or [])} 张）"
    if result.get("cleared_roles"):
        message += "；照片序号已重排，逐张角色已清空，请点「再分析」重建"
    return {"entry": entry_to_summary(updated, store=store),
            "cleared_roles": bool(result.get("cleared_roles")), "message": message}


@app.get("/api/style-library/preview")
async def style_library_preview(platform: str = "taobao", category: str = "",
                                slot: str = "", entry_id: str = "",
                                x_tenant_id: str = Header("default", alias="X-Tenant-ID")):
    """零成本预览：这条档案注入后**实际会写进提示词**的那一段（不调用任何模型）"""
    tenant = _resolve_tenant(x_tenant_id)
    from src.core.platforms import platform_slot_table, slot_kind
    from src.harness.style_library import (
        background_policy, render_anchor_block, render_slot_block, select_by_slot,
        usage_snapshot,
    )
    from src.harness.style_store import StyleStore

    store = StyleStore()
    library = None
    if entry_id:
        entry = store.get_entry(entry_id, tenant.tenant_id)
        if entry is None:
            raise HTTPException(404, "词条不存在")
        library = {"entries": [entry], "anchors": [], "dropped": [], "defaults": {},
                   "exists": True}

    rows = platform_slot_table(platform)
    subset = [piece.strip() for piece in slot.replace("，", ",").split(",") if piece.strip()]
    notes: list[str] = []
    if subset:
        matched = [row for row in rows if row["slot_id"] in subset]
        missing = [item for item in subset if item not in {row["slot_id"] for row in rows}]
        if missing:
            # 不静默降级成"预览全部"：说清哪些槽位不在该平台的档案里
            notes.append(f"这些槽位不在 {platform} 的平台档案里，已忽略：{'、'.join(missing)}")
        rows = matched or rows
    selection = select_by_slot(rows, analysis={"category": category} if category else {},
                               platform=platform, library=library,
                               tenant_id=tenant.tenant_id)
    block = render_slot_block(selection)
    anchors_block = render_anchor_block(selection.get("anchors"))
    return {
        "platform": platform,
        "policy": background_policy(platform),
        "slots": {slot_id: [{"id": item["id"], "name": item["name"]} for item in picked]
                  for slot_id, picked in selection["slots"].items()},
        "block": "\n\n".join(part for part in (block, anchors_block) if part),
        "notes": notes + (selection.get("notes") or []),
        "summary": usage_snapshot(selection),
        "message": selection.get("selection_note") or "",
        "slot_kinds": {row["slot_id"]: slot_kind(row["slot_id"]) for row in rows},
    }


@app.get("/api/style-library/{entry_id}")
async def style_library_get(entry_id: str,
                            x_tenant_id: str = Header("default", alias="X-Tenant-ID")):
    """词条详情"""
    tenant = _resolve_tenant(x_tenant_id)
    from src.harness.style_library import load_library
    from src.harness.style_store import StyleStore

    store = StyleStore()
    entry = store.get_entry(entry_id, tenant.tenant_id)
    if entry is not None:
        return {"entry": _style_detail(entry, store)}
    # 内置档案（只读）
    for item in load_library(include_user=False)["entries"]:
        if str(item.get("id")) == entry_id:
            detail = _style_detail(item, store)
            detail["source"] = "builtin"
            return {"entry": detail}
    raise HTTPException(404, "词条不存在")


@app.patch("/api/style-library/{entry_id}")
async def style_library_update(entry_id: str, request: Request,
                               x_tenant_id: str = Header("default", alias="X-Tenant-ID")):
    """编辑词条（**内置档案只允许启停**）；改动会再过一遍事实中立清洗并如实报数"""
    tenant = _resolve_tenant(x_tenant_id)
    from src.harness.style_library import load_library, similar_entries
    from src.harness.style_store import StyleStore

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "请求体必须是 JSON")
    if not isinstance(body, dict) or not body:
        raise HTTPException(400, "没有要更新的字段")

    store = StyleStore()
    entry = store.get_entry(entry_id, tenant.tenant_id)
    if entry is None:
        from src.harness.style_library import builtin_ids
        if entry_id in builtin_ids():
            if set(body) != {"enabled"}:
                raise HTTPException(403, "内置档案只能启停，不能编辑内容")
            store.set_builtin_enabled(entry_id, bool(body["enabled"]))
            return {"entry": {"id": entry_id, "enabled": bool(body["enabled"]), "source": "builtin"},
                    "removed": [], "similar": [],
                    "message": f"内置档案已{'启用' if body['enabled'] else '停用'}"}
        raise HTTPException(404, "词条不存在")

    editable = {"name", "summary", "style_words", "background", "composition", "lighting",
                "materials", "elements", "palette_roles", "whitespace", "forbid",
                "taste_verdict", "reward_points", "avoid_points", "applies_to", "as_anchor",
                "enabled", "keep_clear_hint",
                # 套图结构（逐张角色可在界面上改：识别错了要能修）
                "shot_flow", "shot_roles"}
    unknown = sorted(set(body) - editable)
    if unknown:
        raise HTTPException(400, f"不支持的字段: {', '.join(unknown)}")

    from src.harness.style_library import sanitize_entry

    cleaned, removed = sanitize_entry({**entry, **body})
    updates = {key: cleaned[key] for key in body if key in cleaned}
    if "name" in body and not cleaned.get("name"):
        raise HTTPException(400, "名称不能为空（也不能含有商标符号或色值）")
    # 启用/停用走 radio 路径（**一轮会话一套风格词**：启用一套 → 其他用户词条自动停用）；
    # 其他字段照旧走 update_entry。用户 2026-09-20：词库里不能两套同时生效（单位是"一套"）。
    auto_disabled: list[dict] = []
    try:
        enabled_value = updates.pop("enabled", None)
        updated = store.update_entry(entry_id, updates, tenant_id=tenant.tenant_id) if updates else None
        if enabled_value is not None:
            switched = store.set_entry_enabled(entry_id, bool(enabled_value),
                                               tenant_id=tenant.tenant_id)
            updated = switched["entry"]
            auto_disabled = switched["auto_disabled"]
        if updated is None:
            updated = store.get_entry(entry_id, tenant.tenant_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    library = load_library(tenant.tenant_id)
    similar = similar_entries(updated, library["entries"], threshold=0.8)
    message = "已保存"
    if removed:
        message += f"；已剔除 {len(removed)} 处品牌/成分/认证/色值内容（不进提示词）"
    if auto_disabled:
        message = (f"已启用「{updated.get('name') or entry_id}」；已自动停用 " +
                   "、".join(f"「{item.get('name') or item.get('id')}」" for item in auto_disabled) +
                   "（一轮会话只用一套风格词）")
    if similar:
        message += "；与已有档案相似：" + "、".join(
            f"「{item['name']}」({item['similarity']})" for item in similar[:2])
    return {"entry": _style_detail(store.get_entry(entry_id, tenant.tenant_id), store),
            "removed": removed, "similar": similar, "auto_disabled": auto_disabled,
            "message": message}


@app.delete("/api/style-library/{entry_id}")
async def style_library_delete(entry_id: str,
                               x_tenant_id: str = Header("default", alias="X-Tenant-ID")):
    """删除词条（连照片目录一起清）；被会话采用过的会如实回报次数"""
    tenant = _resolve_tenant(x_tenant_id)
    from src.harness.style_store import StyleStore

    store = StyleStore()
    try:
        removed = store.delete_entry(entry_id, tenant_id=tenant.tenant_id)
    except ValueError:
        raise HTTPException(404, "词条不存在")
    adopted = store.adopted_counts().get(entry_id, 0)
    return {"deleted": entry_id, "adopted": adopted,
            "message": f"已删除「{removed.get('name') or entry_id}」"
                       + (f"（曾被 {adopted} 次会话采用，已生成的图不受影响）" if adopted else "")}


@app.post("/api/style-library/{entry_id}/reanalyze")
async def style_library_reanalyze(entry_id: str, request: Request = None,
                                  x_tenant_id: str = Header("default", alias="X-Tenant-ID")):
    """再分析一次（可带侧重说明）——**会再花一次视觉调用**，前端要先确认"""
    tenant = _resolve_tenant(x_tenant_id)
    from src.harness.style_store import StyleStore

    hint = ""
    if request is not None:
        try:
            body = await request.json()
            if isinstance(body, dict):
                hint = str(body.get("hint") or "").strip()[:STYLE_HINT_CHARS]
        except Exception:
            hint = ""

    store = StyleStore()
    entry = store.get_entry(entry_id, tenant.tenant_id)
    if entry is None:
        raise HTTPException(404, "词条不存在（内置档案不能重跑分析）")
    if not store.read_photos(entry):
        raise HTTPException(400, "这条词条没有照片可分析")
    store.set_status(entry_id, "analyzing", error="")
    asyncio.create_task(_analyze_style_entry(entry_id, tenant.tenant_id, hint=hint))
    return {"entry": _style_detail(store.get_entry(entry_id, tenant.tenant_id), store),
            "estimate": _style_estimate(len(entry.get("photos") or [])),
            "message": "已重新开始分析" + (f"（侧重：{hint}）" if hint else "")}


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
#
# 服务商目录（路由 / 凭据槽 / 模型目录 / 可用密钥）统一来自 `src/providers/routes.py`：
# 内置 7 条 + 用户在设置页添加的自定义服务商。此前这些全是本文件里的静态列表，
# 用户无法新增服务商（用户反馈："我无法自己添加模型服务商，局限性太大了"）。


def _route_specs() -> dict:
    """当前生效的路由表（内置 + 自定义）"""
    from src.providers import all_route_specs
    return all_route_specs()


def _model_catalog() -> dict[str, list[str]]:
    """各路由的模型目录（含自定义服务商；供设置页下拉与模型映射建议）"""
    catalog = {route: list(spec.models) for route, spec in _route_specs().items()}
    catalog["mock"] = ["mock"]
    return catalog


def _api_key_meta() -> list[dict]:
    """设置页的凭据槽（内置路由的多凭据 + 每个自定义服务商一条）"""
    out: list[dict] = []
    for route, spec in _route_specs().items():
        for cred in spec.credentials:
            out.append({
                "name": cred.get("name", spec.label),
                "env": cred["env"],
                "provider": route,
                "capabilities": spec.capability_text(),
            })
    return out


def _allowed_secret_keys() -> set[str]:
    """允许经设置页保存的密钥变量名（动态：内置 + 自定义服务商 + MOCK_MODE）"""
    return {m["env"] for m in _api_key_meta()} | {"MOCK_MODE"}


def _route_meta(spec) -> dict:
    """单条路由的元信息（与 `_provider_routes_payload()` 条目同构，供写端点复用）"""
    return next(r for r in _provider_routes_payload() if r["route"] == spec.route)


def _provider_routes_payload() -> list[dict]:
    """Provider 路由配置（端点生效值/是否自定义/官方默认/自定义模型），供设置页渲染"""
    from src.core.config import base_url_source, load_provider_config, resolve_base_url
    overrides = load_provider_config()
    catalog = _model_catalog()
    out = []
    for route, spec in _route_specs().items():
        override = overrides.get(route, {})
        default_url = spec.default_base_url
        effective = resolve_base_url(route, default_url,
                                     env_names=spec.base_url_envs or None) if default_url else ""
        out.append({
            "route": route,
            "label": spec.label,
            "capabilities": spec.capability_text(),
            "default_base_url": default_url,
            "base_url_supported": spec.base_url_supported,
            "credential_hint": spec.credential_hint,
            # 自定义服务商附加字段（前端据此显示「自定义」标记、删除按钮与协议名）
            "custom": spec.custom,
            "kind": spec.kind,
            "api_key_env": spec.api_key_env,
            "deprecated": spec.deprecated,
            "deprecated_hint": spec.deprecated_hint,
            "effective_base_url": effective,
            "base_url_custom": bool(default_url and effective != default_url),
            "base_url_source": base_url_source(route, env_names=spec.base_url_envs or None)
                                if default_url else "",
            "models": list(override.get("models", [])),
            "official_models": list(catalog.get(route, [])),
            # 按能力分组的官方模型（前端可按能力给建议，后端测试连接按能力选 model）
            "models_by_capability": {c: list(v) for c, v in spec.models_by_capability.items()},
        })
    return out


def _tenant_keys_payload() -> list[dict]:
    """租户 Key 状态列表（仅 configured 布尔 + 来源，绝不返回密钥内容）"""
    keys = get_tenant_keys()
    env_keys = env_tenant_keys()
    out = []
    for tid in get_tenant_registry().list_ids():
        ctx = get_tenant_registry().get(tid)
        out.append({
            "tenant_id": tid,
            "name": ctx.name if ctx else tid,
            "tier": ctx.tier if ctx else "free",
            "configured": bool(keys.get(tid)),
            "source": "env" if tid in env_keys else "file",  # env 供给的 Key 禁止在设置页修改
        })
    return out


def _settings_payload() -> dict:
    """汇总当前设置状态（含脱敏密钥、租户 Key、Provider、Agent、模型配置）"""
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
    available_routes = {p["name"] for p in _provider_registry.list_available()}
    from src.core.config import secret_key_source
    api_keys_payload = []
    for m in _api_key_meta():
        configured = bool(os.getenv(m["env"]))
        api_keys_payload.append({
            **m,
            "configured": configured,
            # 审计修复：不再返回任何密钥片段（此前泄漏前 4 后 4 共 8 字符）
            # P7（设置页重做）：来源（env 供给 / secrets.yaml 持久化）与注册表可用性，
            # 供 provider 行的状态点与只读锁定使用
            "source": secret_key_source(m["env"]) if configured else "",
            "available": m["provider"] in available_routes,
        })
    # 自定义模型并入目录（第三方 coding plan / 代理的模型 id 进建议列表）
    from src.core.config import load_provider_config
    catalog = {k: list(v) for k, v in _model_catalog().items()}
    for route, cfg in load_provider_config().items():
        custom = [m for m in cfg.get("models", []) if m not in catalog.get(route, [])]
        if custom:
            catalog[route] = catalog.get(route, []) + custom
    return {
        "mock_mode": is_mock_mode(),
        "api_keys": api_keys_payload,
        "tenant_keys": _tenant_keys_payload(),
        "provider_routes": _provider_routes_payload(),
        "capabilities": _capabilities_payload(),
        "output": _output_payload(),
        "providers": _provider_registry.list_available(),
        "agents": agents,
        "models_config": load_models_config(),
        "model_catalog": catalog,
        # A41：覆盖键与该 Agent 的 requires 不匹配（如给"审查员"写 text 覆盖，
        # 但它 requires=vision）→ 覆盖永远不生效。前端据此提示，避免"改了没反应"。
        "agent_overrides_issues": agent_override_issues(agents),
        # B1：会话策略（连续失败即停 / 最大轮次 / TTL），设置页可改
        "chat": chat_settings(),
        # 生图质量策略（文字策略/参考图模式/水印/体检阈值），设置页可改
        "image": image_settings(),
        "cors_origins": _origins,
    }


def agent_override_issues(agents: list[dict]) -> list[dict]:
    """找出 capability 键不在该 Agent `requires` 里的覆盖配置（A41）"""
    required = {a.get("name"): set(a.get("requires") or []) for a in agents if a.get("name")}
    issues: list[dict] = []
    overrides = (load_models_config().get("agent_overrides") or {})
    if not isinstance(overrides, dict):
        return issues
    for agent_name, cap_map in overrides.items():
        if not isinstance(cap_map, dict):
            continue
        needs = required.get(agent_name)
        for capability in cap_map:
            if needs is None:
                issues.append({"agent": agent_name, "capability": capability,
                               "requires": [], "reason": "该 Agent 不存在（配置已失效）"})
            elif capability not in needs:
                issues.append({"agent": agent_name, "capability": capability,
                               "requires": sorted(needs),
                               "reason": f"该 Agent 需要 {'/'.join(sorted(needs)) or '（无）'}，"
                                         f"“{capability}”覆盖不会生效"})
    return issues


# 租户可见的 Agent 字段白名单（第三轮审计 B0-1）
# prompt = 系统提示词（运营资产），config_file/params 之外的运营配置一律不下发
_TENANT_SAFE_AGENT_FIELDS = (
    "name", "description", "version", "requires",
    "timeout_ms", "retry", "params", "resolved_model",
)


def _output_payload() -> dict:
    """生成图输出目录状态（用户反馈：没法自己设置导出路径）

    含生效路径、来源（env/file/default）、是否被环境变量锁定、以及一次真实的
    可写探针结果——路径配错（盘符不存在/无权限/只读挂载）在设置页要能立刻看见。
    """
    from src.core.config import output_status
    try:
        return output_status()
    except Exception as e:  # noqa: BLE001 — 状态查询失败不能让设置页 500
        logger.warning("输出目录状态查询失败: %s", e)
        return {"dir": "", "effective_dir": "", "source": "default",
                "env_locked": False, "writable": False, "error": f"{type(e).__name__}: {e}"}


def _capabilities_payload() -> list[dict]:
    """能力 → 实际生效的 provider / model / 是否 Mock（第三轮审计 B3-26）

    `available` 只表示"某个环境变量非空"，用户看不到"我配了 DeepSeek，但生图其实
    回落到 Mock 占位图"这种落差（跑完才发现出的不是真图）。这里把三个能力的真实
    解析结果摊开，供设置页与新建任务表单做警示。
    """
    out = []
    for cap in ("text", "vision", "image"):
        provider_name, model, is_mock = "", "", True
        try:
            provider, model = _provider_registry.resolve([cap], "")
            provider_name = getattr(provider, "name", "") or ""
            model = model or ""
            is_mock = provider_name in ("", "mock")
        except Exception as e:  # noqa: BLE001 — 解析失败降级为"回落 Mock"，不能让设置页 500
            logger.warning("能力解析失败 (capability=%s): %s", cap, e)
        out.append({
            "capability": cap,
            "provider": provider_name,
            "model": model,
            "is_mock": is_mock,
        })
    return out


def _tenant_settings_payload() -> dict:
    """租户可见的设置子集（第三轮审计 B0-1：补上管理面守卫缺口）

    `GET /api/settings` 此前无守卫：持租户 Key 即可读全量设置——
    `provider_routes[].effective_base_url` 可能内嵌凭据
    （`https://user:SECRET@internal-gw/v1`）、`tenant_keys` 是全租户清单、
    `agents[].prompt` 是系统提示词、`model_catalog` 含自定义端点模型 id。
    这里只下发租户界面真正需要的最小集合，并显式标记 `redacted`。
    """
    return {
        "redacted": True,
        "mock_mode": is_mock_mode(),
        "providers": _provider_registry.list_available(),
        "capabilities": _capabilities_payload(),
        "agents": [
            {k: v for k, v in a.items() if k in _TENANT_SAFE_AGENT_FIELDS}
            for a in _settings_payload()["agents"]
        ],
        # 以下为管理面配置：租户一律拿不到（密钥状态、租户清单、端点、模型映射）
        "api_keys": [],
        "tenant_keys": [],
        "provider_routes": [],
        "models_config": {},
        "model_catalog": {k: list(v) for k, v in _model_catalog().items()},
        "cors_origins": [],
    }


@app.get("/api/settings")
async def get_settings(request: Request):
    """获取当前系统设置。

    第三轮审计 B0-1：补管理面守卫。admin / 本机 dev → 全量（密钥脱敏）；
    租户 Key → 脱敏子集（保留 Agent 列表供 Agents 页渲染）。
    """
    if getattr(request.state, "auth_role", "") == "tenant":
        return _tenant_settings_payload()
    _require_admin_access(request)
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

    unknown = set(api_keys) - _allowed_secret_keys()
    if unknown:
        raise HTTPException(400, f"不支持的密钥变量: {', '.join(sorted(unknown))}")

    # 环境变量供给的密钥优先级最高：经设置页保存不会生效（P7：与其静默失效，
    # 不如显式拒绝——沿用 C2 租户 Key 的既有约定）
    from src.core.config import secret_key_source
    env_locked = sorted(k for k in api_keys if secret_key_source(k) == "env")
    if env_locked:
        raise HTTPException(
            403,
            f"以下密钥由环境变量提供，设置页修改不会生效（请改环境变量后重启）："
            f"{', '.join(env_locked)}",
        )

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


@app.get("/api/settings/tenant-keys")
async def get_tenant_keys_status(request: Request):
    """租户 Key 状态列表（仅 admin；不返回密钥内容）"""
    _require_admin_access(request)
    return {"tenant_keys": _tenant_keys_payload()}


@app.post("/api/settings/tenant-keys")
async def update_tenant_key(request: Request):
    """创建/轮换/删除租户 Key（C2 方案①，仅 admin；写入 config/tenant_keys.yaml）

    Body: {"tenant_id": "t1", "api_key": "<新Key>"}；api_key 为空表示删除。
    租户须已在租户注册中心（ECOMM_TENANTS）存在，否则 403。
    """
    _require_admin_access(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "请求体必须是 JSON")

    tenant_id = str(body.get("tenant_id", "")).strip()
    api_key = str(body.get("api_key", "")).strip()
    if not tenant_id:
        raise HTTPException(400, "tenant_id 必填")
    if tenant_id not in get_tenant_registry().list_ids():
        raise HTTPException(403, f"未知租户: '{tenant_id}'（请先配置 ECOMM_TENANTS）")
    if tenant_id in env_tenant_keys():
        raise HTTPException(
            403,
            f"租户 '{tenant_id}' 的 Key 由 ECOMM_TENANT_KEYS 环境变量提供（优先级最高）："
            "请在环境变量中修改后重启；设置页仅管理 config/tenant_keys.yaml 持久化的 Key")
    if api_key and not (TENANT_KEY_MIN_LEN <= len(api_key) <= TENANT_KEY_MAX_LEN):
        raise HTTPException(
            400, f"租户 Key 长度须在 {TENANT_KEY_MIN_LEN}-{TENANT_KEY_MAX_LEN} 字符之间")
    if api_key and not all(0x21 <= ord(ch) <= 0x7E for ch in api_key):
        # 审计修复：HTTP 头只能承载可打印 ASCII——非 ASCII Key 既永远无法认证，
        # 又会让 hmac.compare_digest 抛 TypeError（认证链 500），必须挡在入口
        raise HTTPException(400, "租户 Key 只能包含可打印 ASCII 字符（不含空格）")

    from src.core.config import save_tenant_keys_file
    save_tenant_keys_file({tenant_id: api_key})
    reload_tenant_keys()
    logger.info("租户 Key 已更新", extra={"tenant_id": tenant_id, "configured": bool(api_key)})
    return {
        "tenant_id": tenant_id,
        "configured": bool(api_key),
        "tenant_keys": _tenant_keys_payload(),
    }


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


# ── 生图质量策略（用户要求：文字策略/参考图模式/水印/信息图排版可设置） ──

_IMAGE_SETTING_FIELDS = ("size", "variants", "slot_candidates", "text_strategy",
                         "reference_mode", "max_references", "watermark", "quality", "platforms",
                         "typography")


@app.get("/api/platforms")
async def list_platforms_api():
    """平台档案（config/platforms.yaml）—— 前端选择器与套图槽位的单一事实来源

    平台清单此前散落在 9 处（提示词 YAML 的静态风格、6 个 workflow 模板的 options、
    前端硬编码 6 项…）。现在新增平台只改配置：前端从本接口渲染，提示词按档案渲染风格。
    """
    from src.core.platforms import list_platforms
    return {"platforms": list_platforms()}


@app.post("/api/settings/image")
async def update_image_settings(request: Request):
    """更新生图质量策略（写入 config/image.yaml 并立即生效）

    Body（部分更新）: {"text_strategy": "preserve|blur|none", "reference_mode": "auto|off",
                       "max_references": 4, "watermark": false, "slot_candidates": 1,
                       "size": "2K", "quality": {...}, "platforms": {"taobao": {"slots": [...]}}}
    """
    _require_admin_access(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "请求体必须是 JSON")
    if not isinstance(body, dict):
        raise HTTPException(400, "请求体必须是 JSON 对象")
    unknown = sorted(set(body) - set(_IMAGE_SETTING_FIELDS))
    if unknown:
        raise HTTPException(400, f"不支持的字段: {', '.join(unknown)}")
    if not body:
        raise HTTPException(400, "请求体不能为空")
    try:
        save_image_settings(body)
    except ValueError as e:
        raise HTTPException(400, str(e))
    logger.info("生图质量策略已更新", extra={"fields": sorted(body)})
    return _settings_payload()


@app.post("/api/settings/image/probe")
async def probe_image_generation(request: Request):
    """试生成一张（用当前策略 + 参考图），用于在设置页直接验证效果

    为什么需要：生图策略（文字/参考图/水印）改完，只有真正出一次图才知道对不对；
    跑整个会话要花一整轮的钱。这里只出一张，并把**本地体检**结果一并返回。

    Body: {"route": "ark", "model": "", "reference_image": "<base64 或 data URI>",
           "prompt": "...", "slot_id": "main_white", "text_strategy": "..."}  —— 均可选
    """
    _require_admin_access(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        raise HTTPException(400, "请求体必须是 JSON 对象")

    from src.core.config import image_settings, resolve_image_size
    from src.harness.image_prompt import compose_prompt, effective_strategy
    from src.harness.image_quality import inspect_image
    from src.harness.reference_images import collect_references

    options = image_settings()
    route = str(body.get("route") or "ark").strip()
    spec = _provider_registry.spec(route)
    if spec is None:
        raise HTTPException(404, f"未登记的路由: {route}")
    provider = _provider_registry.get_image(route)
    if provider is None:
        raise HTTPException(404, f"路由 {route} 的 Image Provider 未就绪（该路由不支持生图？）")
    if getattr(provider, "name", "") == "mock":
        raise HTTPException(400, "当前为 Mock（未配置真实凭据）：试生成不会产生真实图片")
    # 凭据预检：缺 Key 时**不要**发起真实调用（会得到 httpx `Illegal header value b'Bearer '`
    # 这种看不懂的错，用户实测踩过）——直接给出可执行的配置指引
    ok_creds, cred_detail, _suggestion = _credential_precheck(spec)
    if not ok_creds:
        raise HTTPException(400, cred_detail)

    strategy = str(body.get("text_strategy") or options["text_strategy"])
    strategy = strategy if strategy in ("preserve", "blur", "none") else options["text_strategy"]
    references, ref_notes = collect_references(
        [body.get("reference_image")] if body.get("reference_image") else [],
        limit=int(options["max_references"]))
    effective = effective_strategy(strategy, has_references=bool(references))
    prompt = compose_prompt(
        prompt=str(body.get("prompt") or "商品本体正面平视，纯白背景 #FFFFFF，"
                                        "柔和均匀商业摄影布光，主体居中占比 85% 以上"),
        background=str(body.get("background") or "#FFFFFF"),
        aspect=str(body.get("aspect") or "1:1"),
        text_strategy=strategy, has_references=bool(references))
    model = str(body.get("model") or "").strip()
    if not model and spec is not None:
        model = _test_model_for(route, "image", spec)

    started = time.perf_counter()
    try:
        raw = await asyncio.wait_for(
            provider.generate(prompt=prompt,
                              size=resolve_image_size(getattr(provider, "default_size", "")),
                              model=model,
                              reference_images=references or None,
                              options={"watermark": bool(options["watermark"])},
                              ),
            timeout=PROVIDER_TEST_IMAGE_TIMEOUT_S)
    except asyncio.TimeoutError:
        raise HTTPException(504, f"试生成超时（>{PROVIDER_TEST_IMAGE_TIMEOUT_S:.0f}s）")
    latency_ms = int((time.perf_counter() - started) * 1000)
    if isinstance(raw, dict) and raw.get("error"):
        raise HTTPException(502, str(raw["error"])[:400])

    payload = {
        "ok": True,
        "route": route,
        "model": raw.get("model_used") or model,
        "latency_ms": latency_ms,
        "prompt": prompt,
        "text_strategy": effective,
        "reference_count": int(raw.get("reference_count") or len(references)),
        "ignored_params": raw.get("ignored_params") or [],
        "request_params": raw.get("request_params") or {},
        "reference_notes": ref_notes,
        "image_url": raw.get("image_url", ""),
    }
    # 本地体检（零成本）：把"背景是否纯白/有没有水印"直接量给用户看
    data = b""
    if raw.get("base64_data"):
        try:
            data = base64.b64decode(raw["base64_data"])
        except Exception:  # noqa: BLE001
            data = b""
    if not data and str(raw.get("image_url") or "").startswith("data:"):
        try:
            data = base64.b64decode(str(raw["image_url"]).partition(",")[2])
        except Exception:  # noqa: BLE001
            data = b""
    if not data and raw.get("image_url"):
        try:
            import httpx
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                resp = await client.get(raw["image_url"])
                if resp.status_code == 200:
                    data = resp.content
        except Exception:  # noqa: BLE001 — 拿不到就跳过体检
            data = b""
    if data:
        payload["quality"] = inspect_image(data, thresholds=options.get("quality"))
    return payload


MAX_PROVIDER_MODELS = 50       # 单个 Provider 自定义模型上限
MAX_MODEL_ID_LEN = 200         # 单个模型 id 长度上限


# ── 会话策略（B1：用户要求「审查/合规连续失败 N 次即停」可设置） ──

_CHAT_SETTING_FIELDS = ("max_consecutive_review_failures", "max_turns", "session_ttl_hours",
                        "require_identity_confirm", "require_prompt_review",
                        "prompt_aesthetic_threshold", "prompt_review_max_rounds",
                        "require_prompt_confirm")


@app.post("/api/settings/chat")
async def update_chat_settings(request: Request):
    """更新会话策略（写入 config/default.yaml 的 chat 段并立即生效）

    Body（部分更新）: {"max_consecutive_review_failures": 2, "max_turns": 15,
                      "session_ttl_hours": 24}
    """
    _require_admin_access(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "请求体必须是 JSON")
    if not isinstance(body, dict):
        raise HTTPException(400, "请求体必须是 JSON 对象")

    unknown = sorted(set(body) - set(_CHAT_SETTING_FIELDS))
    if unknown:
        raise HTTPException(400, f"不支持的字段: {', '.join(unknown)}")
    if not body:
        raise HTTPException(400, "请求体不能为空")

    try:
        saved = save_chat_settings(body)
    except ValueError as e:
        raise HTTPException(400, str(e))

    # TTL 即刻生效（会话管理器的惰性驱逐用这个值）
    _session_manager.set_ttl_hours(saved["session_ttl_hours"])
    logger.info("会话策略已更新", extra={"keys": sorted(body), "chat": saved})
    return _settings_payload()


# ── 计价与标定（A94-A96，用户质疑"硬编码价格表会误导"）──
#
# 用户原话："不同的模型的花费是不同的，其次每个模型的花费又会随着时间被各大模型商来回修改……
# 所以这个东西还是否有必要呢？"
# 定稿规则：**用量是事实（永远显示），金额是估算（只在有来源时显示）**；
# 未标定价格的模型一律 `amount=None`，界面显示"未标定（N 张图）"，绝不拿兜底常量编数字。
# 详见 `src/harness/pricing.py` 与 `frontend/src/cost.js`（全站唯一金额口径）。

@app.get("/api/settings/pricing")
async def get_pricing(request: Request):
    """价格表现状：已标定的条目 + 你实际用过的模型（未标定的排前面）"""
    _require_admin_access(request)
    from src.harness.pricing import observed_models, price_table, staleness

    table = price_table()
    entries = table.get("entries") or {}
    observations = observed_models()
    priced_keys = set(entries)
    # 内置参考价也算"有价格"（DALL·E / GPT-4o 这类公开定价）
    for item in observations:
        item["priced"] = str(item.get("key") or "") in priced_keys
    return {
        "path": table.get("path", "config/pricing.yaml"),
        "entries": [{"key": key, **value} for key, value in entries.items()],
        "models": observations,
        "staleness": staleness(),
        "unpriced": [item for item in observations if not item.get("priced")],
        "message": ("未标定的模型不会估算金额（只显示用量）。"
                    "模型商改价后，改这里即可，不需要改代码。"),
    }


@app.post("/api/settings/pricing")
async def update_pricing(request: Request):
    """写入单价（白名单校验）

    Body: `{"prices": {"gpt-4o": {"unit": "1M_tokens", "in": 2.5, "out": 10, "currency": "USD"},
                        "doubao-seedream-5-0-260128": {"unit": "image", "in": 0.28, "out": 0.28,
                                                       "currency": "CNY"}}}`
    """
    _require_admin_access(request)
    from src.harness.pricing import save_prices

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "请求体必须是 JSON")
    prices = (body or {}).get("prices")
    if not isinstance(prices, dict) or not prices:
        raise HTTPException(400, "prices 必须是非空对象（键 = 模型 id）")
    try:
        table = save_prices(prices)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    logger.info("价格表已更新", extra={"keys": sorted(prices)})
    entries = table.get("entries") or {}
    return {"entries": [{"key": key, **value} for key, value in entries.items()],
            "message": f"已保存 {len(prices)} 条价格（金额按你填的值估算，带 ≈ 前缀）"}


@app.post("/api/settings/pricing/calibrate")
async def calibrate_pricing(request: Request):
    """标定：用"本次实际花费 + 本次用量"反推单价并写入（`source=实测标定`）

    Body: `{"route": "ark", "model": "doubao-seedream-5-0-260128", "capability": "image",
            "actual_amount": 1.4, "usage": {"images": 5, "currency": "CNY"}}`
    """
    _require_admin_access(request)
    from src.harness.pricing import calibrate

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "请求体必须是 JSON")
    body = body or {}
    model = str(body.get("model") or "").strip()
    if not model:
        raise HTTPException(400, "请给出 model（要标定的模型 id）")
    try:
        entry = calibrate(str(body.get("route") or ""), model,
                          str(body.get("capability") or "image"),
                          body.get("actual_amount"),
                          body.get("usage") or {})
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"entry": entry,
            "message": "已按本次实际花费反推单价并写入（来源：实测标定）"}


# ── 自定义服务商（用户反馈："我无法自己添加模型服务商，局限性太大了"） ──
#
# 注意：以下三条路由必须注册在 `/api/settings/providers/{route}` **之前**，
# 否则 Starlette 会先匹配到带路径参数的那条，把 "custom" 当成路由 id。

@app.get("/api/settings/providers/presets")
async def list_provider_presets(request: Request):
    """一键添加用的常见服务商预设（智谱 / Kimi / 硅基流动 / OpenRouter / 方舟 / Ollama）

    `available=false` 表示该 route 已存在（内置或已添加），前端应禁用该项。
    """
    _require_admin_access(request)
    from src.providers.routes import PROVIDER_PRESETS

    existing = set(_route_specs())
    out = []
    for preset in PROVIDER_PRESETS:
        item = dict(preset)
        item["available"] = preset["route"] not in existing
        item["reason"] = "" if item["available"] else "已存在（内置或已添加）"
        out.append(item)
    return {"presets": out, "existing_routes": sorted(existing)}


@app.post("/api/settings/providers/custom")
async def upsert_custom_provider(request: Request):
    """新增 / 修改一个自定义服务商（OpenAI 或 Anthropic 兼容），仅 admin

    Body: {route, label, kind, base_url, api_key_env, capabilities[], models[], credential_hint?}
    校验通过后写入 config/custom_providers.yaml（已 gitignore）并重建 Provider 注册表。
    """
    _require_admin_access(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "请求体必须是 JSON")
    if not isinstance(body, dict):
        raise HTTPException(400, "请求体必须是 JSON 对象")

    from src.core.config import load_custom_providers, upsert_custom_provider as save_provider
    from src.providers.routes import validate_spec

    route = str(body.get("route") or "").strip().lower()
    others = {str(p.get("route") or "").strip().lower() for p in load_custom_providers()}
    others.discard(route)   # 同 route = 覆盖更新，不算重名
    _, errors = validate_spec(body, existing_routes=others)
    if errors:
        raise HTTPException(400, "；".join(errors))

    try:
        saved = save_provider(body)
    except ValueError as e:
        raise HTTPException(400, str(e))

    global _provider_registry
    from src.providers import reset_provider_registry
    _provider_registry = reset_provider_registry()
    await _agent_registry.load_from_config(_provider_registry)
    logger.info("自定义服务商已保存并重载", extra={"route": route,
                                                "total": len(saved)})
    return _settings_payload()


@app.delete("/api/settings/providers/custom/{route}")
async def delete_custom_provider(route: str, request: Request):
    """删除一个自定义服务商（内置路由不可删），仅 admin

    连带清理该服务商的凭据（环境变量 + secrets.yaml）：否则删掉服务商后 Key 会变成
    孤儿留在磁盘上，而且因为可保存密钥白名单已收窄，之后连 API 都删不掉它。
    """
    _require_admin_access(request)
    from src.core.config import load_custom_providers, remove_custom_provider

    target = next((p for p in load_custom_providers()
                   if str(p.get("route") or "").strip().lower() == route.strip().lower()), None)
    if target is None or not remove_custom_provider(route):
        raise HTTPException(404, f"没有名为 '{route}' 的自定义服务商（内置路由不可删除）")

    key_env = str(target.get("api_key_env") or "").strip().upper()
    global _provider_registry
    if key_env:
        still_used = any(key_env in spec.key_envs or key_env in spec.all_key_envs
                         for spec in _route_specs().values())
        if not still_used:
            os.environ.pop(key_env, None)
            try:
                from src.core.config import save_runtime_secrets
                save_runtime_secrets({key_env: ""})      # 空值 = 从 secrets.yaml 删除
                logger.info("已连带清理服务商凭据", extra={"route": route, "env": key_env})
            except Exception as e:  # noqa: BLE001 — 清理失败不影响删除本身
                logger.warning("清理服务商凭据失败 (%s): %s", key_env, e)

    from src.providers import reset_provider_registry
    _provider_registry = reset_provider_registry()
    await _agent_registry.load_from_config(_provider_registry)
    logger.info("自定义服务商已删除并重载", extra={"route": route})
    return _settings_payload()




@app.post("/api/settings/providers/move-credential")
async def move_provider_credential(request: Request):
    """把一个凭据从一个槽位迁移到另一个（用户实测场景：方舟 Key 被填进旧版签名卡片）

    Body: {"from_env": "VOLCANO_ACCESS_KEY", "to_env": "ARK_API_KEY"}
    迁移 = 目标槽写入同一密钥 + 源槽清空（内存 env 与 secrets.yaml 同步），随后重建注册表。
    仅当目标槽属于已知路由时才允许，避免把任意变量名写进密钥文件。
    """
    _require_admin_access(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "请求体必须是 JSON")
    if not isinstance(body, dict):
        raise HTTPException(400, "请求体必须是 JSON 对象")

    from_env = str(body.get("from_env") or "").strip().upper()
    to_env = str(body.get("to_env") or "").strip().upper()
    allowed = _allowed_secret_keys()
    if from_env not in allowed or to_env not in allowed:
        raise HTTPException(400, f"变量名必须是已知的服务商凭据（{from_env} → {to_env}）")

    secret = os.getenv(from_env, "")
    if not secret:
        raise HTTPException(400, f"{from_env} 当前没有已配置的密钥可迁移")

    os.environ[to_env] = secret
    os.environ.pop(from_env, None)
    try:
        from src.core.config import save_runtime_secrets
        save_runtime_secrets({to_env: secret, from_env: ""})   # 空值 = 删除该条
    except Exception as e:  # noqa: BLE001
        logger.warning("迁移凭据时写盘失败: %s", e)

    global _provider_registry
    from src.providers import reset_provider_registry
    _provider_registry = reset_provider_registry()
    await _agent_registry.load_from_config(_provider_registry)
    # 绝不回显密钥本身
    logger.info("凭据已迁移", extra={"from_env": from_env, "to_env": to_env})
    return {**_settings_payload(), "moved": {"from_env": from_env, "to_env": to_env}}


@app.get("/api/settings/providers/{route}/models")
async def list_provider_models(route: str, request: Request):
    """拉取该服务商**账号实际可用**的模型列表（OpenAI 兼容的 `GET /models`）

    用户实测痛点：模型 id 靠猜（方舟是「小写-短横线-日期」如
    `doubao-seedream-5-0-260128`，写成 `Doubao-Seedream-5.0-lite` 就 404）。
    这里用已配置的凭据问一次服务商，把可见模型与**按能力分组**的结果返回，
    设置页可一键填入模型目录。仅 admin；绝不回显密钥。
    """
    _require_admin_access(request)
    spec = _route_specs().get(route)
    if spec is None:
        raise HTTPException(404, f"未知 Provider 路由: '{route}'")
    if spec.kind != "openai":
        raise HTTPException(
            400,
            f"{spec.label} 不支持模型列表接口（仅 OpenAI 兼容路由支持；"
            f"当前协议 {spec.kind}）",
        )

    ok, detail, _suggestion = _credential_precheck(spec)
    if not ok:
        return {"route": route, "ok": False, "base_url": "", "total": 0,
                "models": [], "applicable": {}, "detail": detail}

    base_url = _resolve_route_base_url(spec)
    if not base_url:
        return {"route": route, "ok": False, "base_url": "", "total": 0,
                "models": [], "applicable": {}, "detail": "该路由没有可用的端点"}

    import httpx
    api_key = _provider_registry._key_for(spec) if hasattr(_provider_registry, "_key_for") \
        else _key_for_spec(spec)
    try:
        async with httpx.AsyncClient(timeout=PROVIDER_TEST_TIMEOUT_S) as client:
            resp = await client.get(f"{base_url}/models",
                                    headers={"Authorization": f"Bearer {api_key}"})
    except Exception as e:  # noqa: BLE001 — 网络异常转成可读结果
        return {"route": route, "ok": False, "base_url": base_url, "total": 0,
                "models": [], "applicable": {},
                "detail": f"请求模型列表失败：{type(e).__name__}: {str(e)[:200]}"}

    if resp.status_code != 200:
        return {"route": route, "ok": False, "base_url": base_url, "total": 0,
                "models": [], "applicable": {},
                "detail": (f"{spec.label} 模型列表接口返回 {resp.status_code}"
                           f"（部分服务商不提供 /models；可直接手填模型 id）")}

    try:
        raw = resp.json()
    except Exception:
        return {"route": route, "ok": False, "base_url": base_url, "total": 0,
                "models": [], "applicable": {}, "detail": "模型列表不是合法 JSON"}

    items = raw.get("data") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        return {"route": route, "ok": False, "base_url": base_url, "total": 0,
                "models": [], "applicable": {}, "detail": "模型列表格式不符合 OpenAI 约定"}

    models, applicable = [], {"text": [], "vision": [], "image": []}
    recommendable: list[tuple[int, str, str]] = []      # (created, capability, id)
    in_mods_by_id: dict[str, list[str]] = {}
    for item in items:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        mods = item.get("modalities") or {}
        out_mods = [str(x) for x in (mods.get("output_modalities") or [])]
        in_mods = [str(x) for x in (mods.get("input_modalities") or [])]
        status = str(item.get("status") or "")
        mid = str(item["id"])
        # 上游每条都带 name/version（此前被我丢掉 → 界面只剩原始 id，看着像"怪名字"）
        reason = _model_skip_reason(mid, status, item.get("task_type"))
        models.append({
            "id": mid,
            "name": str(item.get("name") or ""),
            "version": str(item.get("version") or ""),
            "status": status,
            "input_modalities": in_mods, "output_modalities": out_mods,
            "recommended": not reason,
            "skip_reason": reason,          # 空 = 可推荐；否则说明为何不进建议
        })
        in_mods_by_id[mid] = in_mods
        if reason or not out_mods:
            continue
        # 一个模型可以同时进多个能力：多模态模型（能看图 + 能写字）既属视觉也属文本，
        # 只归一类会让"文本"里只剩纯文本模型，用户反而选不到该路由的主力模型
        if out_mods == ["image"]:
            recommendable.append((int(item.get("created") or 0), "image", mid))
        elif out_mods == ["text"]:
            created = int(item.get("created") or 0)
            recommendable.append((created, "text", mid))
            if "image" in in_mods:
                recommendable.append((created, "vision", mid))

    # 建议按"新 → 旧"排序：平台目录里历史型号很多，把当前代放最前面
    for _created, cap, mid in sorted(recommendable, key=lambda x: (-x[0], x[2])):
        applicable[cap].append(mid)

    return {"route": route, "ok": True, "base_url": base_url, "total": len(models),
            "models": models, "applicable": applicable, "detail": ""}


# 平台目录里与"选个模型来跑业务"无关的家族（进了建议只会让用户选错）
_AUX_MODEL_KEYWORDS = ("embedding", "translation", "smart-router", "seed3d", "seedance",
                       "character", "ocr", "rerank", "asr", "tts", "sd3", "wanx")


def _model_skip_reason(model_id: str, status: str, task_type=None) -> str:
    """该模型为何不进"可点选"建议；空字符串表示可推荐

    实测（用户反馈"名字有些问题"）：只按 output_modalities 分类会把
    角色扮演 / 翻译 / 路由 / 代码专用模型混进"文本"，把即将下线的老型号混进"视觉"。
    """
    state = str(status or "").strip().lower()
    if state == "shutdown":
        return "已下线"
    if state == "retiring":
        return "即将下线"
    low = str(model_id or "").lower()
    for kw in _AUX_MODEL_KEYWORDS:
        if kw in low:
            return f"专用模型（{kw}）"
    if "-code-" in low or low.endswith("-code") or "code-preview" in low:
        return "代码专用"
    tasks = " ".join(str(t) for t in (task_type or [])).lower()
    if "embedding" in tasks or "rerank" in tasks:
        return "向量/排序专用"
    return ""


def _key_for_spec(spec) -> str:
    """该路由的凭据值（按 key_envs 顺序取第一个非空；不回显）"""
    for env in list(spec.key_envs) + list(spec.all_key_envs):
        value = os.getenv(env, "")
        if value:
            return value
    return ""


def _resolve_route_base_url(spec) -> str:
    """该路由的生效端点（env → providers.yaml → 表内默认）"""
    from src.core.config import resolve_base_url
    if not spec.default_base_url:
        return ""
    return resolve_base_url(spec.route, spec.default_base_url,
                            env_names=spec.base_url_envs or None)


# ── 测试连接（第三轮审计 B3-24） ──

PROVIDER_TEST_TIMEOUT_S = 20.0      # 测试连接硬超时（含网络往返）
# 生图测试要真出一张图：实测方舟 2048×2048 约 30s，20s 会把成功判成超时。
PROVIDER_TEST_IMAGE_TIMEOUT_S = 120.0
_TEST_PROMPT = "ping"               # 最小调用载荷（1 次极短 chat，成本可忽略）


def _is_image_only_route(meta: dict) -> bool:
    """仅图像能力的路由（Seedream/FLUX：测试连接要走生图，默认跳过以免产生费用）"""
    caps = meta.get("capabilities", "")
    return "image" in caps and "vision" not in caps and "text" not in caps


def _test_model_for(route: str, capability: str, spec=None) -> str:
    """测试连接用哪个模型：该路由自定义模型 → 能力映射中属于该路由的模型
    → **该路由按能力分组的官方模型** → 空（Provider 默认）

    两个实测教训：
    - 用户的自定义模型列表常混着生图与文本模型（方舟就是），直接取第一条会把
      生图模型发给 chat 接口 → 先挑"属于本能力的"，挑不到再退回第一条；
    - 火山方舟这类端点没有"默认模型"，不带 model 直接 400 MissingParameter，
      所以必须有官方模型兜底。
    """
    from src.core.config import load_provider_config
    custom = [str(m) for m in (load_provider_config().get(route, {}).get("models") or [])]
    known_for_cap = set(spec.models_for(capability)) if spec is not None else set()
    known_any = set(spec.models) if spec is not None else set()

    # 1) 用户自定义 且 **就是本能力的模型**（方舟等路由的列表常混着生图/文本模型）
    preferred = next((m for m in custom if m in known_for_cap), "")
    if preferred:
        return preferred
    # 2) 自定义里"我们已知属于别的能力"的要排除，其余（coding plan 自有模型名）尊重用户 ——
    #    对第三方 coding plan，用户填的模型名才是唯一可用的，不能被内置映射顶掉
    unknown = [m for m in custom if m not in known_any]
    if unknown:
        return unknown[0]
    # 3) 能力映射里属于该路由的模型
    cap_cfg = ((load_models_config() or {}).get("capabilities", {}) or {}).get(capability) or {}
    for item in [cap_cfg.get("default", "")] + list(cap_cfg.get("alternatives", []) or []):
        item = str(item or "")
        if item.split("/", 1)[0] == route:
            return item.split("/", 1)[1] if "/" in item else ""
    # 4) 该能力分组的官方模型（按目录顺序取第一个）
    if spec is not None:
        official = spec.models_for(capability)
        if official:
            return official[0]
    return ""


def _looks_like_ark_key(value: str) -> bool:
    """火山方舟 API Key 的形态特征（用户实测：被误填进旧版 AK/SK 卡片的 AccessKey 槽）"""
    return str(value or "").strip().startswith("ark-")


def _credential_precheck(spec) -> tuple[bool, str, dict]:
    """测试连接前的凭据预检 → (是否可继续, 不可继续时的说明, 迁移建议)

    三类问题都要给出**可执行的下一步**，而不是把下游裸错误丢出来：
    - 完全没配凭据 → 告诉用户该填哪个变量；
    - AK/SK 只配了一半 → 指出缺哪一个（成对才能签名）；
    - 方舟 Key（ark- 前缀）被填进旧版签名卡片 → 指出它属于哪个路由，并附一键迁移建议。
    """
    values = {env: os.getenv(env, "") for env in spec.key_envs + spec.all_key_envs}
    configured = [env for env, val in values.items() if val]

    if spec.all_key_envs:
        missing = [env for env in spec.all_key_envs if not values.get(env)]
        ark_slot = next((env for env, val in values.items() if _looks_like_ark_key(val)), "")
        if ark_slot:
            return False, (
                f"{ark_slot} 里填的看起来是**火山方舟 API Key**（ark- 开头），"
                f"它不属于本路由（{spec.label}）。即梦/Seedream 与豆包模型都在方舟调用，"
                f"请改填到「火山引擎方舟（Ark）」卡片的 ARK_API_KEY（单把 Key 即可，无需 AK/SK）。"
            ), {"route": "ark", "env": "ARK_API_KEY", "from_env": ark_slot}
        if not configured:
            return False, (f"未配置凭据：请先填写 {spec.credentials[0]['name']}"
                           f"（{spec.key_envs[0]}）"), {}
        if missing and not values.get(spec.key_envs[0]):
            # 只填了 AK/SK 的一半（且没有平台 Key）
            return False, (f"凭据不完整：还缺 {', '.join(missing)}"
                           f"（AK/SK 必须成对才能签名；或改用即梦平台 Key）"), {}
        return True, "", {}

    if not configured:
        env = spec.key_envs[0] if spec.key_envs else (spec.api_key_env or "API Key")
        return False, f"未配置凭据：请先在上方填写 {env}", {}
    return True, "", {}


@app.post("/api/settings/providers/{route}")
async def update_provider_config(route: str, request: Request):
    """配置单个 Provider 的端点与模型（第三方 coding plan / 代理 / 自建网关）

    Body（部分更新）:
        {"base_url": "https://my-proxy.example.com/v1", "models": ["gpt-5-codex", "..."]}
    空 base_url / 空 models 列表表示清除该项；端点与模型持久化到
    config/providers.yaml（已 gitignore）并立即重建 Provider 注册表生效。
    """
    _require_admin_access(request)
    spec = _route_specs().get(route)
    if spec is None:
        raise HTTPException(404, f"未知 Provider 路由: '{route}'")
    meta = _route_meta(spec)

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "请求体必须是 JSON")
    if not isinstance(body, dict):
        raise HTTPException(400, "请求体必须是 JSON 对象")

    updates: dict = {}
    from src.core.config import base_url_env_names, base_url_from_env

    if "base_url" in body:
        if not meta["base_url_supported"]:
            raise HTTPException(
                400,
                f"{meta['label']} 使用官方固定端点（多上游），暂不支持自定义端点",
            )
        env_name = base_url_env_names(route)[0]
        raw = str(body.get("base_url") or "").strip()
        if raw:
            from urllib.parse import urlparse
            parsed = urlparse(raw)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                raise HTTPException(400, "端点必须是可解析的 http(s) URL")
            if base_url_from_env(route):
                raise HTTPException(
                    403,
                    f"{meta['label']} 的端点由环境变量 {env_name} 提供，"
                    f"设置页修改不会生效（请改环境变量后重启）",
                )
            updates["base_url"] = raw
        else:
            if base_url_from_env(route):
                raise HTTPException(
                    403,
                    f"{meta['label']} 的端点由环境变量 {env_name} 提供，无法在此清除",
                )
            updates["base_url"] = ""      # 清除 → 回落官方端点

    if "models" in body:
        raw_models = body.get("models")
        if not isinstance(raw_models, list):
            raise HTTPException(400, "models 必须是数组")
        models: list[str] = []
        for item in raw_models:
            mid = str(item).strip()
            if not mid:
                continue
            if len(mid) > MAX_MODEL_ID_LEN:
                raise HTTPException(400, f"模型 id 过长（>{MAX_MODEL_ID_LEN} 字符）")
            if not all(0x21 <= ord(ch) <= 0x7E for ch in mid):
                raise HTTPException(400, f"模型 id 含非法字符（仅可打印 ASCII，无空格）: {mid[:40]}")
            if mid not in models:
                models.append(mid)
        if len(models) > MAX_PROVIDER_MODELS:
            raise HTTPException(400, f"单个 Provider 最多 {MAX_PROVIDER_MODELS} 个自定义模型")
        updates["models"] = models

    if not updates:
        raise HTTPException(400, "未提供 base_url 或 models")

    from src.core.config import save_provider_config
    save_provider_config({route: updates})

    # 立即生效：重建 Provider 注册表（新端点）+ 重载 Agent（新模型名）
    global _provider_registry
    from src.providers import reset_provider_registry
    _provider_registry = reset_provider_registry()
    await _agent_registry.load_from_config(_provider_registry)
    logger.info("Provider 端点/模型已更新并重载", extra={"route": route})
    return _settings_payload()


@app.post("/api/settings/output")
async def update_output_dir(request: Request):
    """设置生成图输出根目录（仅 admin；`{"dir": ""}` 清除回默认）

    校验用一次真实 mkdir + 探针写入（借用 `output_status()` 同一套逻辑）：
    路径不可用就 400 并**不写入配置**，避免把坏路径持久化下来。
    """
    _require_admin_access(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "请求体必须是 JSON")
    if not isinstance(body, dict):
        raise HTTPException(400, "请求体必须是 JSON 对象")
    if "dir" not in body:
        raise HTTPException(400, "缺少 dir 字段")
    raw = body.get("dir")
    if not isinstance(raw, str):
        raise HTTPException(400, "dir 必须是字符串")
    target = raw.strip()

    from src.core.config import OUTPUT_DIR_ENV, output_status, save_output_dir
    if os.getenv(OUTPUT_DIR_ENV, "").strip():
        raise HTTPException(
            403,
            f"输出目录由环境变量 {OUTPUT_DIR_ENV} 提供，设置页修改不会生效（请改环境变量后重启）",
        )

    saved = os.environ.get(OUTPUT_DIR_ENV)
    os.environ[OUTPUT_DIR_ENV] = target          # 临时借用同一套解析 + 探针
    try:
        status = output_status()
    finally:
        if saved is None:
            os.environ.pop(OUTPUT_DIR_ENV, None)
        else:
            os.environ[OUTPUT_DIR_ENV] = saved
    if target and not status["writable"]:
        raise HTTPException(400, f"输出目录不可用（{status['error']}）——请检查盘符与写入权限")

    save_output_dir(target)
    logger.info("生成图输出目录已更新", extra={"dir": target or "(默认)"})
    return _settings_payload()


@app.post("/api/settings/providers/{route}/test")
async def test_provider_connection(route: str, request: Request):
    """测试连接：对该 Provider 发起 1 次最小调用，验证 Key / 端点 / 模型真的可用

    Body（可选）: {"model": "...", "allow_image": true}
    - `available` 此前只表示"环境变量非空"：配置第三方 coding plan（自填端点与模型 id）后
      无法验证是否真的能调通，只能等真正跑图时才发现；
    - 图像路由默认跳过（真实生成有费用与数十秒等待），`allow_image=true` 才真跑；
    - Mock 路由不发起真实调用（如实标记 `is_mock`）；
    - 连接失败仍返回 200 + `ok=false` + 上游原文，供界面直接展示。
    """
    _require_admin_access(request)
    spec = _route_specs().get(route)
    if spec is None:
        raise HTTPException(404, f"未知 Provider 路由: '{route}'")
    meta = _route_meta(spec)

    raw_body = await request.body()
    if raw_body and raw_body.strip():
        try:
            body = json.loads(raw_body)
        except Exception:
            raise HTTPException(400, "请求体必须是 JSON")
        if not isinstance(body, dict):
            raise HTTPException(400, "请求体必须是 JSON 对象")
    else:
        body = {}

    model_arg = body.get("model", "")
    if model_arg and not isinstance(model_arg, str):
        raise HTTPException(400, "model 必须是字符串")
    allow_image = bool(body.get("allow_image"))

    from src.core.config import resolve_base_url
    image_only = _is_image_only_route(meta)
    base_url = resolve_base_url(route, meta["default_base_url"],
                                env_names=spec.base_url_envs or None) \
        if meta["default_base_url"] else ""

    def _result(**kw) -> dict:
        out = {"route": route, "label": meta["label"], "base_url": base_url,
               "ok": False, "skipped": False, "is_mock": False,
               "model": "", "latency_ms": 0, "detail": "", "reason": "",
               "suggested": None}
        out.update(kw)
        return out

    if image_only and not allow_image:
        return _result(
            skipped=True,
            reason="生图验证会产生真实费用与数十秒等待；如需验证请勾选「包含生图测试」",
        )

    # 凭据预检（用户实测反馈：只配了方舟 Key 却填在旧卡片 → 报错只给内部变量名。
    # 这里先判断"缺凭据 / 凭据不完整 / Key 放错槽位"，给出可执行的下一步，
    # 而不是把下游的裸错误（如 httpx `Illegal header value b'Bearer '`）丢给用户）
    ok_creds, cred_detail, suggestion = _credential_precheck(spec)
    if not ok_creds:
        return _result(detail=cred_detail, **({"suggested": suggestion} if suggestion else {}))

    # 能力选择：图像路由走生图；其余优先 text（最便宜、各家都支持），无 text 才退 vision
    caps = meta.get("capabilities", "")
    capability = "image" if image_only else ("text" if "text" in caps else "vision")
    model = (model_arg or _test_model_for(route, capability, spec)).strip()
    provider = (_provider_registry.get_image(route) if image_only
                else _provider_registry.get_llm(route))
    if provider is None:
        return _result(model=model,
                       detail=f"{meta['label']} Provider 未就绪（凭据缺失或初始化失败）")
    if getattr(provider, "name", "") == "mock":
        return _result(model=model, is_mock=True, skipped=True,
                       reason="当前为 Mock（未配置该 Provider 的真实凭据）：未发起真实调用")

    started = time.perf_counter()
    call_timeout = PROVIDER_TEST_IMAGE_TIMEOUT_S if image_only else PROVIDER_TEST_TIMEOUT_S
    try:
        if image_only:
            # 尺寸必须走路由默认值：方舟 Seedream 5.0 低于 3,686,400 像素直接 400，
            # 写死 1024x1024 会让"测试连接"永远失败（实测事故）
            from src.core.config import resolve_image_size
            raw = await asyncio.wait_for(
                provider.generate("white background product photo",
                                  size=resolve_image_size(getattr(provider, "default_size", "")),
                                  model=model),
                timeout=call_timeout,
            )
        else:
            raw = await asyncio.wait_for(
                provider.chat([{"role": "user", "content": _TEST_PROMPT}], model=model),
                timeout=call_timeout,
            )
    except asyncio.TimeoutError:
        return _result(model=model, latency_ms=int(call_timeout * 1000),
                       detail=f"调用超时（>{call_timeout:.0f}s）：端点不可达或响应过慢")
    except Exception as e:  # noqa: BLE001 — 测试端点把异常转成可读结果
        return _result(model=model, latency_ms=int((time.perf_counter() - started) * 1000),
                       detail=f"{type(e).__name__}: {str(e)[:300]}")

    latency_ms = int((time.perf_counter() - started) * 1000)
    if isinstance(raw, dict) and raw.get("error"):
        return _result(model=model, latency_ms=latency_ms,
                       detail=_with_auth_hint(
                           _with_model_hint(str(raw["error"])[:400], route, model), route))
    logger.info("Provider 测试连接成功", extra={"route": route, "model": model, "latency_ms": latency_ms})
    return _result(model=model, latency_ms=latency_ms, ok=True, detail="连接正常")


def _with_model_hint(detail: str, route: str, model: str = "") -> str:
    """给"模型不可用"类错误补一句可执行说明（用户实测：Key 正确但模型 id 不对）

    火山方舟等平台：鉴权通过但模型未开通/未授权/写错 id 时返回 404
    `InvalidEndpointOrModel.NotFound` —— 用户看到 404 会以为 Key 错了。
    实测案例：把 `doubao-seedream-5-0-260128` 写成 `Doubao-Seedream-5.0-lite`
    （方舟 id 是**小写 + 短横线 + 日期后缀**，且账号里没有 lite 版本）。
    """
    if not any(k in detail for k in ("InvalidEndpointOrModel", "does not exist or you do not have access",
                                     "model_not_found", "Model not found", "does not exist",
                                     "ModelNotOpen", "has not activated")):
        return detail
    parts = [detail,
             "—— 说明：鉴权已通过（Key 有效），是**模型不可用**。"]
    if "has not activated" in detail or "ModelNotOpen" in detail:
        parts.append("方舟明确回复「该账号未开通此模型」：请到方舟控制台「开通管理」里"
                     "开通对应模型（文本与生图要分别开通），开通后无需改配置即可重试。")
    if model and (any(c.isupper() for c in model) or "." in model):
        parts.append(f"另外注意：'{model}' 的形态不寻常——方舟/多数平台的模型 id 是"
                     f"**小写字母 + 短横线 + 日期后缀**（例：doubao-seedream-5-0-260128），"
                     f"带大写字母或点号通常不是有效 id。")
    parts.append("也可用「拉取可用模型」按账号实际可见列表挑选；或改用推理接入点 ID"
                 "（如火山方舟的 ep-…）——填到本卡片的「自定义设置 → 模型目录」后，"
                 "测试连接会优先使用它。")
    return "\n".join(parts)


def _with_auth_hint(detail: str, route: str) -> str:
    """给 401 鉴权失败补说明（用户实测：ARk 卡片里存了个 27 位 `apik…` 的值 → 401）

    实测原文：`401 AuthenticationError — "The API key format is incorrect"`。
    这类失败常见于：粘贴错位（把 AK/SK、接入点 ID、别的平台的 Key 填进来）、
    复制不完整、或在控制台轮换/撤销了 Key。
    """
    if not any(k in detail for k in ("401", "AuthenticationError", "Unauthorized",
                                     "invalid api key", "Incorrect API key")):
        return detail
    parts = [detail, "—— 说明：这是**鉴权失败**（Key 格式/有效性有问题），不是模型问题。"]
    if route == "ark":
        parts.append("方舟 API Key 形如 `ark-…`（约 46 位）。请确认：① 没有把"
                     "火山 AK/SK、推理接入点 ID（`ep-…`）或别家平台的 Key 填进这一栏；"
                     "② 复制完整（没有首尾空格/被截断）；③ 该 Key 未在控制台被轮换或撤销。")
    else:
        parts.append("请确认 Key 复制完整（无首尾空格/未被截断），且未在服务商控制台被轮换或撤销。")
    parts.append("改动后点「保存」再重试；凭据是只写的，页面不会回显，"
                 "如不确定可到服务商控制台重新复制一份。")
    return "\n".join(parts)


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

    第三轮审计 B0-2：模板是**全局**资源（`force=true` 可覆盖内置模板），补管理面守卫。
    """
    _require_admin_access(request)
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
    if not hmac.compare_digest(request.headers.get("X-Webhook-Token", ""), expected_token):
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
    """工作流实时事件流（鉴权：全局 Key 或租户 Key；租户 Key 绑定租户身份）"""
    role, bound_tenant = authenticate_api_key(websocket.query_params.get("api_key", ""))
    if auth_enabled() and role == "none":
        await websocket.close(code=4001, reason="Missing or invalid API Key")
        return

    job = await _workflow_store.get_job(job_id)
    if job is None:
        await websocket.close(code=4004, reason="作业不存在")
        return
    # 租户校验（审计修复：WS 也按租户隔离；租户 Key 绑定身份，覆盖 tenant 查询参数）
    tenant = bound_tenant or websocket.query_params.get("tenant", "")
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
    """实时群聊消息流（鉴权：全局 Key 或租户 Key；租户 Key 绑定租户身份）"""
    # WebSocket 鉴权：检查查询参数 ?api_key=...（子协议方式未实现，见注释）
    role, bound_tenant = authenticate_api_key(websocket.query_params.get("api_key", ""))
    if auth_enabled() and role == "none":
        await websocket.close(code=4001, reason="Missing or invalid API Key")
        return

    # 租户校验（审计修复：WS 也按租户隔离；租户 Key 绑定身份，覆盖 tenant 查询参数）
    tenant = bound_tenant or websocket.query_params.get("tenant", "")
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
