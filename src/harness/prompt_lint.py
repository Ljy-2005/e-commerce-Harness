"""提示词体检 —— 出图前的**零成本确定性护栏**

## 为什么有这个东西

用户 2026-09-18 两条反馈：
1. "我发现生成的图片质量太差了，缺少了该有的商品图审美，这跟提示词生成员的问题脱不开关系"；
2. "我发现他未有明确约束每一张该有的提示词…这样可以有效的指定生成照片"。

核实（真实会话 `ee9a3b80e19b4010`，6 张、$0.2519、人工 reject）：
- 提示词把**包装版式逐字写进画面**（"左上深藍色品牌區塊、金色金屬燙印粗體字…肝臟解剖線稿圖示"）
  → 模型按文字重画小字 → `Sickle Ligament` 写成 `Sadle Ligement`、手写体 `Schneiski` 变乱码，
  身份相似度 0.22–0.33（阈值 0.45）；
- 6 张画面描述高度同质（都是"纯白 + 盒子 + 胶囊 + 迷迭香"），卖点图里**没有商品实物**；
- 单条提示词 586 字，近半是否定词堆叠。

**审美判断交给 LLM（提示词审核优化员），可枚举的错交给这里**：本模块纯函数、零成本、确定性，
结论可测试。它的 `errors` 是硬伤（必须修），`warnings` 是质量问题（建议改）。

## 与 `set_plan.validate_plan` 的分工

- `validate_plan`：**结构**（槽位覆盖、编号、额度、空提示词）；
- 本模块：**内容与审美**（身份词、包装版式复述、设计语言缺失、反模式、同质化、违禁词）。
"""

import hashlib
import re
from typing import Any

from src.core.platforms import slot_label
from src.harness.image_prompt import identity_terms_in_prompt, palette_block
from src.harness.product_identity import identity_terms
from src.harness.set_plan import platform_slot_plan

# ── 规则关键词表（都是实测踩过的写法，不是凭空规定）──

# 复述包装版式：诱导模型"按文字重画小字"
PACKAGING_PROSE_MARKERS = (
    "燙印", "烫印", "品牌區", "品牌区", "字樣", "字样", "大字", "區塊", "区块",
    "標籤區", "标签区", "解剖", "手寫", "手写", "印刷上", "版式結構", "版式结构",
    "字區", "字区", "白底黑字", "金色粗體", "金色粗体", "英文字",
)
# 要求画面里渲染文字（与"文字由系统本地排版"直接冲突）
TEXT_REQUEST_MARKERS = (
    "写着", "寫著", "印着", "印有", "寫有", "写有", "文字位于", "字樣為", "字样为",
    "包装上写", "包裝上寫", "标注文字", "標註文字",
)
# 魔法词：只增加噪声
MAGIC_WORDS = (
    "8k", "16k", "超高解析度", "超高清晰度", "超高画质", "大师作品", "杰作", "极致",
    "完美无瑕", "masterpiece", "ultra hd", "best quality", "hyper realistic",
)
# 否定标记（堆叠会让人机双方都变得呆板拘谨）
NEGATION_MARKERS = ("禁止", "不要", "不得", "避免", "不出现", "不含", "杜绝", "no ", "avoid")
# 矛盾形容词（拼多多风格块实测同时写着"高饱和强对比"与"弱化高级感"）
CONTRADICTION_PAIRS = (
    (("高饱和", "强对比"), ("低饱和", "高级感", "克制")),
)
# 设计语言三类关键词：版式 / 背景设计 / 光影，各至少命中一个
DESIGN_TERMS = {
    "版式": ("构图", "占比", "居中", "三分", "留白", "版式", "置于", "位置", "呼吸"),
    "背景": ("背景", "底色", "纯白", "渐层", "渐变", "无缝", "色块", "分割", "微质感"),
    "光影": ("光", "投影", "高光", "柔光", "补光", "布光", "阴影"),
}
MAX_NEGATIONS = 6
DUPLICATE_SIMILARITY = 0.6
SHORT_PROMPT_CHARS = 40
LONG_PROMPT_CHARS = 800
# 与所注入风格档案的"逐字照抄"判定：连续重合多少个字算抄（档案是"怎么设计"的参考，
# 原样搬进画面描述会让 10 张长成一个模子）
STYLE_COPY_CHARS = 12


def style_slot_map(style_entries) -> dict:
    """把「风格档案选择结果」归一成 `{slot_id: [entry]}`

    接受 `select_by_slot()` 的完整结果（取其中的 `slots`）或直接给槽位映射。
    """
    raw = style_entries or {}
    if not isinstance(raw, dict):
        return {}
    slots = raw.get("slots")
    if isinstance(slots, dict):
        return slots
    return raw


def style_copy_overlap(prompt: str, entries, slot_id: str = "") -> tuple[str, str]:
    """`(命中的档案名, 重合片段)`；没有重合返回 `("", "")`

    实现：在画面描述上滑 `STYLE_COPY_CHARS` 长的窗口，看它是否**原样**出现在档案文本里。
    比"整体相似度"更贴切——部分照抄（半句话）也要能查出来。

    `slot_id`：只比对该槽位的"逐张做法"。不传就并上全部做法 —— 那样会把
    "X 槽抄了 Y 槽的做法"报成命中（`lint_prompts` 逐槽位调用时会传当前槽位）。
    """
    text = str(prompt or "")
    if len(text) < STYLE_COPY_CHARS:
        return "", ""
    windows = {text[index:index + STYLE_COPY_CHARS]
               for index in range(len(text) - STYLE_COPY_CHARS + 1)}
    from src.harness.style_library import style_plain_text  # 惰性导入：避免模块级互相引用
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        plain = style_plain_text(entry, slot_id)
        if not plain:
            continue
        for window in windows:
            if window in plain:
                return str(entry.get("name") or entry.get("id") or "未命名档案"), window
    return "", ""


def _trigrams(text: str) -> set[str]:
    """字符 3-gram（中文按字切，简单且对语序变化敏感）"""
    cleaned = re.sub(r"[\s，。；、,.;:!?！？（）()【】\[\]]+", "", str(text or ""))
    return {cleaned[index:index + 3] for index in range(max(0, len(cleaned) - 2))}


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    union = left | right
    return round(len(left & right) / len(union), 4) if union else 0.0


def text_similarity(left: str, right: str) -> float:
    """两段文字的字面相似度（字符 3-gram Jaccard，0–1）

    公开出来是给「风格档案库」复用的：判断用户新建的词条是否与已有档案/内置档案高度重合
    （"净白硬照 / 纯白棚拍 / 白底商品图"这类同义条目堆叠）。
    与槽位同质化检测同一口径，避免两处各写一套。
    """
    return _jaccard(_trigrams(left), _trigrams(right))


def text_overlap(left: str, right: str) -> float:
    """**包含度**（交集 / 较短者的 3-gram 数，0–1）

    与 `text_similarity` 的分工：Jaccard 会被长短差异稀释（25 字包含 150 字的长档案时只有
    ~0.13），而"短的那条其实在重复已有档案"这件事需要被发现 —— 用户新建词条时用它。
    """
    left_grams, right_grams = _trigrams(left), _trigrams(right)
    if not left_grams or not right_grams:
        return 0.0
    shorter = min(len(left_grams), len(right_grams))
    return round(len(left_grams & right_grams) / shorter, 4) if shorter else 0.0


def prompt_digest(plan) -> str:
    """提示词版本指纹（幂等键）：审核优化员据此跳过已审过且通过的同一版提示词"""
    slots = [slot for slot in ((plan or {}).get("slots") or []) if isinstance(slot, dict)]
    raw = "\n".join(f"{slot.get('slot_id')}::{str(slot.get('prompt') or '').strip()}"
                    for slot in slots)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _finding(rule: str, level: str, message: str, *, slot=None) -> dict:
    item = {"rule": rule, "level": level, "message": message}
    if isinstance(slot, dict):
        number = slot.get("number")
        item["slot_id"] = slot.get("slot_id") or ""
        item["number"] = number
        item["slot_label"] = slot_label(slot.get("slot_id") or "") if slot.get("slot_id") else ""
    return item


def lint_prompts(plan, *, platform=None, identity=None, style_entries=None) -> dict[str, Any]:
    """对套图编排逐槽位体检；返回 `{errors, warnings, findings, checked, digest}`

    Args:
        style_entries: 本次注入的风格档案选择结果（`style_library.select_by_slot()` 的返回，
            或直接是 `{slot_id: [entry]}`）——用于 `style_template_copy` 规则：
            画面描述与档案**逐字重合**说明是在搬模板而不是在设计画面。
    """
    plan = plan if isinstance(plan, dict) else {}
    slots = [slot for slot in (plan.get("slots") or []) if isinstance(slot, dict)]
    slug = plan.get("platform") or platform or ""
    findings: list[dict] = []
    style_map = style_slot_map(style_entries)

    # 1) 槽位覆盖（一张不少）
    expected = platform_slot_plan(slug)
    produced = {str(slot.get("slot_id") or "") for slot in slots}
    missing = [slot_id for slot_id in expected if slot_id not in produced]
    if missing:
        findings.append(_finding(
            "missing_slot", "error",
            "缺少平台槽位：" + "、".join(f"{slot_id}（{slot_label(slot_id)}）" for slot_id in missing)))

    palette_available = False
    trigrams: list[tuple[str, set[str]]] = []
    for slot in slots:
        prompt = str(slot.get("prompt") or "").strip()
        slot_id = str(slot.get("slot_id") or "")
        is_info = str(slot.get("kind") or "photo") == "info"

        # 2) 逐张契约是否齐（intent 是"这张图要干什么"）
        if not str(slot.get("intent") or "").strip():
            findings.append(_finding("missing_intent", "warning",
                                     f"槽位 {slot_id} 缺少 intent（这一张要让买家看懂什么）", slot=slot))

        # 3) 身份文字进了画面描述
        if identity:
            hits = identity_terms_in_prompt(prompt, identity_terms(identity))
            if hits:
                findings.append(_finding(
                    "identity_in_prompt", "error",
                    f"画面描述里出现身份文字：{'、'.join(hits[:6])}"
                    f"（会诱导模型重画包装小字，实测产生乱码）", slot=slot))

        # 4) 复述包装版式
        prose_hits = [marker for marker in PACKAGING_PROSE_MARKERS if marker in prompt]
        if prose_hits:
            findings.append(_finding(
                "packaging_prose", "warning",
                f"疑似复述包装版式：{'、'.join(prose_hits[:6])}"
                f"（只写画面与光线，包装由参考图承载）", slot=slot))

        # 5) 要求画面里渲染文字
        text_hits = [marker for marker in TEXT_REQUEST_MARKERS if marker in prompt]
        if text_hits:
            findings.append(_finding(
                "fake_text_request", "error",
                f"提示词要求画面渲染文字：{'、'.join(text_hits[:6])}"
                f"（文字由系统本地排版绘制，模型不得画字）", slot=slot))

        # 6) 信息图必须写清留白区
        if is_info and not any(word in prompt for word in ("留白", "空白", "纯净区", "净空")):
            findings.append(_finding(
                "missing_keep_clear", "error",
                "信息图没有描述留白区（后期排版文字会压到商品上）", slot=slot))

        # 7) 设计语言（版式/背景/光影各≥1）—— "符合基本要求但审美不合格"的主要成因
        absent = [name for name, words in DESIGN_TERMS.items()
                  if not any(word in prompt for word in words)]
        if absent:
            findings.append(_finding(
                "missing_design_terms", "error",
                "画面描述缺少设计语言：" + "、".join(absent) + "（要写清版式/背景/光影）", slot=slot))

        # 8) 品牌色值
        if palette_block(slot.get("palette") or {}, fallback=False):
            palette_available = True

        # 9) 否定词堆叠
        negations = sum(prompt.count(marker) for marker in NEGATION_MARKERS)
        if negations > MAX_NEGATIONS:
            findings.append(_finding(
                "negation_pile", "warning",
                f"单张提示词里否定表述 {negations} 处（建议 ≤{MAX_NEGATIONS}）："
                "堆叠否定会让画面呆板拘谨", slot=slot))

        # 10) 魔法词
        magic_hits = [word for word in MAGIC_WORDS if word in prompt.lower()]
        if magic_hits:
            findings.append(_finding("magic_words", "warning",
                                     f"魔法词：{'、'.join(magic_hits[:6])}（只增加噪声）", slot=slot))

        # 11) 自相矛盾的形容词
        for positive, negative in CONTRADICTION_PAIRS:
            if any(word in prompt for word in positive) and any(word in prompt for word in negative):
                findings.append(_finding(
                    "contradiction", "warning",
                    f"形容词自相矛盾：{'/'.join(positive)} 与 {'/'.join(negative)} 同时出现", slot=slot))

        # 12) 长度
        if len(prompt) < SHORT_PROMPT_CHARS:
            findings.append(_finding("prompt_too_short", "warning",
                                     f"画面描述过短（{len(prompt)} 字），细节不足以定住画面",
                                     slot=slot))
        elif len(prompt) > LONG_PROMPT_CHARS:
            findings.append(_finding("prompt_too_long", "warning",
                                     f"画面描述过长（{len(prompt)} 字 > {LONG_PROMPT_CHARS}）",
                                     slot=slot))

        # 13) 违禁宣称（广告法词表，复用 slot_copy）
        try:
            from src.harness.slot_copy import find_forbidden_claims
            claims = find_forbidden_claims([prompt])
            if claims:
                findings.append(_finding(
                    "forbidden_claim", "error",
                    f"提示词含违禁宣称：{'、'.join(claims[:6])}", slot=slot))
        except Exception:  # noqa: BLE001 — 合规词表不可用不应阻断体检
            pass

        trigrams.append((slot_id, _trigrams(prompt)))

        # 13.5) 逐字照抄风格档案（warning）：档案是"怎么设计"的参考，
        #       原样搬进画面描述会让 10 张长成一个模子。
        #       传入 slot_id：只比对该槽位的"逐张做法"（避免跨槽位误报）
        if style_map:
            entry_name, fragment = style_copy_overlap(prompt, style_map.get(slot_id) or [],
                                                      slot_id)
            if entry_name:
                findings.append(_finding(
                    "style_template_copy", "warning",
                    f"画面描述与风格档案「{entry_name}」连续重合（“{fragment}”）："
                    "档案只作设计参考，请按它重新组织画面措辞", slot=slot))

    # 14) 平台硬要求纯白时，首图必须写纯白底（Amazon 这类平台会直接拒审）
    if slots:
        first = slots[0]
        first_prompt = str(first.get("prompt") or "")
        policy = str(plan.get("background_policy") or "")
        if policy == "white_required" and not re.search(r"纯白|#FFFFFF", first_prompt, re.IGNORECASE):
            findings.append(_finding("white_required_bg", "error",
                                     "该平台要求纯白背景，但首图画面描述没有写纯白底",
                                     slot=first))

    # 15) 槽位同质化（实测 6 张几乎一样：都是"纯白 + 盒子 + 胶囊 + 迷迭香"）
    if len(trigrams) > 1:
        first_id, first_grams = trigrams[0]
        for other_id, other_grams in trigrams[1:]:
            similarity = _jaccard(first_grams, other_grams)
            if similarity > DUPLICATE_SIMILARITY:
                findings.append(_finding(
                    "duplicate_prompts", "warning",
                    f"槽位 {other_id} 与 {first_id} 的画面描述过于相似（{similarity}）："
                    "每张应该是不同的画面角色"))

    if slots and not palette_available:
        findings.append(_finding(
            "missing_palette", "warning",
            "没有取到品牌色系：画面配色会靠模型自由发挥（建议让分析员补 brand_palette）"))

    errors = [item["message"] for item in findings if item["level"] == "error"]
    warnings = [item["message"] for item in findings if item["level"] != "error"]
    return {
        "errors": errors,
        "warnings": warnings,
        "findings": findings,
        "checked": len(slots),
        "expected": len(expected),
        "digest": prompt_digest(plan),
    }


def summarize_lint(report) -> str:
    """一行摘要（群聊/产物用）"""
    report = report if isinstance(report, dict) else {}
    errors = report.get("errors") or []
    warnings = report.get("warnings") or []
    if not errors and not warnings:
        return f"✅ 提示词体检通过（{report.get('checked', 0)} 张）"
    parts = [f"提示词体检：{report.get('checked', 0)} 张"]
    if errors:
        parts.append(f"❌ {len(errors)} 项硬伤")
    if warnings:
        parts.append(f"⚠️ {len(warnings)} 项建议")
    return "；".join(parts)
