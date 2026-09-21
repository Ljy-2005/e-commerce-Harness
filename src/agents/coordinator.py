"""中心决策者 — LLM 驱动的群聊协调器"""

from src.agents.base import BaseAgent
from src.core.models import CoordinatorDecision, Message
from src.core.config import load_yaml
from src.core.logging_config import get_logger

_coord_logger = get_logger(__name__)

# 群聊历史里每条消息的摘要上限（防止一条 2.5MB 的产物进上下文）
#
# 为什么是 500：真实会话实测，最长的一类消息是**协调者自己写的邀请简报**（412 字，
# 里面带"商品事实仅限：品牌/规格/认证…"），按 400 截断会把事实串切掉；500 让
# 这类简报完整留存，同时远小于一条生图产物的体量。超限时如实标注"（原文共 N 字）"。
HISTORY_ITEM_CHARS = 500


def _short(value, limit: int = 120) -> str:
    text = " ".join(str(value or "").split())   # 压掉换行，省 token 也更易读
    return text if len(text) <= limit else text[:limit] + "…"


def _quality_bits(content: dict) -> str:
    """体检 + 套图覆盖度的**计数结论**（与 `_artifact_status()` 同口径，这里只取要点）"""
    report = content.get("quality_report") if isinstance(content.get("quality_report"), dict) else {}
    bits = [f"本地体检：{report.get('count', 0)} 张"]
    if report.get("white_bg_ok") is not None:
        bits.append(f"白底合格={'是' if report.get('white_bg_ok') else '否'}")
    if report.get("watermark_free") is not None:
        bits.append(f"无水印={'是' if report.get('watermark_free') else '否'}")
    lost = report.get("identity_lost") or []
    if lost:
        bits.append(f"身份疑似丢失 {len(lost)} 张（{'、'.join(str(x) for x in lost[:4])}）")
    coverage = content.get("set_plan_coverage")
    if isinstance(coverage, dict) and coverage.get("expected"):
        bits.append(f"套图 {coverage.get('produced', 0)}/{coverage['expected']}")
        blocked = coverage.get("blocked_slots") or []
        if blocked:
            bits.append(f"缺素材 {len(blocked)} 张")
    return "；".join(bits)


def readable_content(content, *, limit: int = HISTORY_ITEM_CHARS) -> str:
    """把一条消息的产物**转成人话**（协调者读的历史摘要）

    为什么需要它（真实会话 `bb6cc0fa56a54921` 取证）：此前这里是 `str(content)[:400]`
    —— Python 字典的 repr，**单引号、非 JSON、还带 `{'images': [{...` 这种噪声**：
    10 条消息里 5 条被 400 字截断，而截掉的正好是体检结论、生图说明、审查意见；
    这段历史**每一轮都重复进上下文**（对账：该会话审计只记 $0.010999，实际 $0.018143，
    差额 $0.007144 全在协调者每轮的 decide 调用上）。
    职责分工：**格式化只做展示**，判断依据是上方 `_artifact_status()` 的权威状态块。

    渲染顺序刻意如此（与前端 `chatFormat.js` 同一套纪律）：
    `message` → `error` → 邀请说明 → 分类/评分结论 → 计数摘要 → **键名清单兜底**
    —— 任何分支都**不吐原始结构**，避免又变成"字典汤"。
    """
    if content is None or content == "":
        return "（空）"
    if isinstance(content, str):
        return _short(content, limit)
    if not isinstance(content, dict):
        return _short(content, limit)

    def wrap(text: str) -> str:
        text = " ".join(str(text).split())
        if len(text) <= limit:
            return text
        return f"{text[:limit]}…（原文共 {len(text)} 字）"

    # 出图 + 体检：`message`（人写的那句）与 `quality_report`（计数结论）**互补，两者都要**
    if content.get("images") is not None or content.get("quality_report") is not None:
        bits: list[str] = []
        if isinstance(content.get("images"), list):
            images = [img for img in content["images"] if isinstance(img, dict)]
            failed = [img.get("slot_id") or img.get("prompt_name") for img in images
                      if img.get("error")]
            bits.append(f"出图 {len(images)} 张")
            if failed:
                bits.append(f"失败 {len(failed)} 张（{'、'.join(str(x) for x in failed[:5])}）")
            for note in (content.get("image_notes") or [])[:1]:
                bits.append(_short(note, 80))
        if content.get("quality_report") is not None:
            bits.append(_quality_bits(content))
        head = content.get("message") or ""
        return wrap("；".join([p for p in [head, *bits] if p]))
    if content.get("error"):
        return wrap(f"❌ {content['error']}")
    if content.get("agent_name"):
        brief = content.get("task_brief") or ""
        return wrap(f"邀请 {content['agent_name']}：{brief}")
    if content.get("message"):
        return wrap(content["message"])
    if content.get("summary"):
        return wrap(f"摘要：{content['summary']}")
    if content.get("memory_recall"):
        return wrap(f"🧠 {content['memory_recall']}")
    if isinstance(content.get("prompt_plan"), list):
        return wrap(f"逐张提示词 {len(content['prompt_plan'])} 张")
    if content.get("prompt_lint") is not None:
        lint = content["prompt_lint"] if isinstance(content["prompt_lint"], dict) else {}
        errors = lint.get("errors") or []
        warns = lint.get("warnings") or []
        return wrap(f"提示词体检：{'❌ ' + str(len(errors)) + ' 项硬伤' if errors else '✅ 通过'}"
                    + (f"，{len(warns)} 项建议" if warns else ""))
    if content.get("review") is not None and not content.get("message"):
        review = content["review"] if isinstance(content["review"], dict) else {}
        if review.get("error"):
            return wrap(f"审查未完成：{review['error']}")
    if content.get("decision"):
        return wrap(f"人工决策：{content['decision']}")
    if content.get("context_action"):
        return wrap(f"上下文：{content['context_action']}")

    # 兜底：键名清单（**不是**原始结构）
    keys = [k for k in content.keys() if not str(k).startswith("_")]
    return "字段：" + "、".join(keys[:6]) + (f" 等 {len(keys)} 项" if len(keys) > 6 else "")


class _CoordState:
    """Coordinator 的可变状态载体（P5 修复：并发会话串扰）。

    - 有 session 时：状态存于 session["_coordinator_state"]——共享 Agent 实例下
      每个会话独立推进、互不干扰（此前 _workflow_index/_memory_context 是
      实例属性，并发会话会互相覆盖索引导致流程跳步/记忆串台）；
    - 无 session（单元测试直调 _mock_decision()）：写穿到实例属性，保持兼容。
    """

    _ATTRS = {
        "workflow_index": "_workflow_index",
        "mode": "_current_mode",
        "memory_context": "_memory_context",
    }

    def __init__(self, owner: "CoordinatorAgent", session: dict | None):
        self._owner = owner
        self._session = session
        self._data = {k: getattr(owner, a) for k, a in self._ATTRS.items()}
        if session is not None:
            saved = session.setdefault("_coordinator_state", {})
            self._data.update({k: saved[k] for k in self._ATTRS if k in saved})

    def __getitem__(self, key: str):
        return self._data[key]

    def __setitem__(self, key: str, value):
        self._data[key] = value
        if self._session is not None:
            self._session["_coordinator_state"][key] = value
        else:
            setattr(self._owner, self._ATTRS[key], value)


class CoordinatorAgent(BaseAgent):
    """LLM 驱动的中心决策者

    每轮读取群聊历史 → 决定邀请哪个 Agent 或宣布 DONE
    """

    meta_name = "中心决策者"
    timeout_ms = 90_000

    def __init__(self, provider=None, registry=None):
        super().__init__(provider=provider)
        self.registry = registry
        self._workflow_index = 0  # Mock 模式用（无 session 时的默认状态）
        self._current_mode = "serial"  # 默认串行
        self._memory_context = None

    def _state(self, session: dict | None) -> _CoordState:
        """获取会话级可变状态（并发安全）；session 缺失时回退实例属性"""
        return _CoordState(self, session)

    async def _execute_impl(self, task_brief: str, session) -> dict:
        """根据群聊历史返回 CoordinatorDecision"""
        agent_list = self._build_agent_list()
        messages_history = session.get("messages", [])

        # 构建 prompt
        st = self._state(session)
        system_prompt = self._build_system_prompt(agent_list, st["memory_context"])
        user_prompt = self._build_user_prompt(session, task_brief)

        if self.provider is None:
            # 无 Provider：返回 Mock 决策序列
            return self._mock_decision(session)

        # 调用 LLM
        result = await self.provider.chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            json_mode=True,
            **self._model_kwargs(),
        )
        return result.get("content", {"action": "done", "reasoning": "Mock fallback"})

    async def decide(self, session) -> dict:
        """返回单步决策。

        当真实 LLM Provider（非 Mock）可用时，调用 LLM 做决策；
        否则回退到 Mock 工作流。
        """
        from src.providers.mock import MockLLMProvider

        st = self._state(session)
        if self.provider and not isinstance(self.provider, MockLLMProvider):
            try:
                agent_list = self._build_agent_list()
                system_prompt = self._build_system_prompt(agent_list, st["memory_context"])
                user_prompt = self._build_user_prompt(session, "")

                result = await self.provider.chat(
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    json_mode=True,
                    **self._model_kwargs(),
                )
                llm_decision = result.get("content", {})
                if isinstance(llm_decision, dict) and llm_decision.get("action") in ("invite", "done"):
                    if llm_decision["action"] == "invite":
                        st["workflow_index"] += 1
                    return llm_decision
            except Exception as e:
                _coord_logger.warning("Coordinator LLM 调用失败，回退 Mock: %s", e, exc_info=True)

        return self._mock_decision(session)

    def _mock_decision(self, session: dict | None = None) -> dict:
        """Mock：按预设工作流程返回下一步决策，根据 collaboration_mode 选不同流程"""
        st = self._state(session)
        mode = st["mode"]

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
                 "task_brief": "按 6 维度审查生成图片的质量"},
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
        step = min(st["workflow_index"], len(workflow) - 1)
        decision = workflow[step]
        if decision["action"] == "invite":
            st["workflow_index"] += 1
        return decision

    def set_mode(self, mode: str, session: dict | None = None):
        """设置协作模式。传 session 时写入会话级状态（并发安全）"""
        self._state(session)["mode"] = mode

    def set_memory_context(self, ctx: dict | None, session: dict | None = None):
        """P3: 注入历史记忆上下文，引导更精准的决策（会话级，并发安全）"""
        self._state(session)["memory_context"] = ctx

    def reset(self, session: dict | None = None):
        """重置工作流索引与模式（会话级；无 session 时重置实例默认值）"""
        st = self._state(session)
        st["workflow_index"] = 0
        st["mode"] = "serial"
        st["memory_context"] = None

    def _build_agent_list(self) -> str:
        if self.registry is None:
            return "- 商品分析员: 分析商品图片\n- 提示词生成员: 生成提示词\n- 生图员: 生成图片\n- 审查员: 质量审查\n- 合规审查员: 合规检查\n- 品类专项分析员: 深度品类分析\n- 图像后处理员: 去背景增强"

        lines = []
        # 名单只列**可邀请**的 Agent：后台 Agent（`invitable: false`，如「风格档案员」）
        # 由界面/脚本直接调用，不进名单 —— 既不占用每一轮决策的 token，
        # 也不给模型"提这个名字"的上下文。（第二层兜底：引擎的邀请路径用 get_invitable。）
        for meta in self.registry.list_all(invitable_only=True):
            lines.append(f"- **{meta.name}**: {meta.description}（需要能力: {', '.join(meta.requires)}）")
        return "\n".join(lines)

    def _build_system_prompt(self, agent_list: str, memory_context: dict | None = None) -> str:
        # 尝试从配置文件加载
        prompt = ""
        try:
            prompt_cfg = load_yaml("config/prompts/coordinator.yaml")
            if prompt_cfg and prompt_cfg.get("system"):
                prompt = prompt_cfg["system"].format(agent_list=agent_list)
        except Exception:
            prompt = ""

        if not prompt:
            prompt = self._fallback_system_prompt(agent_list)

        # 记忆上下文此前只拼在 fallback 分支里 → 只要 YAML 有 system 键就永远拼不进去
        # （实测 "历史成功经验" 不在最终 prompt 中，属死代码）。现在无条件追加。
        return prompt + self._memory_section(memory_context)

    @staticmethod
    def _memory_section(memory_context: dict | None) -> str:
        """历史经验段（仅风格/构图参考，禁止照搬事实——A36 记忆串味事故）"""
        if not memory_context:
            return ""
        mc = memory_context
        return f"""

## 历史成功经验（{mc.get('category', '通用')}品类）

最近 {mc.get('recalled_count', 0)} 次成功记录：
- 平均分: {mc.get('best_score', 0)}/100
- 常用卖点: {', '.join(mc.get('common_features', []))}
- 常见好评: {', '.join(mc.get('common_praises', []))}

参考以上经验引导本次决策（仅限风格/构图层面）；**成分、卖点、认证等事实性内容必须来自
本次商品分析**——历史记录属于其他商品，不得照搬。"""

    @staticmethod
    def _fallback_system_prompt(agent_list: str) -> str:
        return f"""你是电商商品图生成系统的中心决策者。你负责协调多个 AI Agent 完成任务。

## 可用 Agent

{agent_list}

## 你的职责

1. 读取群聊历史，判断当前任务进度
2. 决定下一步应该邀请哪个 Agent，并给出明确的任务描述
3. 当所有必要的产出物都就绪时输出 DONE。**交付物是一整套可上传的图**（套图槽位全覆盖），
   而不是"同一张图的几个候选"：产物状态里"套图完整 ✅"才算齐；"套图不完整 ⚠"要重新邀请
   生图员补齐缺的槽位；"无套图编排 ⚠"要重新邀请提示词生成员。

## 典型流程参考

- 先邀请商品分析员，了解商品属性（**必须**拿到商品品牌与商品名，见下）
- 如果涉及保健品/化妆品等特殊品类，邀请品类专项分析员
- 分析完成后，邀请提示词生成员生成各平台提示词（要"整套套图编排"）
- 邀请生图员将提示词转化为图片（参考图 = 用户上传的真实商品图，文+图双条件出图）
- 邀请图像后处理员进行去背景和增强
- 邀请审查员做质量评分
- 对保健品等敏感品类，邀请合规审查员做合规检查

## 产物状态判定（硬性）

「当前产物状态」是唯一权威依据：

- 图片标为"不可用 / 可用 0 张"时**不得**声称生图已完成，应重新邀请生图员或转人工；
- 审查 verdict 缺失或报错时**不得**视为通过；
- **商品身份未确认**（产物状态里标 ⚠ 未确认）时：不得声称"品牌已还原"，画面文字必须
  虚化处理；如用户已在群聊里给出品牌/商品名，则请商品分析员按用户信息重新分析一次；
- **本地体检**报出的问题（背景非纯白 / 疑似水印 / 商品身份相似度过低 / 疑似复制原图）
  要当作事实对待：必要时重新邀请生图员，并在简报里点明要修的具体问题。
- **状态里显示已就绪的产出物，不要再邀请对应 Agent**；同一个 Agent 在同一次会话里也不要
  重复邀请（除非上一步明确报错、或产物缺失需要重做）—— 重复邀请会白烧一轮调用，
  还可能覆盖掉已经审核定稿的产物。
- 带 ⛔ / ⚠ 的行是系统的明确指导，**照它办**：例如「⛔ 信息图缺素材…重新邀请生图员没有用」，
  那就不要再邀请生图员，而应把"缺什么素材"讲清楚（用户可以补充素材）。

## 输出格式

每次只输出一个决策（JSON）：
{{"action": "invite", "agent_name": "商品分析员", "task_brief": "分析上传的商品图片...", "reasoning": "首先需要了解商品属性"}}
或
{{"action": "done", "reasoning": "所有产出物就绪"}}"""

    @staticmethod
    def _is_number(value) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool)

    @staticmethod
    def _image_usable(img) -> bool:
        if not isinstance(img, dict):
            return False
        return bool(str(img.get("base64_data") or "").strip()
                    or str(img.get("image_url") or "").strip()
                    or str(img.get("saved_path") or "").strip())

    def _artifact_status(self, session) -> str:
        """产物状态摘要（A35）

        实测事故：协调者只看得到"最近 10 条消息 × 每条 200 字"，artifacts 从不进 prompt
        → 生图员写下 3 条 url/base64 全空的图记录，它却宣布"已确认生图员已完成 taobao
        主图生成（variant_1）"，逼审查员对着不存在的图打分。
        """
        artifacts = session.get("artifacts") or {}
        lines: list[str] = []

        analysis = artifacts.get("analysis") or {}
        if analysis:
            conf = analysis.get("confidence_score")
            issues = analysis.get("_output_issues") or []
            line = f"- 商品分析：品类={analysis.get('category') or '未知'}"
            if self._is_number(conf):
                line += f"，置信度={conf}"
            if issues:
                line += f"，⚠ 输出校验问题：{'；'.join(str(i) for i in issues)[:120]}"
            lines.append(line)

        # 商品身份卡：全链路的事实基准（用户要求"要强调/提醒"）——
        # 协调者必须知道品牌有没有确认：没确认就出图 = 让模型编造包装文字
        identity = artifacts.get("product_identity") or {}
        if identity:
            status = identity.get("status") or "uncertain"
            if status == "confirmed":
                lines.append(f"- 商品身份：**已确认** 品牌={identity.get('brand')}"
                             f"｜品名={identity.get('product_name')}"
                             f"｜规格={identity.get('spec') or '—'}")
            else:
                missing = "、".join(identity.get("missing") or ["品牌", "商品名"])
                lines.append(f"- 商品身份：⚠ **未确认**（缺少 {missing}，来源={identity.get('source')}）"
                             "—— 画面文字将被虚化处理，**不得**让模型生成任何品牌/包装文字；"
                             "若用户已确认，可继续推进；否则应先转人工确认")

        prompts = artifacts.get("prompts") or {}
        if prompts:
            plan = artifacts.get("set_plan") or prompts.get("set_plan") or {}
            slots = plan.get("slots") if isinstance(plan, dict) else None
            if slots:
                lines.append(f"- 套图编排：{plan.get('platform_label') or plan.get('platform') or ''}"
                             f"共 {len(slots)} 张（" +
                             "、".join(f"第{slot.get('number')}张{slot.get('slot_id')}"
                                       for slot in slots[:10]) + "）")
            else:
                main_prompt = (prompts.get("main_image") or {}).get("prompt") or ""
                lines.append(
                    f"- 提示词：主图 {len(main_prompt)} 字"
                    f"｜场景图 {len(prompts.get('scene_images') or [])} 张"
                    f"｜社交图 {len(prompts.get('social_images') or [])} 张"
                    f"｜多模型版本 {len(prompts.get('model_variants') or {})} 个"
                    "｜⚠ 无套图编排（set_plan）：交付物只是「同一张图的多个候选」，"
                    "建议重新邀请提示词生成员"
                )

        # 提示词体检 + 审美审核（用户 2026-09-18）：协调者必须知道"提示词这一关过没过"，
        # 否则它可能看到群聊里的意见后又重复邀请提示词生成员（白烧一轮 LLM 调用）
        lint = artifacts.get("prompt_lint") or {}
        if lint:
            errors = lint.get("errors") or []
            warnings = lint.get("warnings") or []
            state = f"❌ {len(errors)} 项硬伤" if errors else "✅ 通过"
            if warnings:
                state += f"（{len(warnings)} 项建议）"
            lines.append(f"- 提示词体检：{state}"
                         f"（{lint.get('checked', 0)}/{lint.get('expected', 0)} 张）")
        review = artifacts.get("prompt_review") or {}
        if review:
            status = review.get("status")
            if status == "reviewed":
                scores = review.get("scores") or {}
                average = (round(sum(float(v) for v in scores.values()) / len(scores), 1)
                           if scores else "未给出")
                line = (f"- 提示词审美审核：平均 {average}"
                        f"（阈值 {review.get('threshold')}）")
                revised = review.get("revised_slots") or []
                if revised:
                    line += f"，已改写 {len(revised)} 张（{'、'.join(revised[:6])}）"
                if review.get("refine_rejected"):
                    line += f"，{len(review['refine_rejected'])} 张改写被体检拦下"
                line += "——**不要重复邀请提示词生成员**，提示词已经是审核后的定稿"
                lines.append(line)
            else:
                lines.append(f"- 提示词审美审核：未执行（status={status or 'unknown'}；"
                             f"{str(review.get('message') or '')[:80]}）")

        images = artifacts.get("images")
        if isinstance(images, list) and images:
            usable = [img for img in images if self._image_usable(img)]
            lines.append(f"- 生成图片：{len(images)} 条记录，其中可用 {len(usable)} 张")
            if not usable:
                lines.append("  ⚠ 没有任何可用图像数据（无 URL / base64 / 落盘文件）——"
                             "**禁止判定生图已完成**：应重新邀请生图员，或先排查生图失败原因")
            else:
                for img in images[:3]:
                    state = "可访问" if self._image_usable(img) else "不可用"
                    saved = f"，已落盘 {img.get('saved_path')}" if img.get("saved_path") else ""
                    slot = f"[{img.get('slot_id')}] " if img.get("slot_id") else ""
                    lines.append(f"  · {slot}{img.get('prompt_name') or '未命名'}：{state}{saved}")

            coverage = artifacts.get("set_plan_coverage")
            if isinstance(coverage, dict) and coverage.get("expected"):
                if coverage.get("complete"):
                    lines.append(f"  ✅ 套图完整：{coverage['produced']}/{coverage['expected']} 张")
                else:
                    lines.append(f"  ⚠ 套图不完整：{coverage['produced']}/{coverage['expected']} 张，"
                                 f"缺 {'、'.join(coverage.get('missing_slots') or [])}"
                                 " —— 交付物不可直接上传，应重新邀请生图员补齐缺的槽位")
                blocked = coverage.get("blocked_slots") or []
                if blocked:
                    # 信息类槽位缺**事实依据**（如包装正面看不到成分表）→ 缺的是素材，不是缺生成
                    lines.append(f"  ⛔ 信息图缺素材（已拦下，未编造）：{'、'.join(blocked)}"
                                 " —— 需要用户补充素材（如包装背面/成分表照片）或手工填写；"
                                 "**重新邀请生图员没有用**，不要因此反复重跑")

            quality = artifacts.get("quality_report")
            if isinstance(quality, dict) and quality.get("count"):
                lines.append(f"- 本地体检：白底合格={'是' if quality.get('white_bg_ok') else '否'}"
                             f"｜无水印={'是' if quality.get('watermark_free') else '否'}")
                if quality.get("identity_lost"):
                    lines.append(f"  ⚠ 商品身份相似度过低的图：{quality['identity_lost']}"
                                 "（疑似模型在凭文字想象商品，而不是照着参考图生成）")
                if quality.get("near_copy"):
                    lines.append(f"  ⚠ 与参考图高度相似、疑似直接复制的图：{quality['near_copy']}")
                for issue in (quality.get("issues") or [])[:4]:
                    lines.append(f"  · {issue}")

        review = artifacts.get("review") or {}
        if review:
            score = review.get("overall_score")
            line = (f"- 审查：verdict={review.get('verdict') or '（缺失）'}"
                    f"｜分数={score if self._is_number(score) else '未给出'}"
                    f"｜需人工={bool(review.get('needs_human_review'))}")
            if review.get("review_blocked_reason"):
                line += f"｜阻断原因={review['review_blocked_reason']}"
            if review.get("error"):
                line += f"｜错误={str(review['error'])[:120]}"
            lines.append(line)

        compliance = artifacts.get("compliance") or {}
        if compliance:
            lines.append(f"- 合规：passed={compliance.get('passed')}"
                         f"｜risk_level={compliance.get('risk_level')}")

        errors = session.get("error_history") or []
        if errors:
            lines.append("- 最近失败：" + "；".join(
                f"{e.get('agent')}: {str(e.get('error'))[:80]}" for e in errors[-5:]))

        return "\n".join(lines) if lines else "（尚无产物）"

    def _build_user_prompt(self, session, task_brief: str) -> str:
        task = session.get("task", {})
        messages = session.get("messages", [])

        history = ""
        for m in messages[-10:]:  # 最近 10 条（每条截断：长简报不必全文进上下文）
            sender = m.get("sender", "系统")
            history += f"[{sender}]: {readable_content(m.get('content'))}\n"

        return f"""## 当前任务

平台: {task.get('platform', '未指定')}
商品信息: {task.get('product_info', '无')}
品类提示: {task.get('category_hint', '无')}

## 当前产物状态

{self._artifact_status(session)}

## 群聊历史（最近 10 条摘要；**完整状态以上方「当前产物状态」为准**）

{history if history else "（尚无消息，任务刚开始）"}

请决定下一步行动。"""
