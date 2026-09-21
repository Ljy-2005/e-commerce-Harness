"""商品身份卡 —— 全链路唯一的事实基准

## 为什么有这个东西

用户反馈："产品分析员根本没有识别到我喂的图是什么品牌，商品名是什么都没强调或者提醒，
这是一个很大的缺失"。核实：`config/prompts/analyst.yaml` 的字段里没有品牌、没有商品名、
没有任何包装文字转录 —— 于是提示词、生图、审查、合规**全程没有事实基准**，只能让模型自由
发挥，实测结果就是把包装上的 `DEFOEBUENA®` 编成了 `NUTRIVA®`。

## 两条硬规则

1. **来源可溯源**：身份卡带 `source`，只有 `vision`（本次会话视觉识别）与 `user_confirmed`
   （用户在暂停里确认/填写）才算"已确认"。`mock`（演示数据）、无来源、记忆库召回、历史会话
   **永远不算** —— 用户明确担心"会不会把我这款商品套成别的牌子"，`MOCK_ANALYSIS` 里那套
   写死的"保健品/水飞蓟/蓝帽"正是这种污染源。
2. **未确认就禁止编造**：`status=uncertain` 时，下游提示词禁止出现任何品牌/品名/成分/认证
   文字，包装文字区域一律"干净虚化"交设计师后期贴图。

本模块是纯函数（无 IO、无 Provider），便于测试与复用。
"""

import re
from typing import Any

# 来源可信度：只有这两个算"已确认"
CONFIRMED_SOURCES = ("vision", "user_confirmed")
ALL_SOURCES = ("vision", "user_confirmed", "mock", "none")

# 低于该置信度即使字段齐全也不认（识别不准就该问人，不该猜）
MIN_CONFIDENCE = 0.5

_IDENTITY_FIELDS = ("brand", "product_name", "spec", "certifications",
                    "package_form", "confidence", "evidence", "brand_palette")

# 身份分词时要剔除的泛用词（它们出现在提示词里不代表"复述包装"）
_GENERIC_TERMS = {
    "德国", "德國", "德国制造", "德國製造", "made in germany", "gmp",
    "德国gmp優質產品", "德国gmp优质产品", "德國gmp優質產品", "原装", "原裝",
    "进口", "進口", "正货", "正貨", "包装", "包裝", "纸盒", "紙盒", "礼盒", "禮盒",
}
_HEX_RE = re.compile(r"^#?[0-9a-fA-F]{6}$")


def _text(value) -> str:
    if value is None or isinstance(value, (dict, list, tuple, set)):
        return ""
    return str(value).strip()


def _text_list(value) -> list[str]:
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, (list, tuple, set)):
        items = list(value)
    else:
        items = []
    return [text for text in (_text(item) for item in items) if text]


def _confidence(value) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, number))


def _hex_color(value) -> str:
    """规范化十六进制色值（非法返回空串，绝不把脏值写进提示词）"""
    text = _text(value)
    if not text or not _HEX_RE.match(text):
        return ""
    return text.upper() if text.startswith("#") else f"#{text.upper()}"


def normalize_palette(raw) -> dict[str, str]:
    """品牌色板（从包装上取的**事实**，不是模型想象的配色）

    用户 2026-09-18："电商商品图需要平面设计风格…要有高级感让人觉得牌子正规"——
    画面与本地排版文字层共用同一套包装真实色系，是最便宜的"正规感"来源。
    """
    cfg = raw if isinstance(raw, dict) else {}
    return {
        "primary": _hex_color(cfg.get("primary")),
        "secondary": _hex_color(cfg.get("secondary")),
        "background": _hex_color(cfg.get("background")),
        "evidence": _text(cfg.get("evidence")),
    }


def palette_summary(palette) -> str:
    """一行色板摘要（写进群聊/产物；空色板返回空串）"""
    if not isinstance(palette, dict):
        return ""
    parts = []
    for key, label in (("primary", "主色"), ("secondary", "辅色"), ("background", "背景色")):
        value = _text(palette.get(key))
        if value:
            parts.append(f"{label} {value}")
    if not parts:
        return ""
    text = " ｜ ".join(parts)
    if palette.get("evidence"):
        text += f"（取自：{palette['evidence']}）"
    return text


def identity_terms(identity) -> list[str]:
    """生图提示词里**不该出现**的身份文字（品牌/品名/规格/认证及其分词变体）

    实测事故（用户 2026-09-18 会话）：把包装版式与品牌名逐字写进画面描述，模型于是
    "按文字重画小字" —— `Sickle Ligament` 写成 `Sadle Ligement`、手写体 `Schneiski`
    变成乱码，本地身份相似度 0.22–0.33（阈值 0.45）。

    **颜色不在其列**：色值是包装上的事实，写进提示词安全且必要（见 `normalize_palette`）。
    长词优先返回，便于调用方先删长词、再删短词，避免留下碎片。
    """
    if not isinstance(identity, dict):
        return []
    values: list[Any] = [identity.get("brand"), identity.get("product_name"),
                         identity.get("spec"), identity.get("package_form")]
    certs = identity.get("certifications")
    if isinstance(certs, (list, tuple, set)):
        values.extend(certs)
    elif certs:
        values.append(certs)

    terms: list[str] = []

    def _add(text: Any) -> None:
        value = str(text or "").strip(" 　·、,，.。;；:：")
        if len(value) < 2 or value.lower() in _GENERIC_TERMS:
            return
        # 纯 ASCII 片段 <3 字符（如 "IN"）会误伤英文提示词里的其他单词 → 丢弃
        if value.isascii() and len(value) < 3:
            return
        if value not in terms:
            terms.append(value)

    for value in values:
        _add(value)
        for piece in re.split(r"[\s/｜|、,，()（）\[\]【】®™]+", str(value or "")):
            _add(piece)
    terms.sort(key=len, reverse=True)
    return terms


def normalize_identity(analysis, *, source: str = "vision") -> dict[str, Any]:
    """从分析结果里提取商品身份卡（纯函数；任何垃圾输入都不抛异常）

    `analysis` 可以是 Agent 的产物字典，也可以直接是 `product_identity` 子字典。
    """
    payload = analysis if isinstance(analysis, dict) else {}
    raw = payload.get("product_identity")
    if not isinstance(raw, dict):
        raw = payload if "brand" in payload or "product_name" in payload else {}

    visible = payload.get("visible_text")
    if not isinstance(visible, dict):
        visible = {}
    lines: list[dict[str, Any]] = []
    for item in (visible.get("lines") or []):
        if isinstance(item, dict):
            text = _text(item.get("text"))
            if text:
                lines.append({"text": text,
                              "location": _text(item.get("location")),
                              "legible": item.get("legible", True) is not False})
        elif isinstance(item, str) and item.strip():
            lines.append({"text": item.strip(), "location": "", "legible": True})

    identity: dict[str, Any] = {
        "brand": _text(raw.get("brand")),
        "product_name": _text(raw.get("product_name")),
        "spec": _text(raw.get("spec")),
        "certifications": _text_list(raw.get("certifications")),
        "package_form": _text(raw.get("package_form")),
        "confidence": _confidence(raw.get("confidence")),
        "evidence": _text(raw.get("evidence")),
        "source": source if source in ALL_SOURCES else "none",
        "derived_from": _text_list(raw.get("derived_from")),
        # 品牌色板（从包装取的真实色值；缺色板时下游回落中性配色并标注）
        "brand_palette": normalize_palette(raw.get("brand_palette")),
        "visible_text": {
            "lines": lines,
            "language": _text(visible.get("language")),
            "has_illegible": bool(visible.get("has_illegible")),
        },
    }

    missing = [label for label, value in (("品牌", identity["brand"]),
                                          ("商品名", identity["product_name"]))
               if not value]
    identity["missing"] = missing

    confirmed = not missing and identity["source"] in CONFIRMED_SOURCES
    # 人工确认过的优先（人比模型可信）；否则低置信度不认
    if confirmed and identity["source"] == "vision" and identity["confidence"] < MIN_CONFIDENCE:
        confirmed = False
        identity["low_confidence"] = True
    identity["status"] = "confirmed" if confirmed else "uncertain"
    return identity


def is_confirmed(identity) -> bool:
    return isinstance(identity, dict) and identity.get("status") == "confirmed"


def _mock_note(identity: dict) -> str:
    return "（⚠️ 演示数据，非本次商品）" if identity.get("source") == "mock" else ""


def identity_summary(identity) -> str:
    """群聊里播报的一行摘要（confirmed 用 ✅ 醒目展示，uncertain 用 ⚠️ 说明缺什么）"""
    if not isinstance(identity, dict):
        identity = normalize_identity({}, source="none")

    if is_confirmed(identity):
        parts = [f"品牌 `{identity['brand']}`", f"品名 `{identity['product_name']}`"]
        if identity.get("spec"):
            parts.append(f"规格 `{identity['spec']}`")
        if identity.get("certifications"):
            parts.append("认证 `" + "、".join(identity["certifications"]) + "`")
        palette = palette_summary(identity.get("brand_palette"))
        if palette:
            parts.append(f"品牌色 {palette}")
        return "✅ 商品身份：" + " ｜ ".join(parts)

    missing = "、".join(identity.get("missing") or []) or "品牌与商品名"
    return (f"⚠️ 商品身份未确认：缺少 {missing}"
            f"{_mock_note(identity)} —— 未确认前不会让模型编造品牌/文字，"
            "确认后才继续出图")


def identity_card_block(identity) -> str:
    """注入下游简报刊用的"事实基准"块（前置，权重最高）"""
    if not isinstance(identity, dict):
        identity = normalize_identity({}, source="none")

    if not is_confirmed(identity):
        note = _mock_note(identity)
        return (
            "## 商品身份（未确认）\n"
            f"⚠️ 本次未能确认商品品牌与商品名（缺少：{'、'.join(identity.get('missing') or ['品牌', '商品名'])}）"
            f"{note}。\n"
            "**禁止**在提示词、画面或文案中出现任何品牌名、商品名、成分、规格、认证文字：\n"
            "包装上的文字区域一律做**干净虚化**（自然过渡，不留乱码字形），交设计师后期贴图。\n"
            "也不要根据任何参考资料推断这些信息。"
        )

    lines = [
        "## 商品身份（事实基准，必须逐字沿用）",
        f"- 品牌：{identity['brand']}",
        f"- 商品名：{identity['product_name']}",
    ]
    if identity.get("spec"):
        lines.append(f"- 规格：{identity['spec']}")
    if identity.get("certifications"):
        lines.append("- 认证：" + "、".join(identity["certifications"]))
    if identity.get("package_form"):
        lines.append(f"- 包装形式：{identity['package_form']}")
    palette = identity.get("brand_palette") or {}
    palette_line = palette_summary(palette)
    if palette_line:
        lines.append(f"- 品牌色系（取自包装，画面与文字层都要用）：{palette_line}")
    else:
        lines.append("- 品牌色系：未取到（本次用中性商业配色：白 / 深灰 + 一处品牌近似色）")
    if identity.get("evidence"):
        lines.append(f"- 识别依据：{identity['evidence']}")
    visible = identity.get("visible_text") or {}
    if visible.get("lines"):
        lines.append("- 包装可见文字（逐字转录）：")
        lines.extend(f"  - {row['text']}" + (f"（{row['location']}）" if row.get("location") else "")
                     for row in visible["lines"][:20])
    lines.append(
        "规则（两条都要遵守）：\n"
        "1. 上表没有的信息**一律不得出现**，也不得从参考资料推断；\n"
        "2. **品牌文字不进画面描述**——不要在提示词里复述品牌名/品名/规格/认证，也不要描述"
        "包装上的版式与小字（实测这会诱导模型重画文字，产生乱码）。"
        "商品身份由**参考图（图一）**承载；但**品牌色值要写**，画面色系必须与上表一致。")
    return "\n".join(lines)
