"""ChatEngine — 群聊主循环"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from src.core.state import SessionState, RunStatus
from src.core.models import Message, CoordinatorDecision
from src.agents.registry import AgentRegistry
from src.chat.session import SessionManager
from src.chat.broadcaster import Broadcaster
from src.harness.context_manager import ContextManager, WindowAction
from src.harness.audit_logger import AuditLogger
from src.harness.output_pipeline import OutputPipeline
from src.core.logging_config import get_logger

_engine_logger = get_logger(__name__)


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
            return session

        coordinator.reset()
        if start_index is not None:
            coordinator._workflow_index = start_index
        mode = session.get("task", {}).get("collaboration_mode", "serial") or "serial"
        coordinator.set_mode(mode)

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

        # 将记忆上下文注入 Coordinator
        coordinator.set_memory_context(session.get("_memory_context"))

        ctx_mgr = ContextManager(model="gpt-4o")
        window_index = session.get("context_window_index", 0)

        completed = False
        for turn in range(max_turns):
            session["turn_count"] = turn + 1

            # 0. 上下文检查（含 Coordinator system prompt）
            coord_system = coordinator._build_system_prompt(
                coordinator._build_agent_list()
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
                agent = self.registry.get(agent_name)

                if agent is None:
                    error_msg = {
                        "id": uuid.uuid4().hex[:12],
                        "turn": turn,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "role": "system",
                        "sender": "系统",
                        "action": "error",
                        "content": {"error": f"Agent '{agent_name}' 未注册"},
                    }
                    session["messages"].append(error_msg)
                    if self.broadcaster:
                        await self.broadcaster.broadcast(session_id, error_msg)
                    continue

                # 2. Agent 执行（注入记忆上下文增强 task_brief）
                task_brief = decision.get("task_brief", "")
                mem_ctx = session.get("_memory_context")
                if mem_ctx and agent_name == "提示词生成员":
                    best_prompts = mem_ctx.get("best_prompts", {})
                    if best_prompts.get("main"):
                        task_brief = (
                            f"{task_brief}\n\n[记忆库参考] 该品类历史最佳提示词示例："
                            f"\n主图提示词: {best_prompts['main'][:200]}"
                            f"\n场景图数量参考: {best_prompts.get('scene_count', 3)}"
                            f"\n请参考以上成功模式，结合当前商品特征生成新的提示词。"
                        )
                result = await agent.execute(task_brief, session)

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

                # 4b. 输出验证
                output_pipe = OutputPipeline()
                output_check = output_pipe.validate(agent_name, result)
                if not output_check.passed and "error" not in result:
                    result["_output_issues"] = output_check.errors

                # 5. 审计日志
                try:
                    audit = AuditLogger()
                    provider_name = getattr(agent.provider, "name", "unknown") if agent.provider else "mock"
                    await audit.log(
                        session_id=session_id,
                        agent_name=agent_name,
                        provider_name=provider_name,
                        model=agent._get_model() or "unknown",
                        action="execute",
                        duration_ms=result.get("elapsed_ms", 0),
                        tokens_used=result.get("tokens_used", 0) or 0,
                        cost_usd=result.get("cost_usd", 0.0) or 0.0,
                        status="ok" if "error" not in result else "failed",
                        error=result.get("error", ""),
                        tenant_id=session.get("tenant_id", ""),  # 审计修复：租户隔离
                    )
                except Exception as e:
                    _engine_logger.warning("审计日志写入失败: %s", e, exc_info=True)

                # 6. 更新 artifacts
                self._update_artifacts(session, agent_name, result)

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
                    verdict = result.get("verdict", "pass")
                    review_score = result.get("overall_score", 0)
                    needs_human = result.get("needs_human_review", False)

                    if needs_human:
                        # 人工审查暂停
                        session["status"] = "waiting_human"
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
                                "message": f"审查评分 {review_score}/100，需要人工判定。请回复 approve/retry/reject",
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
                        pg_agent = self.registry.get("提示词生成员")
                        ig_agent = self.registry.get("生图员")
                        if pg_agent and ig_agent:
                            pg_result = await pg_agent.execute(
                                f"基于审查反馈重新生成提示词。反馈：{result.get('top_issues', [])}", session
                            )
                            session["messages"].append({
                                "id": uuid.uuid4().hex[:12], "turn": turn,
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                                "role": "agent", "sender": "提示词生成员",
                                "action": "respond", "content": pg_result,
                            })
                            self._update_artifacts(session, "提示词生成员", pg_result)

                            ig_result = await ig_agent.execute("基于新的提示词重新生成图片", session)
                            session["messages"].append({
                                "id": uuid.uuid4().hex[:12], "turn": turn,
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                                "role": "agent", "sender": "生图员",
                                "action": "respond", "content": ig_result,
                            })
                            self._update_artifacts(session, "生图员", ig_result)

                        # 让 Coordinator 跳过到审查步骤
                        coordinator._workflow_index = 5  # 审查员之前的步骤
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
                    ABTestConfig, ABVariant, ABTestRunner,
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
                         "cost_usd": vr.cost_usd, "elapsed_ms": vr.elapsed_ms}
                        for vr in ab_result.ranking
                    ],
                    "total_cost_usd": ab_result.total_cost_usd,
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

        if decision == "approve":
            # 修改审查结果为 pass
            review = session["artifacts"].get("review", {})
            review["verdict"] = "pass"
            review["needs_human_review"] = False
            session["artifacts"]["review"] = review
            session["status"] = RunStatus.RUNNING.value
            session["messages"].append({
                "id": uuid.uuid4().hex[:12],
                "turn": session.get("turn_count", 0),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "role": "system",
                "sender": "人工审查",
                "action": "respond",
                "content": {"decision": "approve", "message": "人工审查通过，接受当前结果"},
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
            pg_agent = self.registry.get("提示词生成员")
            ig_agent = self.registry.get("生图员")
            if pg_agent:
                pg_result = await pg_agent.execute(feedback, session)
                session["messages"].append({
                    "id": uuid.uuid4().hex[:12],
                    "turn": session.get("turn_count", 0) + 1,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "role": "agent", "sender": "提示词生成员",
                    "action": "respond", "content": pg_result,
                })
                self._update_artifacts(session, "提示词生成员", pg_result)
            if ig_agent:
                ig_result = await ig_agent.execute("基于新提示词重新生成图片", session)
                session["messages"].append({
                    "id": uuid.uuid4().hex[:12],
                    "turn": session.get("turn_count", 0) + 1,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "role": "agent", "sender": "生图员",
                    "action": "respond", "content": ig_result,
                })
                self._update_artifacts(session, "生图员", ig_result)

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
            await self.sessions.update(session_id, session)
            return session

        # approve/retry → 继续运行（retry 带 start_index 跳转到审查员）
        return await self.run(session, start_index=start_index)

    def _update_artifacts(self, session: SessionState, agent_name: str, result: dict):
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

        if key == "images" and "images" in result:
            session["artifacts"]["images"] = result["images"]
        elif key == "analysis":
            # 品类专项分析 → 合并到 analysis（补充维度）
            existing = session["artifacts"].get("analysis", {})
            existing.update(result)
            session["artifacts"]["analysis"] = existing
        else:
            session["artifacts"][key] = result
