"""提示词生成员 — 分析结果 → 各平台各模型生图提示词（**初稿**）

## 历史改动

1. **平台风格从配置来**：此前 `config/prompts/prompt_gen.yaml` 里只有 4 个平台的静态风格
   （淘宝/小红书/抖音/Amazon），选"拼多多"时送进模型的只有一个裸字符串 —— 风格全靠模型猜。
   现在由 `src/core/platforms.py` 渲染 `config/platforms.yaml` 的平台规范块（新增平台零改动）。
2. **输出"套图编排"而不是一张图**：模型必须给出 `set_plan.slots`（每个槽位一张），
   生图员按槽位出图。
3. **商品身份作为事实基准前置**：品牌/品名/规格必须逐字沿用。
4. **本轮（用户 2026-09-18）**：
   - "未有明确约束每一张该有的提示词" → 平台规范块升级为**编号槽位表**（第 N 张 + 逐张契约
     `intent/design/must/forbid/keep_clear`，主图与详情图都在里面）；
   - "电商商品图需要平面设计风格、要有高级感" → 入参带**视觉设计方向**与**品牌色板**
     （从包装上取的真实色值），并要求初稿按设计七项写；
   - 初稿只说"画面"，**不再自己复述品牌文字与包装版式**（那是上一轮乱码的根因）；
   - 本成员只出**初稿**：系统随后跑提示词体检 + 提示词审核优化员（审美改写），
     所以初稿不必自我审查、更不要堆否定词。
"""

from src.agents.base import BaseAgent
from src.core.config import load_yaml
from src.core.platforms import platform_style_block
from src.harness.product_identity import identity_card_block, normalize_identity
from src.harness.set_plan import finalize_prompts, set_plan_lines
from src.harness.style_library import style_block_for


class PromptGeneratorAgent(BaseAgent):
    """使用 Text 能力，将分析结果转化为专业生图提示词"""

    meta_name = "提示词生成员"
    timeout_ms = 150_000

    async def _execute_impl(self, task_brief: str, session) -> dict:
        artifacts = session.get("artifacts", {})
        analysis = artifacts.get("analysis", {})
        task = session.get("task", {})
        platform = task.get("platform", "taobao")
        # 逐槽位风格档案（用户指定的「风格词库」）：按**平台槽位表**检索，与模型产出无关，
        # 所以 Mock 路径也必须算 —— 否则 e2e/前端看不到"用了哪条档案"（实测踩到）。
        style_selection = self._select_styles(session, analysis, platform)

        # 尝试召回历史成功模板
        recalled = ""
        try:
            from src.harness.agent_memory import AgentMemory
            memory = AgentMemory()
            category = analysis.get("category", "")
            features = analysis.get("features", [])
            similar = await memory.recall_similar(category, features, limit=2,
                                                  tenant_id=session.get("tenant_id", ""))
            if similar:
                recalled = "\n## 历史成功参考（**仅供风格与构图层面借鉴，严禁照搬其中的品牌/成分/认证**）\n"
                for i, e in enumerate(similar):
                    recalled += (f"{i + 1}. 评分: {e['score']}/100 | "
                                 f"提示词: {e.get('prompts', {}).get('main', '')[:200]}\n")
        except Exception:
            pass

        from src.providers.mock import MockLLMProvider
        if self.provider is None or isinstance(self.provider, MockLLMProvider):
            return self._attach_styles(self._mock_prompts(), style_selection)

        system_prompt = self._load_prompt("config/prompts/prompt_gen.yaml")
        identity = self._identity_of(session, analysis)
        style_block = style_block_for(selection=style_selection, audience="gen")
        user_msg = f"""{identity_card_block(identity)}

{platform_style_block(platform)}

{style_block}

## 商品分析结果
{analysis}

## 任务
{task_brief}
{recalled}
请按上面的「目标平台规范」逐张产出 `set_plan.slots`：**编号、顺序、槽位 id 一律照抄规范里的槽位表**
（主图 + 详情图，一张不少），每张给出 `intent`（这一张要让买家看懂什么）与 `prompt`
（画面设计描述）。这是初稿：只写画面，不要复述品牌文字与包装版式；「适用风格档案」按"逐张对应"
借鉴设计做法（背景/构图/光影/材质/留白），**不要照搬档案措辞**；系统随后会做一次审美优化。"""

        result = await self.provider.chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
            json_mode=True,
            **self._model_kwargs(),
        )
        content = self._content_or_error(result, self._mock_prompts())
        if isinstance(content, dict) and "error" in content:
            return content
        payload = self._finalize(content if isinstance(content, dict) else {}, platform, identity)
        return self._attach_styles(payload, style_selection)

    # ── 风格档案（A79-A96）──

    def _select_styles(self, session, analysis, platform) -> dict:
        """逐槽位检索风格档案；**任何异常都不阻断出图**（档案库是增强，不是依赖）

        `locked_entry_id`：**一轮会话只用一套风格词**（用户 2026-09-20：单位是
        "一组生成图/一轮会话"，且是**一套**词而不是"一个词"）。首次检索时把选中的那套锁进产物，
        后续（审核、体检、重跑）都读同一把锁 —— 否则中途在词库切换启用项，同一套图会前后用两套风格。
        """
        from src.core.platforms import platform_slot_table
        from src.harness.style_library import select_by_slot, session_style_lock

        task = session.get("task", {}) if isinstance(session, dict) else {}
        subset = [str(item).strip() for item in (task.get("slot_override") or [])
                  if str(item or "").strip()]
        lock = session_style_lock(session)
        try:
            rows = platform_slot_table(platform)
            if subset:
                rows = [row for row in rows if row.get("slot_id") in subset] or rows
            return select_by_slot(rows, analysis=analysis, platform=platform,
                                  tenant_id=str(session.get("tenant_id") or "") or None,
                                  locked_entry_id=lock)
        except Exception as exc:  # noqa: BLE001 — 档案库不可用不能挡住生图
            return {"enabled": False, "slots": {}, "entries": [], "anchors": [],
                    "active_entry_id": "", "locked_entry_id": lock,
                    "dropped": [], "notes": [f"风格档案库不可用：{type(exc).__name__}: {exc}"],
                    "message": ""}

    def _attach_styles(self, payload: dict, selection: dict) -> dict:
        """把"用了哪条档案"写进产物与群聊消息（`artifacts.prompts.style_refs`）"""
        from src.harness.style_library import usage_snapshot

        out = dict(payload) if isinstance(payload, dict) else {}
        snapshot = usage_snapshot(selection)
        out["style_refs"] = snapshot
        line = snapshot.get("message") or ""
        if line:
            message = str(out.get("message") or "").strip()
            out["message"] = f"{message}\n{line}" if message else line
        return out

    def _identity_of(self, session, analysis) -> dict:
        """身份卡：优先用引擎落盘的产物，其次从分析结果里现取（workflow 直调时没有产物）"""
        artifacts = session.get("artifacts", {}) if isinstance(session, dict) else {}
        stored = artifacts.get("product_identity")
        if isinstance(stored, dict) and stored.get("status"):
            return stored
        return normalize_identity(analysis if isinstance(analysis, dict) else {})

    def _finalize(self, content: dict, platform, identity: dict | None = None) -> dict:
        """补平台归一化结果、套图编排摘要与**给人看的逐张清单**

        `message`：群聊里直接展示"第1张…第2张…"（用户反馈：此前只丢一坨 JSON 出来，
        看不出哪张是什么提示词）。
        """
        palette = identity.get("brand_palette") if isinstance(identity, dict) else None
        payload = finalize_prompts(content, platform, palette=palette)
        plan = payload.get("set_plan")
        if plan:
            lines = set_plan_lines(plan)
            payload["prompt_plan"] = [{"number": slot.get("number"),
                                       "slot_id": slot.get("slot_id"),
                                       "role": slot.get("role"),
                                       "usage": slot.get("usage"),
                                       "kind": slot.get("kind"),
                                       "intent": slot.get("intent"),
                                       "prompt": slot.get("prompt")}
                                      for slot in plan.get("slots", [])]
            label = plan.get("platform_label") or plan.get("platform") or ""
            header = f"📝 本套逐张提示词（{label} 共 {len(lines)} 张，初稿）"
            payload["message"] = header + "\n" + "\n".join(lines)
        return payload

    def _mock_prompts(self) -> dict:
        from src.providers.mock import MOCK_PROMPTS
        return dict(MOCK_PROMPTS)

    def _load_prompt(self, path: str) -> str:
        try:
            cfg = load_yaml(path)
            return cfg.get("system", "你是专业的 AI 图像提示词工程师。")
        except Exception:
            return "你是专业的 AI 图像提示词工程师。请根据商品分析结果生成高质量的生图提示词。"
