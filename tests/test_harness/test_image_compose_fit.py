"""本地排版引擎的自适应与保真（A73）

用户 2026-09-18 实测两个真缺陷：
1. **事实被截断成错信息**：6 条卖点全被截（`…明確標示百`）、页脚把"每粒 0.391g"
   截成 **"每粒 0.3"**；
2. **信息图比摄影图糊**：2048 的底图被按 1:1 强制缩到 1200。

这里钉住修复后的行为：零截断（换行 + 缩字号兜底）、保持底图分辨率、按版式真实容量限条、
品牌色默认取包装品牌色。
"""

import io

import pytest

from src.harness.image_compose import (
    DEFAULT_INFO_CANVAS,
    LAYOUT_CAPACITY,
    compose_info_image,
    render_info_image,
)

COPY = {
    "title": "金裝 強力 肝迅康（升級版）",
    "items": [
        "高含量檸檬酸膽鹼 51.2%，配方主成分明確標示",
        "植物來源組合：迷迭香葉粉 17.9% ＋ 蒲公英根粉 10.3%",
        "金裝強力升級版，60粒裝、每日1–2粒×2–3次",
        "成分與份量全透明：營養標籤、批號2507032、效期",
        "德國製造（MADE IN GERMANY）／原產國：德國",
        "香港代理：HEALTH WINNER TRADING LIMITED",
    ],
    # 改前这一条被截断成 "每粒 0.3"
    "footer": "德國 樂美寶® / DEFOEBUENA ｜ 60's / 60粒；每粒 0.391g",
    "layout": "top_title_bullets",
}


def _jpeg(width=2048, height=2048) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (240, 244, 248)).save(buffer, format="JPEG")
    return buffer.getvalue()


class TestAdaptiveText:
    def test_long_items_and_footer_are_not_truncated(self):
        data, meta = render_info_image(None, COPY, layout="top_title_bullets")
        assert data and meta["ok"]
        assert meta["items"] == 6
        assert meta["truncated"] == []          # 改前这里会有 6 条"超宽已截断"
        assert "每粒 0.391g" in meta["footer"]   # 改前是"每粒 0.3"

    def test_small_canvas_also_avoids_truncation(self):
        """自适应与画布尺寸无关（字号按画布宽度算，小画布同样能放下）"""
        data, meta = render_info_image(None, COPY, layout="top_title_bullets", size=(320, 320))
        assert data and meta["ok"]
        assert meta["truncated"] == []

    def test_truncation_is_recorded_when_it_really_happens(self):
        """极端放不下时允许截断，但**必须记账**（不静默丢字）"""
        from PIL import Image, ImageDraw

        from src.harness.image_compose import _draw_text, _font

        draw = ImageDraw.Draw(Image.new("RGB", (600, 200), (255, 255, 255)))
        clipped: list[str] = []
        _draw_text(draw, (0, 0), "超長且沒有斷點的連續字串" * 6, _font(120), (0, 0, 0),
                   max_width=40, clipped=clipped, label="条目")
        assert clipped and "截断" in clipped[0]

    def test_capacity_clamps_items_per_layout(self):
        _, meta = render_info_image(None, COPY, layout="cert_badge")
        assert meta["items"] <= LAYOUT_CAPACITY["cert_badge"]
        assert any("版式" in note for note in meta["truncated"])


class TestResolutionAndPalette:
    def test_base_resolution_is_preserved(self):
        """改前：1:1 槽位被强制缩到 1200x1200（信息图比摄影图糊一截）"""
        _, meta = compose_info_image(_jpeg(2048, 2048), COPY,
                                     {"layout": "top_title_bullets", "aspect": "1:1"})
        assert meta["size"] == "2048x2048"

    def test_explicit_size_still_wins(self):
        _, meta = compose_info_image(_jpeg(2048, 2048), COPY,
                                     {"layout": "top_title_bullets"}, size=(1200, 1200))
        assert meta["size"] == "1200x1200"

    def test_without_base_uses_default_canvas(self):
        _, meta = render_info_image(None, COPY, layout="top_title_bullets")
        assert meta["size"] == f"{DEFAULT_INFO_CANVAS[0]}x{DEFAULT_INFO_CANVAS[1]}"

    def test_typography_brand_colors_are_applied(self):
        _, meta = render_info_image(
            None, COPY, layout="top_title_bullets",
            typography={"brand_color": "#1B3F94", "accent_color": "#7CBF4A",
                        "font_scale": 1.2, "max_items": 3, "show_footer": False})
        assert meta["typography"]["brand_color"] == "#1B3F94"
        assert meta["typography"]["accent_color"] == "#7CBF4A"
        assert meta["items"] == 3
        assert meta["footer"] == ""             # show_footer=False

    def test_blocked_copy_is_not_drawn(self):
        data, meta = render_info_image(None, {"blocked": True, "reason": "需要成分表"},
                                       layout="ingredient_list")
        assert meta["ok"] is False and meta["blocked"] is True
        assert "成分表" in meta["reason"]


class TestWhiteExpectations:
    """白底预期按**平台背景策略**（用户质疑"很多商品图都不是白底的啊？"）"""

    def _plan(self, policy: str) -> dict:
        return {"background_policy": policy, "slots": [
            {"slot_id": "main_white", "kind": "photo"},
            {"slot_id": "main_scene", "kind": "photo"},
            {"slot_id": "main_selling_point", "kind": "info"},
        ]}

    def test_design_allowed_does_not_require_white(self):
        from src.chat.engine import _white_expectations

        assert _white_expectations(self._plan("design_allowed")) == {
            "main_white": False, "main_scene": False, "main_selling_point": False}

    def test_white_required_requires_white_only_for_photos(self):
        from src.chat.engine import _white_expectations

        result = _white_expectations(self._plan("white_required"))
        assert result == {"main_white": True, "main_scene": True,
                          "main_selling_point": False}

    @pytest.mark.parametrize("plan", [None, {}, {"slots": []}])
    def test_missing_plan_is_safe(self, plan):
        from src.chat.engine import _white_expectations

        assert _white_expectations(plan) == {}
