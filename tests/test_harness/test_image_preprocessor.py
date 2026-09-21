"""ImagePreprocessor 单元测试 — 图片预处理 + 解压炸弹防护"""

import base64
import io
import struct
import zlib

import pytest
from PIL import Image

from src.harness.image_preprocessor import (
    DEFAULT_MAX_TOTAL_PIXELS,
    ImagePreprocessor,
    PreprocessResult,
)

# ── 测试辅助 ──


def _make_image_bytes(mode: str = "RGB", size: tuple[int, int] = (64, 64), fmt: str = "PNG") -> bytes:
    """用 Pillow 生成一张真实图片的字节"""
    color = (255, 0, 0, 128) if mode == "RGBA" else (255, 0, 0)
    img = Image.new(mode, size, color)
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


def _make_png_header(width: int, height: int) -> bytes:
    """手工构造只含头部、无像素数据的 PNG（模拟解压炸弹：小文件 + 超大尺寸头）"""
    def _chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit RGB
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", b"") + _chunk(b"IEND", b"")


# ── 基础边界 ──


class TestBasicValidation:
    def test_empty_bytes_returns_error(self):
        p = ImagePreprocessor()
        result = p.process(b"")
        assert result.error == "空文件"
        assert result.data == b""

    def test_oversized_file_rejected(self):
        p = ImagePreprocessor(max_bytes=10)
        result = p.process(b"x" * 100)
        assert "文件过大" in result.error

    def test_corrupt_image_returns_error(self):
        p = ImagePreprocessor()
        result = p.process(b"this is not an image at all")
        assert "预处理异常" in result.error

    def test_invalid_max_total_pixels_rejected(self):
        with pytest.raises(ValueError, match="正数"):
            ImagePreprocessor(max_total_pixels=0)
        with pytest.raises(ValueError, match="正数"):
            ImagePreprocessor(max_total_pixels=-1)


# ── 正常处理链 ──


class TestNormalProcessing:
    def test_valid_png_passes(self):
        p = ImagePreprocessor()
        result = p.process(_make_image_bytes(size=(100, 80)))
        assert result.error == ""
        assert result.width == 100
        assert result.height == 80
        assert result.format == "PNG"
        # 输出仍是一张有效图片
        out = Image.open(io.BytesIO(result.data))
        assert out.size == (100, 80)

    def test_valid_jpeg_passes(self):
        p = ImagePreprocessor()
        result = p.process(_make_image_bytes(fmt="JPEG"))
        assert result.error == ""
        assert result.format == "JPEG"
        out = Image.open(io.BytesIO(result.data))
        assert out.format == "JPEG"

    def test_rgba_png_flattened_to_rgb(self):
        p = ImagePreprocessor()
        result = p.process(_make_image_bytes(mode="RGBA", size=(32, 32)))
        assert result.error == ""
        out = Image.open(io.BytesIO(result.data))
        assert out.mode == "RGB"  # alpha 已压平到白底

    def test_resize_when_over_max_pixels(self):
        p = ImagePreprocessor(max_pixels=512)
        result = p.process(_make_image_bytes(size=(3000, 2000)))
        assert result.error == ""
        assert result.resized is True
        assert result.width == 512
        assert result.height == 341  # int(2000 * 512/3000)
        out = Image.open(io.BytesIO(result.data))
        assert max(out.size) <= 512

    def test_no_resize_within_limit(self):
        p = ImagePreprocessor(max_pixels=1024)
        result = p.process(_make_image_bytes(size=(300, 200)))
        assert result.error == ""
        assert result.resized is False
        assert result.width == 300

    def test_process_for_vision_returns_base64(self):
        p = ImagePreprocessor()
        raw = _make_image_bytes(size=(64, 48))
        b64, width, height = p.process_for_vision(raw)
        assert isinstance(b64, str)
        assert width == 64
        assert height == 48
        # base64 可解码为处理后的图片
        assert len(base64.b64decode(b64)) > 0


# ── 解压炸弹防护 ──


class TestDecompressionBombProtection:
    def test_custom_cap_rejects_oversized_header(self):
        """小文件 + 大尺寸头（4MP > 1MP 上限）→ 在像素访问前被拒绝"""
        p = ImagePreprocessor(max_total_pixels=1_000_000)
        result = p.process(_make_png_header(2000, 2000))
        assert "解压炸弹" in result.error
        assert "1000000" in result.error

    def test_default_cap_rejects_huge_dimensions(self):
        """超过默认 64MP 上限（或 Pillow 全局上限）→ 拒绝处理"""
        p = ImagePreprocessor()
        result = p.process(_make_png_header(20000, 20000))
        assert "解压炸弹" in result.error
        assert result.resized is False  # 从未进入像素处理

    def test_within_cap_passes(self):
        p = ImagePreprocessor(max_total_pixels=100_000)
        result = p.process(_make_image_bytes(size=(200, 200)))  # 40K px < 100K cap
        assert result.error == ""

    def test_exact_cap_boundary_allowed(self):
        p = ImagePreprocessor(max_total_pixels=10_000)
        result = p.process(_make_image_bytes(size=(100, 100)))  # 恰好 10K px
        assert result.error == ""

    def test_default_cap_is_reasonable(self):
        """默认上限应远大于常规手机照片（12MP），远小于炸弹规模"""
        assert DEFAULT_MAX_TOTAL_PIXELS >= 12_000_000
        assert DEFAULT_MAX_TOTAL_PIXELS < 500_000_000

    def test_bomb_rejected_before_pixel_access(self):
        """解压炸弹被拒绝时不得发生像素解压（构造无像素数据的 PNG 验证）"""
        p = ImagePreprocessor(max_total_pixels=1_000)
        result = p.process(_make_png_header(1000, 1000))  # 1MP > 1K cap，且文件无像素数据
        assert "解压炸弹" in result.error
        assert result.data == _make_png_header(1000, 1000)  # 原始数据未被动过


# ── PreprocessResult 数据类 ──


class TestPreprocessResult:
    def test_defaults(self):
        r = PreprocessResult(data=b"abc")
        assert r.data == b"abc"
        assert r.width == 0
        assert r.error == ""
        assert r.resized is False
        assert r.compressed is False
