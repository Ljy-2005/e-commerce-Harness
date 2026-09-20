"""风格档案库（`harness/style_library.py`）—— 内置档案、检索、渲染与事实中立回归

用户 2026-09-18：先说"缺少了该有的商品图审美"（提示词生成员的问题），随后澄清审美标准是
**平面设计**而不是纪实摄影；再指出"未有明确约束每一张该有的提示词"。
本模块把可复用的设计决策沉淀成**逐槽位可检索**的档案；用户自己挑的图转成文字判词（锚点）。

这份测试守三件事：
1. **出货内容干净**：内置档案加载 0 丢弃（商标符号 / 色值 / 成分认证功效词全部零命中）；
2. **不会打架**：档案的正向描述与本槽位 `forbid` 零冲突（`main_white` 永远拿不到设计底）；
3. **真能命中**：任何平台 × 任何槽位都至少拿到 1 条档案（否则"逐张约束"就是空话）。
"""

import pytest

from src.core.platforms import (
    background_policy,
    list_platforms,
    platform_detail_slots,
    platform_slots,
    slot_forbid,
    slot_kind,
)
from src.harness import style_library as sl

# 一个"有品类信息"的分析结果（用于品类优先级）
ANALYSIS_HEALTH = {"category": "保健品", "sub_category": "护肝类", "features": ["便携装"]}
ANALYSIS_3C = {"category": "3C数码", "sub_category": "耳机", "features": ["降噪"]}


def _slots(platform: str) -> list[dict]:
    ids = [str(s) for s in platform_slots(platform)] + \
          [str(s) for s in platform_detail_slots(platform)]
    return [{"slot_id": slot_id, "kind": slot_kind(slot_id)} for slot_id in ids]


def _all_platforms() -> list[str]:
    return [item["slug"] for item in list_platforms()]


# ── 内置内容 ──


def test_builtin_entries_load_clean():
    """内置档案全部通过事实中立校验（有被丢弃的条目要立刻看见）

    "已停用"是**用户的显式选择**（不是配置错误），不算被丢弃 —— 只挑校验类原因断言。
    """
    lib = sl.load_library(include_user=False)
    assert lib["exists"] is True
    catalog = lib["entries"] + (lib.get("disabled") or [])
    assert len(catalog) >= 8, f"内置档案不足，实际 {len(catalog)} 条"
    invalid = [item for item in lib["dropped"]
               if not all("已停用" in reason for reason in item["reasons"])]
    assert invalid == [], f"有档案未通过校验：{invalid}"


def test_entries_have_identity_and_design_points():
    for entry in sl.load_library(include_user=False)["entries"]:
        assert entry["id"] and entry["name"] and entry["summary"]
        assert entry["style_words"], f"{entry['id']} 缺 style_words"
        assert any(entry.get(field) for field in
                   ("background", "composition", "lighting", "materials")), \
            f"{entry['id']} 没有任何设计要点"


def test_entry_platform_slugs_are_registered():
    """档案里声明的平台 slug 必须是真实存在的（否则永远命中不了，属配置错误）"""
    known = {item["slug"] for item in list_platforms()}
    for entry in sl.load_library()["entries"]:
        declared = set((entry["applies_to"].get("platforms") or []))
        assert declared <= known, f"{entry['id']} 声明了未登记平台：{declared - known}"


def test_entry_slots_are_consistent_with_kinds():
    for entry in sl.load_library()["entries"]:
        applies = entry["applies_to"]
        kinds = applies.get("kinds") or []
        for slot in applies.get("slots") or []:
            assert not kinds or slot_kind(slot) in kinds, \
                f"{entry['id']} 的 slots 与 kinds 不一致：{slot}"


def test_defaults_are_sane():
    defaults = sl.load_library()["defaults"]
    assert int(defaults.get("max_entries", 2)) >= 1
    assert sl.resolve_policy(defaults)["enabled"] is True


# ── 事实中立 ──


@pytest.mark.parametrize("text,expect", [
    ("品牌 ® 烫金", "商标"),
    ("底色 #EEF2F7 渐层", "色值"),
    ("含 12 种成分", "事实词"),
    ("通过 GMP 认证", "事实词"),
    ("美白功效显著", "事实词"),
    ("干净的白底与短投影", ""),
])
def test_fact_violations(text, expect):
    hits = sl.fact_violations(text)
    if expect:
        assert any(expect in item for item in hits), f"{text!r} 应命中 {expect}：{hits}"
    else:
        assert hits == []


def test_forbid_field_is_not_scanned_for_claims():
    """`forbid` 是否定声明（"不要疗效暗示"），不该被事实词误杀；商标仍然拦"""
    entry = {"name": "测试", "background": "纯白底", "forbid": ["疗效暗示元素"]}
    assert sl.scan_entry(entry) == []
    entry["forbid"] = ["某™品牌同款"]
    assert any("商标" in item for item in sl.scan_entry(entry))


def test_bad_entry_is_dropped_with_reason():
    library = {
        "entries": [{"id": "bad", "name": "带色值", "background": "底色 #FFFFFF"}],
        "anchors": [], "dropped": [], "defaults": {}, "exists": True,
    }
    result = sl.select_by_slot([{"slot_id": "main_white", "kind": "photo"}],
                               platform="taobao", library=library)
    assert result["entries"] == []
    assert any("风格档案库为空" in note for note in result["notes"])


def test_anchors_default_empty():
    """锚点是**用户的审美**，交付时不预置任何内容"""
    assert sl.load_library()["anchors"] == []


# ── 覆盖率与冲突（"逐张约束"的前提）──


@pytest.mark.parametrize("platform", _all_platforms())
def test_every_slot_gets_at_least_one_entry(platform):
    slots = _slots(platform)
    assert slots, f"{platform} 没有槽位"
    result = sl.select_by_slot(slots, platform=platform)
    empty = [slot_id for slot_id, picked in result["slots"].items() if not picked]
    assert empty == [], f"{platform} 这些槽位没命中档案：{empty}"
    assert result["enabled"] is True


@pytest.mark.parametrize("platform", _all_platforms())
def test_no_entry_contradicts_slot_forbid(platform):
    """档案的正向描述不得与适用槽位的 `forbid` 冲突（否则等于自己给自己下禁令）"""
    for entry in sl.load_library()["entries"]:
        applicable = sl.applicable_slots(entry, platform, haystack="保健品 3C数码")
        conflicts = sl.entry_conflicts(entry, applicable)
        assert conflicts == [], f"{platform}／{entry['id']} 冲突：{conflicts}"


def test_main_white_never_gets_design_background():
    """`main_white` 的契约要求纯白（禁灰底/渐变/纹理）→ 设计底档案必须排除它"""
    for platform in _all_platforms():
        result = sl.select_by_slot([{"slot_id": "main_white", "kind": "photo"}],
                                   platform=platform)
        picked = result["slots"]["main_white"]
        assert picked, f"{platform} 的 main_white 没命中档案"
        for entry in picked:
            assert "main_white" not in (entry["applies_to"].get("slots") or []) \
                or entry["id"] == "clean_hero_white"
        assert any(entry["id"] == "clean_hero_white" for entry in picked)
        conflicts = sl.entry_conflicts(picked[0], ["main_white"])
        assert conflicts == []


def test_amazon_gets_white_entries_only():
    """white_required 平台不该出现设计底档案"""
    amazon = "amazon"
    assert background_policy(amazon) == "white_required"
    result = sl.select_by_slot(_slots(amazon), platform=amazon)
    picked_ids = {entry["id"] for entry in result["entries"]}
    assert "brand_color_block" not in picked_ids
    assert "tech_dark_metal" not in picked_ids


# ── 检索 ──


def test_category_entry_outranks_generic():
    result = sl.select_by_slot([{"slot_id": "main_scene", "kind": "photo"}],
                               analysis=ANALYSIS_HEALTH, platform="taobao")
    picked = result["slots"]["main_scene"]
    assert picked, "没命中任何档案"
    categories = [entry["id"] for entry in picked
                  if entry["applies_to"].get("categories")]
    assert categories, f"品类档案没有排在前面：{[e['id'] for e in picked]}"


def test_tech_category_picks_dark_metal():
    result = sl.select_by_slot([{"slot_id": "main_detail", "kind": "photo"}],
                               analysis=ANALYSIS_3C, platform="taobao")
    ids = [entry["id"] for entry in result["slots"]["main_detail"]]
    assert ids[0] == "tech_dark_metal", ids


def test_info_slots_use_info_entry():
    result = sl.select_by_slot([{"slot_id": "main_selling_point", "kind": "info"}],
                               platform="pinduoduo")
    ids = [entry["id"] for entry in result["slots"]["main_selling_point"]]
    assert ids == ["info_bullet_sheet"], ids


def test_max_entries_cap_and_determinism():
    slots = [{"slot_id": "main_scene", "kind": "photo"}]
    first = sl.select_by_slot(slots, analysis=ANALYSIS_HEALTH, platform="taobao",
                              max_entries=1)
    assert len(first["slots"]["main_scene"]) == 1
    again = sl.select_by_slot(slots, analysis=ANALYSIS_HEALTH, platform="taobao",
                              max_entries=1)
    assert [e["id"] for e in again["slots"]["main_scene"]] == \
           [e["id"] for e in first["slots"]["main_scene"]]
    zero = sl.select_by_slot(slots, analysis=ANALYSIS_HEALTH, platform="taobao",
                             max_entries=0)
    assert zero["slots"]["main_scene"] == []


def test_disabled_returns_nothing(monkeypatch):
    monkeypatch.setattr(sl, "_chat_settings", lambda: {"style_library_enabled": False})
    result = sl.select_by_slot(_slots("taobao"), analysis=ANALYSIS_HEALTH,
                               platform="taobao")
    assert result["enabled"] is False
    assert result["entries"] == []
    assert sl.render_slot_block(result) == ""
    assert "关闭" in sl.describe_selection(result)


def test_chat_settings_override_file_defaults(monkeypatch):
    monkeypatch.setattr(sl, "_chat_settings", lambda: {"style_library_max": 1})
    policy = sl.resolve_policy({"max_entries": 3, "enabled": False})
    assert policy["max_entries"] == 1
    # 显式值优先于文件默认值
    assert policy["enabled"] is False
    monkeypatch.setattr(sl, "_chat_settings", lambda: {"style_library_enabled": True})
    assert sl.resolve_policy({"enabled": False})["enabled"] is True


# ── 一轮会话一套风格词（用户 2026-09-20：单位是"一组生成图/一轮会话"，且是**一套**词）──
#
# 取证：此前默认"每张最多 2 条"，实测拼多多 10 个槽位**张张 2 条** ——「你的风格」＋一条
# 内置原型，且常互相矛盾（"浅粉渐层 + 亚克力几何体" vs "纯白无缝、无道具无装饰无文字"）。
# 且 `describe_selection()` 只报 `picked[0]`，群聊里完全看不出第二条的存在。


def _user_entry(entry_id="st_a", *, name="同色清新", slots=None, **extra):
    """一条用户风格词（够过 `validate_entry`）"""
    entry = {
        "id": entry_id, "name": name, "source": "用户导入", "enabled": True,
        "summary": "浅色渐层 + 大留白", "style_words": "极浅同色渐层，主体居中偏右占六成",
        "background": "极浅同色渐层底", "composition": "主体居中偏右、占画面 60%",
        "lighting": "均匀柔光、投影极淡", "materials": "哑光塑料瓶身",
        "elements": [], "palette_roles": {}, "whitespace": "一侧留白 ≥30%",
        "forbid": [], "keep_clear_hint": "", "applies_to": {},
    }
    if slots:
        entry["shot_roles"] = [{"number": index, "slot": slot, "treatment": "留白换到左侧"}
                               for index, slot in enumerate(slots, start=1)]
    entry.update(extra)
    return entry


def _library(*entries):
    return {"entries": list(entries), "anchors": [], "dropped": [],
            "defaults": {}, "exists": True}


def _builtin(entry_id, *, kinds=None, slots=None):
    """测试用的**内置**原型（不依赖 config/ 与数据目录的状态，结果完全确定）"""
    entry = {"id": entry_id, "name": f"内置{entry_id}", "source": "内置", "enabled": True,
             "summary": "通用原型", "style_words": "干净规整的通用做法",
             "background": "纯白或极浅底", "composition": "主体居中、四周留白",
             "lighting": "均匀柔光", "materials": "哑光纸纹", "elements": [],
             "palette_roles": {}, "whitespace": "留白均匀", "forbid": [],
             "keep_clear_hint": "", "applies_to": {}}
    if kinds:
        entry["applies_to"]["kinds"] = list(kinds)
    if slots:
        entry["applies_to"]["slots"] = list(slots)
    return entry


def _mixed_library(*user_entries):
    """内置原型 + 用户风格词（模拟真实合并后的档案库）"""
    return _library(_builtin("b_photo", kinds=["photo"]),
                    _builtin("b_info", kinds=["info"]), *user_entries)


def test_one_style_for_the_whole_set():
    """一组生成图只用一套风格词：覆盖的槽位**不再叠加内置原型**"""
    result = sl.select_by_slot(_slots("pinduoduo"), analysis=ANALYSIS_HEALTH,
                               platform="pinduoduo", library=_library(_user_entry()))
    assert result["strict_single"] is True
    assert result["active_entry_id"] == "st_a"
    assert all(len(picked) == 1 for picked in result["slots"].values()), result["slots"]
    assert {picked[0]["id"] for picked in result["slots"].values()} == {"st_a"}


def test_uncovered_slot_gets_no_second_style_in_strict_mode():
    """参考套图没覆盖的槽位：严格模式只按平台槽位契约写，**不塞第二个风格**"""
    entry = _user_entry(slots=["main_white"])
    strict = sl.select_by_slot(_slots("pinduoduo"), analysis=ANALYSIS_HEALTH,
                               platform="pinduoduo", library=_mixed_library(entry),
                               max_entries=1)
    assert [item["id"] for item in strict["slots"]["main_white"]] == ["st_a"]
    assert strict["slots"]["main_ingredients"] == []
    assert any("没有注入第二个风格" in note for note in strict["notes"])

    # 显式调到 2 种 → 才允许内置原型补未覆盖的槽位（风格会混，是用户的选择）
    fallback = sl.select_by_slot(_slots("pinduoduo"), analysis=ANALYSIS_HEALTH,
                                 platform="pinduoduo", library=_mixed_library(entry),
                                 max_entries=2)
    assert [item["id"] for item in fallback["slots"]["main_white"]] == ["st_a"]
    assert [item["id"] for item in fallback["slots"]["main_ingredients"]] == ["b_info"]
    assert "内置兜底" in sl.describe_selection(fallback)


def test_session_lock_pins_one_style():
    """会话锁：本轮固定用哪一条；锁不可用时回落内置并**如实说明**"""
    entry_a = _user_entry("st_a", name="风格A", updated_at="2026-09-19T00:00:00+00:00")
    entry_b = _user_entry("st_b", name="风格B", updated_at="2026-09-20T00:00:00+00:00")
    library = _mixed_library(entry_a, entry_b)   # 两条用户词条都 enabled（数据被手改/迁移遗留）

    pinned = sl.select_by_slot(_slots("taobao"), analysis=ANALYSIS_HEALTH, platform="taobao",
                               library=library, locked_entry_id="st_a")
    assert pinned["active_entry_id"] == "st_a"
    assert pinned["locked_entry_id"] == "st_a"
    assert {picked[0]["id"] for picked in pinned["slots"].values()} == {"st_a"}
    assert "已固定" in sl.describe_selection(pinned)

    loose = sl.select_by_slot(_slots("taobao"), analysis=ANALYSIS_HEALTH, platform="taobao",
                              library=library)
    assert loose["active_entry_id"] == "st_b", "多条启用时应取最近更新的那条"
    assert any("只启用一条" in note for note in loose["notes"])

    gone = sl.select_by_slot(_slots("taobao"), analysis=ANALYSIS_HEALTH, platform="taobao",
                             library=library, locked_entry_id="st_gone")
    assert gone["active_entry_id"] == ""
    assert any("已不可用" in note for note in gone["notes"])
    # 锁失效时**不静默换另一条用户风格** → 只用内置原型
    assert all(picked and picked[0]["source"] == "内置" for picked in gone["slots"].values())


def test_session_style_lock_prefers_task_then_artifacts():
    assert sl.session_style_lock({}) == ""
    assert sl.session_style_lock(
        {"artifacts": {"prompts": {"style_refs": {sl.STYLE_LOCK_KEY: "st_a"}}}}) == "st_a"
    # 用户点「换风格」写进 task.style_entry_id → 优先级最高
    assert sl.session_style_lock(
        {"task": {"style_entry_id": "st_b"},
         "artifacts": {"prompts": {"style_refs": {sl.STYLE_LOCK_KEY: "st_a"}}}}) == "st_b"


def test_snapshot_reports_active_style_only_when_present():
    locked = sl.usage_snapshot(sl.select_by_slot(
        _slots("taobao"), analysis=ANALYSIS_HEALTH, platform="taobao",
        library=_library(_user_entry()), locked_entry_id="st_a"))
    assert locked["active_entry"] == {"id": "st_a", "name": "同色清新"}
    assert locked[sl.STYLE_LOCK_KEY] == "st_a"
    # 没有用户风格时**不写这两个键** → 旧产物与前端行为逐字节可比
    plain = sl.usage_snapshot(sl.select_by_slot(_slots("taobao"), platform="taobao"))
    assert sl.STYLE_LOCK_KEY not in plain and "active_entry" not in plain
    assert plain["message"].startswith("🎯 采用风格档案".replace("🎯", "🎨"))


# ── 套图结构：shot_flow / shot_roles（用户 2026-09-19："我给的是一套图片"）──


def test_normalize_shot_roles_renumbers_and_rejects_unknown_slots():
    """没有照片数时按位置编号；未登记槽位归空，但**不丢这张**"""
    roles = sl.normalize_shot_roles([
        {"number": 7, "slot": "main_white", "treatment": "纯白无缝、无设计元素"},
        {"number": 9, "slot": "not_a_slot", "treatment": "看不清的一张"},
        {"number": 3, "slot": "", "treatment": ""},
    ])
    assert [item["number"] for item in roles] == [1, 2]
    assert roles[0]["slot"] == "main_white"
    assert roles[1]["slot"] == "" and roles[1]["treatment"] == "看不清的一张"


def test_normalize_shot_roles_keeps_numbers_aligned_with_photos():
    """**序号必须与照片对得上**：第2张没识别出来，第3张仍然是第3张

    若按数组位置重编号，"参考第2张"就会指到第3张照片上 —— 提示词按错的照片写画面。
    """
    roles = sl.normalize_shot_roles([
        {"number": 1, "slot": "main_white", "treatment": "纯白无缝"},
        {"number": 3, "slot": "main_audience", "treatment": "上方留标题区"},
    ], image_count=4)
    assert [item["number"] for item in roles] == [1, 3]

    # 非法/越界/重复的序号 → 补进空位（仍然落在照片范围内）
    fixed = sl.normalize_shot_roles([
        {"number": 0, "slot": "main_white", "treatment": "a"},
        {"number": 99, "slot": "main_ingredients", "treatment": "b"},
        {"number": 1, "slot": "main_audience", "treatment": "c"},
    ], image_count=3)
    assert [item["number"] for item in fixed] == [1, 2, 3]

    # 角色比照片多 → 多余的丢掉（不硬塞到不存在的照片上）
    extra = sl.normalize_shot_roles(
        [{"number": index, "slot": "main_white", "treatment": "x"} for index in range(1, 6)],
        image_count=3)
    assert [item["number"] for item in extra] == [1, 2, 3]


def test_sanitize_strips_facts_from_treatments():
    """逐张做法同样是正向文本：命中事实词/商标/色值就逐片段剔除并**记账**

    但**槽位角色名要豁免**：「成分配方图」是系统自己的受控词汇（`slot_label`），
    不是"照片上的成分表" —— 否则用户写"再讲成分配方图"会被整段剔掉。
    """
    cleaned, removed = sl.sanitize_entry({
        "background": "浅色渐层底",
        "shot_flow": "先白底立信任；再讲成分配方图",
        "shot_roles": [{"number": 1, "slot": "main_white",
                        "treatment": "纯白无缝；印着 ® 品牌字样"}],
    })
    # 角色名被豁免（保留），商标符号照样剔除并记账
    assert cleaned["shot_flow"] == "先白底立信任；再讲成分配方图"
    assert cleaned["shot_roles"][0]["treatment"] == "纯白无缝"
    assert any("商标" in item for item in removed), removed
    # 真实事实（含数字的成分宣称）不含角色名原文 → 仍然被剔除
    cleaned2, removed2 = sl.sanitize_entry({"background": "白底",
                                            "shot_flow": "含 12 种成分的配方"})
    assert cleaned2["shot_flow"] == ""
    assert any("事实词" in item for item in removed2), removed2


def test_builtin_entries_have_no_shot_roles():
    """内置原型是"逐槽位怎么做"，**不描述某一套图的顺序** —— 别让示例变成数据"""
    library = sl.load_library(include_user=False)
    catalog = library["entries"] + (library.get("disabled") or [])
    assert catalog
    for entry in catalog:
        assert not entry.get("shot_roles"), entry["id"]
        assert not entry.get("shot_flow"), entry["id"]


def test_sequence_block_is_rendered_per_slot():
    entry = _user_entry(slots=["main_white", "main_ingredients"],
                        shot_flow="先白底立信任 → 再讲成分配方图")
    selection = sl.select_by_slot(_slots("pinduoduo"), analysis=ANALYSIS_HEALTH,
                                  platform="pinduoduo", library=_mixed_library(entry))
    assert selection["active_entry_id"] == "st_a", selection["dropped"]
    block = sl.render_slot_block(selection)
    assert "### 参考套图的编排习惯" in block
    assert "参考第1张 纯商品图" in block and "参考第2张 成分配方图" in block
    assert "叙事顺序：先白底立信任 → 再讲成分配方图" in block
    assert "该张做法：留白换到左侧" in block
    assert "仅按槽位契约写（参考套图里没有这个角色" in block


def test_entry_without_shot_roles_renders_as_before():
    """没有套图结构的档案（含全部内置原型）渲染块里**不出现新段落**"""
    selection = sl.select_by_slot(_slots("taobao"), platform="taobao")
    block = sl.render_slot_block(selection)
    assert "参考套图的编排习惯" not in block
    assert "该张做法" not in block
    assert "- 第1张｜main_white（纯商品图｜纯摄影）：" in block


def test_sequence_coverage_reports_gaps():
    entry = _user_entry(slots=["main_white", "main_ingredients", "activity_banner"])
    selection = sl.select_by_slot(_slots("taobao"), analysis=ANALYSIS_HEALTH,
                                  platform="taobao", library=_mixed_library(entry))
    coverage = sl.sequence_coverage(selection)
    assert len(coverage) == 1
    item = coverage[0]
    assert item["entry_id"] == "st_a" and item["ref_count"] == 3
    assert [m["slot"] for m in item["matched"]] == ["main_white", "main_ingredients"]
    assert "main_spec" in item["missing_in_ref"]
    assert item["extra_in_ref"] == ["activity_banner"]      # 本平台套图没有这个角色
    snapshot = sl.usage_snapshot(selection)
    assert snapshot["coverage"][0]["entry_id"] == "st_a"
    assert snapshot["sequence"][0]["flow"] == ""
    # 没有结构的档案 → 不多出这两个键
    plain = sl.usage_snapshot(sl.select_by_slot(_slots("taobao"), platform="taobao"))
    assert "coverage" not in plain and "sequence" not in plain


def test_conflicts_are_scoped_to_the_slot():
    """参考套图第2张的做法不该拿去撞首图的禁止项（跨槽位会全部误报）"""
    entry = _user_entry(slots=["main_white", "main_ingredients"])
    entry["shot_roles"][1]["treatment"] = "画面里放一件植物道具作语境"   # 属于第2张
    assert sl.entry_conflicts(entry, ["main_white"]) == [], "第2张的做法撞了首图"
    # 首图自己的做法确实违规时，必须查得出来
    entry["shot_roles"][0]["treatment"] = "左侧放一件道具"
    assert [item["slot_id"] for item in sl.entry_conflicts(entry, ["main_white"])] == ["main_white"]


def test_text_rules_are_not_reported_as_archive_conflicts():
    """"文字/数字"是"不要往画面里画字"，档案里提到文字多在交代本地排版层的位置

    实测：用户那条真实词条因此报了 **36 处**"与契约不一致"（全是同一个误报），
    把真需要看的问题埋掉了。这类违规由 `prompt_lint` 在提示词层面精确拦截。
    """
    entry = _user_entry("st_text")
    entry["composition"] = "主体与文字之间留出明显气口"
    assert sl.entry_conflicts(entry, ["main_benefits"]) == []
    # 几何/元素类冲突照旧要报
    entry["composition"] = "左侧放一件道具"
    assert sl.entry_conflicts(entry, ["main_white"])


def test_no_slots_returns_note():
    result = sl.select_by_slot([], platform="taobao")
    assert result["slots"] == {}
    assert any("没有槽位" in note for note in result["notes"])


def test_missing_library_file_is_reported(monkeypatch, tmp_path):
    """配置被删掉时要**显式说明**，不能静默当"没有档案"处理"""
    monkeypatch.setenv("ECOMM_PROJECT_ROOT", str(tmp_path))
    result = sl.select_by_slot([{"slot_id": "main_white", "kind": "photo"}],
                               platform="taobao")
    assert any("未找到" in note for note in result["notes"])


def test_mtime_cache_picks_up_new_content(monkeypatch, tmp_path):
    """配置改了立刻生效（不是永久缓存）——A70 的教训"""
    import os
    import time
    cfg = tmp_path / "config"
    cfg.mkdir()
    path = cfg / "style_library.yaml"
    path.write_text("entries:\n  - id: a\n    name: 甲\n    background: 纯白底\n",
                    encoding="utf-8")
    monkeypatch.setenv("ECOMM_PROJECT_ROOT", str(tmp_path))
    assert [e["id"] for e in sl.load_library(include_user=False)["entries"]] == ["a"]
    time.sleep(0.01)
    path.write_text("entries:\n  - id: b\n    name: 乙\n    background: 浅灰底\n",
                    encoding="utf-8")
    os.utime(path, None)
    assert [e["id"] for e in sl.load_library(include_user=False)["entries"]] == ["b"]


# ── 渲染与产物 ──


def test_render_slot_block_lists_every_slot():
    result = sl.select_by_slot(_slots("pinduoduo"), analysis=ANALYSIS_HEALTH,
                               platform="pinduoduo")
    block = sl.render_slot_block(result, audience="gen")
    assert "风格档案" in block
    for slot_id, picked in result["slots"].items():
        if picked:
            assert slot_id in block
    assert "禁止照搬" in block


def test_render_review_audience_mentions_anchor_priority():
    result = sl.select_by_slot(_slots("taobao"), platform="taobao")
    assert "锚点优先" in sl.render_slot_block(result, audience="review")


def test_render_truncates_long_line():
    long_entry = {
        "id": "long", "name": "超长", "applies_to": {"kinds": ["photo"]},
        "background": "背" * 300, "composition": "构" * 300,
    }
    library = {"entries": [long_entry], "anchors": [], "dropped": [], "defaults": {},
               "exists": True}
    result = sl.select_by_slot([{"slot_id": "main_scene", "kind": "photo"}],
                               platform="taobao", library=library)
    line = [row for row in sl.render_slot_block(result).splitlines() if "超长" in row][0]
    assert "已截断" in line
    assert len(line) <= sl.SLOT_BLOCK_MAX_CHARS + 40


def test_render_anchor_block():
    anchors = [{"id": "a1", "name": "冷白实验室感", "taste_verdict": "冷白留白",
                "reward_points": ["留白充足"], "avoid_points": ["暖黄调"]}]
    block = sl.render_anchor_block(anchors, audience="review")
    assert "审美锚点" in block and "冷白实验室感" in block
    assert "留白充足" in block and "暖黄调" in block
    assert sl.render_anchor_block([], audience="review") == ""


def test_anchors_are_selected_by_applicability():
    library = {
        "entries": [], "dropped": [], "defaults": {}, "exists": True,
        "anchors": [
            {"id": "a_photo", "name": "照片锚点", "taste_verdict": "冷白留白",
             "applies_to": {"kinds": ["photo"]}},
            {"id": "a_health", "name": "保健品锚点", "taste_verdict": "暖白木质",
             "applies_to": {"categories": ["保健品"]}},
        ],
    }
    result = sl.select_by_slot([{"slot_id": "main_white", "kind": "photo"}],
                               analysis=ANALYSIS_3C, platform="taobao", library=library)
    names = [anchor["name"] for anchor in result["anchors"]]
    assert names == ["照片锚点"], names


def test_usage_snapshot_shape():
    result = sl.select_by_slot(_slots("taobao"), analysis=ANALYSIS_HEALTH,
                               platform="taobao")
    snapshot = sl.usage_snapshot(result)
    assert snapshot["enabled"] is True
    assert snapshot["platform"] == "taobao"
    assert snapshot["entries"] and snapshot["entries"][0]["id"]
    assert set(snapshot["slots"]) <= {slot["slot_id"] for slot in _slots("taobao")}
    assert "采用风格档案" in snapshot["message"]


def test_style_block_for_skips_anchors_when_asked():
    library = {
        "entries": [{"id": "e1", "name": "通用", "background": "纯白底",
                     "applies_to": {"kinds": ["photo"]}}],
        "anchors": [{"id": "a1", "name": "锚", "taste_verdict": "冷白"}],
        "dropped": [], "defaults": {}, "exists": True,
    }
    block = sl.style_block_for(slots=[{"slot_id": "main_scene", "kind": "photo"}],
                               platform="taobao", include_anchors=False, selection=None)
    assert block  # 走真实内置库
    picked = sl.select_by_slot([{"slot_id": "main_scene", "kind": "photo"}],
                               platform="taobao", library=library)
    assert sl.render_anchor_block(picked["anchors"]) != ""
    assert sl.render_slot_block(picked) != ""


def test_similar_entries_flags_duplicates():
    base = {"id": "x", "style_words": "纯白无缝底，主体居中，占画面 85–92%，投影短椭圆"}
    others = [dict(base, id="y"),
              {"id": "z", "style_words": "深灰渐层背景，单侧轮廓光，镜面台面反射"}]
    hits = sl.similar_entries(base, [base, *others])
    assert [item["id"] for item in hits] == ["y"]


def test_library_stats():
    stats = sl.library_stats()
    assert stats["entries"] >= 8 and stats["dropped"] == 0
    assert stats["user_store"].startswith("data/")


@pytest.mark.parametrize("platform", _all_platforms())
def test_applicable_slots_never_include_excluded(platform):
    for entry in sl.load_library()["entries"]:
        excluded = set(entry["applies_to"].get("not_slots") or [])
        assert not (set(sl.applicable_slots(entry, platform, haystack="保健品 3C数码"))
                    & excluded), f"{platform}／{entry['id']} 违反了 not_slots"


def test_slot_forbid_fragments_are_used_for_conflict_check():
    """自检：冲突检测真的在比对槽位 forbid（而不是永远返回空）"""
    rules = slot_forbid("main_white")
    assert rules, "main_white 应当有 forbid 规则"
    fake = {"name": "假设计底", "background": "灰底渐变底背景"}
    assert sl.entry_conflicts(fake, ["main_white"])
