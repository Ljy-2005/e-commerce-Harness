"""图片预处理 — 上传时 resize/compress/format-normalize

在图片进入分析管线前统一处理，减少 API 调用失败率。
使用 Pillow (PIL)，降级到简单字节透传（无 Pillow 时）。

安全防护：
- 文件大小上限（max_bytes）— 限制压缩后字节数
- 解压炸弹防护（max_total_pixels）— 仅读取图片头部的宽高，
  在任何像素解压/分配之前校验总像素数，防止小文件+超大尺寸头耗尽内存
- 长边缩放（max_pixels）— 先缩放再转 RGB，避免为大图分配中间缓冲
"""

import io
from dataclasses import dataclass


# ── 默认限制 ──

DEFAULT_MAX_PIXELS = 2048      # 长边最大值（输出）
DEFAULT_MAX_BYTES = 10 * 1024 * 1024   # 10MB（压缩后文件大小）
DEFAULT_MAX_TOTAL_PIXELS = 64 * 1024 * 1024  # 64MP 总像素上限（解压炸弹防护）
DEFAULT_QUALITY = 85            # JPEG 质量
DEFAULT_FORMAT = "JPEG"         # 输出格式（源格式非 JPEG/PNG/WEBP 时使用）


@dataclass
class PreprocessResult:
    """预处理结果"""
    data: bytes
    width: int = 0
    height: int = 0
    original_bytes: int = 0
    format: str = "JPEG"
    resized: bool = False
    compressed: bool = False
    error: str = ""


class ImagePreprocessor:
    """上传图片预处理

    处理链: 头部尺寸校验（解压炸弹防护）→ EXIF 校正 → 尺寸缩放 → 转 RGB → 压缩编码
    """

    def __init__(
        self,
        max_pixels: int = DEFAULT_MAX_PIXELS,
        max_bytes: int = DEFAULT_MAX_BYTES,
        quality: int = DEFAULT_QUALITY,
        output_format: str = DEFAULT_FORMAT,
        max_total_pixels: int = DEFAULT_MAX_TOTAL_PIXELS,
    ):
        if max_total_pixels <= 0:
            raise ValueError("max_total_pixels 必须为正数")
        self.max_pixels = max_pixels
        self.max_bytes = max_bytes
        self.quality = quality
        self.output_format = output_format
        self.max_total_pixels = max_total_pixels

    def process(self, raw_bytes: bytes, source_name: str = "upload") -> PreprocessResult:
        """预处理一张图片，返回处理后的字节"""
        original_size = len(raw_bytes)
        result = PreprocessResult(data=raw_bytes, original_bytes=original_size, format="unknown")

        # 空文件
        if not raw_bytes:
            result.error = "空文件"
            return result

        # 过大文件
        if original_size > self.max_bytes:
            result.error = f"文件过大 ({original_size / 1024 / 1024:.1f}MB > {self.max_bytes / 1024 / 1024:.0f}MB)"
            return result

        # Pillow 可用性检测（独立于处理逻辑，便于异常分支引用 Image 类型）
        try:
            from PIL import Image, ImageOps
        except ImportError:
            # 无 Pillow → 不做处理，原样透传
            result.data = raw_bytes
            result.format = source_name.split(".")[-1].upper() if "." in source_name else "unknown"
            return result

        try:
            img = Image.open(io.BytesIO(raw_bytes))
            src_format = img.format

            # ── 解压炸弹防护：先于任何像素访问，仅用头部尺寸校验 ──
            width, height = img.size
            total_pixels = width * height
            if total_pixels > self.max_total_pixels:
                result.error = (
                    f"图片像素数异常 ({width}x{height} = {total_pixels}px)，"
                    f"超过安全上限 {self.max_total_pixels}px，疑似解压炸弹，已拒绝处理"
                )
                return result

            # EXIF 方向校正
            img = ImageOps.exif_transpose(img) if hasattr(ImageOps, "exif_transpose") else img

            # 尺寸限制：先缩放再转 RGB，避免为大图分配全尺寸中间缓冲
            if max(img.size) > self.max_pixels:
                ratio = self.max_pixels / max(img.size)
                new_size = (int(img.size[0] * ratio), int(img.size[1] * ratio))
                img = img.resize(new_size, Image.LANCZOS)
                result.width, result.height = new_size
                result.resized = True
            else:
                result.width, result.height = img.size

            # 转为 RGB（RGBA/P 等格式）— 此时图片已不超过 max_pixels，内存安全
            if img.mode in ("RGBA", "P", "LA"):
                background = Image.new("RGB", img.size, (255, 255, 255))
                if img.mode in ("RGBA", "LA"):
                    background.paste(img, mask=img.split()[-1])
                else:
                    background.paste(img)
                img = background
            elif img.mode not in ("RGB", "L"):
                img = img.convert("RGB")

            result.format = src_format or self.output_format

            # 编码输出（保留 JPEG/PNG/WEBP 源格式，其余统一为 output_format）
            out_format = self.output_format if src_format not in ("JPEG", "PNG", "WEBP") else src_format
            buf = io.BytesIO()
            save_kwargs = {"format": out_format}
            if out_format == "JPEG":
                save_kwargs["quality"] = self.quality
                save_kwargs["optimize"] = True

            img.save(buf, **save_kwargs)
            result.data = buf.getvalue()

            if len(result.data) < original_size:
                result.compressed = True

        except Image.DecompressionBombError:
            # Pillow 自带的解压炸弹拦截（超过其全局上限）
            result.error = "疑似解压炸弹：图片像素尺寸超过 Pillow 安全上限，已拒绝处理"
        except Exception as e:
            # 图片损坏 → 保留原始数据，标记错误
            result.error = f"预处理异常: {str(e)[:100]}"

        return result

    def process_for_vision(self, raw_bytes: bytes) -> tuple[str, int, int]:
        """处理图片用于 Vision API 调用

        返回 (base64_string, width, height) 供 OpenAI/Claude Vision 使用。
        自动限制到 2048px 并转为 JPEG。
        """
        import base64
        result = self.process(raw_bytes)
        b64 = base64.b64encode(result.data).decode("utf-8")
        return b64, result.width, result.height
