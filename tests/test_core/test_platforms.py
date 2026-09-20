"""平台档案（config/platforms.yaml）—— 单一事实来源

背景（用户提出"我还要做拼多多，风格也要写上"时暴露）：平台清单此前散落 9 处
（prompt_gen.yaml 的静态风格清单只有 4 个平台、config/agents/prompt_generator.yaml 用的是
**中文名**、6 个 workflow 模板各存一份 options、前端 Sessions.jsx 硬编码 6 项）——
结果是"在界面上选一个平台，提示词里只有一个裸字符串，风格全靠模型猜"。

这里钉住：档案可加载、别名归一（中文名/slug/大小写）、未登记平台必须显式报"未登记"
而不是默不作声地让模型编，以及拼多多的平台硬规则（白底、图片上不添加文字）确实进了风格块。
"""

import pytest

from src.core.config import load_yaml
from src.core.platforms import (
    PLATFORMS_REL,
    background_policy,
    get_platform,
    list_platforms,
    load_platforms,
    platform_slot_table,
    platform_slots,
    platform_style_block,
    resolve_platform,
    slot_design,
    slot_forbid,
    slot_intent,
    slot_keep_clear,
    slot_kind,
    slot_must,
)


def test_pinduoduo_and_others_are_registered():
    profiles = load_platforms()
    assert "pinduoduo" in profiles, "拼多多（用户明确要求）必须在平台档案里"
    assert profiles["pinduoduo"]["label"] == "拼多多"
    for slug in ("taobao", "jd", "amazon", "xiaohongshu", "douyin"):
        assert slug in profiles, f"{slug} 应在平台档案里"


def test_every_profile_has_required_keys():
    required = {"label", "aspect", "bg", "text_policy", "style", "slots"}
    for slug, profile in load_platforms().items():
        missing = required - set(profile)
        assert not missing, f"{slug} 缺少字段 {missing}"
        assert str(profile["style"]).strip(), f"{slug} 的风格描述不能为空"
        assert profile["slots"], f"{slug} 至少要有一个 slot"


@pytest.mark.parametrize("raw,expected", [
    ("拼多多", "pinduoduo"),
    ("pdd", "pinduoduo"),
    ("PDD", "pinduoduo"),
    ("  Pinduoduo ", "pinduoduo"),
    ("淘宝", "taobao"),
    ("天猫", "tmall"),
    ("Amazon", "amazon"),
    ("小红书", "xiaohongshu"),
    ("taobao", "taobao"),
])
def test_alias_normalization(raw, expected):
    assert resolve_platform(raw) == expected


def test_unknown_platform_is_flagged_not_guessed():
    assert resolve_platform("temu") == ""
    profile = get_platform("temu")
    assert profile["registered"] is False
    # 未登记平台：风格块必须显式提示"未登记"，不得留空让模型自由发挥
    block = platform_style_block("temu")
    assert "未登记" in block
    assert "temu" in block


def test_get_platform_falls_back_to_default_for_blank():
    profile = get_platform("")
    assert profile["registered"] is True
    assert profile["slug"] == "taobao"


def test_pinduoduo_style_carries_platform_rules():
    block = platform_style_block("拼多多")
    assert "拼多多" in block
    # 平台硬规则：白底主图/活动主图不得添加文字（实测规范），必须出现在风格块里
    assert "纯白" in block or "FFFFFF" in block
    assert "文字" in block
    # 用户 2026-09-18 更正：不要"写实/低质感"那套，要**平面设计导向**的高级感
    assert "高明度大主体" in block
    assert "设计" in block
    # 主图风格与信息图版式风格**分开**（改前"不要大面积留白"会压掉信息图版式需求）
    assert "主图风格" in block and "版式风格" in block
    # 编号槽位表：主图 + 详情图都要在规范里（改前详情槽位从未进模型视野，4 张一张没出）
    assert "第1张" in block and "详情图" in block


def test_background_policy_is_declared_per_platform():
    """用户质疑"很多商品图都不是白底的啊？"→ 背景按平台声明三档，不再谎称全局硬规则"""
    assert background_policy("amazon") == "white_required"
    assert background_policy("jd") == "white_preferred"
    assert background_policy("pinduoduo") == "design_allowed"
    assert background_policy("taobao") == "design_allowed"
    # 未登记/空 → 顶层默认
    assert background_policy("") in ("design_allowed", "white_preferred", "white_required")
    block = platform_style_block("pinduoduo")
    assert "设计底" in block and "不要求写实背景" in block


def test_slot_contract_is_complete():
    """每个槽位都要有 intent/design/must/forbid（用户："未有明确约束每一张该有的提示词"）"""
    for slot_id in load_yaml(PLATFORMS_REL).get("slot_catalog", {}):
        assert slot_intent(slot_id), f"{slot_id} 缺少 intent"
        assert slot_design(slot_id), f"{slot_id} 缺少 design"
        assert slot_must(slot_id), f"{slot_id} 缺少 must"
        assert slot_forbid(slot_id), f"{slot_id} 缺少 forbid"
        if slot_kind(slot_id) == "info":
            assert slot_keep_clear(slot_id), f"{slot_id} 缺少 keep_clear"


def test_slot_table_numbers_main_then_detail():
    rows = platform_slot_table("pinduoduo")
    assert [row["number"] for row in rows] == list(range(1, len(rows) + 1))
    assert [row["slot_id"] for row in rows[:6]] == platform_slots("pinduoduo")
    assert all(row["usage"] == "detail" for row in rows[6:])
    assert rows[0]["intent"] and rows[0]["must"]


def test_pinduoduo_slots_start_with_white_main_image():
    slots = platform_slots("pinduoduo")
    assert slots and slots[0] == "main_white"


def test_platform_slots_are_sane_for_limits():
    """slot 数量不得超过平台主图上限（超了就是生成了一堆传不上去的图）"""
    for slug, profile in load_platforms().items():
        limit = profile.get("max_images")
        if not limit:
            continue
        assert len(profile["slots"]) <= limit, f"{slug} 的 slot 数超过平台主图上限 {limit}"


def test_list_platforms_payload_for_api():
    items = list_platforms()
    assert isinstance(items, list) and items
    first = items[0]
    for key in ("slug", "label", "aspect", "slot_count", "text_policy"):
        assert key in first
    labels = {item["slug"]: item["label"] for item in items}
    assert labels["pinduoduo"] == "拼多多"


def test_registry_untouched_by_reader(tmp_path, monkeypatch):
    """读取不得改动配置文件（防止"读着读着把注释写没了"）"""
    from src.core.config import _project_root
    path = _project_root() / "config" / "platforms.yaml"
    before = path.read_bytes()
    load_platforms()
    platform_style_block("pinduoduo")
    list_platforms()
    assert path.read_bytes() == before
