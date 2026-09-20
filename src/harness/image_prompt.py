"""生图提示词组装 —— 把「逐张设计契约 + 品牌色系 + 文字规则」拼成一条可执行的提示词

## 设计导向（用户 2026-09-18 明确更正）

"电商商品图需要类似于平面设计的风格的，他的背景不一定非要'真实'，而是要有一定的高级感，
让人一看就觉得这个牌子看着挺不错挺正规的样子。"

因此提示词**不追求纪实写真感**：画面是"设计出来的" —— 光影与投影是设计元素，背景可以是纯白、
品牌浅色底、双色分割、径向渐层或微质感底。写实只用于**场景槽位**（使用情境）。

## 六段式结构（用户："未有明确约束每一张该有的提示词"）

```
第3张｜成分配方图（main_ingredients）｜详情图｜1:1｜信息图·文字由系统本地排版
【画面】…            ← 模型/审核优化员写的画面描述（背景·光影·版式·道具）
【品牌色系】主色 #… ｜ 辅色 #… ｜ 背景 #…
【必须】…            ← 槽位契约（配置注入，模型改不掉）
【留白】…            ← 信息图留白区（供本地排版文字层）
【禁止】…            ← 槽位禁止项 + 设计禁忌 + 通用负面（合并成一段，不堆叠）
【文字】…            ← 文字策略（信息图/无参考图时不允许出现文字）
【身份】…            ← 商品以参考图为准（不复述品牌文字）
```

**长度纪律**：长指导清单（`ART_DIRECTION_GUIDE`）只给**提示词生成员与审核优化员**看，
不塞进最终提示词；最终提示词只带一段精简风格行 —— 上一轮实测 586 字的"约束堆叠"会让模型
变得呆板拘谨（而且近一半是否定词）。

## 两条硬规则

1. **不在提示词里复述品牌文字与包装版式**：实测把包装版式写进画面描述后，模型"按文字重画小字"
   —— `Sickle Ligament` 写成 `Sadle Ligement`、手写体 `Schneiski` 变成乱码，身份相似度掉到
   0.22–0.33（阈值 0.45）。身份由**参考图**承载；但**品牌色值要写**（色值是包装上的事实，
   而且是"正规感"最便宜的那一招）。
2. **信息图的文字永远不由模型画**：字形准确性与事实边界都靠本地排版（`harness/image_compose.py`）。
"""

import re

# ── 给提示词生成员/审核优化员的长指导（不塞进最终提示词）──

ART_DIRECTION_GUIDE = (
    "商业设计感商品图（**不是纪实随手拍、不是生活照**）：画面干净、色系统一、留白有节奏；"
    "光影是设计出来的——顶部柔光 + 双侧补光，需要时加一条背后轮廓光勾出商品边缘，"
    "投影短而干净（平台要求去投影时不要投影）、高光克制不爆；"
    "材质要有真实质感（哑光纸纹、烫金细窄镜面高光、通透胶囊）；"
    "构图要有明确视觉重心与几何节奏（横向色带 / 细线框 / 圆角卡片，最多两处且不遮挡商品）；"
    "整体精修质感，无 HDR 味、无过饱和、无过度锐化、无 AI 塑料感"
)
INFO_BASELINE_GUIDE = (
    "信息图底图（**画面内不得出现任何文字、字母、数字**）：给出版式所需的纯净留白区，"
    "留白区内不得有图案、道具或主体穿过；商品实物完整可见且与包装同色系；"
    "最多一条品牌色带作版式分隔；背景干净明亮（纯白或极浅品牌色），不做暗调、不做复杂装饰"
)

# ── 进最终提示词的**精简**风格行（一段，不堆叠）──

PHOTO_STYLE_LINE = ("设计感商业布光：顶部柔光 + 双侧补光，投影短而干净、高光克制，"
                    "材质真实有质感，精修画面")
INFO_STYLE_LINE = ("版式底图：干净明亮、留白区无图案干扰，主体完整可见并与包装同色系，"
                   "最多一条品牌色带，不做暗调")
# 设计禁忌（反廉价信号）与通用负面（合并进【禁止】段，不再各写一句）
FORBIDDEN_LOOKS = "撞色、彩虹渐变、贴纸描边、密集光斑、暗调脏底、HDR 味、过度锐化、AI 塑料感、画面内文字"
NEGATIVE_FOLD = "画面避免：模糊、暗沉、脏污、杂乱背景、水印、人物、拼接"

# 身份规则：商品身份由参考图承载，而不是靠提示词复述
IDENTITY_RULE = (
    "商品以参考图（图一）为唯一依据：包装上的品牌文字、图案、配色与形制必须逐字逐样保留，"
    "不得改写、翻译、补全或新增任何文字"
)
# 完全没有参考图时的补充要求（只有在虚化/无文字策略下才需要）
NO_REFERENCE_HINT = "本次没有可用的参考图，务必不要凭想象生成包装文字"
# 无品牌色板时的回落说明
PALETTE_FALLBACK_NOTE = (
    "未取到包装品牌色 → 中性商业配色：纯白或极浅灰底 + 一处深色与一处点缀色，色系不超过三色"
)

TEXT_RULES = {
    "preserve": IDENTITY_RULE,
    "blur": (
        "参考图中包装上的文字区域做**干净虚化**（自然过渡，不留乱码字形、不出现半糊的假字），"
        "文字由设计师后期贴图；画面中不要生成任何可辨认的文字、字母或数字"
    ),
    "none": "画面中不出现任何文字、字母、数字、logo、水印或角标",
}
INFO_TEXT_RULE = "画面内不出现任何文字、字母、数字或角标（文字由系统本地排版绘制）"

# 「画面」段长度上限（模型/审核员写的那部分；契约段不受此限，不许丢）
MAX_SCENE_CHARS = 260
_SCENE_MIN_CHARS = 60

# 移除身份词后残留的连接词（"MADE IN GERMANY" 里的 IN 之类）
_RESIDUAL_TOKENS = ("in", "of", "and", "the", "for", "by", "no", "a", "an", "with")


def effective_strategy(text_strategy: str, *, has_references: bool,
                       text_in_image: bool = False) -> str:
    """生效的文字策略

    - 需要文字版面（`text_in_image`）的槽位：**仍然不让模型画字**，改为留白交后期贴图；
    - 没有参考图：强制虚化（没有任何可靠的文字来源）。
    """
    if text_in_image:
        return "blur"
    if text_strategy == "preserve" and not has_references:
        return "blur"
    return text_strategy if text_strategy in TEXT_RULES else "blur"


# ── 身份词治理（A71）──


def _term_pattern(term: str) -> re.Pattern:
    """身份词匹配：ASCII 词按单词边界（避免删 "IN" 把 "INSIDE" 打碎），中文按子串"""
    escaped = re.escape(term)
    if term.isascii():
        return re.compile(rf"(?<![0-9A-Za-z]){escaped}(?![0-9A-Za-z])", re.IGNORECASE)
    return re.compile(escaped)


def identity_terms_in_prompt(prompt: str, terms) -> list[str]:
    """提示词里命中的身份词（体检用；不修改文本）"""
    text = str(prompt or "")
    return [str(term) for term in (terms or []) if str(term or "").strip()
            and _term_pattern(str(term).strip()).search(text)]


def _residual_cleanup(text: str) -> str:
    """清理移除身份词后留下的残渣（®/™、孤立连接词、重复标点）"""
    text = re.sub(r"[®™©]+", "", text)
    for token in _RESIDUAL_TOKENS:
        text = re.sub(rf"(?<![0-9A-Za-z]){token}(?![0-9A-Za-z])", " ", text,
                      flags=re.IGNORECASE)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"(?:[；;，,、]\s*){2,}", "；", text)
    text = re.sub(r"[；;，,、]\s*(?=[；;，,、]|$)", "", text)
    return text.strip(" ；;，,、·")


def strip_identity_terms(prompt: str, terms) -> tuple[str, list[str]]:
    """把身份词从画面描述里移除，返回 `(新文本, 被移除的词)`

    **不静默**：移除项由调用方记进 `prompt_notes` 并展示给用户。
    身份信息改由参考图承载（见 `IDENTITY_RULE`）；**色值不在移除范围**。
    """
    text = str(prompt or "")
    removed: list[str] = []
    for term in terms or []:
        value = str(term or "").strip()
        if not value:
            continue
        pattern = _term_pattern(value)
        if pattern.search(text):
            text = pattern.sub("", text)
            removed.append(value)
    if removed:
        text = _residual_cleanup(text)
    return text, removed


# ── 品牌色系 ──


def palette_block(palette, *, fallback: bool = True) -> str:
    """渲染 `【品牌色系】` 段（无有效色值时给中性回落说明）"""
    cfg = palette if isinstance(palette, dict) else {}
    parts = []
    for key, label in (("primary", "主色"), ("secondary", "辅色"), ("background", "背景色")):
        value = str(cfg.get(key) or "").strip()
        if value:
            parts.append(f"{label} {value}")
    if not parts:
        return PALETTE_FALLBACK_NOTE if fallback else ""
    text = " ｜ ".join(parts)
    if cfg.get("evidence"):
        text += f"（取自包装：{cfg['evidence']}）"
    return text


def _text_list(value) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple, set)):
        return []
    return [str(item).strip() for item in value if str(item or "").strip()]


def _section(name: str, text: str) -> str:
    return f"【{name}】{text}" if text else ""


def build_image_prompt(*, scene: str, composition: str = "", background: str = "",
                       aspect: str = "", kind: str = "photo", usage: str = "",
                       slot_id: str = "", role: str = "", number: int = 0,
                       must=None, avoid=None, keep_clear: str = "",
                       palette=None, identity=None, text_strategy: str = "preserve",
                       has_references: bool = True, text_in_image: bool = False,
                       extra_rules=None, max_scene_chars: int = MAX_SCENE_CHARS
                       ) -> tuple[str, list[str]]:
    """组装最终生图提示词；返回 `(提示词, 说明)`

    说明里记录"哪些内容被裁掉/移除"（不静默）：身份词移除、画面段裁剪、文字策略降级。
    契约段（必须/留白/禁止/文字/身份）由配置与代码注入，**不受长度裁剪影响**。
    """
    notes: list[str] = []
    body = str(scene or "").strip()
    if identity:
        from src.harness.product_identity import identity_terms
        body, removed = strip_identity_terms(body, identity_terms(identity))
        if removed:
            notes.append("已从画面描述移除身份文字（品牌/品名/规格/认证由参考图承载）："
                         + "、".join(removed[:8]))
    if composition:
        body = f"{body}；构图：{composition}" if body else f"构图：{composition}"
    if background:
        body = f"{body}；背景：{background}" if body else f"背景：{background}"

    strategy = effective_strategy(text_strategy, has_references=has_references,
                                  text_in_image=text_in_image)
    downgraded = text_strategy == "preserve" and not has_references
    if downgraded:
        notes.append("没有可用的参考图，文字策略自动降级为 blur（不让模型编造包装文字）")
    if text_in_image and strategy == "blur":
        notes.append("信息图槽位：模型只出无字底图，文字由本地排版绘制")

    is_info = str(kind) == "info"
    style_line = INFO_STYLE_LINE if is_info else PHOTO_STYLE_LINE
    must_items = _text_list(must)
    avoid_items = _text_list(avoid) + [FORBIDDEN_LOOKS, NEGATIVE_FOLD]
    if extra_rules:
        must_items = must_items + _text_list(extra_rules)
    if is_info:
        # 信息图：既要有"留白版式、画面无文字"，也要保留包装文字的处理规则
        # （底图里的商品本体仍来自参考图，包装文字该虚化就虚化）
        text_rule = f"{INFO_TEXT_RULE}；{TEXT_RULES[strategy]}"
    else:
        text_rule = TEXT_RULES[strategy]
    if not has_references and strategy != "preserve":
        text_rule = f"{text_rule}。{NO_REFERENCE_HINT}"

    head_bits = []
    if number:
        head_bits.append(f"第{number}张")
    if role or slot_id:
        head_bits.append(f"{role or slot_id}" + (f"（{slot_id}）" if role and slot_id else ""))
    if usage:
        head_bits.append("主图" if usage == "main" else "详情图")
    if aspect:
        head_bits.append(str(aspect))
    head_bits.append("信息图·文字由系统本地排版" if is_info else "纯摄影·画面无文字")
    head = "｜".join(head_bits)

    if max_scene_chars > 0 and len(body) > max_scene_chars:
        trimmed = body[:max_scene_chars].rstrip(" ；;，,、")
        notes.append(f"画面描述超长已裁剪（{len(body)} → {len(trimmed)} 字；"
                     f"上限 {max_scene_chars} 字，契约段不受影响）")
        body = trimmed

    sections = [
        head,
        _section("画面", body),
        _section("品牌色系", palette_block(palette)),
        _section("必须", "；".join(must_items + [style_line])),
        _section("留白", keep_clear or ""),
        _section("禁止", "；".join(avoid_items)),
        _section("文字", text_rule),
        _section("身份", IDENTITY_RULE),
    ]
    return "\n".join(item for item in sections if item), notes


def compose_prompt(*, prompt: str, composition: str = "", background: str = "",
                   aspect: str = "", text_in_image: bool = False,
                   text_strategy: str = "preserve", has_references: bool = True,
                   extra_rules: list[str] | None = None, palette=None) -> str:
    """兼容旧调用方（设置页"试生成一张"、`scripts/quality_probe.py`）：只返回文本"""
    text, _ = build_image_prompt(
        scene=prompt, composition=composition, background=background, aspect=aspect,
        kind="info" if text_in_image else "photo", palette=palette,
        text_in_image=text_in_image, text_strategy=text_strategy,
        has_references=has_references, extra_rules=extra_rules)
    return text


def strategy_notes(text_strategy: str, *, has_references: bool) -> list[str]:
    """策略生效说明（写进产物/群聊，解释"为什么这次没按你设的策略来"）"""
    notes: list[str] = []
    if text_strategy == "preserve" and not has_references:
        notes.append("没有可用的参考图，文字策略自动降级为 blur（不让模型编造包装文字）")
    return notes
