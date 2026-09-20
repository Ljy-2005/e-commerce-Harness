"""信息图文案 —— 从**已确认的事实**里取字，缺依据就标记 blocked（绝不编造）

## 为什么单独成模块

用户要的"一套能实际使用的图"里，成分图/人群图/功效图/规格图都是**图文版面**（信息图）。
这类图上的文字如果交给生图模型画，会出现两个无法接受的问题：
1. **字形错误**——中文（尤其繁体+®）几乎必然糊或写错；
2. **事实编造**——模型会顺手补出包装上根本没有的成分、认证、功效宣称（实测把
   `DEFOEBUENA®` 编成 `NUTRIVA®`；本商品分析员还专门写了"未見成分表，嚴禁臆造奶薊草、
   水飞薊…"）。

所以文字**不交给模型**：由本地排版引擎（`image_compose.py`）绘制，而文案只能来自：
- 身份卡（品牌/品名/规格/认证）—— 来自**本次上传图的视觉识别**；
- 分析结果（成分/卖点/人群）—— 只取"真实可见/已确认"的；
- 包装可见文字转录（`visible_text`）；
- 用户自己填的信息。

**缺依据的槽位一律 `blocked` + 可读原因**（例如"包装正面未见成分表：请上传背面/成分表照片"），
由前端显示成"待补素材"，而不是生成一张编造的成分图。

另外做一道**广告法词表过滤**：极限词/疗效词（"最好""第一""治疗""根治"…）在绘制前拦下，
命中即 blocked（保健品/化妆品尤其敏感）。
"""

import re
from typing import Any

from src.core.platforms import slot_copy_sources, slot_label

# 广告法/平台规范里明确的违禁或高风险表达（命中即**不绘制**，交由人处理）
# 简繁并列：实测本商品的分析结论是**繁体**（港澳台/海外华人市场），只写简体会漏掉
FORBIDDEN_CLAIMS = (
    "最好", "最佳", "最强", "第一", "顶级", "极致", "国家级", "世界级", "全网最低",
    "治疗", "治療", "治愈", "治癒", "根治", "药到病除", "藥到病除", "包治", "疗效", "療效",
    "代替药物", "代替藥物", "替代药物", "替代藥物", "无副作用", "無副作用",
    "绝对安全", "絕對安全", "100%有效", "无效退款", "無效退款", "永久", "立刻见效",
    "立刻見效", "三天见效", "三天見效",
)
# 分析结论里表示"看不见/待确认"的措辞 —— 这类内容不能进文案（同样简繁并列）
_UNCONFIRMED_MARKERS = (
    "不可见", "不可見", "待确认", "待確認", "未見", "未见", "无法确认", "無法確認",
    "不确定", "不確定", "未标注", "未標註", "无标注", "無標註", "看不清楚", "看不清",
)

MAX_TITLE_CHARS = 24
MAX_ITEM_CHARS = 34
MAX_ITEMS = 6
MAX_FOOTER_CHARS = 40


def _clean(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _is_unconfirmed(text: str) -> bool:
    return any(marker in text for marker in _UNCONFIRMED_MARKERS)


def _pick_items(values, *, skip_unconfirmed: bool = True) -> list[str]:
    """把列表/字符串整理成条目：去空、去未确认项、限长限量"""
    if isinstance(values, str):
        values = [values]
    if isinstance(values, dict):
        values = [f"{key}：{value}" for key, value in values.items() if _clean(value)]
    if not isinstance(values, (list, tuple)):
        return []
    items: list[str] = []
    for raw in values:
        text = _clean(raw)
        if not text or (skip_unconfirmed and _is_unconfirmed(text)):
            continue
        text = text[:MAX_ITEM_CHARS]
        if text not in items:
            items.append(text)
        if len(items) >= MAX_ITEMS:
            break
    return items


def find_forbidden_claims(texts) -> list[str]:
    """返回命中的违禁词（空 = 通过）"""
    joined = " ".join(_clean(t) for t in (texts or []))
    return [word for word in FORBIDDEN_CLAIMS if word in joined]


def _audience_items(audience) -> list[str]:
    if not isinstance(audience, dict):
        return _pick_items(audience)
    labels = {"age": "年龄段", "gender": "性别", "lifestyle": "生活状态", "concerns": "关注点"}
    items: list[str] = []
    for key, label in labels.items():
        value = _clean(audience.get(key))
        if not value or _is_unconfirmed(value):
            continue
        items.append(f"{label}：{value}"[:MAX_ITEM_CHARS])
        if len(items) >= MAX_ITEMS:
            break
    return items


def _facts_from_identity(identity: dict) -> dict[str, Any]:
    identity = identity if isinstance(identity, dict) else {}
    return {
        "brand": _clean(identity.get("brand")),
        "product_name": _clean(identity.get("product_name")),
        "spec": _clean(identity.get("spec")),
        "certifications": [_clean(c) for c in (identity.get("certifications") or []) if _clean(c)],
    }


def _facts_from_analysis(analysis: dict) -> dict[str, Any]:
    analysis = analysis if isinstance(analysis, dict) else {}
    angles = analysis.get("marketing_angles") if isinstance(analysis.get("marketing_angles"), dict) else {}
    return {
        "ingredients": _pick_items(analysis.get("ingredients")),
        "features": _pick_items(analysis.get("features")),
        "selling_points": _pick_items(angles.get("selling_points") or analysis.get("features")),
        "scene_suggestions": _pick_items(angles.get("scene_suggestions")),
        "usage": _pick_items(analysis.get("usage") or angles.get("usage")),
        "target_audience": _audience_items(analysis.get("target_audience")),
    }


def _visible_text_lines(analysis: dict) -> list[str]:
    visible = analysis.get("visible_text") if isinstance(analysis, dict) else {}
    lines = (visible or {}).get("lines") if isinstance(visible, dict) else []
    result: list[str] = []
    for row in lines or []:
        text = _clean(row.get("text") if isinstance(row, dict) else row)
        if text and not (isinstance(row, dict) and row.get("legible") is False):
            result.append(text)
    return result[:MAX_ITEMS]


def build_slot_copy(slot_id: str, *, identity=None, analysis=None, user_copy=None) -> dict[str, Any]:
    """生成某个信息槽位的文案（纯函数）

    Returns:
        `{"slot_id", "role", "kind", "title", "items", "footer", "blocked", "reason",
           "sources"}` —— `blocked=True` 时 `items` 为空，调用方**不得**绘制该图。
    """
    sources = slot_copy_sources(slot_id)
    identity_facts = _facts_from_identity(identity)
    analysis_facts = _facts_from_analysis(analysis)
    lines = _visible_text_lines(analysis or {})
    user_facts = user_copy if isinstance(user_copy, dict) else {}

    # 标题优先用**商品名**（比"品牌 / 英文"更像电商信息图的标题），品牌放页脚
    title = _clean(identity_facts["product_name"] or identity_facts["brand"]) or slot_label(slot_id)
    title = title[:MAX_TITLE_CHARS]
    footer_parts = [part for part in (identity_facts["brand"], identity_facts["spec"],
                                      "／".join(identity_facts["certifications"])) if part]
    footer = _clean(" ｜ ".join(footer_parts))[:MAX_FOOTER_CHARS]

    items: list[str] = []
    reason = ""
    # 优先级：**分析结论（已确认事实）→ 用户自己填的 → 内置兜底（身份卡/包装文字）→ 缺依据**
    # 实测踩坑（夹具测试抓到）：原先把"内置兜底"排在用户输入之前，用户明明填了
    # "食用方法/对比对象"，系统仍然判 blocked —— 用户显式提供的内容必须优先
    for source in sources:
        if analysis_facts.get(source):
            items.extend(analysis_facts[source])
        elif source in user_facts:
            items.extend(_pick_items(user_facts[source], skip_unconfirmed=False))
        elif source == "spec":
            # 规格：身份卡（来自本次上传图）→ 包装文字里带数字的行
            if identity_facts["spec"]:
                items.append(f"规格：{identity_facts['spec']}"[:MAX_ITEM_CHARS])
            items.extend([line for line in lines if re.search(r"\d", line)][:2])
        elif source == "certifications":
            items.extend(f"认证：{cert}"[:MAX_ITEM_CHARS] for cert in identity_facts["certifications"])
        elif source == "usage":
            items.extend([line for line in lines if any(
                key in line for key in ("食用", "服用", "用法", "用量", "每日", "每次"))][:3])
        elif source == "compare":
            reason = "对比图需要你提供对比对象（竞品/旧包装照片），否则无法生成"

    items = _pick_items(items, skip_unconfirmed=True)
    if not items and not reason:
        reason = _missing_reason(slot_id, sources)
    blocked = bool(reason and not items)

    forbidden = find_forbidden_claims([title, footer, *items])
    if forbidden:
        blocked = True
        reason = f"文案命中违禁/高风险表述（{'、'.join(forbidden)}），已拦下不绘制，请人工改写"

    return {
        "slot_id": str(slot_id),
        "role": slot_label(slot_id),
        "kind": "info",
        "title": title if not blocked else (title or ""),
        "items": [] if blocked else items,
        "footer": "" if blocked else footer,
        "blocked": blocked,
        "reason": reason,
        "sources": sources,
    }


def _missing_reason(slot_id: str, sources: list[str]) -> str:
    """缺依据时给出**可执行**的原因（而不是一句"没有数据"）"""
    hints = {
        "main_ingredients": "包装正面看不到成分表：请上传包装背面/成分表照片，或手工填写成分",
        "main_usage": "包装上未见食用方法/用法用量：请上传对应面照片或手工填写",
        "main_spec": "未读到规格/净含量信息：请上传含规格的包装面，或在身份卡里补填",
        "main_cert": "未识别到认证信息：请确认包装上的认证字样（如 GMP/蓝帽），或手工填写",
        "main_benefits": "没有可用的卖点/特征素材：请先完成商品分析或手工填写卖点",
        "main_selling_point": "没有可用的卖点素材：请先完成商品分析或手工填写卖点",
        "main_audience": "未识别到适用人群信息：请在商品信息里补充目标人群",
        "main_compare": "对比图需要你提供对比对象（竞品/旧包装照片），否则无法生成",
    }
    return hints.get(str(slot_id),
                     f"该槽位需要 {'/'.join(sources) or '更多商品信息'} 才能排文案，当前没有已确认的素材")
