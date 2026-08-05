"""中心决策者 — LLM 驱动的群聊协调器"""

from src.agents.base import BaseAgent
from src.core.models import CoordinatorDecision, Message
from src.core.config import load_yaml
from src.core.logging_config import get_logger

_coord_logger = get_logger(__name__)


class CoordinatorAgent(BaseAgent):
    """LLM 驱动的中心决策者

    每轮读取群聊历史 → 决定邀请哪个 Agent 或宣布 DONE
    """

    meta_name = "中心决策者"
    timeout_ms = 15_000

    def __init__(self, provider=None, registry=None):
        super().__init__(provider=provider)
        self.registry = registry
        self._workflow_index = 0  # Mock 模式用
        self._current_mode = "serial"  # 默认串行

    async def _execute_impl(self, task_brief: str, session) -> dict:
        """根据群聊历史返回 CoordinatorDecision"""
        agent_list = self._build_agent_list()
        messages_history = session.get("messages", [])

        # 构建 prompt
        system_prompt = self._build_system_prompt(agent_list)
        user_prompt = self._build_user_prompt(session, task_brief)

        if self.provider is None:
            # 无 Provider：返回 Mock 决策序列
            return self._mock_decision()

        # 调用 LLM
        result = await self.provider.chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            json_mode=True,
        )
        return result.get("content", {"action": "done", "reasoning": "Mock fallback"})

    async def decide(self, session) -> dict:
        """返回单步决策。

        当真实 LLM Provider（非 Mock）可用时，调用 LLM 做决策；
        否则回退到 Mock 工作流。
        """
        from src.providers.mock import MockLLMProvider

        if self.provider and not isinstance(self.provider, MockLLMProvider):
            try:
                agent_list = self._build_agent_list()
                system_prompt = self._build_system_prompt(agent_list)
                user_prompt = self._build_user_prompt(session, "")

                result = await self.provider.chat(
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    json_mode=True,
                )
                llm_decision = result.get("content", {})
                if isinstance(llm_decision, dict) and llm_decision.get("action") in ("invite", "done"):
                    if llm_decision["action"] == "invite":
                        self._workflow_index += 1
                    return llm_decision
            except Exception as e:
                _coord_logger.warning("Coordinator LLM 调用失败，回退 Mock: %s", e, exc_info=True)

        return self._mock_decision()

    def _mock_decision(self) -> dict:
        """Mock：按预设工作流程返回下一步决策，根据 collaboration_mode 选不同流程"""
        mode = self._current_mode

        workflows = {
            "ab_test": [
                {"action": "invite", "agent_name": "商品分析员",
                 "task_brief": "分析上传的商品图片"},
                {"action": "invite", "agent_name": "品类专项分析员",
                 "task_brief": "针对品类进行深度分析"},
                # 提示词生成 → 由引擎 A/B Test Runner 接管，此处跳过
                {"action": "done", "agent_name": "",
                 "task_brief": "分析已完成，A/B 测试框架接管提示词对比"},
            ],
            "serial": [
                {"action": "invite", "agent_name": "商品分析员",
                 "task_brief": "分析上传的商品图片，识别品类、材质、卖点、目标人群和风格约束"},
                {"action": "invite", "agent_name": "品类专项分析员",
                 "task_brief": "针对品类进行深度分析"},
                {"action": "invite", "agent_name": "提示词生成员",
                 "task_brief": "基于分析结果，生成完整的提示词"},
                {"action": "invite", "agent_name": "生图员",
                 "task_brief": "使用提示词生成商品展示图片"},
                {"action": "invite", "agent_name": "图像后处理员",
                 "task_brief": "对生成图片进行去背景和增强处理"},
                {"action": "invite", "agent_name": "审查员",
                 "task_brief": "按 5 维度审查生成图片的质量"},
                {"action": "invite", "agent_name": "合规审查员",
                 "task_brief": "检查图片是否符合广告法和平台规范"},
                {"action": "done", "agent_name": "", "task_brief": "所有产出物已就绪，任务完成"},
            ],
            "ab_generate": [
                {"action": "invite", "agent_name": "商品分析员",
                 "task_brief": "分析上传的商品图片"},
                {"action": "invite", "agent_name": "品类专项分析员",
                 "task_brief": "针对品类进行深度分析"},
                {"action": "invite", "agent_name": "提示词生成员",
                 "task_brief": "基于分析结果，生成提示词"},
                {"action": "invite", "agent_name": "生图员",
                 "task_brief": "[A/B 方案A] 使用提示词生成第一套图片"},
                {"action": "invite", "agent_name": "生图员",
                 "task_brief": "[A/B 方案B] 使用提示词生成第二套不同风格的图片"},
                {"action": "invite", "agent_name": "图像后处理员",
                 "task_brief": "对两套图片进行后处理"},
                {"action": "invite", "agent_name": "审查员",
                 "task_brief": "对比方案A和方案B的图片，选出最佳方案并评分"},
                {"action": "invite", "agent_name": "合规审查员",
                 "task_brief": "检查最佳方案图片的合规性"},
                {"action": "done", "agent_name": "", "task_brief": "A/B 对比完成，最佳方案已选出"},
            ],
            "debate": [
                {"action": "invite", "agent_name": "商品分析员",
                 "task_brief": "分析上传的商品图片"},
                {"action": "invite", "agent_name": "品类专项分析员",
                 "task_brief": "针对品类进行深度分析"},
                {"action": "invite", "agent_name": "提示词生成员",
                 "task_brief": "基于分析结果，生成提示词"},
                {"action": "invite", "agent_name": "生图员",
                 "task_brief": "使用提示词生成商品展示图片"},
                {"action": "invite", "agent_name": "图像后处理员",
                 "task_brief": "对生成图片进行后处理"},
                {"action": "invite", "agent_name": "审查员",
                 "task_brief": "[正方审查] 指出图片的优点和可接受之处"},
                {"action": "invite", "agent_name": "审查员",
                 "task_brief": "[反方审查] 指出图片的缺点和问题"},
                {"action": "done", "agent_name": "",
                 "task_brief": "辩论完成，综合双方意见后判定"},
            ],
            "vote": [
                {"action": "invite", "agent_name": "商品分析员",
                 "task_brief": "分析上传的商品图片"},
                {"action": "invite", "agent_name": "品类专项分析员",
                 "task_brief": "针对品类进行深度分析"},
                {"action": "invite", "agent_name": "提示词生成员",
                 "task_brief": "基于分析结果，生成提示词"},
                {"action": "invite", "agent_name": "生图员",
                 "task_brief": "使用提示词生成商品展示图片"},
                {"action": "invite", "agent_name": "图像后处理员",
                 "task_brief": "对生成图片进行后处理"},
                {"action": "invite", "agent_name": "审查员",
                 "task_brief": "[审查员1] 独立审查并评分"},
                {"action": "invite", "agent_name": "审查员",
                 "task_brief": "[审查员2] 独立审查并评分"},
                {"action": "invite", "agent_name": "审查员",
                 "task_brief": "[审查员3] 独立审查并评分"},
                {"action": "done", "agent_name": "",
                 "task_brief": "投票完成，取多数意见为最终判定"},
            ],
        }

        workflow = workflows.get(mode, workflows["serial"])
        step = min(self._workflow_index, len(workflow) - 1)
        decision = workflow[step]
        if decision["action"] == "invite":
            self._workflow_index += 1
        return decision

    def set_mode(self, mode: str):
        self._current_mode = mode

    def set_memory_context(self, ctx: dict | None):
        """P3: 注入历史记忆上下文，引导更精准的决策"""
        self._memory_context = ctx

    def reset(self):
        self._workflow_index = 0
        self._current_mode = "serial"
        self._memory_context = None

    def _build_agent_list(self) -> str:
        if self.registry is None:
            return "- 商品分析员: 分析商品图片\n- 提示词生成员: 生成提示词\n- 生图员: 生成图片\n- 审查员: 质量审查\n- 合规审查员: 合规检查\n- 品类专项分析员: 深度品类分析\n- 图像后处理员: 去背景增强"

        lines = []
        for meta in self.registry.list_all():
            lines.append(f"- **{meta.name}**: {meta.description}（需要能力: {', '.join(meta.requires)}）")
        return "\n".join(lines)

    def _build_system_prompt(self, agent_list: str) -> str:
        # 尝试从配置文件加载
        try:
            prompt_cfg = load_yaml("config/prompts/coordinator.yaml")
            if prompt_cfg and prompt_cfg.get("system"):
                return prompt_cfg["system"].format(agent_list=agent_list)
        except Exception:
            pass

        # 追加记忆上下文
        memory_section = ""
        if getattr(self, "_memory_context", None):
            mc = self._memory_context
            memory_section = f"""

## 历史成功经验（{mc.get('category', '通用')}品类）

最近 {mc.get('recalled_count', 0)} 次成功记录：
- 平均分: {mc.get('best_score', 0)}/100
- 常用卖点: {', '.join(mc.get('common_features', []))}
- 常见好评: {', '.join(mc.get('common_praises', []))}

参考以上经验引导本次决策，优先采用被验证过的分析角度和风格。"""

        return f"""你是电商商品图生成系统的中心决策者。你负责协调多个 AI Agent 完成任务。

## 可用 Agent

{agent_list}
{memory_section}

## 你的职责

1. 读取群聊历史，判断当前任务进度
2. 决定下一步应该邀请哪个 Agent，并给出明确的任务描述
3. 当所有必要的产出物（分析报告 + 提示词 + 生成图片 + 审查报告 + 合规报告）都就绪时，输出 DONE

## 典型流程参考

- 先邀请商品分析员，了解商品属性
- 如果涉及保健品/化妆品等特殊品类，邀请品类专项分析员
- 分析完成后，邀请提示词生成员生成各平台提示词
- 邀请生图员将提示词转化为图片
- 邀请图像后处理员进行去背景和增强
- 邀请审查员做质量评分
- 对保健品等敏感品类，邀请合规审查员做合规检查

## 输出格式

每次只输出一个决策（JSON）：
{{"action": "invite", "agent_name": "商品分析员", "task_brief": "分析上传的商品图片...", "reasoning": "首先需要了解商品属性"}}
或
{{"action": "done", "reasoning": "所有产出物就绪"}}"""

    def _build_user_prompt(self, session, task_brief: str) -> str:
        task = session.get("task", {})
        messages = session.get("messages", [])

        history = ""
        for m in messages[-10:]:  # 最近 10 条
            sender = m.get("sender", "系统")
            content = str(m.get("content", ""))[:200]
            history += f"[{sender}]: {content}\n"

        return f"""## 当前任务

平台: {task.get('platform', '未指定')}
商品信息: {task.get('product_info', '无')}
品类提示: {task.get('category_hint', '无')}

## 群聊历史

{history if history else "（尚无消息，任务刚开始）"}

请决定下一步行动。"""
