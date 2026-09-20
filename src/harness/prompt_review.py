"""提示词审核优化结果 —— 规范化、阈值挑选与**安全落地**

## 定位（用户 2026-09-18 澄清）

用户加这个环节的初衷不是"抓违规"，而是**让提示词接近大众电商商品图的审美**：
"免得跑出一些符合基本要求但是图片审美完全不合格的图片"。

所以审核优化员的主职是**改写画面描述**（版式/品牌色系/背景设计/光影精修/材质/留白），
而不是当门卫。抓违规交给零成本的 `harness/prompt_lint.py`（确定性、可测试）。

## 三条安全边界（写在代码里，不靠模型自觉）

1. **只能改《画面》段**：落地时只替换 `slot.prompt`，`must / avoid / keep_clear / palette /
   number / intent` 一律来自配置，审核员改不掉 —— 物理上杜绝"改写把硬约束改没"；
2. **改完必须过体检**：逐槽位只在该槽位 error 数不增加时才接受（见 `apply_prompt_patches`）；
3. **不静默**：接受与被拒的改写都记进 `applied / rejected`，产物与前端都展示。
"""

from typing import Any

from src.harness.prompt_lint import lint_prompts, prompt_digest


def _score(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(max(0.0, min(100.0, number)), 1)


def normalize_prompt_review(raw) -> dict[str, Any]:
    """规范化审核优化员的输出（宽松）：`{verdict, scores, slots, notes, missing_slots}`"""
    payload = raw if isinstance(raw, dict) else {}
    scores: dict[str, float] = {}
    raw_scores = payload.get("aesthetic_scores")
    if isinstance(raw_scores, dict):
        for key, value in raw_scores.items():
            number = _score(value)
            if number is not None:
                scores[str(key)] = number

    slots: list[dict] = []
    for item in (payload.get("slots") or []):
        if not isinstance(item, dict):
            continue
        slot_id = str(item.get("slot_id") or "").strip()
        if not slot_id:
            continue
        score = _score(item.get("score"))
        if score is not None:
            scores.setdefault(slot_id, score)
        defects = [str(defect).strip() for defect in (item.get("defects") or [])
                   if str(defect or "").strip()]
        scene = str(item.get("scene") or "").strip()
        slots.append({"slot_id": slot_id, "score": score, "defects": defects, "scene": scene})

    verdict = str(payload.get("verdict") or "").strip().lower()
    if verdict not in ("pass", "revise"):
        verdict = "revise" if any(item["scene"] for item in slots) else "pass"
    notes = [str(note).strip() for note in (payload.get("notes") or []) if str(note or "").strip()]
    missing = [str(item).strip() for item in (payload.get("missing_slots") or [])
               if str(item or "").strip()]
    return {
        "verdict": verdict,
        "overall_score": _score(payload.get("overall_score")),
        "scores": scores,
        "slots": slots,
        "notes": notes,
        "missing_slots": missing,
    }


def select_patches(plan, review, *, threshold: float = 85.0) -> tuple[list[dict], list[dict]]:
    """挑出要落地的改写稿：低于阈值 + 给了 `scene` + 与原文确实不同

    Returns: `(patches, skipped)`；`skipped` 记录"为什么没用这条改写"（可展示）。
    """
    by_slot = {str(slot.get("slot_id") or ""): slot
               for slot in ((plan or {}).get("slots") or []) if isinstance(slot, dict)}
    patches: list[dict] = []
    skipped: list[dict] = []
    for item in (review or {}).get("slots") or []:
        slot_id = str(item.get("slot_id") or "")
        scene = str(item.get("scene") or "").strip()
        score = item.get("score")
        target = by_slot.get(slot_id)
        if target is None:
            skipped.append({"slot_id": slot_id, "reason": "槽位不在本次编排里"})
            continue
        threshold_hit = score is not None and float(score) < float(threshold)
        if not scene:
            skipped.append({"slot_id": slot_id,
                            "reason": f"未给出改写稿（审美分 {score}）" if threshold_hit
                            else f"审美分 {score} 达标，无需改写"})
            continue
        if not threshold_hit:
            skipped.append({"slot_id": slot_id, "reason": f"审美分 {score} 达标，保留原稿"})
            continue
        if scene == str(target.get("prompt") or "").strip():
            skipped.append({"slot_id": slot_id, "reason": "改写稿与原文相同"})
            continue
        patches.append({"slot_id": slot_id, "scene": scene, "score": score,
                        "defects": item.get("defects") or []})
    return patches, skipped


def _slot_errors(report, slot_id: str) -> int:
    return sum(1 for item in (report.get("findings") or [])
               if item.get("level") == "error" and str(item.get("slot_id") or "") == slot_id)


def apply_prompt_patches(plan, patches, *, platform=None, identity=None,
                         style_entries=None) -> dict[str, Any]:
    """把改写稿写回槽位（**只换 `prompt`**），并逐槽位用体检把关

    逐槽位策略：应用某条改写后，只要该槽位的 error 数**不增加**就接受；否则回滚该条并记原因。
    这样"改写"永远不可能让一张图变得更不合规。

    `style_entries`：本次注入的风格档案（见 `prompt_lint.lint_prompts`）——改写稿若变成
    "逐字照抄档案"会被记为新的 warning（不阻断，warning 本来就不阻断）。
    """
    base_plan = {"platform": (plan or {}).get("platform") or platform or "",
                 "background_policy": (plan or {}).get("background_policy") or "",
                 "slots": [dict(slot) for slot in ((plan or {}).get("slots") or [])
                           if isinstance(slot, dict)]}
    if not base_plan["slots"] or not patches:
        return {"plan": plan, "applied": [], "rejected": [], "notes": [], "lint": None}

    slug = platform or base_plan["platform"]
    current_plan = base_plan
    current_lint = lint_prompts(current_plan, platform=slug, identity=identity,
                                style_entries=style_entries)
    applied: list[dict] = []
    rejected: list[dict] = []
    for patch in patches:
        slot_id = str(patch.get("slot_id") or "")
        candidate = {"platform": current_plan["platform"],
                     "background_policy": current_plan["background_policy"],
                     "slots": [dict(slot) for slot in current_plan["slots"]]}
        hit = False
        for slot in candidate["slots"]:
            if str(slot.get("slot_id") or "") == slot_id:
                slot["prompt"] = str(patch.get("scene") or "")
                hit = True
                break
        if not hit:
            rejected.append({"slot_id": slot_id, "reason": "槽位不存在"})
            continue
        report = lint_prompts(candidate, platform=slug, identity=identity,
                              style_entries=style_entries)
        if _slot_errors(report, slot_id) > _slot_errors(current_lint, slot_id):
            rejected.append({
                "slot_id": slot_id,
                "reason": "改写稿被体检拦下（会产生新的硬伤），保留原稿",
                "errors": [item["message"] for item in report.get("findings") or []
                           if item.get("level") == "error"
                           and str(item.get("slot_id") or "") == slot_id][:3],
            })
            continue
        current_plan, current_lint = candidate, report
        applied.append({"slot_id": slot_id, "score": patch.get("score"),
                        "defects": patch.get("defects") or []})

    # 保留原计划里的其他字段（role/intent/…），只把 prompt 换成被接受的新稿
    new_scenes = {item["slot_id"]: str(patch.get("scene") or "")
                  for item in applied
                  for patch in patches if patch.get("slot_id") == item["slot_id"]}
    new_plan = dict(plan or {})
    new_plan["slots"] = []
    for slot in (plan or {}).get("slots") or []:
        item = dict(slot)
        scene = new_scenes.get(str(item.get("slot_id") or ""))
        if scene:
            item["prompt"] = scene
            item["revised_by_reviewer"] = True
        new_plan["slots"].append(item)
    new_plan["digest"] = prompt_digest(new_plan)

    notes: list[str] = []
    if applied:
        notes.append("已按审美审核改写：" + "、".join(f"{item['slot_id']}" for item in applied))
    if rejected:
        notes.append("改写被拒：" + "、".join(f"{item['slot_id']}（{item['reason']}）"
                                              for item in rejected))
    return {"plan": new_plan, "applied": applied, "rejected": rejected, "notes": notes,
            "lint": current_lint}


def review_summary(review, *, threshold: float | None = None) -> str:
    """一行摘要（群聊/产物用）"""
    review = review if isinstance(review, dict) else {}
    scores = review.get("scores") or {}
    if scores:
        values = [float(value) for value in scores.values()]
        text = f"审美分 平均 {round(sum(values) / len(values), 1)}（{len(values)} 张）"
        low = [key for key, value in scores.items()
               if threshold is not None and float(value) < float(threshold)]
        if low:
            text += f"，低于阈值 {threshold}：" + "、".join(low[:6])
    else:
        text = f"审美分：未给出（{review.get('verdict') or 'unknown'}）"
    return text
