"""审查员 — 6 维度质量评分（以用户上传的真实商品图为基准）

实测事故：审查员此前**只收到生成图**，没有原图 —— "商品还原度"这个维度没有比对基准，
只能靠常识猜（那次猜中了被臆造的 `NUTRIVA®`，但不可靠）。

现在：
- 送审图 = **图一（用户上传的真实商品图）+ 生成图**，并明确告知"还原度以图一为基准"；
- 同时给出**本地体检的客观数值**（背景白度/水印区/主体占比/清晰度/与参考图的身份相似度），
  让主观评分有客观锚点；还能识别两种退化：身份丢失（纯文生图）与复制原图（纯图生图）；
- 提示词里带**商品身份卡**（已确认的品牌/品名/规格/认证），要求逐项给出"一致/不一致/缺失"。
"""

from src.agents.base import BaseAgent
from src.core.config import load_yaml
from src.harness.product_identity import identity_card_block, normalize_identity

# 分批送审：改前只审前 3 张（`image_parts(images, limit=3)`），审查员自己报告
# "main_ingredients、main_benefits 未送入审查" —— 一套 10 张时等于一半没审。
REVIEW_BATCH_SIZE = 3
REVIEW_MAX_BATCHES = 4          # ≤12 张；再多就记账说明（避免 vision 调用无限增长）

# 兜底审查提示词：`config/prompts/reviewer.yaml` 读不到时使用。
# 必须自带**评分锚点与判定规则** —— 只列维度名的话，审查员拿不到评分标准，
# 打出来的分会变成凭感觉（改前就是这样：一行 5 维度名 + 无任何阈值）。
REVIEW_FALLBACK_PROMPT = """你是专业的电商图片审查专家。图片的评判标准是**品牌视觉设计感**，
不是"够不够写实"——不要因为"背景不是真实场景"扣分，要因为"画面平淡、像随手拍、配色杂乱、
没有视觉重心、留白不足"扣分。

## 6 维度评分（每项 0–100）
1. 质感 (Texture)：材质表现是否可信，边缘是否干净
2. 光影 (Lighting)：光位与光质是否讲究，投影是否干净
3. 构图 (Composition)：视觉重心是否明确、主体占比与留白是否有节奏
4. 商品还原度 (Product Fidelity)：是否准确还原商品特征（形状/颜色/文字）
5. 平台适配 (Platform Fit)：是否符合目标平台的规范与调性
6. 画面真实感 (Realism)：是否像"精修商业图"而不是"AI 图"（AI 塑料感、过饱和、
   HDR 味、过度锐化、伪影、糊字、融化变形都要扣分）

## 评分锚点
- 90–100：像品牌官方主图，一眼"正规、有档次"，可直接上架
- 75–89：干净商业图，能用
- 60–74：平淡、模板感、像随手拍 → 需要改进
- < 60：廉价感／明显 AI 感／商品还原出错 → 不合格

## 判定规则
- overall_score >= 75：pass
- overall_score < 75：retry
- 商品关键特征（文字/标志）错误：fail

输出 JSON：{"overall_score": 0, "dimension_scores": {"texture": 0, "lighting": 0,
"composition": 0, "product_fidelity": 0, "platform_fit": 0, "realism": 0},
"top_issues": [], "top_praises": [], "verdict": "pass|retry|fail"}"""


class ReviewerAgent(BaseAgent):
    """使用 Vision 能力，按 6 维度审查生成图片"""

    meta_name = "审查员"
    timeout_ms = 300_000

    async def _execute_impl(self, task_brief: str, session) -> dict:
        artifacts = session.get("artifacts", {})
        images = artifacts.get("images", [])
        analysis = artifacts.get("analysis", {})

        from src.providers.mock import MockLLMProvider
        if self.provider is None or isinstance(self.provider, MockLLMProvider):
            return self._mock_review()

        # A31：图源四种形态统一处理（base64 / data URI / 本地落盘文件 / 远程 URL）。
        from src.harness.vision_payload import image_parts, reference_image_parts
        ref_parts, ref_notes, _ = reference_image_parts(session, limit=1)

        system_prompt = self._load_prompt("config/prompts/reviewer.yaml")
        identity = artifacts.get("product_identity") or normalize_identity(analysis)

        batches = self._batch_images(images)
        if not batches:
            # 无图可审：确定性报错，不烧 token、也不指望模型自己发现"没附图"
            reason = "未找到任何可用的生成图"
            return {
                "error": f"NO_IMAGE_ACCESSIBLE: {reason}",
                "message": "本次审查未收到任何可访问的生成图，无法进行 6 维度评分。",
                "overall_score": None,
                "dimension_scores": {"texture": None, "lighting": None,
                                     "composition": None, "product_fidelity": None,
                                     "platform_fit": None, "realism": None},
                "top_issues": [f"图像不可用：{reason}"],
                "top_praises": [],
                "verdict": "retry",
                "needs_human_review": True,
                "review_blocked_reason": "no_image_accessible",
            }

        batch_results: list[dict] = []
        notes_all: list[str] = list(ref_notes)
        reviewed_slots: list[str] = []
        unreviewed: list[str] = []
        for index, batch in enumerate(batches, start=1):
            parts, batch_notes = await image_parts(batch, limit=REVIEW_BATCH_SIZE)
            notes_all.extend(batch_notes)
            if not parts:
                continue
            slots = [str(img.get("slot_id") or img.get("prompt_name") or f"#{index}")
                     for img in batch]
            reviewed_slots.extend(slots)
            header = self._build_header(session, analysis, identity, len(ref_parts),
                                        len(parts), notes_all, task_brief,
                                        batch_index=index, batch_total=len(batches),
                                        slots=slots)
            user_content = [{"type": "text", "text": header}, *ref_parts, *parts]
            result = await self.provider.chat_with_vision(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                **self._model_kwargs(),
            )
            review = self._content_or_error(result, self._mock_review())
            if "error" in review:
                return review   # 真实调用失败：原样上报，不伪装成模板审查结果
            batch_results.append(self._tag_batch(review, slots, index))

        if not batch_results:
            return {"error": "NO_IMAGE_ACCESSIBLE: 所有分批送审都未能取到可用图像",
                    "message": "分批送审没有任何一批取到图像。", "verdict": "retry",
                    "overall_score": None, "needs_human_review": True}

        expected = [str(img.get("slot_id") or img.get("prompt_name") or "")
                    for img in images if str(img.get("slot_id") or img.get("prompt_name") or "")]
        unreviewed = [slot for slot in expected if slot not in reviewed_slots]
        if unreviewed:
            notes_all.append("未送审的槽位：" + "、".join(unreviewed[:8]))

        review = self._merge_batches(batch_results)
        # 多图审查：模型常返回逐变体 `{"results": [...]}`（真实会话实测），这里先汇总出
        # 顶层判定，否则引擎门禁只能判为"无法解析"（A43）
        from src.harness.review_normalize import normalize_review_payload
        review, aggregated = normalize_review_payload(review)
        review["aggregation_note"] = aggregated or f"分 {len(batch_results)} 批送审"
        review["reviewed_slots"] = reviewed_slots
        if unreviewed:
            review["unreviewed_slots"] = unreviewed
        review["iteration"] = session.get("turn_count", 0)
        review["reference_compared"] = bool(ref_parts)
        return review

    # ── 分批送审 ──

    def _batch_images(self, images) -> list[list[dict]]:
        """按槽位切批（≤3 张/批，≤4 批）；无可用图像数据时返回空列表"""
        usable = [img for img in (images or []) if isinstance(img, dict)
                  and (img.get("image_url") or img.get("base64_data") or img.get("saved_path"))]
        batches: list[list[dict]] = []
        for start in range(0, len(usable), REVIEW_BATCH_SIZE):
            batches.append(usable[start:start + REVIEW_BATCH_SIZE])
        return batches[:REVIEW_MAX_BATCHES]

    def _tag_batch(self, review: dict, slots: list[str], index: int) -> dict:
        """给一批结果打上槽位名（逐图明细的 variant 用槽位 id，便于前端对号入座）"""
        review = dict(review) if isinstance(review, dict) else {}
        results = review.get("results")
        if isinstance(results, list) and results:
            for position, entry in enumerate(results):
                if isinstance(entry, dict) and position < len(slots):
                    entry.setdefault("variant", slots[position])
                    entry.setdefault("slot_id", slots[position])
        else:
            # 模型只给了整体评分（没有逐图明细）：按本批槽位补齐一条，别让这张图"没被审过"
            review["results"] = [{
                "variant": slots[0] if slots else f"batch_{index}",
                "slot_id": slots[0] if slots else "",
                "overall_score": review.get("overall_score"),
                "dimension_scores": review.get("dimension_scores") or {},
                "top_issues": review.get("top_issues") or [],
                "verdict": review.get("verdict"),
                "batch_note": f"第 {index} 批（{len(slots)} 张）整体评分",
            }]
        return review

    def _merge_batches(self, batch_results: list[dict]) -> dict:
        """把多批结果合并成一份带 `results[]` 的载荷（交给 `normalize_review_payload` 汇总）"""
        merged: dict = {"results": [], "top_issues": [], "top_praises": [],
                        "fidelity_findings": [], "needs_human_review": False}
        for review in batch_results:
            for entry in (review.get("results") or []):
                if isinstance(entry, dict):
                    merged["results"].append(entry)
            for key in ("top_issues", "top_praises"):
                for item in (review.get(key) or []):
                    if item not in merged[key]:
                        merged[key].append(item)
            for finding in (review.get("fidelity_findings") or []):
                if isinstance(finding, dict) and finding not in merged["fidelity_findings"]:
                    merged["fidelity_findings"].append(finding)
            if review.get("needs_human_review"):
                merged["needs_human_review"] = True
            for key in ("fix_direction", "fix_suggestions"):
                if review.get(key) and not merged.get(key):
                    merged[key] = review[key]
        return merged

    def _build_header(self, session, analysis, identity, ref_count: int, gen_count: int,
                      notes: list[str], task_brief: str, *, batch_index: int = 1,
                      batch_total: int = 1, slots=None) -> str:
        """审查头：把"哪张是基准""客观体检数值"讲清楚"""
        lines = []
        if ref_count:
            lines.append(f"图一 = 用户上传的**真实商品图**（还原度基准，共 {ref_count} 张）；"
                         f"其后 {gen_count} 张 = 本次生成的图。")
            lines.append("请以图一为基准，逐项比对生成图的品牌文字、包装文案、图案、配色与形制。")
        else:
            lines.append(f"本次只有 {gen_count} 张生成图，**没有拿到上传的真实商品图**"
                         "（无法做还原度比对，请在结论里说明这一点）。")
        if batch_total > 1:
            lines.append(f"（这是第 {batch_index}/{batch_total} 批送审，"
                         f"本批槽位：{'、'.join(slots or []) or '未标注'}；"
                         "**每一批都要给顶层 verdict**，系统会按最严重的一批取判定）")

        quality = session.get("artifacts", {}).get("quality_report")
        if isinstance(quality, dict) and quality.get("count"):
            lines.append("\n## 本地体检数值（客观、非主观判断，可直接引用）")
            lines.append(f"- 白底合格：{'是' if quality.get('white_bg_ok') else '否'}"
                         f"；无水印：{'是' if quality.get('watermark_free') else '否'}")
            if quality.get("identity_lost"):
                lines.append(f"- ⚠️ 与参考图身份相似度过低的图：{quality['identity_lost']}"
                             "（疑似模型在凭文字想象商品）")
            if quality.get("near_copy"):
                lines.append(f"- ⚠️ 与参考图高度相似、疑似直接复制的图：{quality['near_copy']}")
            for issue in (quality.get("issues") or [])[:6]:
                lines.append(f"- {issue}")

        # 出图前那套提示词的审美审核结论（改图/改提示词时的重要线索）
        prompt_review = session.get("artifacts", {}).get("prompt_review")
        if isinstance(prompt_review, dict) and prompt_review.get("scores"):
            lines.append("\n## 出图前提示词审美审核（供你判断'是提示词还是模型的问题'）")
            lines.append(f"- 审美分：{prompt_review['scores']}")
            if prompt_review.get("revised_slots"):
                lines.append(f"- 已按审美改写过的槽位：{prompt_review['revised_slots']}")

        # 用户审美锚点（"就要这种感觉"）：成图审查只加**锚点**，不加设计档案 ——
        # 锚点是用户的标准，审美维度据此判定（档案是"怎么写提示词"，与成图判定无关）
        anchor_block = self._anchor_block(session, analysis)
        if anchor_block:
            lines.append("")
            lines.append(anchor_block)

        lines.append("\n## 商品身份（已确认的事实基准）")
        lines.append(identity_card_block(identity))
        lines.append("\n## 原始商品分析")
        lines.append(str(analysis))
        lines.append("\n## 审查要求")
        lines.append(task_brief)
        if notes:
            lines.append("\n## 图像获取说明")
            lines.extend(f"- {note}" for note in notes)
        return "\n".join(lines)

    def _mock_review(self) -> dict:
        from src.providers.mock import MOCK_REVIEW
        return dict(MOCK_REVIEW)

    def _anchor_block(self, session, analysis) -> str:
        """用户审美锚点（`config/style_library.yaml → anchors` + 用户词条里的锚点）"""
        try:
            from src.core.platforms import platform_slot_table
            from src.harness.style_library import render_anchor_block, select_by_slot

            task = (session or {}).get("task") or {}
            prompts = (session or {}).get("artifacts", {}).get("prompts") or {}
            plan = prompts.get("set_plan") if isinstance(prompts, dict) else None
            slots = (plan or {}).get("slots") or platform_slot_table(task.get("platform", ""))
            selection = select_by_slot(slots, analysis=analysis, platform=task.get("platform", ""),
                                       tenant_id=str((session or {}).get("tenant_id") or "") or None)
            return render_anchor_block(selection.get("anchors"), audience="review")
        except Exception:  # noqa: BLE001 — 锚点取不到不能挡住审查
            return ""

    def _load_prompt(self, path: str) -> str:
        try:
            cfg = load_yaml(path)
            return cfg.get("system", REVIEW_FALLBACK_PROMPT)
        except Exception:
            return REVIEW_FALLBACK_PROMPT
