"""用户风格词条存储（`harness/style_store.py`）—— 导入的照片 → 文字档案

用户 2026-09-18 指定的「风格词库」：空白卡片 ＋ → 弹窗导入照片 + 命名 → 「风格档案员」分析风格。

这份测试守三件事（对应三条硬约束）：
1. **照片永不进生图链路**：只落在 `data/style_library/<id>/`，**不写 `data/inputs/`**，
   也不 import 参考图/视觉载荷模块（有静态扫描）；
2. **不静默**：越界的品牌/成分文字被剔除并**如实记账**（`removed`）；必填字段被清空 → `failed` + 原因；
3. **不会卡死**：`analyzing` 超时被惰性收割为 `failed`（进程重启/任务丢失后不永远转圈）。
"""

import base64
import json

import pytest

from src.harness import style_library as sl
from src.harness.style_store import (
    MAX_PHOTOS,
    StyleStore,
    entry_to_summary,
)

JPEG = base64.b64decode(
    b"/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0a"
    b"HBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAABAAAAAAAA"
    b"AAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AKp//2Q==")


@pytest.fixture
def store(tmp_path):
    return StyleStore(storage_dir=str(tmp_path / "style_library"))


@pytest.fixture
def shared_store():
    """指向**测试专用的 data 目录**（与检索链路读到的是同一个），用完清理

    不清理的话，用户词条会留在共享的 `ECOMM_DATA_DIR` 里，污染同一轮其它测试
    （实测：后面的"内置档案内容"用例会把这些词条也算进去）。
    """
    from src.core.config import data_root

    store = StyleStore(storage_dir=str(data_root() / "style_library"))
    before = {item["id"] for item in store.list_entries(None)}
    yield store
    for item in store.list_entries(None):
        if item["id"] not in before:
            try:
                store.delete_entry(item["id"])
            except ValueError:
                pass


def _create(store, name="冷白实验室感", photos=None, tenant="default", **kwargs):
    return store.create_entry(tenant_id=tenant, name=name,
                              photos=photos if photos is not None else [JPEG], **kwargs)


def _ready(store, name, *, tenant="default"):
    """建一条并"分析成功"（走真实落库路径）"""
    entry = _create(store, name=name, tenant=tenant)
    store.apply_analysis(entry["id"], {"background": "冷白渐层底", "composition": "居中占 60%",
                                       "lighting": "顶部柔光", "materials": "哑光纸纹"})
    return store.get_entry(entry["id"])


# ── 建/读/租户隔离 ──


def test_create_writes_entry_and_photos(store):
    entry = _create(store)
    assert entry["status"] == "analyzing"
    assert entry["source"] == "用户导入"
    assert entry["id"].startswith("st_")
    directory = store.photo_dir(entry["id"])
    assert (directory / "photo_1.jpg").exists()
    assert store.get_entry(entry["id"])["name"] == "冷白实验室感"


def test_photos_never_reach_session_inputs(store, tmp_path):
    """**本模块存在的意义**：导入的照片不能被当成生图参考图"""
    _create(store)
    data_root = tmp_path.parent / "data"
    assert not (data_root / "inputs").exists(), "照片被写进了会话上传目录"


def test_name_and_photos_required(store):
    with pytest.raises(ValueError, match="起个名字"):
        store.create_entry(tenant_id="default", name="  ", photos=[JPEG])
    with pytest.raises(ValueError, match="至少导入 1 张"):
        store.create_entry(tenant_id="default", name="x", photos=[])


def test_photo_count_and_size_limits(store):
    with pytest.raises(ValueError, match="最多"):
        store.create_entry(tenant_id="default", name="x",
                           photos=[JPEG] * (MAX_PHOTOS + 1))
    big = b"\xff\xd8\xff" + b"0" * (21 * 1024 * 1024)
    with pytest.raises(ValueError, match="MB"):
        store.create_entry(tenant_id="default", name="x", photos=[big])


def test_tenant_isolation(store):
    _create(store, tenant="t1")
    _create(store, tenant="t2")
    assert len(store.list_entries("t1")) == 1
    assert len(store.list_entries("t2")) == 1
    assert len(store.list_entries(None)) == 2


def test_per_tenant_cap(store, monkeypatch):
    import src.harness.style_store as store_mod

    monkeypatch.setattr(store_mod, "MAX_ENTRIES_PER_TENANT", 2)
    _create(store)
    _create(store)
    with pytest.raises(ValueError, match="最多 2 条"):
        _create(store)


def test_read_cache_refreshes_after_write(store):
    first = _create(store, name="甲")
    assert [item["id"] for item in store.list_entries("default")] == [first["id"]]
    second = _create(store, name="乙")
    ids = {item["id"] for item in store.list_entries("default")}
    assert ids == {first["id"], second["id"]}


# ── 更新 / 删除 ──


def test_update_fields_and_tenant_guard(store):
    entry = _create(store)
    updated = store.update_entry(entry["id"], {"name": "新名字", "background": "纯白底"},
                                 tenant_id="default")
    assert updated["name"] == "新名字"
    assert updated["background"] == "纯白底"
    with pytest.raises(ValueError, match="不存在"):
        store.update_entry(entry["id"], {"name": "偷改"}, tenant_id="other")


def test_builtin_style_entries_only_toggle(store):
    """内置档案只允许启停（内容由配置说了算）"""
    entry = _create(store)
    kept = store.update_entry(entry["id"], {"enabled": False}, allow_all=False)
    assert kept["enabled"] is False
    with pytest.raises(ValueError, match="只能启停"):
        store.update_entry(entry["id"], {"background": "改内容"}, allow_all=False)


def test_delete_removes_entry_and_photos(store):
    entry = _create(store)
    directory = store.photo_dir(entry["id"])
    assert directory.exists()
    store.delete_entry(entry["id"], tenant_id="default")
    assert store.get_entry(entry["id"]) is None
    assert not directory.exists()
    with pytest.raises(ValueError, match="不存在"):
        store.delete_entry(entry["id"])


# ── 分析结果落库（清洗 + 如实记账）──


def test_apply_analysis_sanitizes_and_records(store):
    entry = _create(store)
    payload = {
        "background": "冷白无缝底",
        "composition": "主体居中偏左占画面 60%",
        "lighting": "顶部柔光 + 双侧补光，投影极淡",
        "materials": "哑光纸纹可见",
        "summary": "干净正规",
        "style_words": "冷白留白，明度偏高",
        "taste_verdict": "冷静、留白充足",
        "reward_points": ["留白充足", "含 12 种成分"],       # 第二条应被剔除
        "forbid": ["撞色", "通过 GMP 认证"],                  # 第二条应被剔除
        "name_suggestions": ["冷白留白感"],
        "removed_brand_text": ["SOMEBRAND"],
    }
    result = store.apply_analysis(entry["id"], payload, usage={"calls": 1, "images": 1})
    assert result["status"] == "ready"
    assert result["reward_points"] == ["留白充足"]
    assert result["forbid"] == ["撞色"]
    assert any("成分" in item for item in result["removed"])
    assert any("认证" in item for item in result["removed"])


def test_apply_analysis_without_design_points_fails_loudly(store):
    entry = _create(store)
    result = store.apply_analysis(entry["id"], {"background": "", "composition": "",
                                                "lighting": "", "materials": "",
                                                "style_words": "看不出什么"})
    assert result["status"] == "failed"
    assert "设计要点" in result["error"]


def test_apply_analysis_drops_trademark_in_name(store):
    entry = _create(store)
    result = store.apply_analysis(entry["id"], {"background": "纯白底", "name": "某牌®风格"})
    assert result["status"] == "ready"
    assert any("商标" in item for item in result["removed"])


# ── 合并进检索（与内置同一套校验与渲染路径）──


def test_analysis_keeps_user_chosen_name(store):
    """分析结果里没有 name（名字是用户在弹窗里起的）——**不能**被空串覆盖

    实测踩到：`sanitize_entry` 会给缺席的 name 补一个空串，于是每次分析都把用户起的名字清空，
    词条随后因为"缺少 name"被检索丢弃（用户会看到"我明明起了名字却没用上"）。
    """
    entry = _create(store, name="冷白实验室感")
    result = store.apply_analysis(entry["id"], {"background": "纯白底", "composition": "居中"})
    assert result["name"] == "冷白实验室感"
    assert result["status"] == "ready"


def test_analysis_may_replace_name_when_provided(store):
    entry = _create(store, name="临时名")
    result = store.apply_analysis(entry["id"], {"background": "纯白底", "name": "分析给的名字"})
    assert result["name"] == "分析给的名字"


# ── 启用/停用：同租户同一时刻只有一套生效（用户 2026-09-20："一轮会话只用一套风格词"）──


def test_analysis_success_switches_the_active_style(shared_store):
    """新词条分析成功 → 它成为生效风格，旧的自动停用（并**如实记账**）"""
    first = _ready(shared_store, "旧风格")
    second = _create(shared_store, name="新风格")
    result = shared_store.apply_analysis(second["id"], {"background": "浅粉渐层",
                                                        "composition": "居中偏右"})
    assert result["status"] == "ready"
    auto = (result.get("analysis") or {}).get("auto_disabled") or []
    assert [item["name"] for item in auto] == ["旧风格"]
    assert shared_store.get_entry(first["id"])["enabled"] is False
    assert shared_store.get_entry(second["id"])["enabled"] is True


def test_enabling_one_style_disables_the_others(shared_store):
    first = _ready(shared_store, "风格A")
    _ready(shared_store, "风格B")             # B 分析成功时已把 A 停用
    switched = shared_store.set_entry_enabled(first["id"], True)
    assert [item["name"] for item in switched["auto_disabled"]] == ["风格B"]
    assert switched["entry"]["enabled"] is True
    assert shared_store.get_entry(first["id"])["enabled"] is True


def test_failed_analysis_keeps_previous_style_active(shared_store):
    """分析失败**不制造空档**：旧的风格照常生效（用户仍能出图）"""
    first = _ready(shared_store, "旧风格")
    second = _create(shared_store, name="新风格")
    shared_store.set_status(second["id"], "failed", error="上游 400")
    assert shared_store.get_entry(first["id"])["enabled"] is True
    assert shared_store.get_entry(second["id"])["status"] == "failed"


def test_disabled_entries_do_not_count_as_active(shared_store):
    """停用的词条不参与检索（`library_items` 只交 ready + enabled 的）"""
    first = _ready(shared_store, "风格A")
    shared_store.set_entry_enabled(first["id"], False)
    entries, _anchors, dropped = shared_store.library_items("default")
    assert entries == []
    assert any(item["id"] == first["id"] for item in dropped)


def test_user_entry_outranks_generic_builtin(shared_store):
    """用户自己导入的风格不能被内置通用档案挤掉（max_entries 默认只有 2）"""
    entry = _create(shared_store, name="我的风格")
    shared_store.apply_analysis(entry["id"], {"background": "冷白渐层底", "composition": "居中偏左占 60%",
                                              "lighting": "顶部柔光", "materials": "哑光纸纹",
                                              "applies_to": {"kinds": ["photo"]}})
    result = sl.select_by_slot([{"slot_id": "main_scene", "kind": "photo"}],
                               platform="taobao")
    picked = [item["id"] for item in result["slots"]["main_scene"]]
    assert picked[0] == entry["id"], picked


def test_library_items_only_ready_and_enabled(store):
    ready = _create(store, name="已就绪")
    store.apply_analysis(ready["id"], {"background": "纯白底", "composition": "居中占 60%",
                                       "taste_verdict": "留白充足、投影极淡"})
    pending = _create(store, name="还在分析")
    entries, anchors, dropped = store.library_items("default")
    assert [item["id"] for item in entries] == [ready["id"]]
    assert anchors and anchors[0]["name"] == "已就绪"
    assert any(pending["id"] == item["id"] for item in dropped)


def test_no_verdict_means_not_an_anchor(store):
    """锚点是"打分准绳"：没有判词就不要硬造一条标准出来（宁可不当锚点）"""
    entry = _create(store, name="只有设计要点")
    store.apply_analysis(entry["id"], {"background": "纯白底", "composition": "居中占 60%",
                                       "lighting": "顶部柔光", "materials": "哑光纸纹"})
    entries, anchors, _dropped = store.library_items("default")
    assert [item["id"] for item in entries] == [entry["id"]]
    assert anchors == []


def test_user_entry_flows_into_selection(shared_store):
    """用户词条要真的能被检索到（不是只存在磁盘上）"""
    entry = _create(shared_store, name="冷白实验室感")
    shared_store.apply_analysis(entry["id"], {
        "background": "冷白到极浅灰的竖向渐层", "composition": "主体居中偏左占 60%，右侧留白 30%",
        "lighting": "顶部柔光 + 双侧补光", "materials": "哑光纸纹",
        "applies_to": {"kinds": ["photo"], "not_slots": ["main_white"]},
    })
    lib = sl.load_library("default")
    assert any(item["id"] == entry["id"] for item in lib["entries"])
    result = sl.select_by_slot([{"slot_id": "main_scene", "kind": "photo"}],
                               platform="taobao", library=lib)
    picked = [item["id"] for item in result["slots"]["main_scene"]]
    assert entry["id"] in picked, picked
    # 其它租户看不到
    other = sl.load_library("someone-else")
    assert not any(item["id"] == entry["id"] for item in other["entries"])


def test_anchors_come_from_user_entries(store):
    entry = _create(store, name="我的锚点", as_anchor=True)
    store.apply_analysis(entry["id"], {"background": "纯白底", "taste_verdict": "留白充足、投影极淡"})
    _entries, anchors, _dropped = store.library_items("default")
    assert anchors[0]["taste_verdict"] == "留白充足、投影极淡"
    store.update_entry(entry["id"], {"as_anchor": False})
    _entries, anchors, _dropped = store.library_items("default")
    assert anchors == []


# ── 收割 / 计数 / 摘要 ──


def test_interrupted_analysis_is_reaped(store):
    entry = _create(store)
    items = store._all_entries()
    items[0]["analyzing_at"] = "2020-01-01T00:00:00+00:00"
    store._save_entries(items)
    listed = store.list_entries("default")
    assert listed[0]["status"] == "failed"
    assert "分析中断" in listed[0]["error"]


def test_analyzing_timeout_scales_with_photo_count():
    """分批 = 多次调用 → 收割阈值要放宽，否则 20 张会被误判"分析中断"

    （误判的后果很怪：界面先显示失败，随后 `apply_analysis` 又把状态翻成 ready。）
    """
    from src.harness.style_store import (
        ANALYZING_TIMEOUT_S,
        VISION_BATCH_SIZE,
        analyzing_timeout_s,
    )

    assert analyzing_timeout_s(1) == ANALYZING_TIMEOUT_S
    assert analyzing_timeout_s(VISION_BATCH_SIZE) == ANALYZING_TIMEOUT_S
    assert analyzing_timeout_s(VISION_BATCH_SIZE + 1) > ANALYZING_TIMEOUT_S
    assert analyzing_timeout_s(20) > analyzing_timeout_s(VISION_BATCH_SIZE)
    assert analyzing_timeout_s(0) == ANALYZING_TIMEOUT_S


# ── 照片增删（一整套图片要能补齐/去掉几张）──


def test_add_photos_appends_and_keeps_roles(store):
    entry = _create(store, photos=[JPEG, JPEG])
    store.apply_analysis(entry["id"], {"background": "白底", "composition": "居中",
                                       "shot_flow": "先白底",
                                       "shot_roles": [{"number": 1, "slot": "main_white",
                                                       "treatment": "纯白无缝"}]})
    updated = store.add_photos(entry["id"], [JPEG])
    assert len(updated["photos"]) == 3
    assert [item["file"] for item in updated["photos"]] == \
        ["photo_1.jpg", "photo_2.jpg", "photo_3.jpg"]
    # 序号不变 → 已识别出的逐张角色仍然对得上
    assert updated["shot_roles"][0]["slot"] == "main_white"
    assert store.read_photos(updated) == [JPEG, JPEG, JPEG]


def test_add_photos_enforces_cap(store):
    entry = _create(store)
    with pytest.raises(ValueError, match="最多"):
        store.add_photos(entry["id"], [JPEG] * MAX_PHOTOS)


def test_remove_photo_renumbers_clears_roles_and_files(store):
    entry = _create(store, photos=[JPEG, JPEG, JPEG])
    store.apply_analysis(entry["id"], {"background": "白底", "composition": "居中",
                                       "shot_flow": "先白底；再讲成分配方图",
                                       "shot_roles": [{"number": 1, "slot": "main_white",
                                                       "treatment": "纯白无缝"},
                                                      {"number": 2, "slot": "main_ingredients",
                                                       "treatment": "留白在上"}]})
    result = store.remove_photo(entry["id"], 2)
    assert result["cleared_roles"] is True, "序号变了必须清空逐张角色"
    updated = result["entry"]
    assert [item["file"] for item in updated["photos"]] == ["photo_1.jpg", "photo_2.jpg"]
    assert updated["shot_roles"] == []
    assert updated["background"], "共同美术要保留"
    assert store.read_photos(updated) == [JPEG, JPEG]
    # 目录里不能残留第 3 张（否则"重新分析"会读到已删的照片）
    files = sorted(path.name for path in store.photo_dir(entry["id"]).glob("photo_*.jpg"))
    assert files == ["photo_1.jpg", "photo_2.jpg"]


def test_remove_photo_keeps_at_least_one_and_validates_index(store):
    entry = _create(store)
    with pytest.raises(ValueError, match="至少保留"):
        store.remove_photo(entry["id"], 1)
    two = _create(store, photos=[JPEG, JPEG])
    with pytest.raises(ValueError, match="超出范围"):
        store.remove_photo(two["id"], 5)
    with pytest.raises(ValueError, match="整数"):
        store.remove_photo(two["id"], "x")


def test_adopt_is_best_effort(store, monkeypatch):
    entry = _create(store)
    store.adopt([entry["id"]])
    store.adopt([entry["id"]], count=2)
    assert store.adopted_counts()[entry["id"]] == 3
    # 异常不能影响出图（这里让写文件炸掉）
    import src.harness.style_store as store_mod

    monkeypatch.setattr(store_mod, "_write_json_atomic",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    store.adopt([entry["id"]])          # 不抛


def test_entry_summary_shape(store):
    entry = _create(store)
    store.apply_analysis(entry["id"], {"background": "纯白底", "composition": "居中"},
                         usage={"calls": 1, "images": 1, "elapsed_ms": 1200, "model": "m"})
    summary = entry_to_summary(store.get_entry(entry["id"]), store=store)
    assert summary["source"] == "user"
    assert summary["status"] == "ready"
    assert summary["usage"]["calls"] == 1
    assert summary["usage"]["images"] == 1
    assert summary["cover"].startswith("data:image/jpeg;base64,") or summary["cover"] == ""


def test_photo_data_uri_and_bytes(store):
    entry = _create(store)
    assert store.thumb_data_uri(entry).startswith("data:image/")
    assert store.read_photos(store.get_entry(entry["id"])) == [JPEG]


def test_corrupted_entries_file_is_kept_as_evidence(store):
    store.entries_path.parent.mkdir(parents=True, exist_ok=True)
    store.entries_path.write_text("{not json", encoding="utf-8")
    assert store.list_entries("default") == []
    assert store.entries_path.with_suffix(".json.broken").exists()


def test_no_image_pipeline_imports():
    """静态扫描：本模块**不得 import** 参考图/视觉载荷链路（照片不进生图）

    只扫 import 语句 —— 文档里明确写"不引用 xxx"是允许的（甚至是必须的）。
    """
    import re
    from pathlib import Path

    source = Path("src/harness/style_store.py").read_text(encoding="utf-8")
    imports = "\n".join(line for line in source.splitlines()
                        if re.match(r"\s*(from|import)\s", line))
    for forbidden in ("reference_images", "vision_payload", "collect_references"):
        assert forbidden not in imports, f"style_store 不得 import {forbidden}"


def test_entries_json_is_valid_after_writes(store):
    entry = _create(store)
    store.update_entry(entry["id"], {"name": "改名"})
    payload = json.loads(store.entries_path.read_text(encoding="utf-8"))
    assert isinstance(payload, list) and payload[0]["name"] == "改名"
    assert not list(store.entries_path.parent.glob("*.tmp")), "临时文件应被原子替换清掉"
