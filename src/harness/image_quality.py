"""生成图本地体检 —— 不花钱、确定性、可回归的客观指标

## 为什么需要

审查员（视觉模型）只能"看图说话"，而实测暴露的问题恰恰是**可量化**的：

- 白底主图实测边缘均值 (237,238,241) ≈ #EDEEF1，**不是 #FFFFFF**；
- 右下角 70–100%×90–100% 区域均值 (219,223,233)，比边缘暗 15~20 → 有叠加物
  （视觉模型读出来是 `AI生成` 水印）；
- 用户要求"文+图生图"，那么**既要防止退化成纯文生图**（商品身份丢失），
  **也要防止退化成纯图生图**（直接把原图复制一份，没按提示词重绘）。

这些都能在本地算出来，而且能进测试当回归基线 —— 比"再问一次模型"更便宜也更稳定。

## 指标

| 指标 | 含义 | 判据 |
|---|---|---|
| `edge_mean` / `edge_min` | 图像**最外圈**像素的平均/最小亮度 | 白底图应 ≥ `edge_whiteness`（255=纯白） |
| `watermark_zone_delta` | 右下角区域**背景亮度**（第 80 百分位）− 边缘基准 | 超过 `watermark_zone_delta` 阈值 → 疑似水印/角标 |
| `subject_ratio` | 非背景像素占比 | 白底主图通常 0.05–0.5（过高=没留白，过低=主体太小） |
| `sharpness` | 灰度拉普拉斯方差 | 过低 = 糊 |
| `identity_similarity` | 与参考图**主体区域**的相似度 | 低于 `identity_similarity_min` → 商品身份丢失 |
| `is_near_copy` | 整图与参考图高度相似 | 真 → "直接复制原图，没按文本重绘" |

**适用范围（首次真机会话实测后修正）**：水印与身份两项只在"纯色/浅色背景"的图上成立 ——
场景图右下角偏暗是**正常画面内容**，特写图主体占满画面也会让区域亮度差很大（实测
`main_scene` 边缘 200 被判水印、`main_detail` 主体占比 0.555 被判水印，导致协调者
白跑一轮重生成、多花 $0.2）。现在：

- 水印检测：仅在背景接近白色（`background_luma ≥ PLain_BG_LUMA=240`）时执行，且取
  右下角区域**第 80 百分位**（背景估计）与边缘第 80 百分位比较，避开主体/阴影；
  不适用时返回 `watermark_checked: False` + 说明，不再误报。
- 身份相似度：始终计算（供参考），但只有生成图背景接近白色时才据此判 `identity_lost`
  （场景/特写槽位记 `identity_compared: False` + 说明）。

Pillow 必装（上传预处理本来就用它）；numpy 缺失时自动跳过相似度类指标，不影响基本体检。
"""

import base64
import io
from typing import Any

from src.core.config import DEFAULT_QUALITY_THRESHOLDS, clean_quality_thresholds

# 背景判定：与边缘基准色的灰度距离超过该值即视为"主体像素"
SUBJECT_TOLERANCE = 34
# 相似度计算时的缩略尺寸（够用且快）
PROBE_SIDE = 64
# 只有"背景接近白色"的图才适合做水印检测与身份比对：
# 场景图右下角偏暗是正常画面内容，特写图主体占满画面也会让区域亮度差很大（实测误报）
PLAIN_BG_LUMA = 240
# 最外圈**最暗**像素的下限：商品本体压到画面边缘（特写/满幅）时最外圈会出现很暗的像素，
# 这类图同样不适合做"背景"判定（实测 main_detail edge_min=48 → 误判身份丢失；
# 而上次事故的三张图 edge_min 是 210~219，真阳性必须保留）
PLAIN_BG_MIN_EDGE = 200
# 右下角"深色占比"超过该值说明那一角是画面内容（商品本体）而不是叠加物
WATERMARK_MAX_DARK_RATIO = 0.4
_NUMPY_HINT = "未安装 numpy，跳过相似度类体检指标"


def _pil():
    try:
        from PIL import Image
        return Image
    except ImportError:  # pragma: no cover - 本机已装
        return None


def _numpy():
    try:
        import numpy
        return numpy
    except ImportError:  # pragma: no cover - 本机已装
        return None


def _open(data: bytes):
    Image = _pil()
    if Image is None or not data:
        return None
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
        return image.convert("RGB")
    except Exception:  # noqa: BLE001 — 坏图不该让体检崩掉整个流程
        return None


def edge_stats(image, np) -> tuple[float, float]:
    """最外圈像素的平均亮度与最小亮度（白底图的"白"就看这里）"""
    gray = np.asarray(image.convert("L"), dtype="float32")
    ring = np.concatenate([gray[0, :], gray[-1, :], gray[:, 0], gray[:, -1]])
    return float(ring.mean()), float(ring.min())


def zone_stats(image, np) -> tuple[float, float]:
    """右下角（水印高发区）与边缘的亮度差，以及该区域的"深色占比"

    Returns: `(delta, dark_ratio)`

    实测教训（首次真机会话）：单看"右下角比边缘暗"会把**正常画面**判成水印 ——
    场景图右下角本来就暗、特写图主体直接压在角落。所以：
    - 用**均值差**（对"白底上的一小块深色叠加"敏感，中位数对小块不敏感）；
    - 同时给出**深色占比**：占比很高说明那一角是画面内容（商品本体），调用方据此跳过。
    """
    gray = np.asarray(image.convert("L"), dtype="float32")
    height, width = gray.shape
    zone = gray[int(height * 0.88):, int(width * 0.70):]
    if zone.size == 0:
        return 0.0, 0.0
    border = float(gray[0, :].mean())
    delta = float(zone.mean() - border)
    dark_ratio = float((zone < border - SUBJECT_TOLERANCE).mean())
    return delta, dark_ratio


def subject_ratio(image, np) -> tuple[float, float]:
    """主体占比与背景基准亮度（按边缘中位色判定背景）"""
    small = image.resize((128, 128))
    gray = np.asarray(small.convert("L"), dtype="float32")
    ring = np.concatenate([gray[0, :], gray[-1, :], gray[:, 0], gray[:, -1]])
    background = float(np.median(ring))
    mask = np.abs(gray - background) > SUBJECT_TOLERANCE
    return float(mask.mean()), background


def sharpness(image, np) -> float:
    """清晰度代理：灰度拉普拉斯方差（越小越糊）"""
    gray = np.asarray(image.convert("L").resize((256, 256)), dtype="float32")
    kernel = np.array([[0, 1, 0], [1, -4, 1], [0, 1, 0]], dtype="float32")
    height, width = gray.shape
    view = np.lib.stride_tricks.sliding_window_view(gray, (3, 3))
    response = (view * kernel).sum(axis=(-1, -2))
    _ = (height, width)
    return float(response.var())


def _subject_crop(image, np, side: int = PROBE_SIDE):
    """裁出主体区域并缩放到固定尺寸（用于身份比对；裁不出主体时用整图）

    注意坐标系：掩码在 128×128 缩略图上计算，**必须换算回原图尺寸**再裁剪
    （否则裁到的是左上角四分之一，身份相似度会被算成极低）。
    """
    small = image.resize((128, 128))
    gray = np.asarray(small.convert("L"), dtype="float32")
    ring = np.concatenate([gray[0, :], gray[-1, :], gray[:, 0], gray[:, -1]])
    background = float(np.median(ring))
    mask = np.abs(gray - background) > SUBJECT_TOLERANCE
    box = (0, 0, image.width, image.height)
    if mask.any():
        rows, cols = np.where(mask)
        top, bottom = int(rows.min()), int(rows.max()) + 1
        left, right = int(cols.min()), int(cols.max()) + 1
        if bottom - top >= 8 and right - left >= 8:
            factor = image.width / small.width
            box = (int(left * factor), int(top * factor),
                   min(image.width, int(right * factor)),
                   min(image.height, int(bottom * factor)))
    return image.crop(box).convert("RGB").resize((side, side))


def _hist_overlap(left, right, np) -> float:
    """RGB 三通道直方图交叠系数 ∈ [0,1]（只用灰度会把"紫色方块 vs 绿色方块"判成相似）"""
    total = 0.0
    for channel in range(3):
        left_hist = np.asarray(left.getchannel(channel).histogram(), dtype="float32")
        right_hist = np.asarray(right.getchannel(channel).histogram(), dtype="float32")
        if left_hist.sum() == 0 or right_hist.sum() == 0:
            continue
        left_hist /= left_hist.sum()
        right_hist /= right_hist.sum()
        total += float(np.minimum(left_hist, right_hist).sum())
    return total / 3.0


def _crop_similarity(left, right, np) -> float:
    """两张（已对齐尺寸的）图的相似度 ∈ [0,1]

    直方图交叠看"颜色分布是否一致"，结构相关性看"形状布局是否一致"。
    两边都近似纯色时（结构相关性无意义），用平均亮度接近度兜底 —— 否则
    dHash 这类梯度哈希对纯色区域恒等，会把不同颜色的商品判成同一个。
    """
    hist = _hist_overlap(left, right, np)
    gray_left = np.asarray(left.convert("L").resize((24, 24)), dtype="float32")
    gray_right = np.asarray(right.convert("L").resize((24, 24)), dtype="float32")
    mean_left, mean_right = float(gray_left.mean()), float(gray_right.mean())
    left_centered = gray_left - mean_left
    right_centered = gray_right - mean_right
    denominator = float(left_centered.std() * right_centered.std())
    if denominator > 1e-3:
        structure = max(0.0, float((left_centered * right_centered).mean() / denominator))
    else:
        structure = max(0.0, 1.0 - abs(mean_left - mean_right) / 64.0)
    return round(0.5 * hist + 0.5 * structure, 4)


def compare_to_reference(reference: bytes, generated: bytes, thresholds: dict | None = None) -> dict:
    """把生成图与参考图对比：身份是否保住（不能是纯文生图）、是否只是复制（不能是纯图生图）"""
    limits = clean_quality_thresholds(thresholds)
    np = _numpy()
    ref_image, gen_image = _open(reference), _open(generated)
    if np is None or ref_image is None or gen_image is None:
        return {"available": False, "reason": _NUMPY_HINT if np is None else "图像无法解码",
                "issues": []}

    ref_crop = _subject_crop(ref_image, np)
    gen_crop = _subject_crop(gen_image, np)
    identity = _crop_similarity(ref_crop, gen_crop, np)

    overall = _crop_similarity(ref_image.resize((PROBE_SIDE, PROBE_SIDE)),
                               gen_image.resize((PROBE_SIDE, PROBE_SIDE)), np)

    ref_edge, _ = edge_stats(ref_image, np)
    gen_edge, gen_min = edge_stats(gen_image, np)
    background_shift = round(abs(gen_edge - ref_edge), 2)
    _, gen_background = subject_ratio(gen_image, np)

    # 近乎复制：整图高度相似 **且** 背景没换 → 说明没按提示词重绘
    is_near_copy = bool(overall >= limits["near_copy_max"] and background_shift < 12)

    # 身份比对只在"生成图也是浅色背景、且商品没压到画面边缘"时才有意义：
    # 场景图/特写图本来就会换背景、换构图、换比例，主体裁剪比对必然偏低（实测误报 4 张，
    # 引得协调者白跑一轮重生成）。不适用时仍给出相似度供参考，但不据此判 identity_lost。
    comparable = ((gen_background >= PLAIN_BG_LUMA or gen_edge >= PLAIN_BG_LUMA)
                  and gen_min >= PLAIN_BG_MIN_EDGE)
    identity_lost = bool(comparable and identity < limits["identity_similarity_min"])

    issues: list[str] = []
    if is_near_copy:
        issues.append(f"生成图与参考图高度相似（整图相似度 {overall:.2f}，背景几乎未变）："
                      "疑似直接复制原图，没有按提示词重绘场景")
    if identity_lost:
        issues.append(f"生成图与参考图的主体相似度过低（{identity:.2f} < "
                      f"{limits['identity_similarity_min']:.2f}）：商品身份可能已丢失，"
                      "模型在凭文字想象商品")
    return {
        "available": True,
        "identity_similarity": identity,
        "identity_compared": bool(comparable),
        "identity_note": "" if comparable else
        "生成图不是浅色背景（场景/特写类），主体裁剪比对不适用，相似度仅供参考",
        "global_similarity": overall,
        "background_shift": background_shift,
        "is_near_copy": is_near_copy,
        "identity_lost": identity_lost,
        "issues": issues,
    }


def inspect_image(data: bytes, *, thresholds: dict | None = None,
                  expect_white_bg: bool = True) -> dict[str, Any]:
    """单张图的客观体检（纯函数；Pillow/numpy 缺失时返回 `{"available": False}`）

    `expect_white_bg`：该槽位是否**要求**纯白背景。场景图/特写图本来就不是白底
    （实测把它们的背景判成"不合格"会误导协调者白跑一轮重生成），所以由调用方按
    槽位意图传入；为 False 时仍给出背景数值，但不报"背景不是纯白"这条 issue。
    """
    limits = clean_quality_thresholds(thresholds or DEFAULT_QUALITY_THRESHOLDS)
    Image = _pil()
    np = _numpy()
    if Image is None or np is None or not data:
        return {"available": False, "reason": "缺少 Pillow/numpy 或图像数据为空", "issues": []}
    image = _open(data)
    if image is None:
        return {"available": False, "reason": "图像无法解码", "issues": ["图像无法解码"]}

    width, height = image.size
    mean, minimum = edge_stats(image, np)
    delta, zone_dark_ratio = zone_stats(image, np)
    ratio, background = subject_ratio(image, np)
    sharp = sharpness(image, np)

    is_white_bg = mean >= limits["edge_whiteness"]
    plain_bg = ((background >= PLAIN_BG_LUMA or mean >= PLAIN_BG_LUMA)
                and minimum >= PLAIN_BG_MIN_EDGE)
    # 只有"背景接近白色、且商品没有压到画面边缘"的图才查水印：
    # 场景图右下角本来就暗、特写图主体直接压在角落，都会被误判（首次真机会话实测，
    # 误导协调者白跑一轮重生成、多花 $0.2）
    watermark_checked = plain_bg and zone_dark_ratio < WATERMARK_MAX_DARK_RATIO
    watermark_suspected = bool(watermark_checked and abs(delta) > limits["watermark_zone_delta"])

    issues: list[str] = []
    if not is_white_bg and expect_white_bg:
        # 保留一位小数：实测 249.7 四舍五入成 250 会打出"250 < 250"这种自相矛盾的文案
        issues.append(f"背景不是纯白：边缘平均亮度 {mean:.1f} < {limits['edge_whiteness']:.0f}"
                      "（白底主图要求 #FFFFFF）")
    if watermark_suspected:
        issues.append(f"右下角区域与边缘的背景亮度差 {delta:+.1f}（阈值 "
                      f"{limits['watermark_zone_delta']:.0f}）：疑似水印/角标等叠加物")
    if ratio < 0.03:
        issues.append(f"主体占画面比例过低（{ratio:.2f}）：商品太小")
    if ratio > 0.85:
        issues.append(f"主体占画面比例过高（{ratio:.2f}）：可能没有留白/背景")
    if sharp < 90:
        issues.append(f"画面偏糊（清晰度指标 {sharp:.0f}）")

    return {
        "available": True,
        "width": width,
        "height": height,
        "format": getattr(image, "format", "") or "",
        "bytes": len(data),
        "edge_mean": round(mean, 2),
        "edge_min": round(minimum, 2),
        "is_white_bg": bool(is_white_bg),
        "expect_white_bg": bool(expect_white_bg),
        "watermark_zone_delta": round(delta, 2),
        "watermark_checked": bool(watermark_checked),
        "watermark_suspected": watermark_suspected,
        "subject_ratio": round(ratio, 3),
        "background_luma": round(background, 2),
        "sharpness": round(sharp, 1),
        "issues": issues,
    }


async def inspect_images(images, *, thresholds: dict | None = None,
                         reference: bytes | None = None,
                         expect_white: dict[str, bool] | None = None
                         ) -> tuple[list[dict], list[str]]:
    """对一组生成图逐张体检；给了 `reference`（上传原图字节）时附加身份/复制对比

    `expect_white`：`{slot_id: 是否要求纯白背景}`（来自套图编排里各槽位的 background），
    缺省视为要求纯白（保持旧行为）。

    Returns: `(reports, notes)`；单张失败只进 notes，不影响其余。
    """
    from src.storage.image_export import image_bytes

    limits = clean_quality_thresholds(thresholds)
    white_expectations = expect_white or {}
    reports: list[dict] = []
    notes: list[str] = []
    for index, record in enumerate(images or [], start=1):
        if not isinstance(record, dict):
            notes.append(f"第 {index} 张：非法图像记录")
            continue
        name = str(record.get("slot_id") or record.get("prompt_name") or f"第 {index} 张")
        try:
            data = await image_bytes(record)
        except Exception as exc:  # noqa: BLE001 — 取字节失败只记说明
            notes.append(f"{name}：读取图像数据失败（{type(exc).__name__}）")
            continue
        if not data:
            notes.append(f"{name}：没有可用的图像数据")
            continue
        slot_id = str(record.get("slot_id") or "")
        report = inspect_image(data, thresholds=limits,
                               expect_white_bg=white_expectations.get(slot_id, True))
        report["slot_id"] = slot_id
        report["prompt_name"] = str(record.get("prompt_name") or "")
        # 索引：与入参同序（失败项不进 reports），调用方据此精确回写每张图
        report["index"] = index - 1
        if reference and report.get("available"):
            report["reference_compare"] = compare_to_reference(reference, data, limits)
        reports.append(report)
    return reports, notes


def decode_reference(sources) -> bytes | None:
    """从"上传图"列表里取第一张的字节（用于身份对比）"""
    for source in sources or []:
        text = str(source or "").strip()
        if not text:
            continue
        if text.startswith("data:"):
            text = text.partition(",")[2]
        try:
            return base64.b64decode(text, validate=False)
        except Exception:  # noqa: BLE001
            continue
    return None


def summarize(reports) -> dict[str, Any]:
    """汇总体检结论（给协调者/前端用）

    只汇总**真正可比**的项：背景非浅色的图不做水印判定，场景/特写图不做身份判定
    （首次真机会话实测：误报 4 张身份过低 + 2 张疑似水印，协调者据此白跑一轮重生成）。
    """
    usable = [r for r in (reports or []) if r.get("available")]
    issues: list[str] = []
    for report in usable:
        for issue in report.get("issues") or []:
            issues.append(f"{report.get('slot_id') or report.get('prompt_name') or '图'}：{issue}")
    checked_watermark = [r for r in usable if r.get("watermark_checked", True)]
    checked_white = [r for r in usable if r.get("expect_white_bg", True)]
    return {
        "count": len(usable),
        "white_bg_ok": (all(r.get("is_white_bg") for r in checked_white)
                        if checked_white else True),
        "white_bg_skipped": [r.get("slot_id") for r in usable
                             if not r.get("expect_white_bg", True)],
        "watermark_free": (all(not r.get("watermark_suspected") for r in checked_watermark)
                           if checked_watermark else True),
        "watermark_skipped": [r.get("slot_id") for r in usable
                              if not r.get("watermark_checked", True)],
        "identity_lost": [r.get("slot_id") for r in usable
                          if (r.get("reference_compare") or {}).get("identity_lost")],
        "identity_skipped": [r.get("slot_id") for r in usable
                             if (r.get("reference_compare") or {}).get("identity_compared") is False],
        "near_copy": [r.get("slot_id") for r in usable
                      if (r.get("reference_compare") or {}).get("is_near_copy")],
        "issues": issues,
    }
