"""生成图本地体检 —— 客观、可量化、能当回归基线

实测锚点（本次真实会话的三张图）：
- 2048×2048，边缘均值 (237,238,241) ≈ #EDEEF1（**不是** #FFFFFF）；
- 右下角 70–100%×90–100% 区域均值 (219,223,233)，比边缘暗 15~20 → 水印/叠加物。

用户要求"文+图生图"，所以还要能识别两种退化：
- 退化成**纯文生图**（商品身份丢失）→ `identity_lost`；
- 退化成**纯图生图**（直接复制原图没重绘）→ `is_near_copy`。
"""

import base64
import io

import pytest
from PIL import Image

from src.harness.image_quality import (
    compare_to_reference,
    decode_reference,
    inspect_image,
    inspect_images,
    summarize,
)

THRESHOLDS = {"edge_whiteness": 250, "watermark_zone_delta": 3,
              "identity_similarity_min": 0.45, "near_copy_max": 0.92}


def _png_bytes(size=(256, 256), bg=(255, 255, 255), subject=None) -> bytes:
    """白底 + 中间一个"商品"方块"""
    image = Image.new("RGB", size, bg)
    if subject:
        color, box = subject
        for x in range(box[0], box[2]):
            for y in range(box[1], box[3]):
                image.putpixel((x, y), color)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def _gray_product() -> tuple:
    return ((90, 60, 120), (90, 90, 170, 170))


class TestBackgroundWhiteness:
    def test_pure_white_passes(self):
        report = inspect_image(_png_bytes(subject=_gray_product()), thresholds=THRESHOLDS)
        assert report["available"] is True
        assert report["is_white_bg"] is True
        assert report["edge_mean"] >= 250
        assert not any("纯白" in issue for issue in report["issues"])

    def test_gray_background_is_flagged(self):
        """实测生图背景 ≈ #EDEEF1（(237,238,241)），必须判不合格"""
        report = inspect_image(_png_bytes(bg=(237, 238, 241), subject=_gray_product()),
                               thresholds=THRESHOLDS)
        assert report["is_white_bg"] is False
        assert any("纯白" in issue for issue in report["issues"])


class TestWatermarkZone:
    def test_clean_image_not_flagged(self):
        report = inspect_image(_png_bytes(subject=_gray_product()), thresholds=THRESHOLDS)
        assert report["watermark_suspected"] is False
        assert report["watermark_checked"] is True

    def test_bottom_right_overlay_is_flagged(self):
        """实测右下角比边缘暗 15~20 → 视觉模型读出来是 "AI生成" 水印

        真实水印是**半透明浅灰**（实测上次事故三张图 edge_min 仍在 210~219），
        所以这里用浅灰小角标：既不把最外圈压暗（否则会被当成商品压边），
        又能把右下角区域均值拉低到阈值以上。
        """
        image = Image.open(io.BytesIO(_png_bytes(subject=_gray_product()))).convert("RGB")
        for x in range(int(256 * 0.84), 256):      # 16% 宽
            for y in range(int(256 * 0.955), 256):  # 4.5% 高
                image.putpixel((x, y), (205, 205, 205))
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        report = inspect_image(buf.getvalue(), thresholds=THRESHOLDS)
        assert report["watermark_checked"] is True
        assert report["watermark_suspected"] is True
        assert any("水印" in issue for issue in report["issues"])

    def test_scene_image_with_dark_bottom_right_is_not_flagged(self):
        """首次真机会话实测误报：场景图右下角本来就暗（背景 200），不该判水印"""
        image = Image.new("RGB", (256, 256), (200, 200, 200))
        for x in range(256):                      # 整张是浅灰场景
            for y in range(256):
                if x > 170 and y > 220:           # 右下角一个深色道具
                    image.putpixel((x, y), (60, 60, 60))
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        report = inspect_image(buf.getvalue(), thresholds=THRESHOLDS)
        assert report["watermark_checked"] is False
        assert report["watermark_suspected"] is False
        assert not any("水印" in issue for issue in report["issues"])

    def test_closeup_with_subject_filling_corner_is_not_flagged(self):
        """实测误报 2：特写图主体占满画面（占比 0.555），右下角是商品本身"""
        image = Image.new("RGB", (256, 256), (252, 252, 252))
        for x in range(int(256 * 0.5), 256):      # 商品占满右半边
            for y in range(int(256 * 0.5), 256):
                image.putpixel((x, y), (40, 40, 40))
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        report = inspect_image(buf.getvalue(), thresholds=THRESHOLDS)
        # 背景是浅色 → 会做检查，但取第 80 百分位后不应误判（主体是"内容"不是"叠加"）
        assert report["watermark_suspected"] is False


class TestIssueMessageFormat:
    def test_no_self_contradicting_rounding(self):
        """实测文案 bug：249.7 四舍五入成 250，打出「背景不是纯白：250 < 250」"""
        report = inspect_image(_png_bytes(bg=(249, 249, 249), subject=_gray_product()),
                               thresholds=THRESHOLDS)
        message = next(issue for issue in report["issues"] if "纯白" in issue)
        assert "250 < 250" not in message
        shown = float(message.split("边缘平均亮度 ")[1].split(" <")[0])
        assert abs(shown - report["edge_mean"]) < 0.05, f"文案与实测值不一致：{message}"

    def test_non_white_slot_does_not_report_white_bg_issue(self):
        """场景/特写槽位本来就不是白底：给出数值但不报"背景不合格"（实测误报 3 张）"""
        report = inspect_image(_png_bytes(bg=(200, 200, 200), subject=_gray_product()),
                               thresholds=THRESHOLDS, expect_white_bg=False)
        assert report["is_white_bg"] is False
        assert report["expect_white_bg"] is False
        assert report["edge_mean"] < 250
        assert not any("纯白" in issue for issue in report["issues"])

    @pytest.mark.asyncio
    async def test_summary_skips_non_white_slots(self):
        records = [
            {"slot_id": "main_white", "base64_data": base64.b64encode(
                _png_bytes(subject=_gray_product())).decode()},
            {"slot_id": "main_scene", "base64_data": base64.b64encode(
                _png_bytes(bg=(180, 180, 180), subject=_gray_product())).decode()},
        ]
        reports, _ = await inspect_images(records, thresholds=THRESHOLDS,
                                          expect_white={"main_white": True, "main_scene": False})
        summary = summarize(reports)
        assert summary["white_bg_ok"] is True          # 只有 main_white 要求纯白，它合格
        assert summary["white_bg_skipped"] == ["main_scene"]
        assert not any("纯白" in issue for issue in summary["issues"])


class TestSubjectAndSharpness:
    def test_subject_ratio_reasonable(self):
        report = inspect_image(_png_bytes(subject=_gray_product()), thresholds=THRESHOLDS)
        assert 0.03 < report["subject_ratio"] < 0.85

    def test_tiny_subject_flagged(self):
        report = inspect_image(_png_bytes(subject=((10, 10, 10), (126, 126, 132, 132))),
                               thresholds=THRESHOLDS)
        assert any("主体占画面比例过低" in issue for issue in report["issues"])

    def test_blank_image_flagged(self):
        report = inspect_image(_png_bytes(), thresholds=THRESHOLDS)
        assert any("主体" in issue for issue in report["issues"])

    def test_bad_bytes(self):
        report = inspect_image(b"not an image")
        assert report["available"] is False
        assert report["issues"]

    def test_empty_bytes(self):
        assert inspect_image(b"")["available"] is False


class TestReferenceCompare:
    def test_identical_image_is_near_copy(self):
        """整图一致 + 背景一致 = 直接复制原图（退化成纯图生图）"""
        data = _png_bytes(bg=(210, 208, 205), subject=_gray_product())
        result = compare_to_reference(data, data, THRESHOLDS)
        assert result["is_near_copy"] is True
        assert any("复制原图" in issue for issue in result["issues"])

    def test_white_background_redraw_is_not_near_copy(self):
        """换了背景（原灰底 → 纯白）= 按文本重绘过了，不算复制"""
        reference = _png_bytes(bg=(210, 208, 205), subject=_gray_product())
        generated = _png_bytes(bg=(255, 255, 255), subject=_gray_product())
        result = compare_to_reference(reference, generated, THRESHOLDS)
        assert result["is_near_copy"] is False
        assert result["identity_similarity"] >= 0.45, "同一商品主体，身份应保住"

    def test_different_product_loses_identity(self):
        """主体完全不同的商品 = 身份丢失（退化成纯文生图）"""
        reference = _png_bytes(subject=((90, 60, 120), (90, 90, 170, 170)))
        generated = _png_bytes(subject=((20, 200, 20), (30, 30, 226, 226)))
        result = compare_to_reference(reference, generated, THRESHOLDS)
        assert result["identity_compared"] is True
        assert result["identity_lost"] is True
        assert any("身份" in issue for issue in result["issues"])

    def test_scene_output_is_not_judged_on_identity(self):
        """实测误报：场景/特写图本来就会换背景换构图，主体比对必然偏低 → 不据此判身份丢失"""
        reference = _png_bytes(subject=((90, 60, 120), (90, 90, 170, 170)))
        scene = Image.new("RGB", (256, 256), (70, 80, 60))       # 深色场景背景
        for x in range(40, 120):
            for y in range(120, 210):
                scene.putpixel((x, y), (180, 40, 40))
        buf = io.BytesIO()
        scene.save(buf, format="PNG")
        result = compare_to_reference(reference, buf.getvalue(), THRESHOLDS)
        assert result["identity_compared"] is False
        assert result["identity_lost"] is False
        assert result["identity_note"]
        assert not any("身份" in issue for issue in result["issues"])

    def test_garbage_reference(self):
        result = compare_to_reference(b"", b"")
        assert result["available"] is False


class TestInspectImagesAndSummary:
    @pytest.mark.asyncio
    async def test_reports_per_image_with_slot(self):
        records = [{"slot_id": "main_white", "base64_data": base64.b64encode(
            _png_bytes(subject=_gray_product())).decode()}]
        reports, notes = await inspect_images(records, thresholds=THRESHOLDS)
        assert len(reports) == 1
        assert reports[0]["slot_id"] == "main_white"
        assert notes == []

    @pytest.mark.asyncio
    async def test_reference_compare_attached(self):
        reference = _png_bytes(bg=(210, 208, 205), subject=_gray_product())
        records = [{"slot_id": "main_white",
                    "base64_data": base64.b64encode(reference).decode()}]
        reports, _ = await inspect_images(records, thresholds=THRESHOLDS, reference=reference)
        assert reports[0]["reference_compare"]["is_near_copy"] is True

    @pytest.mark.asyncio
    async def test_bad_record_only_notes(self):
        reports, notes = await inspect_images([{"slot_id": "x"}, "垃圾"], thresholds=THRESHOLDS)
        assert reports == []
        assert notes

    def test_summary_flags(self):
        reports = [{"available": True, "slot_id": "main_white", "is_white_bg": False,
                    "watermark_suspected": True, "issues": ["背景不是纯白"],
                    "reference_compare": {"identity_lost": True, "is_near_copy": False}},
                   {"available": True, "slot_id": "main_scene", "is_white_bg": True,
                    "watermark_suspected": False, "issues": [],
                    "reference_compare": {"identity_lost": False, "is_near_copy": True}}]
        summary = summarize(reports)
        assert summary["count"] == 2
        assert summary["white_bg_ok"] is False
        assert summary["watermark_free"] is False
        assert summary["identity_lost"] == ["main_white"]
        assert summary["near_copy"] == ["main_scene"]
        assert summary["issues"]

    def test_decode_reference(self):
        raw = _png_bytes()
        assert decode_reference([base64.b64encode(raw).decode()]) == raw
        assert decode_reference([f"data:image/png;base64,{base64.b64encode(raw).decode()}"]) == raw
        assert decode_reference([]) is None
