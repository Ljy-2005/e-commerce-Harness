# ImagePreprocessor — 图片预处理

> 覆盖: `src/harness/image_preprocessor.py`
> 测试: `tests/test_harness/test_image_preprocessor.py`（18 个用例，含解压炸弹防护）

## 功能

上传图片在进入分析管线前统一处理，减少 Provider 调用失败率。基于 Pillow（无 Pillow 时降级为字节透传）。

**处理链**：头部尺寸校验（解压炸弹防护）→ EXIF 方向校正 → 长边缩放 → RGBA/P/LA 转 RGB → 压缩编码

## 关键类

### PreprocessResult

```
data / width / height / original_bytes / format / resized / compressed / error
```

### ImagePreprocessor

```
ImagePreprocessor(
    max_pixels=2048,           # 输出长边上限（先缩放再转 RGB）
    max_bytes=10MB,            # 压缩后文件大小上限
    quality=85,                # JPEG 质量
    output_format="JPEG",      # 源格式非 JPEG/PNG/WEBP 时的输出格式
    max_total_pixels=64MP,     # 总像素上限（解压炸弹防护）
)
process(raw_bytes, source_name) → PreprocessResult
process_for_vision(raw_bytes) → (base64_str, width, height)   # 供 Vision 调用方使用
```

## 安全防护（审计修复 HIGH 项）

1. **解压炸弹防护** — 仅读取图片头部的宽高，在**任何像素解压/分配之前**校验 `width × height ≤ max_total_pixels`，小文件 + 超大尺寸头被直接拒绝
2. **内存安全顺序** — 尺寸缩放前置到 RGB 转换之前，避免为大图分配全尺寸中间缓冲
3. **显式拦截** `PIL.Image.DecompressionBombError` 转结构化错误

## 调用方

- `POST /api/sessions`（上传商品图，先 `ImageValidator` 校验再预处理）
- `POST /api/workflows/templates/{name}/instantiate`（多图片输入分发）
- `POST /api/workflows/jobs/{id}/replicate`（参考风格图）

三处统一使用 `src/main.py` 顶部的常量 `PREPROCESS_MAX_PIXELS` / `PREPROCESS_JPEG_QUALITY`。

## 修改指南

- **调整尺寸/质量上限** → `main.py` 常量或调用点的构造参数（默认值在模块顶部常量）
- **支持新输出格式** → 扩展 `process()` 的编码分支（当前 JPEG 为主）
- **接入 rembg 去背景** → 该能力在 `src/agents/post_process.py`（后处理 Agent），不在预处理管线
