"""平台档案 —— 电商平台图片规范的单一事实来源（`config/platforms.yaml`）

## 为什么有这个模块

用户提出"我还要做拼多多，风格也要写上"时暴露：平台清单此前散落在 9 处，
而且 `config/prompts/prompt_gen.yaml` 的静态风格清单只定义了 4 个平台
（淘宝/小红书/抖音/Amazon），`config/agents/prompt_generator.yaml` 用的是**中文名**
（`[淘宝, 天猫, 京东, 拼多多, …]`），其余 8 处全是 slug —— 两套命名空间并存。

后果：在界面上选"拼多多"，送进模型的只有一个裸字符串，平台风格全靠模型自己猜。

## 现在

- 档案集中在 `config/platforms.yaml`：**新增平台只改配置，代码零改动**；
- 别名归一（`拼多多` / `pdd` / `PDD` → `pinduoduo`）把两套命名空间合并；
- 风格块由 `platform_style_block()` 渲染进提示词；
- **未登记平台显式报"未登记"**，不让模型臆造规范（与 A41"死配置"同族的"静默"问题）。

本模块只读配置，**从不写回**（配置文件里的注释是文档）。
"""

from typing import Any

from src.core.config import _project_root, load_yaml

PLATFORMS_REL = "config/platforms.yaml"
DEFAULT_PLATFORM = "taobao"

# ── 解析缓存 ──
#
# 用户实测（2026-09-18）：槽位契约把访问器调用次数放大了几十倍
# （15 个槽位 × intent/design/must/forbid/keep_clear…），而每次访问都要重新读+解析
# 15KB 的 YAML（约 27ms）→ 渲染一次平台规范块要 **10 秒级**、一次体检 6 秒级。
# 这里按 `(路径, mtime_ns, size)` 缓存解析结果：**配置改了立刻生效**（不是永久缓存）。
_DOC_CACHE: dict[str, tuple[tuple, dict]] = {}


def _platforms_doc() -> dict:
    """`config/platforms.yaml` 的解析结果（带 mtime 缓存）"""
    path = _project_root() / PLATFORMS_REL
    try:
        stat = path.stat()
        key = (str(path), stat.st_mtime_ns, stat.st_size)
    except OSError:
        return {}
    cached = _DOC_CACHE.get("doc")
    if cached and cached[0] == key:
        return cached[1]
    raw = load_yaml(PLATFORMS_REL)
    doc = raw if isinstance(raw, dict) else {}
    _DOC_CACHE["doc"] = (key, doc)
    return doc

# 未登记平台时给出的通用规范（宁可保守，也不要模型自由发挥）
_UNREGISTERED_HINT = (
    "只使用通用电商白底主图规范（纯白背景、主体居中、占比 85% 以上、"
    "画面无文字/水印/logo/边框），并在结果里标注“平台规范未登记”。"
)


def load_platforms() -> dict[str, dict]:
    """全部平台档案：`{slug: profile}`（文件缺失/损坏返回空字典）"""
    raw = _platforms_doc()
    platforms = raw.get("platforms")
    if not isinstance(platforms, dict):
        return {}
    return {str(slug): dict(profile or {}) for slug, profile in platforms.items()
            if isinstance(profile, dict)}


def default_platform() -> str:
    """默认平台 slug（配置里的 `default_platform` → taobao）"""
    raw = _platforms_doc()
    value = str(raw.get("default_platform", "") or "").strip().lower()
    return value if value in load_platforms() else DEFAULT_PLATFORM


def _alias_map() -> dict[str, str]:
    """别名 → slug（slug、label、aliases 三者都归一；统一小写去空格）

    把 `label` 也作为可识别写法：中文名是最自然的输入方式（用户填"拼多多"、
    CSV 里写"拼多多"都要能落到 `pinduoduo`）。
    """
    mapping: dict[str, str] = {}
    for slug, profile in load_platforms().items():
        mapping[slug.strip().lower()] = slug
        label = str(profile.get("label") or "").strip().lower()
        if label:
            mapping.setdefault(label, slug)
        for alias in profile.get("aliases", []) or []:
            text = str(alias).strip().lower()
            if text:
                mapping.setdefault(text, slug)
    return mapping


def resolve_platform(raw) -> str:
    """把用户输入的任意写法归一为平台 slug；未登记返回 `""`

    返回空串而不是原样回传，是**有意为之**：调用方必须显式处理"未登记"，
    否则又会变成"看起来正常、其实全靠模型猜"。
    """
    text = str(raw or "").strip().lower()
    if not text:
        return ""
    return _alias_map().get(text, "")


def get_platform(raw) -> dict:
    """平台档案（附加 `slug` / `registered` 两个字段）

    - 空值 → 默认平台（注册成功）
    - 未登记 → 返回"未登记占位档案"（`registered=False`），调用方据此提示用户
    """
    profiles = load_platforms()
    slug = resolve_platform(raw)
    if not slug:
        if str(raw or "").strip():
            label = str(raw).strip()
            return {
                "slug": label.lower(), "label": label, "registered": False,
                "aspect": "", "bg": "", "text_policy": "composite",
                "style": "", "slots": [], "forbidden": [], "aliases": [],
            }
        slug = default_platform()
    profile = dict(profiles.get(slug, {}))
    profile["slug"] = slug
    profile["registered"] = True
    profile.setdefault("label", slug)
    profile.setdefault("slots", [])
    profile.setdefault("forbidden", [])
    return profile


def slot_label(slot_id: str) -> str:
    """槽位中文名（用于前端与提示词；未登记槽位原样返回）"""
    labels = _platforms_doc().get("slot_labels") or {}
    if isinstance(labels, dict) and slot_id in labels:
        return str(labels[slot_id])
    return str(slot_id)


def all_slot_labels() -> list[str]:
    """全部槽位的**中文角色名**清单（受控词汇）

    用途：「风格词库」里描述"参考套图第N张是什么角色"时，角色名就是这份词汇
    （「成分配方图」「资质认证图」…）。事实中立扫描要先摘掉它们再查词表 ——
    否则"再讲成分配方图"会被当成"写了成分事实"而被剔除（那是角色名，不是成分表）。
    """
    return [label for _slot_id, label in slot_label_pairs()]


def slot_label_pairs() -> list[tuple[str, str]]:
    """`[(slot_id, 中文角色名)]` —— 供"从清单里选槽位"这类提示词/界面用"""
    labels = _platforms_doc().get("slot_labels") or {}
    if not isinstance(labels, dict):
        return []
    return [(str(key), str(value)) for key, value in labels.items()
            if str(value or "").strip()]


def platform_slots(raw) -> list[str]:
    """平台的**主图**槽位（未登记平台返回空列表）"""
    slots = get_platform(raw).get("slots") or []
    return [str(s) for s in slots]


def platform_detail_slots(raw) -> list[str]:
    """平台的**详情图**槽位（信息类图文版面的常规位置）"""
    slots = get_platform(raw).get("detail_slots") or []
    return [str(s) for s in slots]


def platform_slot_limits(raw) -> tuple[int, int]:
    """`(主图上限, 详情图上限)`；0 表示不限"""
    profile = get_platform(raw)

    def _int(value) -> int:
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0

    return _int(profile.get("max_images")), _int(profile.get("max_detail_images"))


def _slot_catalog() -> dict[str, dict]:
    catalog = _platforms_doc().get("slot_catalog") or {}
    if not isinstance(catalog, dict):
        return {}
    return {str(key): dict(value or {}) for key, value in catalog.items()
            if isinstance(value, dict)}


def slot_kind(slot_id: str) -> str:
    """槽位产出方式：`photo`（模型直接出成品）｜`info`（模型出无字底图 + 本地排版文字层）"""
    kind = str((_slot_catalog().get(str(slot_id)) or {}).get("kind") or "photo").strip().lower()
    return kind if kind in ("photo", "info") else "photo"


def slot_usage(slot_id: str) -> str:
    """主图还是详情图：`main`（占平台主图额度）｜`detail`（占详情图额度）"""
    usage = str((_slot_catalog().get(str(slot_id)) or {}).get("usage") or "main").strip().lower()
    return usage if usage in ("main", "detail") else "main"


def slot_layout(slot_id: str) -> str:
    """信息图的排版模板名（见 `harness/image_compose.py`）"""
    return str((_slot_catalog().get(str(slot_id)) or {}).get("layout") or "").strip()


def slot_copy_sources(slot_id: str) -> list[str]:
    """信息图的文案来源键（对应分析结果/身份卡字段；空 = 没有可用来源）"""
    value = (_slot_catalog().get(str(slot_id)) or {}).get("copy") or []
    if isinstance(value, str):
        value = [value]
    return [str(item).strip() for item in value if str(item).strip()]


# ── 槽位契约（用户 2026-09-18 反馈："未有明确约束每一张该有的提示词"）──
#
# 每个槽位在 config/platforms.yaml 里声明 intent/design/must/forbid/keep_clear：
#   intent     这张图要让买家看懂什么
#   design     版式/背景/光影/材质的**设计要点**（设计导向，不是纪实摄影）
#   must       硬性要求 → 由代码注入最终提示词，模型与审核员都改不掉
#   forbid     本槽位禁止项 → 同上
#   keep_clear 信息图留白区（供本地排版文字层）
#
# "每一张该有什么"由配置说了算，而不是每次靠模型自由发挥。


def slot_spec(slot_id: str) -> dict:
    """槽位契约原始条目（未登记槽位返回空字典）"""
    return dict(_slot_catalog().get(str(slot_id)) or {})


def _slot_text(slot_id: str, key: str) -> str:
    return str((_slot_catalog().get(str(slot_id)) or {}).get(key) or "").strip()


def _slot_list(slot_id: str, key: str) -> list[str]:
    value = (_slot_catalog().get(str(slot_id)) or {}).get(key) or []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple, set)):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def slot_intent(slot_id: str) -> str:
    """这张图要让买家看懂什么"""
    return _slot_text(slot_id, "intent")


def slot_design(slot_id: str) -> str:
    """版式/背景/光影/材质的设计要点"""
    return _slot_text(slot_id, "design")


def slot_must(slot_id: str) -> list[str]:
    """硬性要求（代码注入最终提示词）"""
    return _slot_list(slot_id, "must")


def slot_forbid(slot_id: str) -> list[str]:
    """本槽位禁止项（与 art_direction.forbidden_looks 叠加）"""
    return _slot_list(slot_id, "forbid")


def slot_keep_clear(slot_id: str) -> str:
    """信息图的留白区位置与比例（供本地排版文字层）"""
    return _slot_text(slot_id, "keep_clear")


# ── 背景策略 ──
#
# 用户实测质疑："我其实是没看到有平台真有白底硬规则这个说法，很多商品图都不是白底的啊？"
# —— 质疑成立：此前 `bg: "#FFFFFF"` 被当成全局硬规则，把设计底主图全部卡死。
# 现在按平台声明三档，且本地体检的"背景是否合格"**按本策略显式判定**（不再猜字符串）。
BACKGROUND_POLICIES = ("white_required", "white_preferred", "design_allowed")
BACKGROUND_POLICY_LABELS = {
    "white_required": "必须纯白（平台/类目硬要求）",
    "white_preferred": "白底为通行做法，也允许极浅品牌色底",
    "design_allowed": "允许干净设计底（品牌浅色/分割/渐层/微质感），只要不含文字/水印/边框/拼接",
}


def background_policy(raw) -> str:
    """该平台的主图背景策略（未声明回落 `background_policy_default` → design_allowed）"""
    value = str(get_platform(raw).get("background_policy") or "").strip().lower()
    if value in BACKGROUND_POLICIES:
        return value
    default = str(_platforms_doc().get("background_policy_default") or "").strip().lower()
    return default if default in BACKGROUND_POLICIES else "design_allowed"


def platform_label(raw) -> str:
    return str(get_platform(raw).get("label") or "")


# ── 视觉设计方向（用户："要平面设计风格、要有高级感让人觉得牌子正规"）──


def art_direction(raw=None) -> dict:
    """视觉设计方向（顶层 art_direction + 该平台的 art_style 覆盖）"""
    cfg = _platforms_doc().get("art_direction") or {}
    direction = dict(cfg) if isinstance(cfg, dict) else {}
    style = str(get_platform(raw).get("art_style") or "").strip() if raw else ""
    if style:
        direction["platform_style"] = style
    return direction


def art_direction_block(raw=None) -> str:
    """渲染给提示词用的设计方向块（**不要写成纪实摄影/随手拍**）"""
    direction = art_direction(raw)
    if not direction:
        return ""
    lines = ["## 视觉设计方向（**不要写成纪实摄影/随手拍**）"]
    if direction.get("goal"):
        lines.append(f"- 目标：{direction['goal']}")
    platform_style = str(direction.get("platform_style") or "").strip()
    if platform_style:
        lines.append(f"- 本平台设计调性：{platform_style}")
    roles = direction.get("palette_roles")
    if isinstance(roles, dict) and roles:
        lines.append("- 色板角色：" + "；".join(f"{k}={v}" for k, v in roles.items()))
    if direction.get("palette_source"):
        lines.append(f"- 色板来源：{direction['palette_source']}")
    elements = direction.get("allowed_elements") or []
    if elements:
        lines.append("- 允许的几何/质感元素：" + "、".join(str(item) for item in elements))
    if direction.get("element_budget"):
        lines.append(f"- 元素预算：{direction['element_budget']}")
    looks = direction.get("forbidden_looks") or []
    if looks:
        lines.append("- 设计禁忌：" + "、".join(str(item) for item in looks))
    ratios = direction.get("whitespace_ratio")
    if isinstance(ratios, dict) and ratios:
        for key, value in ratios.items():
            lines.append(f"- 留白基准（{key}）：{value}")
    return "\n".join(lines)


def slot_contract_block(slot_id: str, *, indent: str = "  ") -> str:
    """单槽位契约（给提示词生成员/审核优化员看的"这一张该有什么"）"""
    lines: list[str] = []
    intent = slot_intent(slot_id)
    design = slot_design(slot_id)
    must = slot_must(slot_id)
    forbid = slot_forbid(slot_id)
    keep_clear = slot_keep_clear(slot_id)
    if intent:
        lines.append(f"{indent}目的：{intent}")
    if design:
        lines.append(f"{indent}设计要点：{design}")
    if keep_clear:
        lines.append(f"{indent}留白区：{keep_clear}")
    if must:
        lines.append(f"{indent}必须：{'；'.join(must)}")
    if forbid:
        lines.append(f"{indent}禁止：{'；'.join(forbid)}")
    return "\n".join(lines)


def platform_slot_table(raw) -> list[dict]:
    """该平台的**编号槽位表**（主图在前、详情图在后；顺序即出图顺序）

    用户反馈"未有明确约束每一张该有的提示词"——这张表就是"第几张该是什么"的唯一来源：
    提示词生成员按它逐张产出，生图员按它逐张出图，审核优化员按它逐张判分。
    """
    profile = get_platform(raw)
    slug = profile.get("slug") or str(raw or "")
    aspect = str(profile.get("aspect") or "").strip()
    rows: list[dict] = []
    for usage, slot_ids in (("main", platform_slots(slug)),
                            ("detail", platform_detail_slots(slug))):
        for slot_id in slot_ids:
            kind = slot_kind(slot_id)
            rows.append({
                "number": len(rows) + 1,
                "slot_id": slot_id,
                "label": slot_label(slot_id),
                "kind": kind,
                "usage": usage,
                "layout": slot_layout(slot_id),
                "copy_sources": slot_copy_sources(slot_id),
                "aspect": aspect,
                "intent": slot_intent(slot_id),
                "design": slot_design(slot_id),
                "must": slot_must(slot_id),
                "forbid": slot_forbid(slot_id),
                "keep_clear": slot_keep_clear(slot_id),
                # 信息图：模型只出无字底图，文字由本地排版绘制
                "text_in_image": kind == "info",
                "background": "#FFFFFF" if kind == "photo" else "",
            })
    return rows


def slot_table_block(raw) -> str:
    """编号槽位表（渲染进提示词生成员的输入）"""
    rows = platform_slot_table(raw)
    if not rows:
        return ""
    lines = ["## 本平台套图槽位（**顺序即出图顺序，必须一张不少**）"]
    for row in rows:
        usage = "主图" if row["usage"] == "main" else "详情图"
        kind = "信息图·文字由系统本地排版" if row["kind"] == "info" else "纯摄影·画面无文字"
        lines.append(f"第{row['number']}张｜{row['slot_id']}（{row['label']}）｜{usage}"
                     f"｜{row['aspect'] or '画幅按平台'}｜{kind}")
        block = slot_contract_block(row["slot_id"])
        if block:
            lines.append(block)
    return "\n".join(lines)


def _text_policy_line(policy: str) -> str:
    if policy == "none":
        return ("**白底图/活动主图**上不得出现任何文字、字母、数字、logo、水印或角标（平台硬规则）；"
                "**信息类图**（卖点/成分/人群/功效/规格）本身就是图文版面 —— "
                "文字由系统本地排版绘制（字形准确、可审可改），模型不要往画面里画字")
    return ("模型**不要往画面里生成文字**：文字由系统本地排版绘制；"
            "请把信息类图给成「可承载文字的干净底图」（大面积留白/纯色版式 + 商品元素）")


def platform_style_block(raw) -> str:
    """渲染给提示词用的平台规范块（未登记平台会带明确的"不得臆造"提示）

    用户实测反馈与更正：
    - "未有明确约束每一张该有的提示词" → 这里给出**编号槽位表**（主图 + 详情图，逐张契约）；
    - 改前只列了主图槽位 → 详情图（规格/用法/资质/对比）从未进入模型视野，4 张一张没出；
    - "很多商品图都不是白底的啊？" → 背景按 `background_policy` 声明，不再谎称全局纯白硬规则。
    """
    profile = get_platform(raw)
    label = profile.get("label") or profile.get("slug") or ""
    if not profile.get("registered"):
        return (f"## 目标平台规范（{label}）\n"
                f"⚠️ 平台「{label}」未登记在 {PLATFORMS_REL} 中，**不得臆造该平台的具体规范**。\n"
                f"{_UNREGISTERED_HINT}")

    slug = profile.get("slug") or str(raw or "")
    lines = [f"## 目标平台规范（{label}）"]
    if profile.get("aspect"):
        lines.append(f"- 长宽比：{profile['aspect']}")
    if profile.get("export_size"):
        export = f"- 上传尺寸：{profile['export_size']}"
        if profile.get("min_side"):
            export += f"（最小边不低于 {profile['min_side']}px）"
        lines.append(export)

    policy = background_policy(slug)
    policy_note = BACKGROUND_POLICY_LABELS.get(policy, "")
    if policy == "white_required":
        lines.append(f"- 背景：必须纯白 {profile.get('bg') or '#FFFFFF'}，不得有渐变/灰底/纹理（{policy_note}）")
    elif policy == "white_preferred":
        lines.append(f"- 背景：优先纯白 {profile.get('bg') or '#FFFFFF'}（{policy_note}）；"
                     "若要设计底，只用极浅品牌色，且保持画面干净")
    else:
        lines.append(f"- 背景：{policy_note}；**不要求写实背景**——设计底是允许且被鼓励的，"
                     "但不得出现文字、水印、边框、拼接")

    lines.append(f"- 文字：{_text_policy_line(str(profile.get('text_policy') or 'composite'))}")
    forbidden = [str(item) for item in (profile.get("forbidden") or [])]
    if forbidden:
        lines.append(f"- 平台禁止出现：{'、'.join(forbidden)}")
    style = str(profile.get("style") or "").strip()
    if style:
        lines.append("- 主图风格：")
        lines.extend(f"  {row.strip()}" for row in style.splitlines() if row.strip())
    detail_style = str(profile.get("detail_style") or "").strip()
    if detail_style:
        lines.append("- 信息图/详情图版式风格（**与主图风格不同：信息图本来就要留白承载文字**）：")
        lines.extend(f"  {row.strip()}" for row in detail_style.splitlines() if row.strip())

    table = slot_table_block(slug)
    if table:
        lines.append("")
        lines.append(table)
    direction = art_direction_block(slug)
    if direction:
        lines.append("")
        lines.append(direction)
    return "\n".join(lines)


def list_platforms() -> list[dict]:
    """给 API/前端用的平台清单（默认平台排第一）"""
    profiles = load_platforms()
    default = default_platform()
    items: list[dict] = []
    for slug, profile in profiles.items():
        main_slots = [str(s) for s in (profile.get("slots") or [])]
        detail_slots = [str(s) for s in (profile.get("detail_slots") or [])]
        items.append({
            "slug": slug,
            "label": str(profile.get("label") or slug),
            "aliases": [str(a) for a in (profile.get("aliases") or [])],
            "aspect": str(profile.get("aspect") or ""),
            "export_size": str(profile.get("export_size") or ""),
            "min_side": profile.get("min_side") or 0,
            "max_images": profile.get("max_images") or 0,
            "max_detail_images": profile.get("max_detail_images") or 0,
            "bg": str(profile.get("bg") or ""),
            # 背景策略（用户质疑"很多商品图都不是白底"→ 三档声明，前端与体检都据此）
            "background_policy": background_policy(slug),
            "background_policy_label": BACKGROUND_POLICY_LABELS.get(background_policy(slug), ""),
            "text_policy": str(profile.get("text_policy") or "composite"),
            "slots": main_slots,
            "detail_slots": detail_slots,
            "slot_count": len(main_slots),
            "detail_slot_count": len(detail_slots),
            # 每个槽位的角色信息（摄影/信息图、主图/详情图），前端据此显示"哪些图带图文排版"
            "slot_roles": [{"slot_id": slot, "label": slot_label(slot),
                            "kind": slot_kind(slot), "usage": "main",
                            "intent": slot_intent(slot)}
                           for slot in main_slots]
                          + [{"slot_id": slot, "label": slot_label(slot),
                              "kind": slot_kind(slot), "usage": "detail",
                              "intent": slot_intent(slot)}
                             for slot in detail_slots],
            "is_default": slug == default,
        })
    items.sort(key=lambda item: (not item["is_default"], item["slug"]))
    return items


def platform_payload(raw) -> dict[str, Any]:
    """单个平台的对外载荷（含渲染好的风格块，供前端预览）"""
    profile = get_platform(raw)

    def _entries(slot_ids, usage: str) -> list[dict]:
        return [{"slot_id": slot, "label": slot_label(slot), "kind": slot_kind(slot),
                 "usage": usage, "layout": slot_layout(slot),
                 "copy_sources": slot_copy_sources(slot),
                 "intent": slot_intent(slot), "design": slot_design(slot),
                 "must": slot_must(slot), "forbid": slot_forbid(slot),
                 "keep_clear": slot_keep_clear(slot)}
                for slot in slot_ids]

    return {
        **{k: v for k, v in profile.items() if k not in ("style",)},
        "background_policy": background_policy(raw),
        "background_policy_label": BACKGROUND_POLICY_LABELS.get(background_policy(raw), ""),
        "slots": _entries([str(s) for s in (profile.get("slots") or [])], "main"),
        "detail_slots": _entries([str(s) for s in (profile.get("detail_slots") or [])], "detail"),
        "art_direction": art_direction(raw),
        "style_block": platform_style_block(raw),
    }
