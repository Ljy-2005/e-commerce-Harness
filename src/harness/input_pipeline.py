"""输入验证管道 — 图片格式/大小/分辨率/安全检查"""

from dataclasses import dataclass, field


@dataclass
class ValidationResult:
    passed: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class ImageValidator:
    """图片输入验证器

    检查: 格式、大小、分辨率、文件完整性
    """

    ALLOWED_FORMATS = {"image/jpeg", "image/png", "image/webp", "image/tiff"}
    MAX_FILE_SIZE = 20 * 1024 * 1024      # 20MB
    MIN_RESOLUTION = (200, 200)
    MAX_RESOLUTION = (8000, 8000)

    def validate(self, image_data) -> ValidationResult:
        """验证单张图片，返回 ValidationResult"""
        result = ValidationResult()

        # 格式检查
        if image_data.media_type not in self.ALLOWED_FORMATS:
            result.errors.append(
                f"不支持的图片格式: {image_data.media_type}，支持: {', '.join(self.ALLOWED_FORMATS)}"
            )

        # 大小检查
        if image_data.file_size > self.MAX_FILE_SIZE:
            result.errors.append(
                f"图片过大: {image_data.file_size / 1024 / 1024:.1f}MB，最大 {self.MAX_FILE_SIZE / 1024 / 1024:.0f}MB"
            )
        if image_data.file_size == 0:
            result.errors.append("图片文件为空")

        # base64 有效性
        if not image_data.base64_data or len(image_data.base64_data) < 10:
            result.errors.append("图片 base64 数据无效")

        result.passed = len(result.errors) == 0
        return result

    def validate_batch(self, images: list) -> ValidationResult:
        """批量验证，返回汇总结果"""
        result = ValidationResult()
        for i, img in enumerate(images):
            r = self.validate(img)
            if not r.passed:
                for e in r.errors:
                    result.errors.append(f"[{i}] {e}")
        result.passed = len(result.errors) == 0
        return result


class ContentSafetyChecker:
    """内容安全检查（占位 — P0 不做真实 API 调用）"""

    async def check(self, image_data) -> ValidationResult:
        """检查图片是否有违规内容"""
        # P0 简化：不做真实安全 API 调用
        # 后续可接入阿里云内容安全 / AWS Rekognition
        return ValidationResult(passed=True)


class InputPipeline:
    """输入验证管道

    阶段: 格式验证 → 大小限制 → 内容安全检查
    """

    def __init__(self):
        self.image_validator = ImageValidator()
        self.safety_checker = ContentSafetyChecker()

    async def process(self, image_data) -> ValidationResult:
        """完整输入验证管道"""
        result = self.image_validator.validate(image_data)
        if not result.passed:
            return result

        safety = await self.safety_checker.check(image_data)
        if not safety.passed:
            result.errors.extend(safety.errors)
            result.passed = False

        return result

    async def process_batch(self, images: list) -> ValidationResult:
        """批量验证"""
        result = self.image_validator.validate_batch(images)
        if not result.passed:
            return result

        for img in images:
            safety = await self.safety_checker.check(img)
            if not safety.passed:
                result.errors.extend(safety.errors)
                result.passed = False

        return result
