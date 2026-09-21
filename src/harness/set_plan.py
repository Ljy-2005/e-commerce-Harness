"""套图编排 —— 把提示词产物规范成"可直接上传的一整套"

## 为什么有这个东西（用户反馈）

"我看了提示词生成的内容，我发现生成的不是一套可直接上传的套图，而只是生成了套图中的一张的
多张选择，这不符合生产。"

核实：`src/agents/image_gen.py` 只取 `artifacts.prompts.main_image.prompt`，然后**用同一个
提示词连打 `variants`(=3) 次** —— 产出的确实是"某一张图的三个候选"。而 `prompt_gen` 明明已经
生成了 `main_image + scene_images + social_images` 三种版式，全被丢掉。

同一根因的另一半（2026-09-18 用户第 2 条反馈）："他未有明确约束每一张该有的提示词，
举个例子'第一张提示词：我想要…，第二张提示词：…，第三张提示词：…'这样可以有效的指定生成照片。"
→ 现在每个槽位带 **`number`（第 N 张）** 与从 `config/platforms.yaml` 槽位目录补齐的
**`intent / design / must / avoid / keep_clear`** 契约；最终提示词由 `harness/image_prompt.py`
组装成六段式。

## 现在

提示词生成员输出 `set_plan.slots`（每个槽位一张图，见 `config/platforms.yaml` 的槽位设置），
生图员**按槽位出图**，落盘文件名带槽位 → 导出的 ZIP 就是一套可直接上传的图。

本模块只做规范化与校验（纯函数），不调用任何 Provider。
"""

from typing import Any

from src.core.platforms import (
    background_policy,
    get_platform,
    platform_detail_slots,
    platform_slot_limits,
    platform_slots,
    slot_design,
    slot_forbid,
    slot_intent,
    slot_keep_clear,
    slot_kind,
    slot_label,
    slot_layout,
    slot_must,
    slot_usage,
)

# 单个槽位允许的字段（模型多给的一律丢弃，避免脏字段流进产物与前端）
# `kind/usage/layout/intent/design/must/avoid/keep_clear` 不是模型给的，而是**从槽位目录补齐**的；
# 这条边界很关键：审核优化员只能改《画面》段，硬约束（must/avoid/keep_clear）代码说了算。
_SLOT_FIELDS = ("number", "slot_id", "role", "prompt", "negative_prompt", "composition",
                "background", "text_in_image", "uses_reference", "aspect", "notes",
                "kind", "usage", "layout", "intent", "design", "must", "avoid",
                "keep_clear", "palette", "bg_policy")

MAX_SLOT_PROMPT_CHARS = 4000


def _text(value, limit: int = 0) -> str:
    if value is None or isinstance(value, (dict, list, tuple, set)):
        return ""
    text = str(value).strip()
    return text[:limit] if limit else text


def _text_list(value) -> list[str]:
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, (list, tuple, set)):
        items = list(value)
    else:
        items = []
    return [text for text in (_text(item) for item in items) if text]


def _as_bool(value, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("true", "yes", "1", "是"):
            return True
        if low in ("false", "no", "0", "否"):
            return False
    return default


def platform_slot_plan(platform, override: list[str] | None = None) -> list[str]:
    """该平台期望的槽位序列：`config/image.yaml` 覆盖 → 平台档案的**主图槽位 + 详情图槽位**

    主图在前、详情图在后（信息类图文版面通常放详情位）。新增槽位角色（成分/人群/功效/
    用法…）只改 `config/platforms.yaml`。
    """
    if override:
        return [str(item).strip() for item in override if str(item).strip()]
    return platform_slots(platform) + platform_detail_slots(platform)


def normalize_slot(raw: dict, index: int, *, fallback_id: str = "", number: int = 0,
                   palette: dict | None = None, policy: str = "design_allowed") -> dict | None:
    """规范化单个槽位；`prompt` 为空的槽位返回 None（空槽位不能出图）

    - `number`：**按平台规范顺序**编号（不按模型输出顺序）—— 用户要的"第几张"必须是
      平台规范里的第几张，否则"第 3 张"在人机之间指的不是同一张图；
    - `intent/design/must/avoid/keep_clear`：从槽位目录补齐（模型改不动）；
    - `background`：`white_required` 平台强制纯白；`design_allowed` 平台允许模型给设计底色。
    """
    if not isinstance(raw, dict):
        return None
    prompt = _text(raw.get("prompt"), MAX_SLOT_PROMPT_CHARS)
    if not prompt:
        return None
    slot_id = _text(raw.get("slot_id")) or fallback_id or f"slot_{index}"
    kind = slot_kind(slot_id)

    background = _text(raw.get("background"))
    if policy == "white_required":
        background = "#FFFFFF"
    elif not background and kind == "photo" and isinstance(palette, dict):
        background = _text(palette.get("background"))

    slot: dict[str, Any] = {
        "number": int(number) if number else index,
        "slot_id": slot_id,
        "role": _text(raw.get("role")) or slot_label(slot_id),
        "prompt": prompt,
        "negative_prompt": _text(raw.get("negative_prompt"), MAX_SLOT_PROMPT_CHARS),
        "composition": _text(raw.get("composition")),
        "background": background,
        # 默认不生成文字：需要文字的版面由**本地排版**绘制（不要让模型画字）
        "text_in_image": _as_bool(raw.get("text_in_image"), False),
        "uses_reference": _text_list(raw.get("uses_reference")),
        "aspect": _text(raw.get("aspect")),
        "notes": _text(raw.get("notes")),
        # 产出方式、位置与**逐张契约**来自 `config/platforms.yaml` 的槽位目录（模型说了不算）
        "kind": kind,
        "usage": slot_usage(slot_id),
        "layout": slot_layout(slot_id),
        "intent": slot_intent(slot_id),
        "design": slot_design(slot_id),
        "must": slot_must(slot_id),
        "avoid": slot_forbid(slot_id),
        "keep_clear": slot_keep_clear(slot_id),
        "palette": dict(palette) if isinstance(palette, dict) else {},
        "bg_policy": policy,
    }
    return {key: slot[key] for key in _SLOT_FIELDS}


def normalize_set_plan(prompts, *, platform, slot_override: list[str] | None = None,
                       palette: dict | None = None,
                       only_slots: list[str] | None = None) -> dict[str, Any] | None:
    """从提示词产物里取出套图编排；没有 `set_plan` 时返回 None（调用方走旧的单图路径）

    **主图与详情图分别限额**：平台主图有硬上限（如淘宝 5 张），而信息类图文版面通常放
    详情位（额度宽松）。此前只按主图上限截断，会把成分图/人群图这类详情图一起砍掉。

    Args:
        slot_override: 期望的**槽位顺序**（缺 slot_id 时的补位依据 / 编号依据）
        only_slots: **只出这些槽位**（会话级子集；空 = 全出）——最小付费冒烟与"只重出某几张"

    Returns:
        `{"platform", "registered", "slots": [...], "notes": [...]}` 或 None
    """
    if not isinstance(prompts, dict):
        return None
    raw = prompts.get("set_plan")
    if isinstance(raw, dict):
        raw_slots = raw.get("slots")
        plan_platform = _text(raw.get("platform"))
    elif isinstance(raw, list):
        raw_slots, plan_platform = raw, ""
    else:
        return None
    if not isinstance(raw_slots, list) or not raw_slots:
        return None

    profile = get_platform(platform or plan_platform)
    slug = profile.get("slug") or platform
    notes: list[str] = []
    main_limit, detail_limit = platform_slot_limits(slug)
    planned = platform_slot_plan(slug, slot_override)
    # 会话级槽位子集（`POST /api/sessions` 的 `slots`）：只出这些槽位（最小付费冒烟 /
    # 只重出某几张）。它不只是"编号顺序"——必须**真的过滤掉**其余槽位。
    subset = [str(item).strip() for item in (only_slots or []) if str(item or "").strip()]
    planned_numbers = {slot_id: index for index, slot_id in enumerate(planned, start=1)}
    next_number = len(planned) + 1
    policy = background_policy(slug)

    slots: list[dict] = []
    seen: set[str] = set()
    counts = {"main": 0, "detail": 0}
    for index, item in enumerate(raw_slots, start=1):
        # 模型没给 slot_id 时按平台槽位顺序补位，保证落盘文件名可读
        planned_iter = (slot for slot in planned if slot not in seen)
        fallback = next(planned_iter, "")
        slot_id_hint = _text(item.get("slot_id")) if isinstance(item, dict) else ""
        number = planned_numbers.get(slot_id_hint or fallback, next_number)
        slot = normalize_slot(item, index, fallback_id=fallback, number=number,
                              palette=palette, policy=policy)
        if slot is None:
            notes.append(f"第 {index} 个槽位缺少提示词，已跳过")
            continue
        if slot["slot_id"] in seen:
            notes.append(f"槽位 {slot['slot_id']} 重复，已跳过")
            continue
        if subset and slot["slot_id"] not in subset:
            notes.append(f"槽位 {slot['slot_id']} 不在本次槽位子集内，已跳过")
            continue
        usage = slot["usage"]
        limit = main_limit if usage == "main" else detail_limit
        label = "主图" if usage == "main" else "详情图"
        if limit and counts[usage] >= limit:
            notes.append(f"{profile.get('label') or platform} {label}上限 {limit} 张，"
                         f"多余的槽位已截断（{slot['slot_id']}）")
            continue
        seen.add(slot["slot_id"])
        counts[usage] += 1
        if number >= next_number:
            next_number += 1
        slots.append(slot)

    if not slots:
        return None

    # 缺哪些平台槽位（一张都不能少）—— 协调者与用户都要看得见
    missing = [slot_id for slot_id in planned if slot_id not in seen]
    if missing:
        notes.append("缺少平台槽位：" + "、".join(f"{slot_id}（{slot_label(slot_id)}）"
                                                   for slot_id in missing))

    slots.sort(key=lambda item: item["number"])   # 输出顺序 = 平台规范顺序
    return {
        "platform": profile.get("slug") or _text(platform),
        "platform_label": profile.get("label") or "",
        "registered": bool(profile.get("registered")),
        "background_policy": policy,
        "slots": slots,
        "missing_slots": missing,
        "notes": notes,
    }


def validate_plan(plan, *, platform=None) -> dict[str, Any]:
    """结构校验（纯函数）：槽位覆盖、编号连续性、主/详情限额、空提示词

    内容层面的规则（身份词、包装版式复述、设计语言缺失…）在 `harness/prompt_lint.py`。
    """
    errors: list[str] = []
    warnings: list[str] = []
    slots = [slot for slot in ((plan or {}).get("slots") or []) if isinstance(slot, dict)]
    slug = (plan or {}).get("platform") or platform or ""
    expected = platform_slot_plan(slug)
    if not slots:
        errors.append("套图编排为空：没有可出图的槽位")
    if expected:
        produced = {str(slot.get("slot_id") or "") for slot in slots}
        missing = [slot_id for slot_id in expected if slot_id not in produced]
        if missing:
            errors.append("缺少平台槽位：" + "、".join(missing))
    numbers = [slot.get("number") for slot in slots]
    if numbers and sorted(numbers) != list(range(1, len(numbers) + 1)):
        warnings.append(f"槽位编号不连续：{numbers}")
    for slot in slots:
        slot_id = str(slot.get("slot_id") or "")
        if not str(slot.get("prompt") or "").strip():
            errors.append(f"槽位 {slot_id} 没有提示词")
        if not slot.get("intent"):
            warnings.append(f"槽位 {slot_id} 缺少 intent（这一张要让买家看懂什么）")
        if not slot.get("must"):
            warnings.append(f"槽位 {slot_id} 没有硬性要求（must）")
    return {"errors": errors, "warnings": warnings, "expected": len(expected),
            "produced": len(slots)}


def finalize_prompts(content: dict, platform, *, palette: dict | None = None) -> dict:
    """补齐平台归一化结果、套图编排与摘要（下游与前端都要用）

    从 `PromptGeneratorAgent._finalize` 提出来：审核优化员改写提示词后也要走同一套收尾，
    否则 `main_image` 与新编排会不一致（真实事故：改写后主图提示词还是旧版）。
    """
    from src.core.platforms import resolve_platform

    payload = dict(content)
    slug = resolve_platform(platform)
    payload["platform"] = slug or str(platform or "")
    plan = normalize_set_plan(payload, platform=platform, palette=palette)
    if plan:
        payload["set_plan"] = plan
        payload["set_plan_summary"] = set_plan_summary(plan)
        # 向后兼容：老代码/老测试读 `main_image.prompt`。有套图编排时，
        # 首个槽位**就是**主图 —— 用槽位覆盖（而不是 setdefault），否则会留下
        # 模型顺手写的旧单图提示词，与套图不一致。
        main = payload.get("main_image")
        main = dict(main) if isinstance(main, dict) else {}
        first = plan["slots"][0]
        main.update({
            "prompt": first["prompt"],
            "negative_prompt": first.get("negative_prompt", ""),
            "composition": first.get("composition", ""),
            "slot_id": first["slot_id"],
            "number": first.get("number", 1),
        })
        payload["main_image"] = main
    else:
        payload["set_plan_note"] = (
            "提示词未包含套图编排（set_plan），本次按单图多候选处理 —— "
            "交付物不是可上传的整套图，建议重跑提示词生成员")
    return payload


def set_plan_coverage(plan, images) -> dict[str, Any]:
    """套图完成度：哪些槽位已出图、哪些还缺（协调者据此判断"是否成套"）

    信息类槽位另有 `blocked_slots`：底图出了但**文案缺事实依据**（如包装正面看不到成分表），
    这类图不能用（是编造的），也不算"成套"。
    """
    slots = [slot["slot_id"] for slot in (plan or {}).get("slots", [])]
    produced = {str(img.get("slot_id") or "") for img in (images or [])
                if isinstance(img, dict) and img.get("slot_id")}
    blocked = {str(img.get("slot_id") or "") for img in (images or [])
               if isinstance(img, dict) and img.get("text_status") == "blocked"}
    done = [slot for slot in slots if slot in produced and slot not in blocked]
    missing = [slot for slot in slots if slot not in done]
    return {
        "expected": len(slots),
        "produced": len(done),
        "done_slots": done,
        "missing_slots": missing,
        "blocked_slots": [slot for slot in slots if slot in blocked],
        "complete": bool(slots) and not missing,
    }


def set_plan_summary(plan) -> str:
    """给人看的一行摘要（群聊/产物提示）"""
    if not isinstance(plan, dict) or not plan.get("slots"):
        return ""
    label = plan.get("platform_label") or plan.get("platform") or "平台"
    names = "、".join(f"第{slot.get('number')}张 {slot['slot_id']}({slot['role']})"
                      for slot in plan["slots"])
    text = f"套图编排：{label} 共 {len(plan['slots'])} 张 —— {names}"
    if plan.get("notes"):
        text += "；" + "；".join(plan["notes"])
    return text


def set_plan_lines(plan) -> list[str]:
    """逐张清单（群聊播报用："第一张…第二张…"）"""
    lines: list[str] = []
    for slot in (plan or {}).get("slots", []):
        usage = "主图" if slot.get("usage") == "main" else "详情图"
        kind = "信息图·文字本地排版" if slot.get("kind") == "info" else "纯摄影"
        intent = slot.get("intent") or ""
        line = (f"第{slot.get('number')}张｜{slot.get('role') or slot.get('slot_id')}"
                f"（{slot.get('slot_id')}）｜{usage}｜{kind}")
        if intent:
            line += f"｜想要：{intent}"
        lines.append(line)
    return lines
