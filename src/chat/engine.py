"""ChatEngine — 群聊主循环"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from src.agents.registry import AgentRegistry
from src.chat.broadcaster import Broadcaster
from src.chat.session import SessionManager
from src.core.config import chat_settings, image_options
from src.core.logging_config import get_logger
from src.core.state import RunStatus, SessionState
from src.harness.audit_logger import AuditLogger
from src.harness.context_manager import ContextManager, WindowAction
from src.harness.output_pipeline import OutputPipeline
from src.harness.product_identity import (
    identity_card_block,
    identity_summary,
    is_confirmed,
    normalize_identity,
)
from src.harness.set_plan import (
    normalize_set_plan,
    platform_slot_plan,
    set_plan_coverage,
    set_plan_summary,
)

_engine_logger = get_logger(__name__)


def _is_blank_value(value) -> bool:
    """空载荷判定（A40）：空串/None/空列表/空 dict 不参与 artifacts 合并"""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, dict, tuple, set)):
        return len(value) == 0
    return False


def _inject_identity_card(session, task_brief: str, agent_name: str = "") -> str:
    """把**商品身份卡**前置到下游简报（与记忆库参考同款：前置 = 权重最高）

    只有真正要用到商品事实的环节才注入（生图员靠参考图而不是文字，跳过可以省 token）。
    未确认的身份卡也会注入 —— 那时它在明确禁止出现任何品牌/文字，正是需要传达的约束。
    """
    if not task_brief or not isinstance(session, dict):
        return task_brief
    if agent_name and agent_name not in _IDENTITY_CONSUMERS:
        return task_brief
    identity = (session.get("artifacts") or {}).get("product_identity")
    if not isinstance(identity, dict) or not identity.get("status"):
        return task_brief
    return f"{identity_card_block(identity)}\n\n## 任务\n{task_brief}"


# 需要"记得"商品身份的环节：提示词（写文案）、审查/合规（比对基准）、生图（决定文字策略）
_IDENTITY_CONSUMERS = ("提示词生成员", "生图员", "审查员", "合规审查员", "品类专项分析员")


def _has_identity_card(analysis) -> bool:
    """该分析产物里是否已有**带溯源**的商品身份卡（有 status 才算）

    身份卡只由商品分析员（vision）或用户确认产生；品类专项分析员等后续写入的裸
    `product_identity` 不得覆盖它 —— 否则溯源信息（status/source）会被抹掉，
    下游"能不能出现品牌文字"的判断就失去依据。
    """
    if not isinstance(analysis, dict):
        return False
    identity = analysis.get("product_identity")
    return isinstance(identity, dict) and bool(identity.get("status"))


def _white_expectations(plan) -> dict[str, bool]:
    """哪些槽位**要求**纯白背景（体检用）

    用户实测质疑"很多商品图都不是白底的啊？"——白底不是全局硬规则，按平台策略判定：
    只有 `white_required`（如 Amazon 主图）才要求纯白；`design_allowed` 平台出设计底
    不属于问题（否则协调者会白跑一轮重生成，A59 踩过）。
    """
    policy = str((plan or {}).get("background_policy") or "design_allowed")
    return {
        str(slot.get("slot_id") or ""): (
            str(slot.get("kind") or "photo") == "photo"
            and (policy == "white_required"
                 or str(slot.get("bg_policy") or "") == "white_required"))
        for slot in (plan or {}).get("slots") or []
        if isinstance(slot, dict)
    }


def _merge_images(existing, incoming) -> list[dict]:
    """把新一轮出图**按槽位合并**进产物（A74）

    改前是整键替换：只要有一轮只出了部分槽位（例如补跑详情图、或用户要求重出某几张），
    上一轮已经出好的图就会从产物里消失（磁盘文件还在，界面与导出却没了）。

    合并规则（整批无键时回落整键替换，兼容旧的单图多候选路径）：
    - 键 = `slot_id` 优先，其次 `prompt_name`；
    - 命中原键 → **原位替换**（保持出图顺序稳定）；新键 → 追加；
    - 新旧条目都没有键 → 直接替换。
    """
    old = [img for img in (existing or []) if isinstance(img, dict)]
    new = [img for img in (incoming or []) if isinstance(img, dict)]
    if not new:
        return old
    if not old:
        return new

    def _key(item) -> str:
        return str(item.get("slot_id") or item.get("prompt_name") or "")

    if not any(_key(item) for item in new + old):
        return new

    merged = list(old)
    index_of = {_key(item): position for position, item in enumerate(merged) if _key(item)}
    for item in new:
        key = _key(item)
        if key and key in index_of:
            merged[index_of[key]] = item
        else:
            if key:
                index_of[key] = len(merged)
            merged.append(item)
    return merged


def _memory_inject_settings() -> tuple[bool, int]:
    """记忆注入开关与长度上限（config/default.yaml 的 memory 段）"""
    from src.core.config import load_default_config
    cfg = (load_default_config().get("memory") or {})
    if not isinstance(cfg, dict):
        cfg = {}
    enabled = bool(cfg.get("inject_prompt_reference", True))
    try:
        max_chars = int(cfg.get("max_ref_chars") or 120)
    except (TypeError, ValueError):
        max_chars = 120
    return enabled, max(0, max_chars)


def _inject_memory_reference(task_brief: str, memory_context, *,
                             max_chars: int | None = None) -> str:
    """把「历史最佳提示词」作为**风格参考**前置到本次任务简报之前（A36）

    实测事故：原实现把参考**追加在 brief 之后**（recency 最强），参考内容来自**别的
    商品**（"护肝胶囊…水飞蓟植物元素环绕"）→ 本次提示词里出现"水飞蓟植物叶片"，而该商品
    的成分分析结论是"不可见/待确认"，协调者还专门叮嘱"严禁臆造具体成分"。

    现在：① 参考前置、brief 收尾（约束在最后出现，权重更高）；
    ② 限定为风格/构图参考，显式禁止照搬成分/卖点/品牌/认证等事实；
    ③ 长度受限；④ 可由 config/default.yaml 的 memory.inject_prompt_reference 整体关闭。
    """
    if not task_brief or not isinstance(memory_context, dict):
        return task_brief

    best = ((memory_context.get("best_prompts") or {}) or {}).get("main") or ""
    if not str(best).strip():
        return task_brief

    if max_chars is None:
        enabled, configured_chars = _memory_inject_settings()
        if not enabled:
            return task_brief
        max_chars = configured_chars

    reference = str(best).strip()[:max_chars]
    return (
        "[记忆库参考·仅风格与构图，不得照搬]\n"
        f"历史同类目最佳提示词（属于**其他商品**，仅供构图/光影/画面调性参考）：{reference}\n"
        "⚠ 该参考中出现的成分、卖点、品牌、认证、规格等事实信息，一律不得写入本次提示词；"
        "事实上限以本次商品分析为准。\n\n"
        f"## 本次任务\n{task_brief}"
    )


class ChatEngine:
    """群聊引擎

    Coordinator 驱动 → 邀请 Agent → Agent 响应 → 广播消息 → 循环 → DONE
    """

    def __init__(
        self,
        registry: AgentRegistry,
        session_manager: SessionManager,
        broadcaster: Optional[Broadcaster] = None,
    ):
        self.registry = registry
        self.sessions = session_manager
        self.broadcaster = broadcaster

    async def run(self, session: SessionState, start_index: Optional[int] = None) -> SessionState:
        """运行群聊主循环

        start_index: 非 None 时在 coordinator.reset() 之后定位到指定工作流索引
        （审计修复：HITL retry 跳转此前被 reset() 清零，导致从分析员全量重跑）
        """
        session_id = session["session_id"]
        max_turns = session.get("max_turns", 15)
        session["status"] = RunStatus.RUNNING.value

        coordinator = self.registry.get("中心决策者")
        if coordinator is None:
            session["status"] = RunStatus.FAILED.value
            session["messages"].append({
                "id": uuid.uuid4().hex[:12],
                "turn": 0,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "role": "system",
                "sender": "系统",
                "action": "error",
                "content": {"error": "中心决策者未注册"},
            })
            self._record_error(session, "中心决策者", "中心决策者未注册", kind="abort")
            return session

        coordinator.reset(session)
        if start_index is not None:
            coordinator._state(session)["workflow_index"] = start_index
        mode = session.get("task", {}).get("collaboration_mode", "serial") or "serial"
        coordinator.set_mode(mode, session)

        # ── P3: Agent 记忆召回 ──
        # 根据品类和历史特征，召回过去成功的分析模式、提示词、审查经验
        task = session.get("task", {})
        category_hint = task.get("category_hint", "")
        try:
            from src.harness.agent_memory import AgentMemory
            memory = AgentMemory()
            recalled = await memory.recall(category=category_hint, limit=5,
                                           tenant_id=session.get("tenant_id", "")) if category_hint else []
            if recalled:
                session["_memory_context"] = {
                    "category": category_hint,
                    "recalled_count": len(recalled),
                    "best_score": recalled[0].get("score", 0),
                    "best_prompts": recalled[0].get("prompts", {}),
                    "common_features": list(set(
                        f for e in recalled for f in e.get("features", [])[:3]
                    ))[:8],
                    "common_praises": list(set(
                        p for e in recalled for p in e.get("top_praises", [])[:2]
                    ))[:5],
                }
                coord_msg = {
                    "id": uuid.uuid4().hex[:12],
                    "turn": 0,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "role": "system",
                    "sender": "记忆库",
                    "action": "respond",
                    "content": {
                        "memory_recall": f"从 {category_hint or '通用'} 品类召回 {len(recalled)} 条历史成功经验，"
                                        f"最佳评分 {recalled[0].get('score', 0)}/100",
                    },
                }
                session["messages"].append(coord_msg)
                if self.broadcaster:
                    await self.broadcaster.broadcast(session_id, coord_msg)
        except Exception as e:
            _engine_logger.warning("记忆召回失败，不影响主流程: %s", e, exc_info=True)

        # 将记忆上下文注入 Coordinator（会话级，并发安全）
        coordinator.set_memory_context(session.get("_memory_context"), session)

        ctx_mgr = ContextManager(model="gpt-4o")
        window_index = session.get("context_window_index", 0)

        completed = False
        for turn in range(max_turns):
            session["turn_count"] = turn + 1

            # 0. 上下文检查（含 Coordinator system prompt）
            coord_system = coordinator._build_system_prompt(
                coordinator._build_agent_list(),
                coordinator._state(session)["memory_context"],
            ) if hasattr(coordinator, '_build_system_prompt') else ""
            ctx_report = ctx_mgr.check(session.get("messages", []), system_prompt=coord_system)
            if ctx_report.action in (WindowAction.COMPACT, WindowAction.NEW_WINDOW):
                # 压缩/新窗口前先持久化，防止崩溃丢失消息
                try:
                    from src.storage.checkpoint import save_checkpoint
                    await save_checkpoint(session_id, dict(session))
                except Exception as e:
                    _engine_logger.warning("压缩前 checkpoint 保存失败: %s", e, exc_info=True)
            if ctx_report.action == WindowAction.COMPACT:
                compacted, summary = ctx_mgr.compact(session["messages"])
                session["messages"] = compacted
                session["context_transition_summary"] = summary
                session["estimated_tokens"] = ctx_mgr.estimate_messages(compacted)

                if self.broadcaster:
                    await self.broadcaster.broadcast(session_id, {
                        "id": uuid.uuid4().hex[:12],
                        "turn": turn,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "role": "system",
                        "sender": "系统",
                        "action": "respond",
                        "content": {"context_action": "compact", "summary": summary},
                    })
            elif ctx_report.action == WindowAction.NEW_WINDOW:
                summary = ctx_mgr.new_window_summary(session["messages"])
                session["context_window_index"] = window_index + 1
                session["context_transition_summary"] = summary
                # 保留最近 5 条消息，其余用摘要替代
                recent = session["messages"][-5:] if len(session["messages"]) > 5 else session["messages"]
                session["messages"] = [{
                    "id": uuid.uuid4().hex[:12],
                    "turn": turn,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "role": "system",
                    "sender": "系统",
                    "action": "respond",
                    "content": {"context_action": "new_window", "window": window_index + 1, "summary": summary},
                }] + recent
                window_index += 1

                if self.broadcaster:
                    await self.broadcaster.broadcast(session_id, {
                        "id": uuid.uuid4().hex[:12],
                        "turn": turn,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "role": "system",
                        "sender": "系统",
                        "action": "respond",
                        "content": {"context_action": "new_window", "window": window_index, "summary": summary},
                    })

            # 1. Coordinator 决策（真实 LLM 或 Mock）
            decision = await coordinator.decide(session)

            # 广播 Coordinator 的决策
            coord_msg = {
                "id": uuid.uuid4().hex[:12],
                "turn": turn,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "role": "coordinator",
                "sender": "中心决策者",
                "action": decision["action"],
                "content": decision,
            }
            session["messages"].append(coord_msg)
            if self.broadcaster:
                await self.broadcaster.broadcast(session_id, coord_msg)

            if decision["action"] == "done":
                session["status"] = RunStatus.COMPLETED.value
                completed = True
                break

            if decision["action"] == "invite":
                agent_name = decision["agent_name"]
                # `get_invitable`：后台 Agent（如「风格档案员」，invitable: false）**不参与群聊**。
                # 它不会出现在协调者的名单里；这里再兜一层 —— 万一是模型幻觉出来的名字，
                # 也**不会调用任何 Provider**（省掉一次白花钱的视觉调用），只在群聊里记一条错误，
                # 协调者读得到（历史里带系统消息）会自行纠正。
                agent = self.registry.get_invitable(agent_name)

                if agent is None:
                    meta = self.registry.get_meta(agent_name)
                    reason = ("该 Agent 是后台 Agent（不参与群聊），不能邀请它。"
                              "请从「可用 Agent」名单中选择"
                              if meta is not None and not getattr(meta, "invitable", True)
                              else f"Agent '{agent_name}' 未注册")
                    error_msg = {
                        "id": uuid.uuid4().hex[:12],
                        "turn": turn,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "role": "system",
                        "sender": "系统",
                        "action": "error",
                        "content": {"error": reason},
                    }
                    session["messages"].append(error_msg)
                    if self.broadcaster:
                        await self.broadcaster.broadcast(session_id, error_msg)
                    continue

                # 2. Agent 执行（注入记忆上下文增强 task_brief）
                task_brief = decision.get("task_brief", "")
                if agent_name == "提示词生成员":
                    task_brief = _inject_memory_reference(task_brief, session.get("_memory_context"))
                # 身份卡前置：让下游每一步都"记得"这件商品的品牌/品名/规格
                # （用户反馈："商品名是什么都没强调或者提醒"）
                task_brief = _inject_identity_card(session, task_brief, agent_name)
                # 用量/耗时出参（A38）：审计此前从 content 里取用量 → 恒为 0
                call_stats: dict = {}
                result = await agent.execute(task_brief, session, stats=call_stats)
                # 未标定次数进会话状态：界面/工作流据此显示"另有 N 次未标定"，
                # 而不是让 cost_so_far=0 看起来像"这一轮没花钱"（N=0 时前端会显示 —
                # 而不是金额，所以我们还需要一个"金额是否可信"的信号）
                session["cost_unknown_calls"] = int(session.get("cost_unknown_calls") or 0)

                # A43：审查员可能返回逐变体 `{"results": [...]}`（真实会话实测：顶层没有
                # overall_score/verdict，内容却完全有效）→ 先汇总出顶层判定，后续的输出校验、
                # 审计、artifacts 与门禁**都以归一化结果为准**（幂等，已合规的结果不变）
                if agent_name == "审查员":
                    from src.harness.review_normalize import normalize_review_payload
                    result, aggregated = normalize_review_payload(result)
                    if aggregated:
                        result["aggregation_note"] = aggregated

                # 3. 广播 Agent 响应
                agent_msg = {
                    "id": uuid.uuid4().hex[:12],
                    "turn": turn,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "role": "agent",
                    "sender": agent_name,
                    "action": "error" if "error" in result else "respond",
                    "content": result,
                }
                session["messages"].append(agent_msg)
                if self.broadcaster:
                    await self.broadcaster.broadcast(session_id, agent_msg)

                # 3b. 失败原因留痕（B3-25）：Agent 报错此前只进群聊消息与审计日志，
                # 会话本身不带原因 → 界面"会话失败"无内容可显示
                if isinstance(result, dict) and result.get("error"):
                    self._record_error(session, agent_name, result["error"], kind="agent")

                # 4a. 防幻觉：分析员置信度检查
                if agent_name == "商品分析员":
                    confidence = result.get("confidence_score", 0)
                    if confidence < 60:
                        low_conf_msg = {
                            "id": uuid.uuid4().hex[:12],
                            "turn": turn,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "role": "system",
                            "sender": "系统",
                            "action": "error",
                            "content": {
                                "hallucination_risk": f"分析置信度偏低 ({confidence}/100)，建议人工确认或重试",
                                "confidence_threshold": 60,
                            },
                        }
                        session["messages"].append(low_conf_msg)
                        if self.broadcaster:
                            await self.broadcaster.broadcast(session_id, low_conf_msg)
                        # 注入到 result 供前端/日志感知
                        result["_low_confidence_warning"] = True

                    # 4a-2. 商品身份门禁（用户反馈："根本没识别到我喂的图是什么品牌，
                    # 商品名是什么都没强调或者提醒"）——身份是全链路的事实基准，
                    # 没确认就出图 = 让模型自由编造包装文字（实测把 DEFOEBUENA® 编成 NUTRIVA®）
                    if await self._identity_gate(session, result, turn):
                        await self.sessions.update(session_id, session)
                        return session

                # 4a-3. 生图完成后：本地体检 + 套图覆盖度（不花钱、确定性）
                if agent_name == "生图员" and isinstance(result.get("images"), list) and result["images"]:
                    await self._attach_image_reports(session, result, turn)

                # 4b. 输出验证
                output_pipe = OutputPipeline()
                output_check = output_pipe.validate(agent_name, result)
                if not output_check.passed and "error" not in result:
                    result["_output_issues"] = output_check.errors

                # 5. 审计日志（用量与耗时优先取 Agent 的出参 stats —— A38）
                try:
                    audit = AuditLogger()
                    provider_name = getattr(agent.provider, "name", "unknown") if agent.provider else "mock"
                    # 金额：**显式区分 None（价格未标定）与 0.0**。此前
                    # `call_stats.get("cost_usd") or result.get("cost_usd", 0.0) or 0.0`
                    # 把 None 静默变成 0，审计里就成了"这次没花钱"（用户质疑的误导）。
                    if call_stats.get("cost_usd") is not None:
                        audit_cost = call_stats.get("cost_usd")
                    elif result.get("cost_usd") is not None:
                        audit_cost = result.get("cost_usd")
                    else:
                        audit_cost = None
                    cost_unknown = bool(call_stats.get("cost_unknown")
                                        or result.get("cost_unknown")
                                        or audit_cost is None)
                    await audit.log(
                        session_id=session_id,
                        agent_name=agent_name,
                        provider_name=provider_name,
                        model=call_stats.get("model_used") or agent._get_model() or "unknown",
                        action="execute",
                        duration_ms=call_stats.get("elapsed_ms", result.get("elapsed_ms", 0)),
                        tokens_used=call_stats.get("tokens_used") or result.get("tokens_used", 0) or 0,
                        cost_usd=audit_cost,
                        cost_unknown=cost_unknown,
                        status="ok" if "error" not in result else "failed",
                        error=result.get("error", ""),
                        tenant_id=session.get("tenant_id", ""),  # 审计修复：租户隔离
                    )
                    # 产物侧也留痕：界面/批量报表据此显示"未标定"而不是 $0.0000
                    result["cost_unknown"] = cost_unknown
                except Exception as e:
                    _engine_logger.warning("审计日志写入失败: %s", e, exc_info=True)

                # 6. 更新 artifacts
                await self._update_artifacts(session, agent_name, result)

                # 6a. 提示词阶段门禁（A70-A78）：体检（$0）+ 审美审核优化（LLM）
                # 用户："未有明确约束每一张该有的提示词" + "让提示词更接近大众商品图审美"。
                # 放在 artifacts 更新之后：审核优化员读的是落盘后的 `artifacts.prompts`；
                # 三处"生成提示词"的入口（协调者邀请 / 审查 retry / 人工 retry）都汇到这里，
                # 保证不会绕过。
                if agent_name == "提示词生成员" and not result.get("error"):
                    if await self._review_prompts(session, turn):
                        await self.sessions.update(session_id, session)
                        return session

                # 6b. 进度落盘（B3）：每个 Agent 步骤后写一次**轻量快照**，崩溃/强停最多丢
                # 当前这一步（此前只在人工暂停/终态/压缩前落盘，实测强停后丢了整段轨迹）。
                # slim 剔掉上传图 base64（3.5MB→几十 KB），完整快照见 update() 的默认路径。
                await self.sessions.update(session_id, session, slim=True)

                # 5. 交叉验证：审查员 vs 分析员 一致性检查
                if agent_name == "审查员":
                    analysis = session["artifacts"].get("analysis", {})
                    review_category = result.get("_implied_category", "")
                    analysis_category = analysis.get("category", "")
                    if review_category and analysis_category and review_category != analysis_category:
                        # 审查员看到的品类与分析结果不一致 → 幻觉风险
                        cross_check_msg = {
                            "id": uuid.uuid4().hex[:12],
                            "turn": turn,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "role": "system",
                            "sender": "系统",
                            "action": "error",
                            "content": {
                                "cross_validation_fail": True,
                                "analysis_category": analysis_category,
                                "reviewer_category": review_category,
                                "message": f"审查员判定的品类 ({review_category}) 与分析结果 ({analysis_category}) 不一致，可能存在幻觉",
                            },
                        }
                        session["messages"].append(cross_check_msg)
                        if self.broadcaster:
                            await self.broadcaster.broadcast(session_id, cross_check_msg)

                # 6. 审查重试 + Human-in-the-loop + 多审查聚合
                if agent_name == "审查员" and mode in ("debate", "vote"):
                    reviews = session.setdefault("_multi_reviews", [])
                    reviews.append(result)
                    result["_multi_index"] = len(reviews)  # 标记是第几个审查
                if agent_name == "审查员":
                    verdict = result.get("verdict")
                    review_score = result.get("overall_score")
                    score_is_number = (isinstance(review_score, (int, float))
                                       and not isinstance(review_score, bool))
                    needs_human = bool(result.get("needs_human_review"))
                    blocked_reason = ""

                    # A33/A37：审查"没完成/看不懂"时**绝不放行**。
                    # 此前 `result.get("verdict", "pass")` 让围栏 JSON 解析失败的审查
                    # 静默按通过处理（既不重试也不转人工），质量门禁形同虚设。
                    if result.get("error"):
                        needs_human = True
                        blocked_reason = f"审查未完成：{str(result.get('error'))[:200]}"
                    elif verdict not in ("pass", "retry", "fail"):
                        needs_human = True
                        blocked_reason = ("审查结果无法解析（缺少合法 verdict）："
                                          f"{str(result.get('text') or result)[:160]}")
                        self._record_error(session, agent_name, blocked_reason, kind="agent")
                    elif verdict == "retry" and not score_is_number:
                        needs_human = True
                        blocked_reason = "审查判定 retry 但未给出有效分数，无法自动判定"
                    elif verdict == "fail":
                        # verdict=fail 表示"商品关键特征错误/不可接受"，不能静默继续跑完
                        needs_human = True
                        blocked_reason = "审查判定 fail（关键特征错误或不可接受），需人工处置"

                    if needs_human:
                        # 人工审查暂停
                        session["status"] = "waiting_human"
                        score_text = f"评分 {review_score}/100" if score_is_number else "未给出分数"
                        hitl_msg = {
                            "id": uuid.uuid4().hex[:12],
                            "turn": turn,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "role": "system",
                            "sender": "系统",
                            "action": "respond",
                            "content": {
                                "hitl": "human_review_needed",
                                "session_id": session_id,
                                "review": result,
                                "blocked_reason": blocked_reason,
                                "message": f"审查{score_text}，需要人工判定。请回复 approve/retry/reject"
                                           + (f"｜{blocked_reason}" if blocked_reason else ""),
                            },
                        }
                        session["messages"].append(hitl_msg)
                        if self.broadcaster:
                            await self.broadcaster.broadcast(session_id, hitl_msg)
                        await self.sessions.update(session_id, session)
                        return session  # 暂停，等待人工决策

                    if verdict == "retry" and review_score < 75:
                        feedback_msg = {
                            "id": uuid.uuid4().hex[:12],
                            "turn": turn,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "role": "system",
                            "sender": "系统",
                            "action": "respond",
                            "content": {
                                "feedback": f"审查未通过（{review_score}/100），重新生成提示词和图片。问题：{result.get('top_issues', [])}"
                            },
                        }
                        session["messages"].append(feedback_msg)
                        if self.broadcaster:
                            await self.broadcaster.broadcast(session_id, feedback_msg)

                        # 直接重新执行 提示词生成 + 生图（不依赖 Coordinator）
                        # 审查意见作为**审美反馈**回流给审核优化员：图丑 → 审查给意见 →
                        # 更好的提示词 → 重出（改前只是把 top_issues 原样丢给提示词生成员）
                        _, halted = await self._prompt_stage(
                            session, "基于审查反馈重新生成提示词", turn,
                            aesthetic_feedback=self._review_feedback(result))
                        if halted:
                            await self.sessions.update(session_id, session)
                            return session
                        ig_agent = self.registry.get("生图员")
                        if ig_agent:
                            ig_result = await ig_agent.execute("基于新的提示词重新生成图片", session)
                            session["messages"].append({
                                "id": uuid.uuid4().hex[:12], "turn": turn,
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                                "role": "agent", "sender": "生图员",
                                "action": "respond", "content": ig_result,
                            })
                            await self._update_artifacts(session, "生图员", ig_result)

                        # 让 Coordinator 跳过到审查步骤（会话级状态，并发安全）
                        coordinator._state(session)["workflow_index"] = 5  # 审查员之前的步骤

                # 质量止损（用户反馈）：审查/合规**连续** N 次未通过即停。
                # 真实会话里协调者会因合规不过而反复重新生成图片（每轮约 ¥1），
                # 而预算只告警不拦截、max_turns=15 又太远 → 这里给一条明确的止损线。
                halt_reason = self._quality_guard(session, agent_name, result)
                if halt_reason:
                    session["status"] = RunStatus.FAILED.value
                    halt_msg = {
                        "id": uuid.uuid4().hex[:12],
                        "turn": turn,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "role": "system",
                        "sender": "系统",
                        "action": "error",
                        "content": {"aborted": "quality_guard", "message": halt_reason},
                    }
                    session["messages"].append(halt_msg)
                    if self.broadcaster:
                        await self.broadcaster.broadcast(session_id, halt_msg)
                    self._record_error(session, "质量门禁", halt_reason, kind="abort")
                    await self.sessions.update(session_id, session)
                    return session
        # 多审查聚合（debate/vote 模式）
        if mode in ("debate", "vote"):
            reviews = session.get("_multi_reviews", [])
            if len(reviews) >= 2 and mode == "debate":
                combined = dict(reviews[0])
                r2 = reviews[1]
                combined["overall_score"] = round((reviews[0].get("overall_score", 0) + r2.get("overall_score", 0)) / 2, 1)
                combined["top_issues"] = reviews[0].get("top_issues", []) + r2.get("top_issues", [])
                combined["verdict"] = "pass" if combined["overall_score"] >= 75 else "retry"
                session["artifacts"]["review"] = combined
            elif len(reviews) >= 3 and mode == "vote":
                from collections import Counter
                scores = [r.get("overall_score", 0) for r in reviews]
                verdicts = [r.get("verdict", "fail") for r in reviews]
                avg_score = round(sum(scores) / len(scores), 1)
                majority_verdict = Counter(verdicts).most_common(1)[0][0]
                combined = dict(reviews[0])
                combined["overall_score"] = avg_score
                combined["verdict"] = majority_verdict
                combined["dimension_scores"] = {
                    k: round(sum(r.get("dimension_scores", {}).get(k, 0) for r in reviews) / len(reviews), 1)
                    for k in reviews[0].get("dimension_scores", {})
                }
                session["artifacts"]["review"] = combined
            session.pop("_multi_reviews", None)

        # ── P3: A/B 测试模式 — 提示词生成员多版本并行对比 ──
        if mode == "ab_test":
            try:
                from src.harness.ab_testing import (
                    ABTestConfig,
                    ABTestRunner,
                    ABVariant,
                    make_model_variants,
                )
                ab_config = session.get("task", {}).get("ab_config")

                if ab_config and isinstance(ab_config, dict):
                    ab_variants = [
                        ABVariant(**v) if isinstance(v, dict) else v
                        for v in ab_config.get("variants", [])
                    ]
                    config = ABTestConfig(
                        agent_name=ab_config.get("agent_name", "提示词生成员"),
                        variants=ab_variants,
                        task_brief=ab_config.get("task_brief", "基于分析结果生成提示词"),
                        review_count=ab_config.get("review_count", 3),
                        scoring_method=ab_config.get("scoring_method", "multi_reviewer"),
                    )
                else:
                    # 默认：3 个模型变体对比
                    config = make_model_variants(
                        "提示词生成员",
                        [
                            ("v_gpt4o", "gpt-4o"),
                            ("v_deepseek", "deepseek-chat"),
                            ("v_qwen", "qwen-max"),
                        ],
                    )
                    config.review_count = 3

                runner = ABTestRunner(self.registry, session)
                ab_result = await runner.run(config)
                session["artifacts"]["ab_test"] = {
                    "agent_name": config.agent_name,
                    "winner": ab_result.winner.variant_id if ab_result.winner else "none",
                    "winner_score": ab_result.winner.avg_score if ab_result.winner else 0,
                    "runner_up": ab_result.runner_up.variant_id if ab_result.runner_up else "none",
                    "ranking": [
                        {"id": vr.variant_id, "label": vr.label, "score": vr.avg_score,
                         "cost_usd": vr.cost_usd, "cost_unknown": vr.cost_unknown,
                         "elapsed_ms": vr.elapsed_ms}
                        for vr in ab_result.ranking
                    ],
                    "total_cost_usd": ab_result.total_cost_usd,
                    "cost_unknown_calls": ab_result.cost_unknown_calls,
                }

                ab_msg = {
                    "id": uuid.uuid4().hex[:12],
                    "turn": session.get("turn_count", 0) + 1,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "role": "system",
                    "sender": "A/B 测试",
                    "action": "respond",
                    "content": session["artifacts"]["ab_test"],
                }
                session["messages"].append(ab_msg)
                if self.broadcaster:
                    await self.broadcaster.broadcast(session_id, ab_msg)
            except Exception as e:
                _engine_logger.warning("A/B 测试执行失败: %s", e, exc_info=True)

        if not completed:
            session["status"] = RunStatus.FAILED.value
            session["messages"].append({
                "id": uuid.uuid4().hex[:12],
                "turn": max_turns,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "role": "system",
                "sender": "系统",
                "action": "error",
                "content": {"error": f"超过最大轮次 ({max_turns})，任务中止"},
            })
            self._record_error(session, "系统", f"超过最大轮次 ({max_turns})，任务中止", kind="abort")

        # 成功后记录到记忆库
        if session["status"] == RunStatus.COMPLETED.value:
            try:
                from src.harness.agent_memory import AgentMemory
                memory = AgentMemory()
                artifacts = session.get("artifacts", {})
                analysis = artifacts.get("analysis", {})
                await memory.remember(
                    session_id=session_id,
                    category=analysis.get("category", ""),
                    analysis=analysis,
                    prompts=artifacts.get("prompts", {}),
                    review=artifacts.get("review", {}),
                    compliance=artifacts.get("compliance"),
                    tenant_id=session.get("tenant_id", ""),  # 审计修复：写入侧补租户（召回侧已透传）
                )
            except Exception as e:
                _engine_logger.warning("成功会话记忆记录失败: %s", e, exc_info=True)

        await self.sessions.update(session_id, session)
        return session

    async def resume_after_hitl(self, session: SessionState, decision: str) -> SessionState:
        """人工决策后恢复会话执行

        decision: "approve" | "retry" | "reject"
        """
        session_id = session["session_id"]
        start_index: Optional[int] = None  # retry 分支会设为审查员索引

        # 人工已接管：清零「连续失败即停」计数（否则上一次的累计会立刻掐断新尝试）
        session[self._QUALITY_FAILURE_KEY] = 0
        # 提示词打回重写的轮次也清零：人工介入后应当允许再走一轮完整的体检/审核
        session[self._PROMPT_REVIEW_ROUND_KEY] = 0

        if decision == "approve":
            # 修改审查结果为 pass —— **只在确实做过审查时**：改前无论有没有审查产物都会写出
            # `artifacts.review = {"verdict": "pass"}`，身份门禁 approve 时凭空造出一条假审查记录
            # （协调者与前端会据此以为图已经审过了）。
            existing_review = session["artifacts"].get("review")
            if isinstance(existing_review, dict) and existing_review.get("verdict"):
                review = dict(existing_review)
                review["verdict"] = "pass"
                review["needs_human_review"] = False
                session["artifacts"]["review"] = review
            session["status"] = RunStatus.RUNNING.value
            pending = self._pending_hitl(session)
            note = "人工确认提示词，按现版出图" if pending == "prompt_review_needed" \
                else "人工审查通过，接受当前结果"
            session["messages"].append({
                "id": uuid.uuid4().hex[:12],
                "turn": session.get("turn_count", 0),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "role": "system",
                "sender": "人工审查",
                "action": "respond",
                "content": {"decision": "approve", "message": note},
            })

        elif decision == "retry":
            session["status"] = RunStatus.RUNNING.value
            review = session["artifacts"].get("review", {})
            feedback = f"人工审查判定重试。问题：{review.get('top_issues', [])}"

            session["messages"].append({
                "id": uuid.uuid4().hex[:12],
                "turn": session.get("turn_count", 0),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "role": "system",
                "sender": "人工审查",
                "action": "respond",
                "content": {"decision": "retry", "message": "回到提示词生成环节"},
            })

            # 直接重新生成提示词 + 图片（跳回步骤，不重跑全部）
            # 人工 retry 的原因通常是"图不好看"：把上一轮审查的审美意见一并回流
            _, halted = await self._prompt_stage(
                session, feedback, session.get("turn_count", 0) + 1,
                aesthetic_feedback=self._review_feedback(review))
            ig_agent = self.registry.get("生图员")
            if halted:
                await self.sessions.update(session_id, session)
                return session
            if ig_agent:
                ig_result = await ig_agent.execute("基于新提示词重新生成图片", session)
                session["messages"].append({
                    "id": uuid.uuid4().hex[:12],
                    "turn": session.get("turn_count", 0) + 1,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "role": "agent", "sender": "生图员",
                    "action": "respond", "content": ig_result,
                })
                await self._update_artifacts(session, "生图员", ig_result)

            # 恢复引擎继续（审计修复：跳转索引经 run(start_index=...) 传入，
            # 在 reset() 之后生效——此前先置 _workflow_index=5 再 run()，
            # 被 run() 开头 reset() 清零，导致从分析员全量重跑）
            start_index = 5  # 审查员步骤（提示词/生图已在上面直接重跑）

        elif decision == "reject":
            session["status"] = RunStatus.FAILED.value
            session["messages"].append({
                "id": uuid.uuid4().hex[:12],
                "turn": session.get("turn_count", 0),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "role": "system",
                "sender": "人工审查",
                "action": "respond",
                "content": {"decision": "reject", "message": "人工审查拒绝，任务终止"},
            })
            self._record_error(session, "人工审查", "人工审查拒绝，任务终止", kind="human_reject")
            await self.sessions.update(session_id, session)
            return session

        # approve/retry → 继续运行（retry 带 start_index 跳转到审查员）
        return await self.run(session, start_index=start_index)

    # ── 失败留痕（第三轮审计 B3-25） ──

    _MAX_ERROR_HISTORY = 20
    _QUALITY_FAILURE_KEY = "_quality_failures"
    # 提示词打回重写的轮次计数（会话级；写在审核产物里会被下一轮结果整键覆盖）
    _PROMPT_REVIEW_ROUND_KEY = "_prompt_review_rounds"

    @staticmethod
    def _pending_hitl(session: SessionState) -> str:
        """最近一条 HITL 消息的类型（approve 时据此给出正确反馈文案）"""
        for message in reversed(session.get("messages") or []):
            content = message.get("content") if isinstance(message, dict) else None
            if isinstance(content, dict) and content.get("hitl"):
                return str(content["hitl"])
        return ""

    @staticmethod
    def _review_feedback(result) -> str:
        """把审查结论整理成"改提示词要解决什么"（喂给提示词生成员与审核优化员）"""
        if not isinstance(result, dict):
            return ""
        parts: list[str] = []
        issues = result.get("top_issues") or []
        if issues:
            parts.append("问题：" + "；".join(str(item) for item in issues[:6]))
        for key in ("fix_direction", "fix_suggestions"):
            values = result.get(key) or []
            if values:
                parts.append("修改方向：" + "；".join(str(item) for item in values[:6]))
        if result.get("overall_score") is not None:
            parts.insert(0, f"上一轮审查得分 {result.get('overall_score')}")
        return "\n".join(parts)

    async def _prompt_stage(self, session: SessionState, brief: str, turn: int, *,
                            aesthetic_feedback: str = "") -> tuple[dict, bool]:
        """生成提示词 + 体检 + 审美审核（两处"直连重跑"入口共用）

        改前这两处（审查 retry、人工 retry）各自重复一份"调用提示词生成员 → 广播 → 更新产物"
        的代码，且**完全绕过任何把关**；现在统一走这里。

        Returns: `(result, halted)`；`halted=True` 表示已暂停等人工确认提示词。
        """
        session_id = session["session_id"]
        agent = self.registry.get("提示词生成员")
        if agent is None:
            return {"error": "提示词生成员未注册"}, False

        task_brief = brief
        if aesthetic_feedback:
            task_brief += ("\n\n【上一轮成图的审查意见（改提示词时要一并解决）】\n"
                           + str(aesthetic_feedback)[:1200])
        task_brief = _inject_identity_card(session, task_brief, "提示词生成员")
        result = await agent.execute(task_brief, session)
        session["messages"].append({
            "id": uuid.uuid4().hex[:12], "turn": turn,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "role": "agent", "sender": "提示词生成员",
            "action": "error" if isinstance(result, dict) and result.get("error") else "respond",
            "content": result,
        })
        if self.broadcaster:
            await self.broadcaster.broadcast(session_id, session["messages"][-1])
        if isinstance(result, dict) and result.get("error"):
            self._record_error(session, "提示词生成员", result["error"], kind="agent")
            return result, False
        await self._update_artifacts(session, "提示词生成员", result)
        halted = await self._review_prompts(session, turn, aesthetic_feedback=aesthetic_feedback)
        return result, halted

    async def _review_prompts(self, session: SessionState, turn: int, *,
                              aesthetic_feedback: str = "") -> bool:
        """提示词体检（$0，恒定）+ 审美审核优化（LLM，可开关）

        用户 2026-09-18 的初衷是**审美**而不是"抓违规"：
        "让生成图提示词更接近大众的商品图审美，免得跑出一些符合基本要求但是图片审美完全不合格的图片"。
        因此分工是：**可枚举的错 → 代码体检**（免费、确定性、一票否决）；
        **审美判断与改写 → LLM**（低于阈值给改写稿，改写稿要过体检才落地）。

        门禁语义：
        - `require_prompt_review=false` → 只记体检结果；
        - 同一版提示词（digest 幂等）已审过且通过 → 不重复花钱；
        - 审核员报错/无 provider/Mock → 记 `skipped|error` 并继续出图（**不静默当通过**）；
        - 仍不达标且 `require_prompt_confirm=true` → 暂停等人工（默认关，不制造摩擦）。

        Returns: True 表示已暂停（调用方应立即 return session）。
        """
        from src.core.config import chat_settings
        from src.harness.prompt_lint import lint_prompts, summarize_lint
        from src.harness.prompt_review import (
            apply_prompt_patches,
            normalize_prompt_review,
            review_summary,
            select_patches,
        )
        from src.harness.set_plan import finalize_prompts, normalize_set_plan, set_plan_lines

        artifacts = session.get("artifacts") or {}
        task = session.get("task") or {}
        platform = task.get("platform", "")
        identity = artifacts.get("product_identity") or {}
        palette = identity.get("brand_palette") if isinstance(identity, dict) else {}
        prompts = artifacts.get("prompts") or {}
        subset = task.get("slot_override") or None
        plan = normalize_set_plan(prompts, platform=platform, palette=palette,
                                 slot_override=platform_slot_plan(platform, subset),
                                 only_slots=subset)
        if not plan:
            return False   # 没有套图编排（旧路径）：没有可审的东西

        settings = chat_settings()
        # 逐槽位风格档案（用户「风格词库」）：体检要用它判"是否逐字照抄档案"，
        # 所以检索一次、体检与后续复用（异常不阻断出图）。
        # **必须带会话锁**：体检/改写要按生成员实际用的那**一套**风格词判，而不是重新挑一套
        # （用户 2026-09-20：一轮会话只用一套风格词）。
        style_selection: dict = {}
        try:
            from src.harness.style_library import select_by_slot, session_style_lock
            style_selection = select_by_slot(plan.get("slots") or [], analysis=artifacts.get("analysis"),
                                             platform=platform,
                                             tenant_id=str(session.get("tenant_id") or "") or None,
                                             locked_entry_id=session_style_lock(session))
            # 采纳计数（best-effort，绝不影响出图）：卡片上的"被 N 次会话采用"
            try:
                from src.harness.style_store import StyleStore
                StyleStore().adopt([entry.get("id") for entry in style_selection.get("entries") or []])
            except Exception:  # noqa: BLE001
                pass
        except Exception as exc:  # noqa: BLE001 — 档案库不可用不能挡住流程
            _engine_logger.warning("风格档案检索失败 (session=%s): %s",
                                   session.get("session_id"), exc, exc_info=True)
        lint = lint_prompts(plan, platform=platform, identity=identity,
                            style_entries=style_selection)
        session["artifacts"]["prompt_lint"] = {
            "checked": lint["checked"], "expected": lint["expected"],
            "digest": lint["digest"], "errors": lint["errors"], "warnings": lint["warnings"],
            "findings": lint["findings"],
        }
        await self._broadcast_system(session, turn, {
            "prompt_lint": session["artifacts"]["prompt_lint"],
            "message": summarize_lint(lint) + ("" if lint["errors"]
                                               else "（无硬伤，进入审美审核）"),
        })

        if not bool(settings.get("require_prompt_review", True)):
            session["artifacts"]["prompt_review"] = {
                "status": "disabled", "digest": lint["digest"], "verdict": "skipped",
                "threshold": self._aesthetic_threshold(settings), "scores": {},
                "revised_slots": [], "refine_rejected": [],
                "message": "提示词审核已在设置页关闭（体检结果仍保留）",
            }
            return await self._after_prompt_review(session, turn, lint, {}, settings)

        previous = artifacts.get("prompt_review") or {}
        if (previous.get("digest") == lint["digest"]
                and previous.get("verdict") in ("pass", "revised")):
            return False   # 同一版提示词审过了，别重复花钱

        threshold = self._aesthetic_threshold(settings)
        reviewer = self.registry.get("提示词审核优化员")
        base_review: dict = {
            "digest": lint["digest"], "threshold": threshold, "round": 1,
            "scores": {}, "revised_slots": [], "refine_rejected": [],
            "lint_errors": len(lint["errors"]),
        }
        if reviewer is None or reviewer.provider is None:
            # 审核员不可用**不阻塞出图**：体检仍然生效，且硬伤照旧打回重写
            session["artifacts"]["prompt_review"] = {
                **base_review, "status": "skipped", "verdict": "skipped",
                "message": "提示词审核优化员不可用（未注册或没有文本模型），已跳过；体检结果仍有效",
            }
            await self._broadcast_system(session, turn, session["artifacts"]["prompt_review"])
            return await self._after_prompt_review(session, turn, lint, {}, settings)

        try:
            raw = await reviewer.execute(
                f"按下述规范审核本套逐张提示词（审美阈值 {threshold}）", session)
        except Exception as exc:  # noqa: BLE001 — 审核失败不能阻塞出图
            _engine_logger.warning("提示词审核失败 (session=%s): %s",
                                   session.get("session_id"), exc, exc_info=True)
            raw = {"error": f"{type(exc).__name__}: {exc}"}

        if isinstance(raw, dict) and raw.get("error"):
            session["artifacts"]["prompt_review"] = {
                **base_review, "status": "error", "verdict": "skipped",
                "message": f"提示词审核失败（已跳过，体检仍有效）：{str(raw['error'])[:200]}",
            }
            await self._broadcast_system(session, turn, session["artifacts"]["prompt_review"])
            return await self._after_prompt_review(session, turn, lint, {}, settings)

        review = raw if raw.get("scores") is not None else normalize_prompt_review(raw)
        scores = review.get("scores") or {}
        base_review["scores"] = scores
        base_review["overall_score"] = review.get("overall_score")
        base_review["reviewer_notes"] = review.get("notes") or []

        patches, skipped_patches = select_patches(plan, review, threshold=threshold)
        applied: list[dict] = []
        rejected: list[dict] = []
        if patches:
            outcome = apply_prompt_patches(plan, patches, platform=platform, identity=identity,
                                           style_entries=style_selection)
            applied = outcome.get("applied") or []
            rejected = outcome.get("rejected") or []
            if applied:
                new_plan = outcome["plan"]
                session["artifacts"]["set_plan"] = new_plan
                session["artifacts"]["prompts"] = finalize_prompts(
                    {**prompts, "set_plan": new_plan}, platform, palette=palette)
                # 给用户看"第几张被改写成了什么"
                session["artifacts"]["prompts"]["prompt_plan"] = [
                    {"number": slot.get("number"), "slot_id": slot.get("slot_id"),
                     "role": slot.get("role"), "usage": slot.get("usage"),
                     "kind": slot.get("kind"), "intent": slot.get("intent"),
                     "prompt": slot.get("prompt"),
                     "aesthetic_score": scores.get(slot.get("slot_id"))}
                    for slot in new_plan.get("slots", [])]
                session["artifacts"]["prompts"]["message"] = (
                    "🎨 审美审核后定稿（" + str(len(applied)) + " 张被改写）\n"
                    + "\n".join(set_plan_lines(new_plan)))

        verdict = "revised" if applied else ("pass" if review.get("verdict") == "pass"
                                             else "revise")
        summary = review_summary(review, threshold=threshold)
        payload = {
            **base_review, "status": "reviewed", "verdict": verdict,
            "revised_slots": [item["slot_id"] for item in applied],
            "refine_rejected": rejected or skipped_patches,
            "message": ("🎨 提示词审核（" + str(len(plan.get("slots") or [])) + " 张）："
                        + summary
                        + (f"；已改写 {len(applied)} 张" if applied else "")
                        + (f"；{len(rejected)} 张改写被体检拦下" if rejected else "")),
        }
        session["artifacts"]["prompt_review"] = payload
        await self._broadcast_system(session, turn, payload)
        return await self._after_prompt_review(session, turn, lint, scores, settings)

    async def _after_prompt_review(self, session: SessionState, turn: int, lint: dict,
                                   scores: dict, settings: dict) -> bool:
        """审核之后的收尾：硬伤打回重写（≤N 轮）→ 仍不达标时按开关决定是否暂停

        与"审核员是否可用"解耦：体检是**确定性**的，它发现硬伤就该打回，
        哪怕审核员这一轮没跑（不可用/报错/被关掉）。

        轮次计数放在**会话级 key**（而不是审核产物的字段里）：产物每轮都会被新的审核结果
        整键替换，写在里面的计数会丢 → 打回重写可能无限递归（真实踩到）。

        Returns: True 表示已暂停（调用方应立即 return session）。
        """
        max_rounds = self._prompt_review_rounds(settings)
        used = int(session.get(self._PROMPT_REVIEW_ROUND_KEY, 0) or 0)
        if lint.get("errors") and max_rounds > 0 and used < max_rounds:
            findings = "；".join(lint["errors"][:8])
            session[self._PROMPT_REVIEW_ROUND_KEY] = used + 1
            await self._broadcast_system(session, turn, {
                "message": f"↩️ 提示词体检仍有 {len(lint['errors'])} 项硬伤，"
                           f"请提示词生成员按意见重写（第 {used + 1}/{max_rounds} 轮）：{findings}",
            })
            await self._prompt_stage(session, "请按提示词体检意见重写这一套提示词：" + findings,
                                     turn)
            return False

        # 仍然不达标（低分或硬伤未清）→ 默认继续出图；开了人工确认则暂停
        threshold = self._aesthetic_threshold(settings)
        low_scores = [slot_id for slot_id, score in (scores or {}).items()
                      if float(score) < threshold]
        if (low_scores or lint.get("errors")) and bool(settings.get("require_prompt_confirm", False)):
            return await self._prompt_confirm_gate(session, turn, low_scores, lint)
        return False

    async def _prompt_confirm_gate(self, session: SessionState, turn: int,
                                   low_scores: list[str], lint: dict) -> bool:
        """提示词人工确认门禁（默认关）：审美分不达标或仍有硬伤时暂停等用户决定"""
        session["status"] = "waiting_human"
        blocked = (f"提示词仍未达标（低于阈值 {len(low_scores)} 张：{'、'.join(low_scores[:6])}；"
                   f"体检硬伤 {len(lint.get('errors') or [])} 项）。"
                   "按当前版本出图会有审美或合规风险。")
        hitl_msg = {
            "id": uuid.uuid4().hex[:12], "turn": turn,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "role": "system", "sender": "系统", "action": "respond",
            "content": {
                "hitl": "prompt_review_needed",
                "session_id": session["session_id"],
                "prompt_review": session["artifacts"].get("prompt_review"),
                "prompt_lint": session["artifacts"].get("prompt_lint"),
                "blocked_reason": blocked,
                "message": (f"{blocked}请回复 approve（按现有提示词出图）或 "
                            "retry（重新生成提示词）"),
            },
        }
        session["messages"].append(hitl_msg)
        if self.broadcaster:
            await self.broadcaster.broadcast(session["session_id"], hitl_msg)
        return True

    async def _broadcast_system(self, session: SessionState, turn: int, content: dict) -> None:
        """播报一条系统消息（体检/审核结论对用户与协调者都要可见）"""
        message = {
            "id": uuid.uuid4().hex[:12], "turn": turn,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "role": "system", "sender": "系统",
            "action": "error" if (content.get("errors") or content.get("blocked_reason"))
            else "respond",
            "content": content,
        }
        session["messages"].append(message)
        if self.broadcaster:
            await self.broadcaster.broadcast(session["session_id"], message)

    @staticmethod
    def _aesthetic_threshold(settings: dict) -> float:
        try:
            value = float(settings.get("prompt_aesthetic_threshold", 85))
        except (TypeError, ValueError):
            value = 85.0
        return max(0.0, min(100.0, value))

    @staticmethod
    def _prompt_review_rounds(settings: dict) -> int:
        try:
            value = int(settings.get("prompt_review_max_rounds", 1))
        except (TypeError, ValueError):
            value = 1
        return max(0, min(3, value))

    async def _identity_gate(self, session: SessionState, result: dict, turn: int) -> bool:
        """商品身份门禁：播报身份卡；未确认时（可配）暂停等用户确认

        用户反馈："产品分析员根本没有识别到我喂的图是什么品牌，商品名是什么都没强调或者提醒，
        这是一个很大的缺失"。身份卡是全链路的事实基准 —— 没确认就让模型出图，等于让它编
        （实测把 `DEFOEBUENA®` 编成了 `NUTRIVA®`）。

        Returns: True 表示已暂停（调用方应立即 return session）。
        """
        identity = result.get("product_identity")
        if not isinstance(identity, dict):
            # 分析员没按 schema 给身份块（自定义/老 Agent）→ 记 source=none：
            # 提醒、但不拦流程（连可确认的内容都没有，拦下来也问不出东西）
            identity = normalize_identity(result, source="none")
        session["artifacts"]["product_identity"] = identity
        confirmed = is_confirmed(identity)

        notice = {
            "id": uuid.uuid4().hex[:12], "turn": turn,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "role": "system", "sender": "系统",
            "action": "respond" if confirmed else "error",
            "content": {
                "product_identity": identity,
                "identity_unconfirmed": not confirmed,
                "message": identity_summary(identity),
            },
        }
        session["messages"].append(notice)
        if self.broadcaster:
            await self.broadcaster.broadcast(session["session_id"], notice)
        if confirmed:
            return False

        self._record_error(session, "商品分析员", identity_summary(identity), kind="identity")
        # 只有**真实视觉识别失败**才值得停下问人：
        # - `source == mock`：演示数据本来就不代表这件商品（界面上已明确标注），
        #   拦下来问"品牌是什么"没有意义，而且会让 Mock 模式的每一条会话都卡住；
        # - `source == none`：连身份块都没有（老数据/自定义 Agent），同样没有可确认的内容。
        if str(identity.get("source") or "none") in ("mock", "none"):
            return False
        if not bool(chat_settings().get("require_identity_confirm", True)):
            return False

        session["status"] = "waiting_human"
        blocked = ("未能确认商品品牌与商品名（缺少："
                   + ("、".join(identity.get("missing") or ["品牌", "商品名"]))
                   + "）。确认前不会让模型生成任何品牌/包装文字。")
        hitl_msg = {
            "id": uuid.uuid4().hex[:12], "turn": turn,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "role": "system", "sender": "系统", "action": "respond",
            "content": {
                "hitl": "identity_unconfirmed",
                "session_id": session["session_id"],
                "product_identity": identity,
                "blocked_reason": blocked,
                "message": (f"{blocked}请回复 approve（按现有信息继续，画面文字将做干净虚化，"
                            "由设计师后期贴图）或 retry（重新分析商品图）"),
            },
        }
        session["messages"].append(hitl_msg)
        if self.broadcaster:
            await self.broadcaster.broadcast(session["session_id"], hitl_msg)
        return True

    async def _attach_image_reports(self, session: SessionState, result: dict, turn: int):
        """生图完成后：本地体检（客观指标）+ 套图覆盖度，写进产物并播报问题

        这些指标不花钱、确定性，能直接识别这次实测的两类退化：
        - 退化成**纯文生图**（商品身份丢失）→ `identity_lost`；
        - 退化成**纯图生图**（复制原图没重绘）→ `is_near_copy`。
        """
        images = result.get("images") or []
        try:
            from src.harness.image_quality import decode_reference, inspect_images, summarize
            from src.harness.reference_images import reference_sources

            options = image_options()
            reference = decode_reference(reference_sources(session))
            # 白底预期按**平台的背景策略**判定（不再猜背景字符串）：用户实测质疑"很多商品图
            # 都不是白底的啊？"——`design_allowed` 平台本来就可以出设计底，若按"背景必须白"
            # 体检，协调者会白跑一轮重生成（A59 踩过）。
            subset = (session.get("task") or {}).get("slot_override") or None
            plan_for_white = normalize_set_plan(
                session["artifacts"].get("prompts") or {},
                platform=(session.get("task") or {}).get("platform", ""),
                slot_override=platform_slot_plan(
                    (session.get("task") or {}).get("platform", ""), subset),
                only_slots=subset)
            expect_white = _white_expectations(plan_for_white)
            reports, notes = await inspect_images(images, thresholds=options.get("quality"),
                                                  reference=reference, expect_white=expect_white)
        except Exception as exc:  # noqa: BLE001 — 体检失败不能影响出图主流程
            _engine_logger.warning("生成图体检失败 (session=%s): %s",
                                   session.get("session_id"), exc, exc_info=True)
            return

        # 按索引回写每张图（inspect_images 与入参同序，失败项不进 reports）
        for report in reports:
            index = report.get("index")
            if isinstance(index, int) and 0 <= index < len(images):
                images[index]["quality"] = report
        summary = summarize(reports)
        quality_payload = {"count": summary["count"], "white_bg_ok": summary["white_bg_ok"],
                           "watermark_free": summary["watermark_free"],
                           "identity_lost": summary["identity_lost"],
                           "near_copy": summary["near_copy"],
                           "watermark_skipped": summary.get("watermark_skipped") or [],
                           "identity_skipped": summary.get("identity_skipped") or [],
                           "white_bg_skipped": summary.get("white_bg_skipped") or [],
                           "issues": summary["issues"], "notes": notes}
        session["artifacts"]["quality_report"] = quality_payload
        result["quality_report"] = quality_payload

        # 套图覆盖度：交付物必须是"一整套"，缺哪个槽位要让协调者/用户看见
        plan = normalize_set_plan(session["artifacts"].get("prompts") or {},
                                  platform=(session.get("task") or {}).get("platform", ""),
                                  slot_override=platform_slot_plan(
                                      (session.get("task") or {}).get("platform", "")))
        coverage = set_plan_coverage(plan, images)
        if plan:
            session["artifacts"]["set_plan"] = plan
            session["artifacts"]["set_plan_coverage"] = coverage

        problems = list(summary["issues"])
        if summary["identity_lost"]:
            problems.insert(0, f"以下图与参考图的商品身份相似度过低（疑似凭文字想象商品）："
                               f"{'、'.join(summary['identity_lost'])}")
        if summary["near_copy"]:
            problems.insert(0, f"以下图与参考图高度相似（疑似直接复制、未按提示词重绘）："
                               f"{'、'.join(summary['near_copy'])}")
        if not coverage["complete"] and coverage["expected"]:
            problems.append(f"套图不完整：应有 {coverage['expected']} 张，"
                            f"缺 {'、'.join(coverage['missing_slots'])}")

        message = ("图一（真实商品图）+ 生成图的**文+图**双条件出图完成，"
                   f"共 {len(images)} 张" if reference else
                   f"出图完成，共 {len(images)} 张（⚠️ 本次没有可用参考图，属纯文生图，"
                   "包装文字已按策略虚化）")
        if problems:
            message += "；本地体检发现 " + str(len(problems)) + " 个问题：" + "；".join(problems[:5])

        report_msg = {
            "id": uuid.uuid4().hex[:12], "turn": turn,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "role": "system", "sender": "系统",
            "action": "error" if problems else "respond",
            "content": {
                "quality_report": quality_payload,
                "set_plan_coverage": coverage,
                "set_plan_summary": set_plan_summary(plan),
                "message": message,
            },
        }
        session["messages"].append(report_msg)
        if self.broadcaster:
            await self.broadcaster.broadcast(session["session_id"], report_msg)

    def _quality_guard(self, session: SessionState, agent_name: str, result: dict) -> str:
        """审查/合规「连续失败 N 次即停」（用户反馈）

        阈值来自 `config/default.yaml → chat.max_consecutive_review_failures`（0 = 关闭）。

        计数规则：
        - 审查员 `verdict ∈ {retry, fail}` 或本次调用报错 → +1；`pass` → 清零；
        - 合规审查员 `passed is False` 或报错 → +1；`passed is True` → 清零；
        - 其它 Agent 不参与；
        - 人工介入（approve/retry）后由 `resume_after_hitl` 清零（人已接管）。

        返回非空字符串表示"应当终止"，内容为可读原因（由调用方写进群聊与 error_history）。
        """
        if agent_name not in ("审查员", "合规审查员"):
            return ""
        limit = chat_settings()["max_consecutive_review_failures"]
        if limit <= 0:
            return ""

        failed = False
        detail = ""
        if isinstance(result, dict) and result.get("error"):
            failed = True
            detail = f"{agent_name}报错：{str(result.get('error'))[:120]}"
        elif agent_name == "审查员":
            verdict = result.get("verdict") if isinstance(result, dict) else None
            if verdict in ("retry", "fail"):
                failed = True
                score = result.get("overall_score")
                detail = f"审查判定 {verdict}（{score if score is not None else '未给分'}）"
        else:
            if isinstance(result, dict) and result.get("passed") is False:
                failed = True
                detail = f"合规未通过（risk_level={result.get('risk_level') or '未知'}）"

        if not failed:
            session[self._QUALITY_FAILURE_KEY] = 0
            return ""

        count = int(session.get(self._QUALITY_FAILURE_KEY, 0) or 0) + 1
        session[self._QUALITY_FAILURE_KEY] = count
        if count < limit:
            return ""
        return (f"审查/合规已连续 {count} 次未通过（阈值 {limit}）：{detail}。"
                f"为避免继续重复生成、消耗额度，本会话已自动停止；"
                f"可在「设置 → 会话策略」调大「连续失败即停」次数，"
                f"或先修改提示词/商品信息后重新发起会话。")

    def _record_error(self, session: SessionState, agent: str, error: str,
                      kind: str = "agent") -> dict:
        """把失败原因写入会话（`error_history` 此前是没人写过的死字段）

        kind: agent（Agent/Provider 报错）/ abort（中止：超轮次、协调者缺失）/
              human_reject（人工拒绝）
        只保留最近 `_MAX_ERROR_HISTORY` 条：长跑会话不无限膨胀。
        """
        entry = {
            "agent": agent,
            "error": str(error)[:500],
            "kind": kind,
            "turn": session.get("turn_count", 0),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        history = session.setdefault("error_history", [])
        history.append(entry)
        if len(history) > self._MAX_ERROR_HISTORY:
            del history[:len(history) - self._MAX_ERROR_HISTORY]
        return entry

    async def _update_artifacts(self, session: SessionState, agent_name: str, result: dict):
        """将 Agent 输出更新到 artifacts"""
        mapping = {
            "商品分析员": "analysis",
            "品类专项分析员": "analysis",  # 合并到 analysis
            "提示词生成员": "prompts",
            "生图员": "images",
            "图像后处理员": "images",
            "审查员": "review",
            "合规审查员": "compliance",
        }
        key = mapping.get(agent_name)
        if key is None:
            return

        # 失败结果不覆盖已成功的产物（第二轮审计修复：此前 Agent 报错时会把
        # artifacts["images"] 等已有产出整键替换成 {"error": ...}，静默销毁产物；
        # 错误本身已通过群聊消息与审计可见，这里只需保住既有产出）
        if isinstance(result, dict) and result.get("error"):
            return

        if key == "images" and "images" in result:
            session["artifacts"]["images"] = _merge_images(
                session["artifacts"].get("images"), result["images"])
            await self._export_images(session)   # 生成即落盘（用户反馈：没法设置导出路径）
        elif key == "analysis":
            # 品类专项分析 → 合并到 analysis（补充维度）。
            # A40：只看"有效载荷"——空串/None/空容器 与 `_` 前缀的元字段都不参与合并，
            # 元字段（校验问题、低置信度告警）一律以**本轮**为准（陈旧的要清掉，
            # 否则第一轮的"低置信度"会一直挂在界面上，即使后来分析已经正常）。
            existing = session["artifacts"].get("analysis", {})
            incoming = {k: v for k, v in result.items()
                        if not k.startswith("_") and not _is_blank_value(v)}
            # 身份卡只由商品分析员（或用户确认）产生：品类专项分析员等后续写入会带一份
            # 没有溯源信息（status/source）的裸 product_identity，合并进来就把带溯源的
            # 身份卡换掉了（实测：分析后身份卡变成 uncertain 且缺 status）→ 这里挡住
            if _has_identity_card(existing):
                for guarded in ("product_identity", "identity_status"):
                    incoming.pop(guarded, None)
            existing.update(incoming)
            for meta_key in ("_output_issues", "_low_confidence_warning"):
                if result.get(meta_key):
                    existing[meta_key] = result[meta_key]
                elif agent_name == "商品分析员":
                    # 只有**分析员本人**重跑才代表"本轮分析结论已更新"，可以清掉陈旧告警；
                    # 品类专项分析员只是补充维度，它没有这个字段不代表告警失效
                    # （实测：它会把低置信度告警清掉，界面因此看不到任何提示）
                    existing.pop(meta_key, None)
            session["artifacts"]["analysis"] = existing
        else:
            session["artifacts"][key] = result

    async def _export_images(self, session: SessionState):
        """把当前生成图落盘到可配置输出目录，并把相对路径回写到 image 记录

        尽力而为：落盘失败只告警，绝不影响生成任务（磁盘只读/路径非法等）。
        """
        artifacts = session.get("artifacts", {}) or {}
        images = artifacts.get("images")
        if not isinstance(images, list) or not images:
            return
        try:
            from src.storage.image_export import save_images
            task = session.get("task", {}) or {}
            analysis = artifacts.get("analysis") or {}
            results = await save_images(
                session_id=session.get("session_id", ""),
                tenant_id=session.get("tenant_id", "default"),
                # 品类优先用分析结果（流水线的结论），退回用户填的提示
                category=analysis.get("category") or task.get("category_hint", ""),
                platform=task.get("platform", ""),
                images=images,
            )
            for img, res in zip(images, results):
                if not isinstance(img, dict):
                    continue
                if res.get("ok"):
                    img["saved_path"] = res.get("rel_path", "")
                else:
                    img.pop("saved_path", None)   # 未落盘不留空字段（产物保持干净）
        except Exception as e:  # noqa: BLE001 — 落盘失败不影响主流程
            _engine_logger.warning("生成图落盘失败 (session=%s): %s",
                                   session.get("session_id"), e, exc_info=True)
