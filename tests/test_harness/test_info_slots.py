"""信息图文案 + 本地排版 —— 让套图里真正出现"能用的信息图"

用户原话："为什么生成的全是白背景＋商品的图，我记得我要求一次性生成的要一套可以实际使用的图片，
例如『纯商品图+成分图+商品面向人群图片+商品效果列举图』这种一套图片"。

根因：① 槽位词表只有"白底/卖点/场景/细节/规格"，没有成分/人群/功效角色；
② 为了防臆造品牌文字，把"模型不画字"推到极端 → 卖点图/规格图变成"白底商品照 + 一块空白"。

现在：信息类槽位（卖点/成分/人群/功效/规格/用法/资质）= **模型出无字底图 + 本地排版文字层**，
文案只取已确认事实，缺依据就 `blocked` 并说明要补什么素材。
"""


import pytest

from src.core.platforms import (
    slot_copy_sources,
    slot_kind,
    slot_layout,
    slot_usage,
)
from src.harness.image_compose import available_fonts, compose_info_image, render_info_image
from src.harness.set_plan import normalize_set_plan
from src.harness.slot_copy import build_slot_copy, find_forbidden_claims

IDENTITY = {
    "status": "confirmed", "source": "vision", "brand": "DEFOEBUENA®",
    "product_name": "金裝強力肝迅康", "spec": "60's",
    "certifications": ["德國GMP優質產品"], "confidence": 0.93,
    "visible_text": {"lines": [{"text": "金裝強力肝迅康", "legible": True},
                               {"text": "60's", "legible": True}]},
}
ANALYSIS = {
    "category": "保健品",
    "ingredients": ["本次圖片僅見正面，未見成分表 → 不可見/待確認（嚴禁臆造奶薊草、水飛薊）"],
    "features": ["正面以肝臟解剖圖為核心視覺", "標示「升級版」", "標示規格 60's"],
    "target_audience": {"age": "25–55 歲", "gender": "男女皆可", "lifestyle": "熬夜加班、應酬飲酒",
                        "concerns": "肝臟負擔、酒後不適"},
    "visible_text": IDENTITY["visible_text"],
    "marketing_angles": {"selling_points": ["德國來源標示", "德國GMP優質產品標示", "升級版配方迭代"],
                         "scene_suggestions": ["辦公桌", "居家餐桌"]},
}


class TestSlotVocabulary:
    def test_info_and_photo_slots_are_distinguished(self):
        assert slot_kind("main_white") == "photo"
        assert slot_kind("main_scene") == "photo"
        for slot in ("main_selling_point", "main_ingredients", "main_audience",
                     "main_benefits", "main_spec", "main_usage", "main_cert"):
            assert slot_kind(slot) == "info", f"{slot} 应该是信息图（需要文案层）"

    def test_usage_splits_main_and_detail(self):
        assert slot_usage("main_white") == "main"
        assert slot_usage("main_benefits") == "main"
        assert slot_usage("main_spec") == "detail"
        assert slot_usage("main_ingredients") == "main"

    def test_layouts_declared_for_info_slots(self):
        assert slot_layout("main_benefits") == "benefit_grid"
        assert slot_layout("main_spec") == "spec_table"
        assert slot_layout("main_white") == ""

    def test_copy_sources(self):
        assert slot_copy_sources("main_ingredients") == ["ingredients"]
        assert "selling_points" in slot_copy_sources("main_selling_point")


class TestSetPlanLimits:
    def _plan(self, slots):
        return normalize_set_plan({"set_plan": {"platform": "taobao", "slots": slots}},
                                  platform="taobao")

    def test_main_limit_and_detail_limit_are_separate(self):
        """淘宝主图上限 5 张，但成分/人群/功效等**信息图**放在主图位、规格/用法放详情位"""
        slots = [{"slot_id": "main_white", "prompt": "a"},
                 {"slot_id": "main_selling_point", "prompt": "b"},
                 {"slot_id": "main_benefits", "prompt": "c"},
                 {"slot_id": "main_ingredients", "prompt": "d"},
                 {"slot_id": "main_audience", "prompt": "e"},
                 {"slot_id": "main_spec", "prompt": "f"},       # detail → 不受主图 5 张限制
                 {"slot_id": "main_usage", "prompt": "g"},
                 {"slot_id": "main_cert", "prompt": "h"}]
        plan = self._plan(slots)
        ids = [slot["slot_id"] for slot in plan["slots"]]
        assert ids == [s["slot_id"] for s in slots], "详情图不该被主图上限截断"
        assert all(slot["kind"] in ("photo", "info") for slot in plan["slots"])

    def test_main_limit_still_enforced(self):
        slots = [{"slot_id": f"main_{i}", "prompt": "p"} for i in range(8)]
        plan = normalize_set_plan(
            {"set_plan": {"slots": [{"slot_id": s, "prompt": "x"} for s in
                                    ["main_white", "main_scene", "main_detail", "note_cover",
                                     "note_scene", "note_detail", "note_cover2"]]}},
            platform="taobao")
        assert len(plan["slots"]) <= 5, "主图额度仍然要被遵守"
        _ = slots


class TestSlotCopy:
    def test_selling_point_copy_from_confirmed_facts(self):
        copy = build_slot_copy("main_selling_point", identity=IDENTITY, analysis=ANALYSIS)
        assert copy["blocked"] is False
        assert copy["items"] and any("德國" in item for item in copy["items"])
        assert copy["title"]
        assert copy["footer"] and "60's" in copy["footer"]

    def test_ingredients_blocked_when_panel_not_visible(self):
        """本商品正面看不到成分表 → 成分图必须 blocked，绝不编造成分"""
        copy = build_slot_copy("main_ingredients", identity=IDENTITY, analysis=ANALYSIS)
        assert copy["blocked"] is True
        assert copy["items"] == []
        assert "背面" in copy["reason"] or "成分表" in copy["reason"]

    def test_ingredients_pass_when_facts_exist(self):
        analysis = {**ANALYSIS, "ingredients": ["水飛薊提取物", "姜黃素"]}
        copy = build_slot_copy("main_ingredients", identity=IDENTITY, analysis=analysis)
        assert copy["blocked"] is False
        assert copy["items"] == ["水飛薊提取物", "姜黃素"]

    def test_unconfirmed_marker_filtered_out(self):
        analysis = {**ANALYSIS, "features": ["不可見/待確認的成分表", "標示「升級版」"]}
        copy = build_slot_copy("main_benefits", identity=IDENTITY, analysis=analysis)
        assert all("待確認" not in item for item in copy["items"])

    def test_audience_copy_from_target_audience(self):
        copy = build_slot_copy("main_audience", identity=IDENTITY, analysis=ANALYSIS)
        assert copy["blocked"] is False
        joined = " ".join(copy["items"])
        assert "25–55" in joined and ("熬夜" in joined or "應酬" in joined)

    def test_forbidden_claims_block_the_image(self):
        analysis = {**ANALYSIS, "features": ["治療肝病，無副作用"]}
        copy = build_slot_copy("main_benefits", identity=IDENTITY, analysis=analysis)
        assert copy["blocked"] is True
        assert "违禁" in copy["reason"]

    def test_find_forbidden_claims(self):
        assert find_forbidden_claims(["本品可治疗肝病"]) == ["治疗"]
        assert find_forbidden_claims(["德國GMP優質產品"]) == []

    def test_spec_copy_uses_identity_spec(self):
        copy = build_slot_copy("main_spec", identity=IDENTITY, analysis=ANALYSIS)
        assert copy["blocked"] is False
        assert any("60's" in item for item in copy["items"])

    def test_compare_needs_user_input(self):
        copy = build_slot_copy("main_compare", identity=IDENTITY, analysis=ANALYSIS)
        assert copy["blocked"] is True
        assert "对比" in copy["reason"]

    def test_usage_hint_when_no_data(self):
        copy = build_slot_copy("main_usage", identity=IDENTITY, analysis=ANALYSIS)
        assert copy["blocked"] is True
        assert "用法" in copy["reason"] or "食用" in copy["reason"]

    def test_usage_slot_draws_on_analyst_usage_field(self):
        """分析员给了 `usage` → 使用方法图不该被拦住

        取证（会话 `bb6cc0fa56a54921`）：`slot_copy` 读 `analysis["usage"]`
        （`slot_copy.py:119`），但 `config/prompts/analyst.yaml` 的 JSON schema 里
        **没有这个字段**（只有 `dosage_form` 剂型）→ 模型永远不会产出它，
        `main_usage` 必然 blocked（10 张只交出 8 张），而那信息就印在盒子侧面。
        """
        analysis = {**ANALYSIS, "usage": ["每日 2 至 3 次，每次 1 至 2 粒"]}
        copy = build_slot_copy("main_usage", identity=IDENTITY, analysis=analysis)
        assert copy["blocked"] is False
        assert any("每日 2 至 3 次" in item for item in copy["items"])

    def test_usage_marker_lines_are_picked_up_from_visible_text(self):
        """侧面/背面的可见文字里带"每日/每次"时也要能排版（不依赖模型另开字段）

        口径与真实产物一致：`visible_text` 挂在**分析结果**上
        （`slot_copy._visible_text_lines(analysis)`），不是身份卡上。
        """
        analysis = {**ANALYSIS, "visible_text": {"lines": [
            {"text": "金裝強力肝迅康", "legible": True},
            {"text": "食用方法：每日 2 次，每次 1 粒", "legible": True},
        ]}}
        copy = build_slot_copy("main_usage", identity=IDENTITY, analysis=analysis)
        assert copy["blocked"] is False
        assert any("每次 1 粒" in item for item in copy["items"])

    def test_garbage_inputs_do_not_crash(self):
        for identity, analysis in ((None, None), ({}, {}), ("文字", [])):
            copy = build_slot_copy("main_selling_point", identity=identity, analysis=analysis)
            assert copy["blocked"] in (True, False)
            assert isinstance(copy["items"], list)


@pytest.mark.skipif(not available_fonts(), reason="系统无中文字体")
class TestLocalCompose:
    def _base(self, size=(600, 600)):
        import io

        from PIL import Image
        image = Image.new("RGB", size, (250, 250, 252))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    def test_renders_text_and_returns_meta(self):
        copy = build_slot_copy("main_selling_point", identity=IDENTITY, analysis=ANALYSIS)
        data, meta = render_info_image(self._base(), copy, layout="top_title_bullets",
                                      size=(800, 800))
        assert meta["ok"] is True
        assert meta["items"] >= 2
        assert meta["font"]
        import io

        from PIL import Image
        image = Image.open(io.BytesIO(data))
        assert image.size == (800, 800)

    def test_blocked_copy_is_not_drawn(self):
        copy = build_slot_copy("main_ingredients", identity=IDENTITY, analysis=ANALYSIS)
        assert copy["blocked"] is True
        base = self._base()
        data, meta = render_info_image(base, copy, layout="ingredient_list")
        assert meta["ok"] is False
        assert meta.get("blocked") is True
        assert data == base, "blocked 时不应产出任何带字图片"

    @pytest.mark.parametrize("layout,slot", [
        ("top_title_bullets", "main_selling_point"),
        ("benefit_grid", "main_benefits"),
        ("audience_panel", "main_audience"),
        ("spec_table", "main_spec"),
        ("cert_badge", "main_cert"),
        ("ingredient_list", "main_ingredients"),
        ("step_list", "main_usage"),
        ("compare_two_column", "main_compare"),
    ])
    def test_each_layout_draws_something(self, layout, slot):
        """**全部 8 套版式**都要能出图（商品/文案用带依据的素材，避免被 blocked 挡掉）"""
        analysis = {**ANALYSIS, "ingredients": ["水飛薊提取物", "姜黃素"]}
        copy = build_slot_copy(slot, identity=IDENTITY, analysis=analysis,
                               user_copy={"usage": ["每日 2 粒，飯後服用"],
                                          "compare": ["對比：普通款 vs 升級版"]})
        assert copy["blocked"] is False, f"{slot} 不该被拦下：{copy['reason']}"
        data, meta = render_info_image(self._base(), copy, layout=layout, size=(600, 600))
        assert meta["ok"] is True, meta
        assert meta["items"] >= 1, f"{layout} 没有画出条目：{meta}"
        assert len(data) > 1000

    def test_all_declared_layouts_are_implemented(self):
        """槽位目录里声明的版式必须都实现（防止写了个名字却画不出来）"""
        from src.core.platforms import load_platforms
        from src.harness.image_compose import layout_options
        declared = set()
        catalog = load_platforms() and None  # noqa: F841 - 触发一次读取，确认配置可解析
        from src.core.platforms import _slot_catalog
        for entry in _slot_catalog().values():
            if entry.get("layout"):
                declared.add(str(entry["layout"]))
        missing = declared - set(layout_options())
        assert not missing, f"这些版式在 slot_catalog 里声明了但没实现：{missing}"
        _ = catalog

    def test_normal_case_draws_every_item(self):
        """条目数在容量内时必须**全部**画出（不能悄悄丢条目）"""
        copy = build_slot_copy("main_selling_point", identity=IDENTITY, analysis=ANALYSIS)
        data, meta = render_info_image(self._base(), copy, layout="top_title_bullets",
                                       size=(900, 900))
        assert meta["ok"] is True
        assert meta["items"] == len(copy["items"]), \
            f"应画出全部 {len(copy['items'])} 条，实际 {meta['items']} 条"
        assert data

    def test_compose_info_image_with_slot(self):
        copy = build_slot_copy("main_benefits", identity=IDENTITY, analysis=ANALYSIS)
        data, meta = compose_info_image(self._base(), copy,
                                        {"layout": "benefit_grid", "aspect": "1:1"})
        assert meta["ok"] is True
        assert data

    def test_long_text_is_truncated_not_overflowing(self):
        copy = {"title": "超长标题" * 20, "items": ["很长的一条内容" * 20], "footer": "尾注" * 30,
                "blocked": False}
        data, meta = render_info_image(self._base(), copy, layout="top_title_bullets",
                                       size=(600, 600))
        assert meta["ok"] is True

    def test_works_without_base_image(self):
        copy = build_slot_copy("main_benefits", identity=IDENTITY, analysis=ANALYSIS)
        data, meta = render_info_image(None, copy, layout="benefit_grid", size=(600, 600))
        assert meta["ok"] is True
        assert data


@pytest.mark.skipif(not available_fonts(), reason="系统无中文字体")
class TestTypographySettings:
    """排版样式可由用户调（设置页 → 生图质量策略 → typography）"""

    def _base(self, size=(600, 600)):
        import io

        from PIL import Image
        image = Image.new("RGB", size, (252, 252, 252))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    def _copy(self):
        return build_slot_copy("main_selling_point", identity=IDENTITY, analysis=ANALYSIS)

    def test_meta_reports_effective_typography(self):
        data, meta = render_info_image(self._base(), self._copy(), layout="top_title_bullets",
                                       size=(600, 600), typography={"font_scale": 1.4,
                                                                     "max_items": 3})
        assert meta["ok"] is True
        assert meta["typography"]["font_scale"] == 1.4
        assert meta["typography"]["max_items"] == 3
        assert data

    def test_max_items_limits_and_reports_omission(self):
        data, meta = render_info_image(self._base(), self._copy(), layout="top_title_bullets",
                                       size=(900, 900), typography={"max_items": 2})
        assert meta["items"] == 2
        assert any("条目超过上限" in note for note in meta["truncated"]), meta["truncated"]
        assert data

    def test_brand_color_is_applied_to_header(self):
        import io

        from PIL import Image
        data, meta = render_info_image(self._base(), self._copy(), layout="top_title_bullets",
                                       size=(600, 600),
                                       typography={"brand_color": "#B02020"})
        assert meta["ok"] is True
        image = Image.open(io.BytesIO(data)).convert("RGB")
        # 顶部标题条（y≈2 一定在色块里，不会被文字覆盖）
        assert image.getpixel((4, 2)) == (176, 32, 32), image.getpixel((4, 2))

    def test_footer_can_be_hidden(self):
        copy = self._copy()
        assert copy["footer"], "该商品应有页脚（品牌/规格/认证）"
        _, shown = render_info_image(self._base(), copy, layout="spec_table", size=(600, 600),
                                     typography={"show_footer": True})
        _, hidden = render_info_image(self._base(), copy, layout="spec_table", size=(600, 600),
                                      typography={"show_footer": False})
        assert shown["footer"] and hidden["footer"] == ""

    def test_font_scale_changes_output(self):
        _, small = render_info_image(self._base(), self._copy(), layout="top_title_bullets",
                                     size=(600, 600), typography={"font_scale": 0.6})
        _, large = render_info_image(self._base(), self._copy(), layout="top_title_bullets",
                                     size=(600, 600), typography={"font_scale": 1.8})
        assert small["typography"]["font_scale"] != large["typography"]["font_scale"]

    def test_invalid_typography_falls_back(self):
        _, meta = render_info_image(self._base(), self._copy(), layout="top_title_bullets",
                                    size=(600, 600),
                                    typography={"font_scale": "很大", "max_items": 999,
                                                "brand_color": "红色"})
        assert meta["typography"]["font_scale"] == 1.0
        assert meta["typography"]["max_items"] == 8      # 收敛到上限而不是报错
        assert meta["typography"]["brand_color"] == "#1860AC"

    def test_truncation_is_reported(self):
        copy = {"title": "超长标题" * 12, "items": ["很长的一条卖点内容" * 8], "footer": "",
                "blocked": False}
        _, meta = render_info_image(self._base(), copy, layout="top_title_bullets",
                                    size=(600, 600))
        assert meta["ok"] is True
        assert meta["truncated"], "超宽文本被截断时必须上报（便于用户缩短文案）"
        assert any("标题" in note or "条目" in note for note in meta["truncated"])
