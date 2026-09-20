"""风格档案库 —— 「大众商品图审美」的可检索载体（用户 2026-09-18 指定）

## 为什么有这个东西

用户原话（第 1 条反馈）："我发现生成的图片质量太差了，缺少了该有的商品图审美，这跟提示词
生成员的问题脱不开关系"；随后澄清审美标准是**平面设计**而不是纪实摄影（"背景不一定非要'真实'，
而是要有一定的高级感，让人一看就觉得这个牌子看着挺不错挺正规的样子"）。

把审美交给模型的自由发挥，结果就是"能过检查但审美不合格"（真实会话 `ee9a3b80e19b4010`：
6 张画面高度同质、单条 586 字近半是否定词）。这里把可复用的**设计决策**（版式 / 光位 / 材质 /
留白节奏）沉淀成档案：按「品类 + 平台 + 槽位 + 背景策略」检索，**逐槽位**注入
`提示词生成员` 与 `提示词审核优化员`；用户自己挑的图经「风格档案员」转成**文字判词**（锚点），
作为审核打分的准绳。

## 硬边界（本模块只读文字，永不碰图片）

用户最初的设想里有一个"底层图库"，但图片直进生图链路会把**别人包装上的文字/图案**带进本商品
（本仓库两次事故：记忆参考把"水飞蓟"抄进成分未确认的商品、`DEFOEBUENA®` 被编成 `NUTRIVA®`），
还会挤占实拍参考额度（默认 4 张全给商品图）。因此：

- 本模块**不 import 任何图像/参考图链路**（`reference_images` / `vision_payload` / base64），
  有静态测试钉住；
- 用户导入的照片只用于**风格分析**，产物是文字；照片本身落在 `data/style_library/`，
  **不进生图参考图池、不落 `data/inputs/`**。

## 事实中立（三道闸）

1. Agent 提示词层面：禁止输出品牌/成分/认证/功效/产地/色值（见 `config/prompts/style_archivist.yaml`）；
2. **落库/加载层面**（本模块）：`®™©`、`#RRGGBB`、事实词表命中即**丢弃条目并记账**（不静默）；
3. 注入层面：`load_library()` 与应用内置换同一套校验，坏条目不会进提示词。

另外：档案里的**色值是不许写的** —— 色板一律取**本商品包装**（`product_identity.brand_palette`），
档案只描述"色板角色怎么用"（主色→色带、辅色→点缀、底色→背景）。材质一律条件句
（"若包装本身有烫金…"），不许凭空给商品加工艺。

## 与 `agent_memory` 的分工

`agent_memory` 是**本项目历史成功提示词**（自动学习、按品类召回、可能夹带商品事实，已有禁令）；
本模块是**人工整理的设计原型 + 用户锚点**（事实中立、按品类/平台/槽位检索）。
两者可以同时注入，边界写在这里避免重复或混淆。
"""

import re
from typing import Any

from src.core.config import _project_root, load_yaml
from src.core.platforms import (
    background_policy,
    get_platform,
    platform_detail_slots,
    platform_slots,
    slot_forbid,
    slot_kind,
    slot_label,
    slot_spec,
)

STYLE_LIBRARY_REL = "config/style_library.yaml"
USER_STORE_REL = "data/style_library/entries.json"   # 用户词条落盘位置（见 style_store）

DEFAULT_ENABLED = True
# 默认**整套只用一套风格词**（用户 2026-09-20 原话："应该是一组生成图用一种风格，
# 或者说是一轮会话里只用一个风格词"；同日更正口径：单位是**一套**风格词 —— 即词库里
# 一条记录所携带的那组字段，不是"一个词"）。此前默认 2 → 实测每张图被「你的风格」与该条
# 内置原型同时指挥，而两者常互相矛盾（"浅粉渐层＋亚克力几何体" vs "纯白无缝、无道具无装饰"）。
# 调到 2+ 才允许内置原型去补"参考套图没覆盖的槽位"（那是用户显式选择的叠加）。
DEFAULT_MAX_ENTRIES = 1
DEFAULT_MAX_ANCHORS = 2
MAX_ENTRIES_CEILING = 4
MAX_ANCHORS_CEILING = 4
SLOT_BLOCK_MAX_CHARS = 400          # 单个槽位渲染上限（不塞爆提示词）
MAX_STYLE_WORDS_CHARS = 400
MAX_SUMMARY_CHARS = 80
USER_ENTRY_BONUS = 20               # 用户亲手导入的风格加成（见 `_score`）

# ── 套图结构（用户 2026-09-19："我给的是一套图片……还有套图的制作习惯"）──
#
# 用户导入的常常是**一整套上架图**（第1张白底、第2张成分、第3张人群…），此前提示词只
# 归纳"共同美术"，把每张的角色差异整段抹掉（`config/prompts/style_archivist.yaml` 原话
# "不要逐张描述"）→ 档案里没有任何字段能表达"这套图是怎么排的"。
#
# `shot_flow`：整套的叙事顺序（一句话）；`shot_roles`：逐张角色与"这张与共同美术的不同"。
# 角色**不由模型写词**：模型只选槽位 id，角色名（"成分配方图"）由 `slot_label()` 派生 ——
# 自由写角色名会踩事实词表（`FACT_MARKERS` 含「成分」）而被清洗掉，且与平台槽位目录脱节。
MAX_SHOT_ITEMS = 24                 # ≥ 照片上限（20）：绝不因上限截断角色
MAX_SHOT_FLOW_CHARS = 60
MAX_SHOT_TREATMENT_CHARS = 120
SEQUENCE_BLOCK_MAX_CHARS = 900      # 注入提示词的"套图结构"段总长上限（约 500 token）

# 用户词条的来源标记（`style_store.library_items()` 写入；内置条目由 normalize_entry 兜底为"内置"）
USER_ENTRY_SOURCE = "用户导入"
# 会话级风格锁：一轮会话（＝一组生成图）只用这**一套**风格词。存在产物里
# （`artifacts.prompts.style_refs[STYLE_LOCK_KEY]`），审核/体检/重跑都读它 ——
# 否则中途在词库切换启用项，同一套图会前几张用 A、后几张用 B（实测路径成立）。
STYLE_LOCK_KEY = "locked_entry_id"

# ── 事实中立词表（命中即丢弃；简繁并列）──

FACT_MARKERS = (
    # 成分/配方/原料
    "成分", "成份", "配方", "提取物", "原料", "添加劑", "添加剂",
    # 认证/资质
    "认证", "認證", "GMP", "gmp", "蓝帽", "藍帽", "有机认证", "有機認證", "专利", "專利",
    "资质", "資質", "检测报告", "檢測報告",
    # 产地/来源
    "原产地", "原產地", "产地", "產地", "进口", "進口", "德国制造", "德國製造",
    # 功效/疗效
    "功效", "疗效", "療效", "治疗", "治療", "治愈", "治癒", "根治", "免疫调节",
    "无副作用", "無副作用", "百分百", "100%有效",
)
# 广告法/极限词（复用信息图文案同一份词表，避免两套标准）
from src.harness.slot_copy import FORBIDDEN_CLAIMS  # noqa: E402  （放在此处：词表与事实词同类）

_TRADEMARK_RE = re.compile(r"[®™©]")
_HEX_RE = re.compile(r"#[0-9A-Fa-f]{3,8}\b")
_MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")

# 白名单字段（模型/配置多给的一律丢弃，避免脏字段流进提示词与前端）
_APPLIES_FIELDS = ("kinds", "slots", "not_slots", "categories", "platforms", "requires_policy")
_ENTRY_FIELDS = ("id", "name", "summary", "style_words", "source", "applies_to",
                 "background", "composition", "lighting", "materials", "elements",
                 "palette_roles", "whitespace", "forbid", "keep_clear_hint", "enabled",
                 # 套图结构：整套叙事顺序 + 逐张角色（只由「风格档案员」分析用户照片后写入）
                 "shot_flow", "shot_roles",
                 # 只用于"多条启用时用哪条"的确定性排序（不是设计字段，不进渲染）
                 "updated_at")
_ANCHOR_FIELDS = ("id", "name", "source", "taste_verdict", "reward_points", "avoid_points",
                  "applies_to", "enabled")
# 会被渲染进提示词的字段（校验与冲突检测只看这些；`forbid` 是**否定声明**，只查商标/色值）
_RENDERED_FIELDS = ("name", "summary", "style_words", "shot_flow", "background", "composition",
                    "lighting", "materials", "elements", "palette_roles", "whitespace",
                    "keep_clear_hint")
_NEGATIVE_FIELDS = ("forbid",)
_POSITIVE_FIELDS = _RENDERED_FIELDS

# ── 解析缓存 ──
#
# 与 core/platforms.py 同一教训：检索会按「槽位数 × 归档数」放大访问次数，
# 每次都重新读+解析 YAML 会把一次渲染拖到秒级。按 (路径, mtime_ns, size) 缓存 →
# **配置改了立刻生效**（不是永久缓存）。
_DOC_CACHE: dict[str, tuple[tuple, dict]] = {}


def _library_path():
    return _project_root() / STYLE_LIBRARY_REL


def _doc() -> dict:
    """`config/style_library.yaml` 的解析结果（带 mtime 缓存）"""
    path = _library_path()
    try:
        stat = path.stat()
        key = (str(path), stat.st_mtime_ns, stat.st_size)
    except OSError:
        return {}
    cached = _DOC_CACHE.get("doc")
    if cached and cached[0] == key:
        return cached[1]
    raw = load_yaml(STYLE_LIBRARY_REL)
    doc = raw if isinstance(raw, dict) else {}
    _DOC_CACHE["doc"] = (key, doc)
    return doc


def _text(value, limit: int = 0) -> str:
    if value is None or isinstance(value, (dict, list, tuple, set)):
        return ""
    text = _MULTI_SPACE_RE.sub(" ", str(value)).strip()
    return text[:limit] if limit else text


def _text_list(value, limit: int = 0) -> list[str]:
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, (list, tuple, set)):
        items = list(value)
    else:
        items = []
    out: list[str] = []
    for item in items:
        text = _text(item)
        if text and text not in out:
            out.append(text)
        if limit and len(out) >= limit:
            break
    return out


def _as_bool(value, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("true", "yes", "1", "on", "是"):
            return True
        if low in ("false", "no", "0", "off", "否"):
            return False
    return default


# ── 事实中立扫描 ──


def fact_violations(text: str, *, include_claims: bool = True) -> list[str]:
    """返回文本里命中的**事实/商标/色值**违规项（人类可读）"""
    raw = str(text or "")
    hits: list[str] = []
    found = _TRADEMARK_RE.findall(raw)
    if found:
        hits.append("商标符号 " + "".join(sorted(set(found))))
    hexes = _HEX_RE.findall(raw)
    if hexes:
        hits.append("色值 " + "、".join(sorted(set(hexes))))
    if include_claims:
        for marker in FACT_MARKERS:
            if marker in raw:
                hits.append(f"事实词「{marker}」")
        for marker in FORBIDDEN_CLAIMS:
            if marker in raw:
                hits.append(f"极限词「{marker}」")
    return hits


def scan_entry(entry) -> list[str]:
    """整条档案的事实中立违规（正向字段查事实词；否定字段只查商标/色值）"""
    entry = entry if isinstance(entry, dict) else {}
    reasons: list[str] = []
    for field in _POSITIVE_FIELDS:
        value = entry.get(field)
        texts = _text_list(value) if field == "elements" else [_text(value)]
        for text in texts:
            # 套图结构的叙事里会出现槽位角色名（「成分配方图」）→ 先摘掉受控词汇再查词表
            probe = _strip_role_vocabulary(text) if field == "shot_flow" else text
            for hit in fact_violations(probe):
                reasons.append(f"{field}：{hit}")
    # 逐张做法（`shot_roles[].treatment`）：也是会被渲染进提示词的正向文本
    for item in entry.get("shot_roles") or []:
        if not isinstance(item, dict):
            continue
        probe = _strip_role_vocabulary(_text(item.get("treatment")))
        for hit in fact_violations(probe):
            reasons.append(f"shot_roles[{item.get('number')}]：{hit}")
    for field in _NEGATIVE_FIELDS:
        for text in _text_list(entry.get(field)):
            for hit in fact_violations(text, include_claims=False):
                reasons.append(f"{field}：{hit}")
    return reasons


def _clean_positive(text: str, *, role_vocab: bool = False) -> tuple[str, list[str]]:
    """逐项清洗（用户词条用）：命中事实词/商标/色值的片段被剔除，返回 `(新文本, 已剔除)`

    `role_vocab=True`（用于"套图结构"的叙事与逐张做法）：先摘掉**槽位角色名**
    （「成分配方图」「资质认证图」…）再查词表 —— 角色名是系统自己的受控词汇
    （与 slot_id 同源，界面也在用），不是"这张照片上的事实"。
    真实事实（"含 12 种成分""德国 GMP 认证"）不含角色名原文，照样会被剔除。
    """
    raw = _text(text)
    if not raw:
        return "", []
    removed: list[str] = []
    parts = re.split(r"[；;]", raw)
    kept: list[str] = []
    for part in parts:
        piece = part.strip()
        if not piece:
            continue
        probe = _strip_role_vocabulary(piece) if role_vocab else piece
        hits = fact_violations(probe)
        if hits:
            removed.extend(hits)
            continue
        kept.append(piece)
    return "；".join(kept), removed


def _role_labels() -> list[str]:
    """槽位角色名清单（长词优先替换，避免"成分"先被换掉留下"配方图"）"""
    try:
        from src.core.platforms import all_slot_labels
        return sorted(set(all_slot_labels()), key=len, reverse=True)
    except Exception:  # noqa: BLE001 — 平台档案不可读时退化为"不豁免"
        return []


def _strip_role_vocabulary(text: str) -> str:
    """把文本里的槽位角色名替换成中性词（只用于**词表探测**，不改写落库内容）"""
    out = str(text or "")
    for label in _role_labels():
        if label and label in out:
            out = out.replace(label, "该角色图")
    return out


# ── 规范化与校验 ──


def normalize_shot_roles(raw, *, image_count: int = 0) -> list[dict[str, Any]]:
    """规范化"逐张角色"：`[{"number", "slot", "treatment"}]`

    - `slot` 必须是 `config/platforms.yaml → slot_catalog` 里登记的槽位 id，否则归空字符串
      （不是错误，只是"这张在平台套图里没有对应角色"）；
    - **序号必须与照片对得上**：给了 `image_count` 时，合法且未被占用的序号**原样保留**
      （"第3张是成分配方图"不能因为第2张没识别出来就被改写成第2张 —— 那会让提示词按错的
      照片写画面）；非法/重复的序号按 **1..image_count 的空位**补；角色比照片多时丢弃多余的；
    - 没给 `image_count` 时按数组位置编号 1..N（配置/测试里的手写条目）；
    - 上限 `MAX_SHOT_ITEMS`（≥ 照片上限 20，绝不因上限截断角色）。
    """
    if not isinstance(raw, (list, tuple)):
        return []
    try:
        total = max(0, int(image_count or 0))
    except (TypeError, ValueError):
        total = 0

    claimed: list[tuple[int, dict[str, Any]]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        slot = _text(item.get("slot"))
        if slot and not slot_spec(slot):
            slot = ""
        treatment = _text(item.get("treatment"), MAX_SHOT_TREATMENT_CHARS)
        if not slot and not treatment:
            continue
        try:
            number = int(item.get("number") or 0)
        except (TypeError, ValueError):
            number = 0
        claimed.append((number, {"slot": slot, "treatment": treatment}))
        if len(claimed) >= MAX_SHOT_ITEMS:
            break

    if not total:
        return [{"number": index, **payload}
                for index, (_number, payload) in enumerate(claimed, start=1)]

    used: set[int] = set()
    out: list[dict[str, Any]] = []
    for number, payload in claimed:
        if 1 <= number <= total and number not in used:
            used.add(number)
            out.append({"number": number, **payload})
    free = [number for number in range(1, total + 1) if number not in used]
    cursor = 0
    for _number, payload in claimed:
        if 1 <= _number <= total and _number in used:
            continue                      # 已经按原序号收下了
        if cursor >= len(free):
            break                          # 角色比照片多：多余的丢掉（上层会核对数量）
        out.append({"number": free[cursor], **payload})
        cursor += 1
    out.sort(key=lambda item: item["number"])
    return out


def _normalize_applies(raw) -> dict[str, list[str]]:
    cfg = raw if isinstance(raw, dict) else {}
    out = {field: _text_list(cfg.get(field)) for field in _APPLIES_FIELDS}
    out["kinds"] = [k for k in out["kinds"] if k in ("photo", "info")]
    out["requires_policy"] = [p for p in out["requires_policy"]
                              if p in ("white_required", "white_preferred", "design_allowed")]
    return out


def normalize_entry(raw: dict, *, index: int = 0) -> dict[str, Any] | None:
    """规范化一条档案（只保留白名单字段）；缺 `id`/`name` 返回 None"""
    if not isinstance(raw, dict):
        return None
    entry: dict[str, Any] = {
        "id": _text(raw.get("id")) or f"entry_{index + 1}",
        "name": _text(raw.get("name")),
        "summary": _text(raw.get("summary"), MAX_SUMMARY_CHARS),
        "style_words": _text(raw.get("style_words"), MAX_STYLE_WORDS_CHARS),
        "source": _text(raw.get("source")) or "内置",
        "applies_to": _normalize_applies(raw.get("applies_to")),
        "background": _text(raw.get("background")),
        "composition": _text(raw.get("composition")),
        "lighting": _text(raw.get("lighting")),
        "materials": _text(raw.get("materials")),
        "elements": _text_list(raw.get("elements"), 2),
        "palette_roles": {str(k): _text(v) for k, v in (raw.get("palette_roles") or {}).items()
                         if isinstance(raw.get("palette_roles"), dict) and _text(v)},
        "whitespace": _text(raw.get("whitespace")),
        "forbid": _text_list(raw.get("forbid"), 4),
        "keep_clear_hint": _text(raw.get("keep_clear_hint")),
        "enabled": _as_bool(raw.get("enabled"), True),
        # 套图结构（用户导入的档案才有：内置原型是"逐槽位怎么做"，不描述某一套图的顺序）
        "shot_flow": _text(raw.get("shot_flow"), MAX_SHOT_FLOW_CHARS),
        "shot_roles": normalize_shot_roles(
            raw.get("shot_roles"),
            image_count=len(raw.get("photos") or []) if isinstance(raw.get("photos"), list) else 0),
        # 用户词条的更新时间（内置档案没有）——「多条启用时用哪条」的排序依据
        "updated_at": _text(raw.get("updated_at")),
    }
    if not entry["name"]:
        return None
    return {key: entry[key] for key in _ENTRY_FIELDS}


def normalize_anchor(raw: dict, *, index: int = 0) -> dict[str, Any] | None:
    """规范化一条用户锚点（判词）"""
    if not isinstance(raw, dict):
        return None
    anchor = {
        "id": _text(raw.get("id")) or f"anchor_{index + 1}",
        "name": _text(raw.get("name")),
        "source": _text(raw.get("source")) or "用户锚点",
        "taste_verdict": _text(raw.get("taste_verdict")),
        "reward_points": _text_list(raw.get("reward_points"), 8),
        "avoid_points": _text_list(raw.get("avoid_points"), 8),
        "applies_to": _normalize_applies(raw.get("applies_to")),
        "enabled": _as_bool(raw.get("enabled"), True),
    }
    if not anchor["name"] or not (anchor["taste_verdict"] or anchor["reward_points"]):
        return None
    return {key: anchor[key] for key in _ANCHOR_FIELDS}


def validate_entry(entry: dict) -> list[str]:
    """可用性校验（返回停用原因；空 = 可用）"""
    reasons: list[str] = []
    if not entry.get("name"):
        reasons.append("缺少 name")
    design_fields = ("background", "composition", "lighting", "materials")
    if not any(entry.get(field) for field in design_fields):
        reasons.append("缺少设计要点（背景/构图/光影/材质至少要有一项）")
    applies = entry.get("applies_to") or {}
    if applies.get("kinds") and applies.get("slots"):
        # slots 指定的槽位必须与 kinds 一致（否则永远命中不了，属于配置错误）
        mismatch = [slot for slot in applies["slots"]
                    if slot_kind(slot) not in applies["kinds"]]
        if mismatch:
            reasons.append("slots 与 kinds 不一致：" + "、".join(mismatch))
    for slot in (applies.get("slots") or []) + (applies.get("not_slots") or []):
        if not slot_label(slot) or slot_label(slot) == slot:
            # 未登记槽位（slot_label 原样返回）——只提示，不阻断：自定义槽位是允许的
            continue
    if applies.get("slots") and set(applies["slots"]) & set(applies.get("not_slots") or []):
        reasons.append("slots 与 not_slots 冲突")
    reasons.extend(scan_entry(entry))
    return reasons


def load_library(tenant_id: str | None = None, *, include_user: bool = True) -> dict[str, Any]:
    """加载内置档案 + **用户词条**（`data/style_library/entries.json`）；坏条目丢弃并记账

    Args:
        tenant_id: 用户词条的租户过滤。`None` = 全部租户（预览脚本/管理视图用）；
            会话链路会显式传 `session["tenant_id"]`（隔离是**后端**保证）。
        include_user: 关掉可只读内置（测试/对比用）

    Returns:
        `{"entries": [...], "anchors": [...], "dropped": [{"id","reasons"}], "defaults": {...}}`
    """
    doc = _doc()
    defaults = doc.get("defaults") if isinstance(doc.get("defaults"), dict) else {}
    entries: list[dict] = []
    anchors: list[dict] = []
    dropped: list[dict] = []
    disabled: list[dict] = []
    seen: set[str] = set()

    # 内置档案的"停用"状态存在数据目录（程序**不得**改写带注释的 config/*.yaml）。
    # 注意与 `include_user` **无关**：那是"要不要合并用户词条"，而停用状态是内置档案自己的状态。
    disabled_builtin: set[str] = set()
    try:
        from src.harness.style_store import StyleStore
        disabled_builtin = StyleStore().disabled_builtin_ids()
    except Exception:  # noqa: BLE001 — 数据目录不可读时按"没有停用"处理
        disabled_builtin = set()

    for index, raw in enumerate(doc.get("entries") or []):
        entry = normalize_entry(raw, index=index)
        if entry is None:
            dropped.append({"id": _text((raw or {}).get("id")) or f"entry_{index + 1}",
                            "reasons": ["缺少 id/name 或格式不正确"]})
            continue
        if entry["id"] in seen:
            dropped.append({"id": entry["id"], "reasons": ["id 重复"]})
            continue
        reasons = validate_entry(entry)
        if reasons:
            dropped.append({"id": entry["id"], "reasons": reasons})
            continue
        if not entry["enabled"] or entry["id"] in disabled_builtin:
            entry["enabled"] = False
            # 停用的内置档案仍要**能被界面看到**（否则用户没法把它重新启用回来）
            disabled.append(entry)
            dropped.append({"id": entry["id"],
                            "reasons": ["已停用（界面可重新启用）"]})
            continue
        seen.add(entry["id"])
        entries.append(entry)

    for index, raw in enumerate(doc.get("anchors") or []):
        anchor = normalize_anchor(raw, index=index)
        if anchor is None:
            continue
        reasons = scan_entry({"name": anchor["name"], "style_words": anchor["taste_verdict"]})
        if reasons:
            dropped.append({"id": anchor["id"], "reasons": [f"锚点：{item}" for item in reasons]})
            continue
        if anchor["enabled"]:
            anchors.append(anchor)

    # ── 用户词条（data/style_library/）：与内置同一套校验与渲染路径 ──
    if include_user:
        try:
            from src.harness.style_store import StyleStore
            store = StyleStore()
            user_entries, user_anchors, user_dropped = store.library_items(tenant_id=tenant_id)
        except Exception as exc:  # noqa: BLE001 — 用户库读不到不能挡住内置档案
            user_entries, user_anchors, user_dropped = [], [], [
                {"id": "user_store", "reasons": [f"用户词条读取失败：{type(exc).__name__}: {exc}"]}]
        for entry in user_entries:
            if entry["id"] in seen:
                continue
            seen.add(entry["id"])
            entries.append(entry)
        anchors.extend(user_anchors)
        dropped.extend(user_dropped)

    return {"entries": entries, "anchors": anchors, "dropped": dropped,
            "disabled": disabled, "defaults": defaults, "path": STYLE_LIBRARY_REL,
            "exists": _library_path().exists()}


def sanitize_entry(raw: dict) -> tuple[dict[str, Any], list[str]]:
    """逐项清洗一条**用户/模型产出**的档案：命中事实词/商标/色值的**片段被剔除**

    与内置档案的处理不同：内置是人写的配置，命中即整条丢弃（配置错误要立刻暴露）；
    用户词条来自视觉模型的一次分析，局部越界（顺手写了"德国 GMP 认证"）很常见，
    整条丢掉会让用户白分析一次 —— 所以这里**保内容、剔越界、如实记账**。

    Returns: `(清洗后的字段, 已剔除说明)`
    """
    raw = raw if isinstance(raw, dict) else {}
    out: dict[str, Any] = {}
    removed: list[str] = []

    for field in ("summary", "style_words", "background", "composition", "lighting",
                  "materials", "whitespace", "keep_clear_hint", "taste_verdict"):
        text, hits = _clean_positive(raw.get(field))
        removed.extend(f"{field}：{hit}" for hit in hits)
        out[field] = text

    # 套图结构：叙事顺序与逐张做法同样是"正向文本"，命中事实词就逐片段剔除并记账；
    # **但先豁免槽位角色名**（「成分配方图」「资质认证图」是系统自己的受控词汇，
    # 不是"照片上的事实"——否则用户写"再讲成分配方图"会被整段剔掉）。
    # `slot`（槽位 id）**永不参与清洗**：它是受控词表里的标识，不是模型写的自由文本。
    flow, flow_hits = _clean_positive(raw.get("shot_flow"), role_vocab=True)
    removed.extend(f"shot_flow：{hit}" for hit in flow_hits)
    out["shot_flow"] = flow[:MAX_SHOT_FLOW_CHARS]
    # 照片数：优先分析结果里的 image_count，其次词条自己的 photos（PATCH 走的是后者）
    total_photos = raw.get("image_count")
    if not isinstance(total_photos, int) or total_photos <= 0:
        total_photos = len(raw.get("photos") or []) if isinstance(raw.get("photos"), list) else 0
    roles: list[dict[str, Any]] = []
    for item in normalize_shot_roles(raw.get("shot_roles"), image_count=total_photos):
        text, hits = _clean_positive(item["treatment"], role_vocab=True)
        removed.extend(f"shot_roles[{item['number']}]：{hit}" for hit in hits)
        if not item["slot"] and not text:
            continue
        roles.append({"number": item["number"], "slot": item["slot"], "treatment": text})
    out["shot_roles"] = roles

    for field in ("elements", "forbid", "reward_points", "avoid_points"):
        kept: list[str] = []
        for item in _text_list(raw.get(field)):
            text, hits = _clean_positive(item)
            removed.extend(f"{field}：{hit}" for hit in hits)
            if text:
                kept.append(text)
        out[field] = kept

    # 名称只查商标/色值（品类词是允许的：它就是用来检索的）
    name = _text(raw.get("name"))
    name_hits = fact_violations(name, include_claims=False)
    if name_hits:
        removed.extend(f"name：{hit}" for hit in name_hits)
        out["name"] = ""
    else:
        out["name"] = name

    roles = raw.get("palette_roles")
    if isinstance(roles, dict):
        cleaned_roles: dict[str, str] = {}
        for key, value in roles.items():
            text, hits = _clean_positive(value)
            removed.extend(f"palette_roles.{key}：{hit}" for hit in hits)
            if text:
                cleaned_roles[_text(key)] = text
        out["palette_roles"] = cleaned_roles
    else:
        out["palette_roles"] = {}

    out["applies_to"] = _normalize_applies(raw.get("applies_to"))
    out["as_anchor"] = _as_bool(raw.get("as_anchor"), True)
    out["enabled"] = _as_bool(raw.get("enabled"), True)
    suggestions = []
    for item in _text_list(raw.get("name_suggestions"), 3):
        text, hits = _clean_positive(item)
        removed.extend(f"name_suggestions：{hit}" for hit in hits)
        if text:
            suggestions.append(text)
    out["name_suggestions"] = suggestions
    return out, removed


# ── 生效策略（设置页可改）──


def _chat_settings() -> dict:
    """会话策略（`config/chat.yaml` → `config/default.yaml`）；读失败一律按未设置"""
    try:
        from src.core.config import chat_settings
        return chat_settings()
    except Exception:  # noqa: BLE001 — 策略不可用不应阻断出图
        return {}


def resolve_policy(defaults: dict | None = None) -> dict[str, Any]:
    """生效的开关与条数上限：设置页显式值 → YAML defaults → 代码默认

    注意"None 一律按未设置"（本仓库踩过 `bool(None)` 把开关静默关掉的坑）。
    """
    file_defaults = defaults if isinstance(defaults, dict) else {}
    settings = _chat_settings()

    enabled = settings.get("style_library_enabled")
    if enabled is None:
        enabled = file_defaults.get("enabled")
    if enabled is None:
        enabled = DEFAULT_ENABLED

    max_entries = settings.get("style_library_max")
    if max_entries is None:
        max_entries = file_defaults.get("max_entries")
    try:
        max_entries = int(max_entries)
    except (TypeError, ValueError):
        max_entries = DEFAULT_MAX_ENTRIES
    max_entries = max(0, min(MAX_ENTRIES_CEILING, max_entries))

    try:
        max_anchors = int(file_defaults.get("max_anchors", DEFAULT_MAX_ANCHORS))
    except (TypeError, ValueError):
        max_anchors = DEFAULT_MAX_ANCHORS
    return {"enabled": bool(enabled), "max_entries": max_entries,
            "max_anchors": max(0, min(MAX_ANCHORS_CEILING, max_anchors))}


# ── 检索 ──


def analyze_text(analysis) -> str:
    """品类检索用的 haystack

    **只用不带商品身份的部分**：品类 / 子品类 / 剂型 / 特征 / 卖点。
    不含品牌与品名 —— 否则一条叫"某某牌"的档案会被名字命中（同族事故：历史参考把
    「水飞蓟」抄进成分未确认的商品）。
    """
    if not isinstance(analysis, dict):
        return ""
    parts: list[str] = []
    for key in ("category", "sub_category", "dosage_form"):
        parts.append(_text(analysis.get(key)))
    parts.extend(_text_list(analysis.get("features")))
    markers = analysis.get("marketing_angles")
    if isinstance(markers, dict):
        parts.extend(_text_list(markers.get("selling_points")))
    return " ".join(part for part in parts if part)


def _slot_dicts(slots) -> list[dict]:
    """把 `plan.slots` / slot_id 列表归一成 `[{"slot_id","kind","number"}]`（保序去重）"""
    out: list[dict] = []
    seen: set[str] = set()
    for index, item in enumerate(slots or [], start=1):
        if isinstance(item, dict):
            slot_id = _text(item.get("slot_id"))
            kind = _text(item.get("kind")) or slot_kind(slot_id)
            try:
                number = int(item.get("number") or 0) or index
            except (TypeError, ValueError):
                number = index
        else:
            slot_id = _text(item)
            kind = slot_kind(slot_id)
            number = index
        if not slot_id or slot_id in seen:
            continue
        seen.add(slot_id)
        out.append({"slot_id": slot_id, "number": number,
                    "kind": kind if kind in ("photo", "info") else slot_kind(slot_id)})
    return out


def _category_hits(entry: dict, haystack: str) -> int:
    categories = (entry.get("applies_to") or {}).get("categories") or []
    if not categories:
        return 0
    return sum(1 for word in categories if word and word in haystack)


def _applies(entry: dict, *, slot_id: str, kind: str, platform: str,
             policy: str, haystack: str) -> tuple[bool, str]:
    """(是否适用, 说明)"""
    applies = entry.get("applies_to") or {}
    kinds = applies.get("kinds") or []
    slots = applies.get("slots") or []
    not_slots = applies.get("not_slots") or []
    platforms = applies.get("platforms") or []
    policies = applies.get("requires_policy") or []
    categories = applies.get("categories") or []

    if kinds and kind not in kinds:
        return False, "产出方式不匹配"
    if slots and slot_id not in slots:
        return False, "不在适用槽位内"
    if slot_id in not_slots:
        return False, "已被该档案排除"
    if platforms and platform and platform not in platforms:
        return False, "平台不匹配"
    if policies and policy and policy not in policies:
        return False, "背景策略不匹配"
    if categories and _category_hits(entry, haystack) == 0:
        return False, "品类未命中"
    return True, ""


def _score(entry: dict, *, slot_id: str, platform: str, haystack: str) -> int:
    """检索优先级（确定性）：品类命中数×100 + 平台显式×10 + 槽位显式×5 + 用户词条加成

    排序只在"同一层"内还有意义：用户词条是**整套唯一**的一条（`_resolve_active_user`），
    内置原型只在没有用户风格词、或用户显式调到 ≥2 允许补位时才参与。
    """
    applies = entry.get("applies_to") or {}
    score = _category_hits(entry, haystack) * 100
    if applies.get("platforms") and platform in applies["platforms"]:
        score += 10
    if applies.get("slots") and slot_id in applies["slots"]:
        score += 5
    if _is_user_entry(entry):
        score += USER_ENTRY_BONUS
    return score


# ── 会话级"一套风格词"（用户 2026-09-20：单位是**一组图/一轮会话**，不是单张；
#    同日晚些时候更正口径："p3 应该是一套风格词，而不是一个风格词"）──


def _is_user_entry(entry) -> bool:
    """是不是用户导入的**风格词**（内置条目是逐槽位的设计原型，不是"一整套风格"）"""
    return str((entry or {}).get("source") or "") == USER_ENTRY_SOURCE


def session_style_lock(session) -> str:
    """本轮会话已固定的风格词 id（任务显式指定 → 产物里的锁）

    - `task.style_entry_id`：用户点「换风格」时写入，优先级最高；
    - `artifacts.prompts.style_refs.locked_entry_id`：首次检索时落下的锁。
    """
    if not isinstance(session, dict):
        return ""
    task = session.get("task") if isinstance(session.get("task"), dict) else {}
    explicit = str(task.get("style_entry_id") or "").strip()
    if explicit:
        return explicit
    artifacts = session.get("artifacts") if isinstance(session.get("artifacts"), dict) else {}
    prompts = artifacts.get("prompts") if isinstance(artifacts.get("prompts"), dict) else {}
    refs = prompts.get("style_refs") if isinstance(prompts.get("style_refs"), dict) else {}
    return str(refs.get(STYLE_LOCK_KEY) or "").strip()


def covered_slots(entry) -> set[str]:
    """这条风格词**覆盖**哪些槽位

    - 有 `shot_roles`：按里面的槽位算（参考套图里没有的角色＝没覆盖）；
    - 没有 `shot_roles`（旧词条/通用美术）：视为**全槽位**都能用。
    """
    roles = (entry or {}).get("shot_roles") or []
    slots = {str(item.get("slot") or "") for item in roles
             if isinstance(item, dict) and item.get("slot")}
    return slots


def _user_covers(entry, slot_id: str) -> bool:
    slots = covered_slots(entry)
    return True if not slots else str(slot_id) in slots


def _resolve_active_user(candidates, *, haystack: str, locked_entry_id: str = ""
                         ) -> tuple[dict | None, str]:
    """本轮会话生效的那**一套**用户风格词 → `(entry|None, 如实说明)`

    优先级：会话锁（已固定/用户显式指定）→ 品类命中最多 → 最近更新 → id 字典序。
    正常路径下 radio（`style_store.set_entry_enabled`）保证只有一套启用；走到"多套"分支
    说明数据被手改或迁移遗留 —— 那时只用一套并**如实说出来**（不静默）。
    """
    items = [entry for entry in (candidates or []) if isinstance(entry, dict)]
    if not items:
        return None, ""
    locked = str(locked_entry_id or "").strip()
    if locked:
        for entry in items:
            if str(entry.get("id")) == locked:
                return entry, ""
        return None, (f"本次会话固定的风格词「{locked}」已不可用（被删除或停用）→ "
                      f"本套图回落内置槽位原型")
    if len(items) > 1:
        top = max(_category_hits(entry, haystack) for entry in items)
        pool = [entry for entry in items if _category_hits(entry, haystack) == top]
        pool.sort(key=lambda entry: str(entry.get("id")))              # 稳定排序的兜底键
        pool.sort(key=lambda entry: str(entry.get("updated_at") or ""), reverse=True)
        best = pool[0]
        others = "、".join(f"「{entry.get('name')}」" for entry in items
                           if entry.get("id") != best.get("id"))
        return best, (f"检测到 {len(items)} 条已启用的风格词，本次只用「{best.get('name')}」"
                      f"（未用：{others}）；建议在「风格词库」只启用一条")
    return items[0], ""


def _pick_for_slot(candidates, *, active_user: dict | None, slot_id: str,
                   max_entries: int, user_locked: bool = False) -> list[dict]:
    """逐槽位取档案 —— **一轮会话只用一套风格词**

    - 生效的那一套覆盖该槽位（且它本身通过适用性校验）→ 只用它（1 条，不再叠加内置）；
    - 覆盖不到：
        · 严格模式（`max_entries <= 1`，默认）→ 返回空，**不塞第二套风格**，
          该张靠平台槽位契约（design/must/forbid/keep_clear，代码注入，从不缺席）兜底；
        · 补位模式（`max_entries >= 2`，用户显式调大）→ 允许内置原型补 1 条；
    - 没有生效风格词 → 内置原型按 `max_entries` 取（未导入风格词时的旧行为）。
    - `user_locked=True`（会话锁指向的词条已不可用）：**不静默换另一条用户风格**，
      只回落内置原型 —— 换了风格而用户不知道，比没有风格更糟。
    """
    ranked = [entry for _score_neg, _index, entry in candidates]
    if active_user is not None:
        active_id = str(active_user.get("id") or "")
        if _user_covers(active_user, slot_id) and any(str(entry.get("id")) == active_id
                                                     for entry in ranked):
            return [active_user]
        if max_entries <= 1:
            return []
        return [entry for entry in ranked if not _is_user_entry(entry)][:1]
    if user_locked:
        ranked = [entry for entry in ranked if not _is_user_entry(entry)]
    return ranked[:max_entries]


def select_by_slot(slots, *, analysis=None, platform: str = "",
                   policy: str | None = None, max_entries: int | None = None,
                   enabled: bool | None = None, library: dict | None = None,
                   tenant_id: str | None = None,
                   locked_entry_id: str = "") -> dict[str, Any]:
    """逐槽位检索适用档案

    Args:
        slots: `plan.slots`（含 slot_id/kind）或 slot_id 列表
        analysis: 商品分析结果（取品类/子品类/剂型/特征做关键词匹配）
        policy: 背景策略（默认取平台档案；`white_required` 平台不会命中设计底档案）
        max_entries: **整套最多用几种风格**（None → 设置页 → YAML defaults → 1）。
            1（默认）＝只用会话固定的那一条用户风格词；≥2 才允许内置原型补未覆盖的槽位
        enabled: 总开关（None → 设置页 → YAML defaults → true）
        library: 已加载的档案库（不传则现加载：内置 + 该租户的用户词条）
        tenant_id: 用户词条的租户过滤（会话链路传 `session["tenant_id"]`）
        locked_entry_id: 本轮会话**已固定**的风格词 id（见 `session_style_lock()`）；
            给出后只用它，锁定的词条不可用时回落内置原型并如实说明

    Returns:
        `{"enabled", "platform", "policy", "max_entries", "slots": {slot_id: [entry]},
          "entries": [...], "anchors": [...], "dropped": [...], "notes": [...],
          "active_entry_id", "locked_entry_id", "strict_single", "selection_note": "..."}`
    """
    lib = library if isinstance(library, dict) else load_library(tenant_id)
    resolved = resolve_policy(lib.get("defaults"))
    if enabled is not None:
        resolved["enabled"] = bool(enabled)
    if max_entries is not None:
        try:
            resolved["max_entries"] = max(0, min(MAX_ENTRIES_CEILING, int(max_entries)))
        except (TypeError, ValueError):
            pass

    slug = get_platform(platform).get("slug") or _text(platform)
    bg_policy = policy or background_policy(slug)
    haystack = analyze_text(analysis)
    slot_dicts = _slot_dicts(slots)
    lock = str(locked_entry_id or "").strip()

    result: dict[str, Any] = {
        "enabled": resolved["enabled"],
        "platform": slug,
        "policy": bg_policy,
        "max_entries": resolved["max_entries"],
        "max_anchors": resolved["max_anchors"],
        "slots": {},
        "entries": [],
        "anchors": [],
        # 一轮会话一套风格词：这两个字段要落进产物（`style_refs`），后续阶段复用它
        "active_entry_id": "",
        "locked_entry_id": lock,
        "strict_single": resolved["max_entries"] <= 1,
        # 逐张对应表（渲染块里用"第 N 张"，与人机之间的指代一致）
        "slot_table": [{"slot_id": slot["slot_id"], "number": slot["number"],
                        "kind": slot["kind"]} for slot in slot_dicts],
        "dropped": list(lib.get("dropped") or []),
        "notes": [],
        "selection_note": "",
    }
    if not slot_dicts:
        result["notes"].append("没有槽位可检索（本次没有套图编排）")
        return result

    # 注入前校验（**防御性**）：调用方可能把用户词条（`data/style_library/`）与本内置档案
    # 合并后传进来，坏条目一律在这里被拦下并记账 —— 与"不静默"原则一致。
    entries: list[dict] = []
    for index, raw in enumerate(lib.get("entries") or []):
        entry = normalize_entry(raw, index=index)
        if entry is None:
            result["dropped"].append({"id": f"entry_{index + 1}",
                                      "reasons": ["缺少 id/name 或格式不正确"]})
            continue
        reasons = validate_entry(entry)
        if reasons:
            result["dropped"].append({"id": entry["id"], "reasons": reasons})
            continue
        if not entry["enabled"]:
            result["dropped"].append({"id": entry["id"], "reasons": ["已停用（enabled: false）"]})
            continue
        entries.append(entry)

    # 锚点：与"本套编排里至少有一个槽位能用它"挂钩（不相关的锚点不参与打分）。
    # 注意要在"没有条目"的提前返回**之前**算，否则锚点会被一起吞掉（实测踩到）。
    anchors: list[dict] = []
    for anchor in lib.get("anchors") or []:
        applies_to = anchor.get("applies_to") or {}
        kinds = applies_to.get("kinds") or []
        categories = applies_to.get("categories") or []
        if kinds and not any(slot["kind"] in kinds for slot in slot_dicts):
            continue
        if categories and not any(word and word in haystack for word in categories):
            continue
        anchors.append(anchor)
    result["anchors"] = anchors[:resolved["max_anchors"]]

    if not resolved["enabled"]:
        result["notes"].append("风格档案库已在设置页关闭（chat.style_library_enabled=false）")
        return result
    if not lib.get("exists"):
        result["notes"].append(f"未找到 {STYLE_LIBRARY_REL}（未注入任何档案）")
        return result
    if not entries:
        result["notes"].append("风格档案库为空（配置缺失，或全部条目未通过事实中立校验）")
        return result

    # ── 本轮生效的**那一套**风格词（会话锁优先；radio 保证通常只有一套）──
    active_user, active_note = _resolve_active_user(
        [entry for entry in entries if _is_user_entry(entry)],
        haystack=haystack, locked_entry_id=lock)
    if active_note:
        result["notes"].append(active_note)
    result["active_entry_id"] = str(active_user.get("id")) if active_user else ""

    used: dict[str, dict] = {}
    for slot in slot_dicts:
        slot_id = slot["slot_id"]
        kind = slot["kind"]
        candidates: list[tuple[int, int, dict]] = []
        for index, entry in enumerate(entries):
            applies, _reason = _applies(entry, slot_id=slot_id, kind=kind, platform=slug,
                                        policy=bg_policy, haystack=haystack)
            if not applies:
                continue
            candidates.append((-_score(entry, slot_id=slot_id, platform=slug,
                                       haystack=haystack), index, entry))
        candidates.sort(key=lambda item: (item[0], item[1]))
        picked = _pick_for_slot(candidates, active_user=active_user, slot_id=slot_id,
                                max_entries=resolved["max_entries"], user_locked=bool(lock))
        result["slots"][slot_id] = picked
        for entry in picked:
            used.setdefault(entry["id"], entry)

    result["entries"] = list(used.values())

    # 没有任何条目命中（配置把某些槽位排除干净了）——如实说，不静默
    empty_slots = [slot_id for slot_id, picked in result["slots"].items() if not picked]
    if empty_slots:
        if active_user is not None and resolved["max_entries"] <= 1:
            result["notes"].append(
                "以下槽位参考套图没有对应角色，本次**没有注入第二个风格**"
                "（只按平台槽位契约写）：" + "、".join(empty_slots))
        else:
            result["notes"].append("以下槽位没有匹配到风格档案：" + "、".join(empty_slots))
    if result["dropped"]:
        result["notes"].append(
            f"{len(result['dropped'])} 条档案未启用（" +
            "；".join(f"{item['id']}：{'、'.join(item['reasons'][:2])}"
                     for item in result["dropped"][:4]) + "）")
    result["selection_note"] = describe_selection(result)
    return result


# ── 渲染 ──


def style_plain_text(entry: dict, slot_id: str = "") -> str:
    """档案的**纯文本**（不带"背景：/构图："标签），供"逐字照抄"检测比对

    只用正向字段：`forbid` 是否定声明，模型抄它反而无害（渲染时也单列）。

    `slot_id`：只并上**该槽位**的逐张做法。不传就并上全部 —— 但注意那样会把
    "X 槽抄了 Y 槽的做法"报成命中（`prompt_lint` 在逐槽位体检时会传当前槽位）。
    """
    entry = entry if isinstance(entry, dict) else {}
    parts: list[str] = []
    for field in _POSITIVE_FIELDS:
        value = entry.get(field)
        if field == "elements":
            parts.extend(_text_list(value))
        elif field == "palette_roles" and isinstance(value, dict):
            parts.extend(_text(item) for item in value.values())
        else:
            parts.append(_text(value))
    for item in entry.get("shot_roles") or []:
        if not isinstance(item, dict):
            continue
        if slot_id and str(item.get("slot") or "") != str(slot_id):
            continue
        parts.append(_text(item.get("treatment")))
    return "；".join(part for part in parts if part)


def _shot_role_of(entry: dict, slot_id: str) -> dict | None:
    """这条档案里"参考套图第 N 张 = 该槽位"的那条角色（同槽位多张时取第一张）"""
    for item in entry.get("shot_roles") or []:
        if isinstance(item, dict) and str(item.get("slot") or "") == str(slot_id):
            return item
    return None


def _entry_line(entry: dict) -> str:
    """一条档案 → 一行设计要点（不写"必须/禁止"式硬约束，那些由槽位契约注入）"""
    bits: list[str] = []
    if entry.get("background"):
        bits.append(f"背景：{entry['background']}")
    if entry.get("composition"):
        bits.append(f"构图：{entry['composition']}")
    if entry.get("lighting"):
        bits.append(f"光影：{entry['lighting']}")
    if entry.get("materials"):
        bits.append(f"材质：{entry['materials']}")
    if entry.get("elements"):
        bits.append("元素：" + "、".join(entry["elements"]))
    if entry.get("palette_roles"):
        roles = "；".join(f"{k}={v}" for k, v in entry["palette_roles"].items())
        bits.append(f"色板角色：{roles}")
    if entry.get("whitespace"):
        bits.append(f"留白：{entry['whitespace']}")
    if entry.get("keep_clear_hint"):
        bits.append(f"留白区提示：{entry['keep_clear_hint']}")
    if entry.get("forbid"):
        bits.append("本档案避免：" + "、".join(entry["forbid"]))
    return f"档案「{entry['name']}」—— " + "；".join(bits)


def render_slot_block(selection, *, audience: str = "gen") -> str:
    """逐槽位档案块（注入「提示词生成员」/「提示词审核优化员」）

    结构（**每条档案只写一次**）：
    ```
    ## 适用风格档案（…纪律…）
    ### 档案定义
    - 「净白硬照」：背景…；构图…；光影…；材质…；…
    ### 参考套图的编排习惯（用户挑的这一组是怎么排的）   ← 只有带 shot_roles 的档案才有
    - 「同色清新」：参考第1张 纯商品图 → 参考第2张 成分配方图
    - 叙事顺序：先白底立信任 → 再讲配方
    ### 逐张对应
    - 第1张｜main_white（纯商品图）：「同色清新」→ 该张做法：…
    - 第8张｜main_cert（资质认证图）：仅按槽位契约写（参考套图里没有这个角色）
    ### 用户审美锚点…  ← 由 render_anchor_block 单独渲染
    ```

    为什么不按槽位各写一遍：8 个信息图槽位会命中同一条「信息图版式」，
    逐槽位重复写一遍就是 3000+ 字重复文本（上一轮实测：586 字约束堆叠已让画面呆板拘谨）。
    这里每条档案只出现一次，逐张关系用一行映射表达。
    """
    selection = selection if isinstance(selection, dict) else {}
    if not selection.get("enabled") or not selection.get("slots"):
        return ""
    used_slots = {slot_id: picked for slot_id, picked in selection["slots"].items() if picked}
    if not used_slots:
        return ""
    if audience == "review":
        head = ("## 适用风格档案（**逐张对照打分**；用户锚点优先于本档案，"
                "槽位契约的必须/禁止优先于档案）")
    else:
        head = ("## 适用风格档案（**只借鉴「怎么设计」**：背景/构图/光影/材质/留白；"
                "槽位契约的必须/禁止优先；**禁止照搬档案措辞或元素**）")

    # 档案定义（按首次出现顺序，每条一次）
    definitions: list[str] = []
    line_by_id: dict[str, str] = {}
    for picked in used_slots.values():
        for entry in picked:
            entry_id = str(entry.get("id") or entry.get("name"))
            if entry_id in line_by_id:
                continue
            body = _entry_line(entry)
            if len(body) > SLOT_BLOCK_MAX_CHARS:
                body = body[:SLOT_BLOCK_MAX_CHARS].rstrip("；;") + "…（已截断）"
            line_by_id[entry_id] = body
            definitions.append(f"- {body}")

    slot_table = [slot for slot in (selection.get("slot_table") or []) if isinstance(slot, dict)]
    numbers = {slot.get("slot_id"): slot.get("number") for slot in slot_table}
    order = [slot.get("slot_id") for slot in slot_table] or list(used_slots)
    order = [slot_id for slot_id in order if slot_id in used_slots or slot_id in numbers]

    # 逐张对应（用 plan 里的"第 N 张"，与人机之间的指代一致）
    mapping: list[str] = []
    for slot_id in order:
        picked = used_slots.get(slot_id) or []
        number = f"第{numbers.get(slot_id) or '?'}张｜"
        kind = "信息图底图" if slot_kind(slot_id) == "info" else "纯摄影"
        label = f"- {number}{slot_id}（{slot_label(slot_id)}｜{kind}）"
        if not picked:
            mapping.append(f"{label}：仅按槽位契约写（参考套图里没有这个角色，"
                           "不注入第二个风格）")
            continue
        names = " + ".join(f"「{entry['name']}」" for entry in picked)
        role = _shot_role_of(picked[0], slot_id)
        if role and role.get("treatment"):
            names += f" → 该张做法：{role['treatment']}"
        mapping.append(f"{label}：{names}")

    # 参考套图的编排习惯（只有带 shot_roles 的档案才产生这一段 —— 旧档案逐字不变）
    sequence: list[str] = []
    for picked in used_slots.values():
        for entry in picked:
            roles = [item for item in (entry.get("shot_roles") or [])
                     if isinstance(item, dict) and (item.get("slot") or item.get("treatment"))]
            if not roles:
                continue
            chain = " → ".join(
                f"参考第{item['number']}张 {slot_label(item['slot']) if item.get('slot') else '其他画面'}"
                for item in roles)
            if f"- 「{entry['name']}」{chain}" in sequence:
                continue
            sequence.append(f"- 「{entry['name']}」：{chain}")
            if entry.get("shot_flow"):
                sequence.append(f"- 叙事顺序：{entry['shot_flow']}")
    sequence_lines: list[str] = []
    if sequence:
        body = "\n".join(sequence)
        if len(body) > SEQUENCE_BLOCK_MAX_CHARS:
            body = body[:SEQUENCE_BLOCK_MAX_CHARS].rstrip("；; \n") + "…（已截断）"
        sequence_lines = ["### 参考套图的编排习惯（用户挑的这一组是怎么排的）",
                          body]

    lines = [head, "### 档案定义", *definitions, *sequence_lines, "### 逐张对应", *mapping]
    return "\n".join(lines)


def render_anchor_block(anchors, *, audience: str = "review") -> str:
    """用户锚点块（"就要这种感觉"的判词）—— 审核打分以此为准绳"""
    items = [anchor for anchor in (anchors or []) if isinstance(anchor, dict)]
    if not items:
        return ""
    if audience == "review":
        lines = ["## 用户审美锚点（用户亲自挑的「就要这种感觉」——**审美打分以它为准绳，"
                 "优先于内置标准**）"]
    else:
        lines = ["## 用户审美锚点（用户亲自挑的「就要这种感觉」——**往这个方向写画面**）"]
    for anchor in items:
        lines.append(f"- 「{anchor.get('name')}」判词：{anchor.get('taste_verdict') or '（未填判词）'}")
        if anchor.get("reward_points"):
            lines.append("  要有的（逐条核对）：" + "、".join(anchor["reward_points"]))
        if anchor.get("avoid_points"):
            lines.append("  要避免的：" + "、".join(anchor["avoid_points"]))
    return "\n".join(lines)


def style_block_for(*, slots=None, analysis=None, platform: str = "", audience: str = "gen",
                    include_anchors: bool = True, selection: dict | None = None) -> str:
    """一步到位：检索 + 渲染

    - 给了 `selection`（已检索过）就直接渲染，避免重复检索；
    - `include_anchors=False`：只给档案（成图审查员只要锚点，不需要档案）。
    """
    picked = selection if isinstance(selection, dict) else select_by_slot(
        slots, analysis=analysis, platform=platform)
    parts = [render_slot_block(picked, audience=audience)]
    if include_anchors:
        parts.append(render_anchor_block(picked.get("anchors"), audience=audience))
    return "\n\n".join(part for part in parts if part)


def describe_selection(selection) -> str:
    """一行摘要（群聊播报用）——**如实列出实际注入的全部**，不再吞掉第二条

    用户 2026-09-20 原话："应该是一组生成图用一种风格，或者说是一轮会话里只用一个风格词"；
    同日的口径更正：单位是**一套**风格词（一条记录携带的那组字段），不是"一个词"。
    此前只报 `picked[0]`，于是"你的风格 + 一套内置原型"同时注入时，群聊里只看得见前者
    （实测：10 个槽位张张 2 条，界面完全看不出来）。

    现在的口径：
    - 有生效的那一套风格词 → `本次会话风格「X」（已固定）`；
    - 没有 → `未启用风格词，使用内置槽位原型`；
    - 逐张只报**例外**：没有注入档案的槽位（仅平台槽位契约）、走内置兜底的槽位。
    """
    selection = selection if isinstance(selection, dict) else {}
    if not selection.get("enabled"):
        return "🎨 风格档案库：已关闭"
    slot_table = [slot for slot in (selection.get("slot_table") or []) if isinstance(slot, dict)]
    slots = selection.get("slots") or {}
    used = {slot_id: picked for slot_id, picked in slots.items() if picked}
    if not used:
        return "🎨 风格档案库：本次没有匹配到档案"
    active_id = str(selection.get("active_entry_id") or "")
    names = {str(entry.get("id")): str(entry.get("name") or "")
             for entry in selection.get("entries") or []}
    active_name = names.get(active_id, "")

    labels = {slot_id: f"第{slot.get('number')}张"
              for slot in slot_table for slot_id in [slot.get("slot_id")]}
    if active_name:
        head = f"🎨 采用风格档案：本次会话风格「{active_name}」"
        if selection.get("locked_entry_id"):
            head += "（已固定）"
    else:
        head = "🎨 采用风格档案：未启用风格词，使用内置槽位原型"

    total = len(slots) or len(used)
    covered = len(used)
    text = f"{head}；覆盖 {covered}/{total} 张"
    if active_name:
        # 只报例外：没注入档案的（严格模式）与走内置兜底的（补位模式）
        empty = [labels.get(slot_id, slot_id) for slot_id, picked in slots.items() if not picked]
        fallback = [labels.get(slot_id, slot_id) for slot_id, picked in slots.items()
                    if picked and not _is_user_entry(picked[0])]
        if empty:
            text += "；仅平台槽位契约：" + "、".join(empty[:4]) + ("…" if len(empty) > 4 else "")
        if fallback:
            text += "；内置兜底：" + "、".join(fallback[:4]) \
                + ("…" if len(fallback) > 4 else "")
    if selection.get("anchors"):
        text += "；锚点：" + "、".join(f"「{anchor.get('name')}」"
                                    for anchor in selection["anchors"])
    return text


def sequence_coverage(selection) -> list[dict]:
    """参考套图与本平台套图的**覆盖差**（逐条档案一组）

    用户 2026-09-19："我给的是一套图片……还有套图的制作习惯"。这份对照回答的是
    "你这套图里有哪几个角色、本平台套图对得上哪几张、哪些张没有对应角色"。
    **只进预览与产物快照，不进提示词**（不为不需要的信息付 token）。
    """
    selection = selection if isinstance(selection, dict) else {}
    slot_table = [slot for slot in (selection.get("slot_table") or [])
                  if isinstance(slot, dict)]
    platform_slot_ids = [str(slot.get("slot_id")) for slot in slot_table]
    out: list[dict] = []
    for entry in selection.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        roles = [item for item in (entry.get("shot_roles") or []) if isinstance(item, dict)]
        if not roles:
            continue
        ref_slots = {str(item.get("slot") or "") for item in roles}
        ref_slots.discard("")
        matched = []
        for slot_id in platform_slot_ids:
            if slot_id in ref_slots:
                number = next(item.get("number") for item in roles
                              if str(item.get("slot") or "") == slot_id)
                matched.append({"slot": slot_id, "ref_number": number})
        out.append({
            "entry_id": entry.get("id"), "name": entry.get("name"),
            "ref_count": len(roles), "flow": entry.get("shot_flow") or "",
            "items": [{"number": item.get("number"), "slot": str(item.get("slot") or ""),
                       "label": slot_label(item["slot"]) if item.get("slot") else "",
                       "treatment": item.get("treatment") or ""} for item in roles],
            "matched": matched,
            "missing_in_ref": [slot for slot in platform_slot_ids if slot not in ref_slots],
            "extra_in_ref": sorted(ref_slots - set(platform_slot_ids)),
        })
    return out


def usage_snapshot(selection, *, dropped=None) -> dict[str, Any]:
    """落进产物的可读快照（`artifacts.prompts.style_refs`）

    存 **id + 名字快照**：档案被改名/删除后，历史会话仍然读得懂当时用了什么。

    `locked_entry_id` / `active_entry` 只在真有风格词时写入 —— 旧产物（没有这两个键）
    与今天逐字节可比，前端也不会因为空值多出一行。
    """
    selection = selection if isinstance(selection, dict) else {}
    slots: dict[str, list[dict]] = {}
    for slot_id, picked in (selection.get("slots") or {}).items():
        slots[slot_id] = [{"id": entry.get("id"), "name": entry.get("name"),
                           "source": entry.get("source")} for entry in picked]
    payload = {
        "enabled": bool(selection.get("enabled")),
        "platform": selection.get("platform", ""),
        "policy": selection.get("policy", ""),
        "max_entries": selection.get("max_entries"),
        "strict_single": bool(selection.get("strict_single", True)),
        "entries": [{"id": entry.get("id"), "name": entry.get("name"),
                     "source": entry.get("source")} for entry in selection.get("entries") or []],
        "slots": slots,
        "anchors": [{"id": anchor.get("id"), "name": anchor.get("name")}
                    for anchor in selection.get("anchors") or []],
        "dropped": dropped if dropped is not None else (selection.get("dropped") or [])[:5],
        "notes": selection.get("notes") or [],
        "message": describe_selection(selection),
    }
    active_id = str(selection.get("active_entry_id") or "")
    if active_id:
        name = next((str(entry.get("name") or "") for entry in selection.get("entries") or []
                     if str(entry.get("id")) == active_id), "")
        payload["active_entry"] = {"id": active_id, "name": name}
        payload[STYLE_LOCK_KEY] = str(selection.get("locked_entry_id") or "") or active_id
    # 套图结构（只有带 shot_roles 的档案才有 → 旧产物/旧档案不多出任何键）
    coverage = sequence_coverage(selection)
    if coverage:
        payload["coverage"] = coverage
        payload["sequence"] = [{"entry_id": item["entry_id"], "name": item.get("name"),
                                "ref_count": item["ref_count"], "flow": item["flow"],
                                "items": item["items"]} for item in coverage]
    return payload


# ── 冲突检测（档案 vs 槽位契约）──


def applicable_slots(entry: dict, platform: str, *, policy: str | None = None,
                     haystack: str = "") -> list[str]:
    """该档案在某平台上**可能命中**的槽位（忽略品类：品类只影响优先级，不影响适用）"""
    slug = get_platform(platform).get("slug") or _text(platform)
    bg_policy = policy or background_policy(slug)
    all_slots = [str(s) for s in (platform_slots(slug) + platform_detail_slots(slug))]
    out: list[str] = []
    for slot_id in all_slots:
        applies, _reason = _applies(entry, slot_id=slot_id, kind=slot_kind(slot_id),
                                    platform=slug, policy=bg_policy, haystack=haystack)
        if applies:
            out.append(slot_id)
    return out


# 否定前缀与"交给本地排版"的委派措辞：档案里说"**无**道具""文字由**本地排版**"
# 是在**配合**槽位禁止项，不是在违反它。没有这两条，冲突检测会把它们全部误报成冲突
# （实测：`clean_hero_white` × main_white 的「道具」、`info_bullet_sheet` × 所有信息图
# 槽位的「文字」，18 条全是误报）。
_NEGATION_PREFIXES = ("无", "不", "非", "零", "禁止", "避免", "杜绝", "没有", "不得", "勿", "去除")
_DELEGATION_PHRASES = ("本地排版文字", "排版文字层", "文字由系统", "系统本地排版", "排版文字", "文字层")
# 信息图槽位的禁止项里「文字/数字/字母」指的是**不要往画面里画字**，而档案里提到"文字"多半是
# 在说"本地排版层往哪儿放"（"主体与文字之间留气口""带内文字上下留 30%"）—— 实测一条用户词条
# 因此报了 **36 处**"与契约不一致"，全是同一个误报，把真正的问题埋掉了。
# 这类违规由 `prompt_lint` 的「要求画面里出现文字」检测在**提示词**层面精确拦截，这里不重复报。
_TEXT_RULE_FRAGMENTS = ("文字", "数字", "字母")
_CONTEXT_CHARS = 8


def _occurs_positively(text: str, fragment: str) -> bool:
    """`fragment` 是否以**肯定**语气出现在 `text` 里（前方有否定词/委派措辞则不算）"""
    start = 0
    while True:
        index = text.find(fragment, start)
        if index < 0:
            return False
        window = text[max(0, index - _CONTEXT_CHARS):index + len(fragment) + _CONTEXT_CHARS]
        if not any(neg in window for neg in _NEGATION_PREFIXES) \
                and not any(phrase in window for phrase in _DELEGATION_PHRASES):
            return True
        start = index + len(fragment)


def entry_conflicts(entry: dict, slot_ids) -> list[dict]:
    """档案的正向描述与槽位 `forbid` 的冲突项（例：给 `main_white` 注入设计底）

    - 只看**正向字段**：`forbid` 本身是否定声明（"不要渐变底"），拿它去撞槽位禁止项是误报；
    - 命中处带否定词或"交给本地排版"的委派措辞时不算冲突（见 `_occurs_positively`）；
    - 逐张做法**只与它自己那个槽位**比对：参考套图第2张（信息图）的做法不该拿去撞
      `main_white` 的禁止项 —— 那样会把"跨槽位"报成冲突。
    """
    positives = " ".join(_text(entry.get(field)) for field in _POSITIVE_FIELDS)
    positives += " " + "、".join(_text_list(entry.get("elements")))
    conflicts: list[dict] = []
    for slot_id in slot_ids or []:
        role = _shot_role_of(entry or {}, slot_id)
        scoped = positives
        if role and role.get("treatment"):
            scoped = f"{positives} {role['treatment']}"
        for rule in slot_forbid(slot_id):
            for fragment in re.split(r"[/、，,；;]", str(rule)):
                piece = fragment.strip()
                if piece in _TEXT_RULE_FRAGMENTS:
                    continue      # 见 `_TEXT_RULE_FRAGMENTS`：这类在提示词体检里精确拦
                if len(piece) >= 2 and _occurs_positively(scoped, piece):
                    conflicts.append({"slot_id": slot_id, "rule": piece})
    return conflicts


def similar_entries(entry: dict, others, *, threshold: float = 0.75) -> list[dict]:
    """与已有档案的重复度提示（"净白硬照 / 纯白棚拍 / 白底商品图"这类同义条目堆叠）

    用**包含度**而不是 Jaccard：一条 25 字的词条把内置档案的整段描术包含进去时，
    Jaccard 只有 0.13（长短差异把相似度稀释掉了），而"这条其实在重复已有档案"是真的。
    口径与槽位同质化检测的 3-gram 一致（`prompt_lint.text_overlap`）。
    """
    from src.harness.prompt_lint import text_overlap

    target = _text(entry.get("style_words")) or _text(entry.get("summary")) \
        or _text(entry.get("background"))
    out: list[dict] = []
    for other in others or []:
        if not isinstance(other, dict) or other.get("id") == entry.get("id"):
            continue
        text = _text(other.get("style_words")) or _text(other.get("summary")) \
            or _text(other.get("background"))
        score = text_overlap(target, text)
        if score >= threshold:
            out.append({"id": other.get("id"), "name": other.get("name"),
                        "similarity": score})
    return out


def builtin_ids() -> set[str]:
    """全部内置档案 id（**含已停用的**）

    用它判断"这个 id 是不是内置档案"：停用的内置档案仍然要能被重新启用，
    只查 `entries` 会把它们当成"不存在"（实测踩到：停用后就再也启用不回来）。
    """
    lib = load_library(include_user=False)
    return {str(item.get("id")) for item in (lib.get("entries") or [])} | \
           {str(item.get("id")) for item in (lib.get("disabled") or [])}


def library_stats(library: dict | None = None) -> dict[str, Any]:
    """给前端/脚本看的概览"""
    lib = library if isinstance(library, dict) else load_library()
    policy = resolve_policy(lib.get("defaults"))
    return {
        "entries": len(lib.get("entries") or []),
        "anchors": len(lib.get("anchors") or []),
        "dropped": len(lib.get("dropped") or []),
        "path": lib.get("path", STYLE_LIBRARY_REL),
        "exists": bool(lib.get("exists")),
        "enabled": policy["enabled"],
        "max_entries": policy["max_entries"],
        "max_anchors": policy["max_anchors"],
        "user_store": USER_STORE_REL,
    }
