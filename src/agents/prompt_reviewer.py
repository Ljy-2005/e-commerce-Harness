"""提示词审核优化员 — 按**大众电商商品图的审美**审核并改写画面描述

## 为什么有这个成员（用户 2026-09-18）

用户原话："我加这个的初衷是为了让生成图提示词更接近大众的商品图审美，免得跑出一些符合
基本要求但是图片审美完全不合格的图片。"

真实会话取证（`ee9a3b80e19b4010`，6 张 / $0.2519 / 人工 reject）暴露的问题：
提示词把包装版式逐字写进画面（诱导模型重画小字 → `Sadle Ligement`、`Schneiski` 乱码）、
6 张画面高度同质、画面里没有商品、单条 586 字且近半是否定词堆叠 —— 全都是"能过检查但审美
不合格"的典型。

## 分工（关键设计）

- `harness/prompt_lint.py`：**确定性护栏**（$0）——身份词、包装版式复述、设计语言缺失、
  反模式、同质化、违禁词。它的 error 是硬伤；
- 本成员：**语义与审美判断** —— 逐张打审美分（设计七项），**低于阈值的槽位直接给改写稿**。
  用户要的是"更接近大众审美的提示词"，所以主职是**改写**，不是当门卫。

## 安全边界

- 只写《画面》段：**不要**写"必须/留白/禁止"这些硬约束（系统会注入，你改也改不掉）；
- **不得**写品牌名/品名/规格/认证文字（会诱导模型重画包装小字 → 乱码）；品牌**色值**要写；
- **不得**新增任何事实（功效、认证、产地、成分）；
- 回归跑一遍体检：改写稿若产生新硬伤会被自动拒绝（`harness/prompt_review.py`）。
"""

from src.agents.base import BaseAgent
from src.core.config import load_yaml
from src.core.platforms import platform_style_block, slot_contract_block
from src.harness.image_prompt import ART_DIRECTION_GUIDE
from src.harness.product_identity import identity_card_block, normalize_identity
from src.harness.prompt_lint import lint_prompts
from src.harness.prompt_review import normalize_prompt_review, review_summary
from src.harness.set_plan import normalize_set_plan


class PromptReviewerAgent(BaseAgent):
    """使用 Text 能力，按品牌视觉设计标准审核并改写提示词"""

    meta_name = "提示词审核优化员"
    timeout_ms = 180_000

    async def _execute_impl(self, task_brief: str, session) -> dict:
        artifacts = session.get("artifacts", {}) if isinstance(session, dict) else {}
        prompts = artifacts.get("prompts") or {}
        task = session.get("task") or {}
        platform = task.get("platform", "")
        analysis = artifacts.get("analysis") or {}

        identity = artifacts.get("product_identity")
        if not isinstance(identity, dict) or not identity.get("status"):
            identity = normalize_identity(analysis)
        palette = identity.get("brand_palette") if isinstance(identity, dict) else {}

        plan = normalize_set_plan(prompts, platform=platform, palette=palette,
                                  only_slots=task.get("slot_override") or None)
        # 逐槽位风格档案 + 用户锚点（"打分以锚点为准绳"）：与提示词生成员**同一条检索路径**
        # 且**同一个会话锁** —— 审核必须按生成员实际用的那**一套**风格词打分，不能自己重挑一套
        from src.harness.style_library import session_style_lock

        style_selection = self._select_styles(session.get("task") or {}, plan, analysis, platform,
                                             tenant_id=str(session.get("tenant_id") or ""),
                                             locked_entry_id=session_style_lock(session))
        if not plan or not plan.get("slots"):
            # 没有套图编排：没有可审的东西（不是错误，交给引擎记 skipped）
            return {"status": "skipped", "verdict": "pass",
                    "reason": "本次没有套图编排（set_plan），无需提示词审核",
                    "message": "⏭ 提示词审核：本次没有套图编排，跳过"}

        lint = lint_prompts(plan, platform=platform, identity=identity,
                            style_entries=style_selection)

        from src.providers.mock import MockLLMProvider
        if self.provider is None or isinstance(self.provider, MockLLMProvider):
            return self._mock_review(plan, lint)

        system_prompt = self._load_prompt("config/prompts/prompt_reviewer.yaml")
        user_msg = self._build_brief(plan, identity, palette, lint, platform, task_brief,
                                     style_selection)

        result = await self.provider.chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
            json_mode=True,
            **self._model_kwargs(),
        )
        content = self._content_or_error(result, {})
        if isinstance(content, dict) and content.get("error"):
            return content
        review = normalize_prompt_review(content)
        review["lint_digest"] = lint.get("digest")
        review["status"] = "reviewed"
        review["style_refs"] = self._style_refs(style_selection)
        review["message"] = self._message(plan, review)
        return review

    # ── 风格档案（A79-A96）──

    def _select_styles(self, task: dict, plan, analysis, platform: str,
                       *, tenant_id: str = "", locked_entry_id: str = "") -> dict:
        """逐槽位检索风格档案；异常不阻断审核（档案是增强，不是依赖）"""
        from src.harness.style_library import select_by_slot

        rows = (plan or {}).get("slots") or []
        subset = [str(item).strip() for item in (task.get("slot_override") or [])
                  if str(item or "").strip()]
        if not rows:
            from src.core.platforms import platform_slot_table
            rows = platform_slot_table(platform)
            if subset:
                rows = [row for row in rows if row.get("slot_id") in subset] or rows
        try:
            return select_by_slot(rows, analysis=analysis, platform=platform,
                                  tenant_id=str(task.get("tenant_id") or "") or None,
                                  locked_entry_id=locked_entry_id)
        except Exception as exc:  # noqa: BLE001
            return {"enabled": False, "slots": {}, "entries": [], "anchors": [], "dropped": [],
                    "active_entry_id": "", "locked_entry_id": locked_entry_id,
                    "notes": [f"风格档案库不可用：{type(exc).__name__}: {exc}"], "message": ""}

    @staticmethod
    def _style_refs(selection) -> dict:
        """审核员产物里只留 id/名字（省体积；完整逐槽位明细在提示词生成员的产物里）"""
        selection = selection if isinstance(selection, dict) else {}
        return {
            "entries": [entry.get("id") for entry in selection.get("entries") or []],
            "anchors": [anchor.get("id") for anchor in selection.get("anchors") or []],
            "notes": selection.get("notes") or [],
        }

    # ── 输入组装 ──

    def _build_brief(self, plan, identity, palette, lint, platform, task_brief: str,
                     style_selection=None) -> str:
        from src.harness.style_library import style_block_for

        lines = [identity_card_block(identity)]
        lines.append("")
        lines.append(f"## 品牌色系（画面必须使用；取自包装）\n{palette or '未取到色板'}")
        lines.append("")
        lines.append("## 视觉设计基准（逐条对照打分）\n" + ART_DIRECTION_GUIDE)
        lines.append("")
        lines.append(platform_style_block(platform))
        style_block = style_block_for(selection=style_selection, audience="review")
        if style_block:
            lines.append("")
            lines.append(style_block)
        lines.append("")
        lines.append("## 待审提示词（逐张：契约 + 初稿画面描述）")
        for slot in plan.get("slots") or []:
            slot_id = str(slot.get("slot_id") or "")
            lines.append(f"\n### 第{slot.get('number')}张｜{slot.get('role') or slot_id}"
                         f"（{slot_id}）｜{'信息图（文字由系统本地排版）' if slot.get('kind') == 'info' else '纯摄影'}")
            contract = slot_contract_block(slot_id)
            if contract:
                lines.append(contract)
            lines.append(f"  初稿画面描述：{slot.get('prompt') or ''}")
        if lint.get("errors") or lint.get("warnings"):
            lines.append("")
            lines.append("## 系统体检已发现的问题（**不要重复报**，只在改写时一并修掉）")
            lines.extend(f"- ❌ {item}" for item in (lint.get("errors") or [])[:10])
            lines.extend(f"- ⚠️ {item}" for item in (lint.get("warnings") or [])[:10])
        if task_brief:
            lines.append("")
            lines.append(f"## 额外的修改要求（可能来自审查员对上一轮成图的意见）\n{task_brief}")
        return "\n".join(lines)

    def _message(self, plan, review) -> str:
        header = f"🎨 提示词审核优化（{len(plan.get('slots') or [])} 张）"
        return header + "\n" + review_summary(review, threshold=None)

    # ── Mock / 兜底 ──

    def _mock_review(self, plan, lint) -> dict:
        from src.providers.mock import MOCK_PROMPT_REVIEW
        review = dict(MOCK_PROMPT_REVIEW)
        review["status"] = "mock"
        review["lint_digest"] = lint.get("digest")
        review["message"] = "⏭ 提示词审核：演示数据（Mock 模式未做真实审核）"
        return review

    def _load_prompt(self, path: str) -> str:
        try:
            cfg = load_yaml(path)
            return cfg.get("system", "你是电商视觉设计方向的提示词审核优化员。")
        except Exception:
            return "你是电商视觉设计方向的提示词审核优化员。"
