"""本地排版引擎 —— 把文案画到生图底图上，产出真正的"信息图"

## 为什么在本地画字

用户要的套图里有成分图/人群图/功效图/规格图 —— 这些都是**图文版面**。文字若交给生图模型：
1. 中文字形（尤其繁体 + ®）几乎必然糊或写错；
2. 模型会顺手补出包装上根本没有的成分、认证、功效（实测把 `DEFOEBUENA®` 编成 `NUTRIVA®`）。

所以：**模型只出无字底图 + 留白版式，文字由这里用 Pillow + 系统中文字体绘制**
（字形 100% 准确、文案可审可改、可随时重排，不额外花钱）。

## 版式模板

| layout | 用途 | 结构 |
|---|---|---|
| `top_title_bullets` | 卖点图 | 顶部标题条 + 条目列表（编号方块） |
| `benefit_grid` | 功效列举图 | 标题 + 2×N 卡片网格（每格一个功效） |
| `ingredient_list` | 成分配方图 | 标题 + 左侧成分条 + 右侧留白（叠商品） |
| `audience_panel` | 适用人群图 | 标题 + 人物标签行 + 说明条目 |
| `spec_table` | 规格参数图 | 标题 + 键值表（横线分隔） |
| `step_list` | 使用方法图 | 标题 + 步骤编号 + 连接线 |
| `cert_badge` | 资质认证图 | 中央认证徽章条 + 说明 |
| `compare_two_column` | 对比图 | 左右两栏标题 + 条目 |

字体：优先系统中文字体（Windows `msyh.ttc`/`msyhbd.ttc`，Linux `NotoSansCJK`，macOS `PingFang`），
都没有时用 `image_compose.available_fonts()` 报出来，调用方降级（不绘制文字并说明原因）。
"""

from __future__ import annotations

import io
import os
from pathlib import Path
from typing import Any

from src.harness.slot_copy import MAX_ITEMS

# 常见中文字体（按优先级）——不打包字体，只用系统已装的
_FONT_CANDIDATES = (
    r"C:\Windows\Fonts\msyhbd.ttc", r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf", r"C:\Windows\Fonts\msyhbd.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
)
# 版式配色（深色文字 + 品牌色块；可被调用方覆盖）
PALETTE = {
    "ink": (34, 40, 52),          # 正文
    "title": (22, 28, 40),        # 标题
    "accent": (24, 96, 172),      # 主色块（蓝）
    "accent2": (36, 148, 96),     # 次色块（绿）
    "panel": (255, 255, 255),
    "muted": (108, 118, 134),
    "line": (222, 228, 236),
    "card": (250, 252, 255),      # 文字底卡（保证任何底图上都读得清）
}
# 无底图时的版式画布（与生图默认尺寸一致：信息图不该比摄影图糊）
DEFAULT_INFO_CANVAS = (2048, 2048)
# 各版式**能放下的条目数**（按各模板的行高/网格算出来的，不是拍脑袋）
#   top_title_bullets / audience_panel / ingredient_list：行高 0.075H，起点 0.18H → 可放 10 行
#   benefit_grid：2 列，格高上限 0.2H → 最多 3 行 × 2 = 6
#   spec_table：行高 0.1H，起点 0.12H → 可放 8 行
#   step_list：步高 0.14H，起点 0.14H → 可放 5 行
#   cert_badge：徽章 0.16H + 间距 0.025H，起点 0.19H → 可放 4 条
#   compare_two_column：2 列 × 0.22H 行距，起点 0.13H → 可放 6 条
LAYOUT_CAPACITY = {
    "top_title_bullets": 6,
    "benefit_grid": 6,
    "ingredient_list": 6,
    "audience_panel": 6,
    "spec_table": 6,
    "step_list": 5,
    "cert_badge": 4,
    "compare_two_column": 6,
}


def available_fonts() -> list[str]:
    """系统里可用的中文字体路径（空 = 无法本地排版，调用方应降级并说明）"""
    found: list[str] = []
    for path in _FONT_CANDIDATES:
        if os.path.exists(path):
            found.append(path)
    if found:
        return found
    # 兜底：扫 Windows 字体目录里的常见中文族
    fonts_dir = Path(r"C:\Windows\Fonts")
    if fonts_dir.is_dir():
        for name in ("msyh.ttc", "msyhl.ttc", "simsun.ttc", "simkai.ttf", "Deng.ttf"):
            candidate = fonts_dir / name
            if candidate.exists():
                found.append(str(candidate))
    return found


def _font(size: int):
    from PIL import ImageFont
    for path in available_fonts():
        try:
            return ImageFont.truetype(path, size)
        except Exception:  # noqa: BLE001 — 换下一个候选
            continue
    return None


def _fits(draw, text: str, font, max_width: int) -> bool:
    return draw.textlength(text, font=font) <= max_width


def _cut_index(draw, text: str, font, max_width: int) -> int:
    """二分找"能塞进 max_width"的最大字符数（逐字试宽是 O(n²)，中文长句会拖慢渲染）"""
    low, high = 1, len(text)
    best = 0
    while low <= high:
        middle = (low + high) // 2
        if _fits(draw, text[:middle], font, max_width):
            best = middle
            low = middle + 1
        else:
            high = middle - 1
    return best


def _draw_text(draw, xy, text, font, fill, *, max_width: int = 0, anchor: str | None = None,
               clipped: list | None = None, label: str = "", line_h: int = 0,
               min_scale: float = 0.7):
    """画一段文字，**尽量不截断**：整行放不下 → 自动换行（≤2 行）→ 逐档缩字号 → 最后才截断

    为什么要自适应（用户 2026-09-18 实测）：改前是"超宽就按字符截断加省略号"，
    结果 6 条卖点全被截（`…明確標示百`）、页脚把"每粒 0.391g"截成 **"每粒 0.3"**
    —— 那不是审美问题，是**事实被截断成错信息**。

    实现上只用**二分**定位断点（`_cut_index`），避免逐字试宽把 2048 画布的渲染拖成秒级。

    截断时仍往 `clipped` 记一行说明（排版结果可核对），而不是悄悄丢字。
    """
    original = str(text)
    if max_width <= 0 or not original:
        draw.text(xy, original, font=font, fill=fill, anchor=anchor)
        return

    if _fits(draw, original, font, max_width):
        draw.text(xy, original, font=font, fill=fill, anchor=anchor)
        return

    # ① 换行（最多两行；二分断点 + 标点优先）
    if line_h > 0:
        lines = _wrap_lines(draw, original, font, max_width, max_lines=2)
        if lines and all(_fits(draw, line, font, max_width) for line in lines):
            _draw_lines(draw, xy, lines, font, fill, anchor=anchor, line_h=line_h)
            return

    # ② 逐档缩字号（②a 先试整行，②b 再试两行 —— 都不做逐字试宽）
    base_size = int(getattr(font, "size", 0) or 0)
    if base_size:
        floor = max(10, int(base_size * min_scale))
        size = base_size
        while size > floor:
            size = int(size * 0.92)
            smaller = _font(size)
            if smaller is None:
                break
            if _fits(draw, original, smaller, max_width):
                draw.text(xy, original, font=smaller, fill=fill, anchor=anchor)
                return
            if line_h > 0:
                lines = _wrap_lines(draw, original, smaller, max_width, max_lines=2)
                if lines and all(_fits(draw, line, smaller, max_width) for line in lines):
                    _draw_lines(draw, xy, lines, smaller, fill, anchor=anchor,
                                line_h=int(line_h * size / base_size))
                    return

    # ③ 兜底截断（二分定位，并记账）
    cut = _cut_index(draw, original, font, max_width)
    text = original[:cut]
    if text != original:
        if text and _fits(draw, f"{text}…", font, max_width):
            text += "…"
        if clipped is not None:
            clipped.append(f"{label or '文本'}超宽已截断：{original[:24]}…")
    draw.text(xy, text, font=font, fill=fill, anchor=anchor)


def _wrap_lines(draw, text: str, font, max_width: int, *, max_lines: int = 2) -> list[str]:
    """按渲染宽度贪心折行（二分断点；标点/空格处优先断开），最多 `max_lines` 行"""
    lines: list[str] = []
    remaining = str(text)
    while remaining and len(lines) < max_lines:
        if _fits(draw, remaining, font, max_width):
            lines.append(remaining)
            remaining = ""
            break
        cut = _cut_index(draw, remaining, font, max_width)
        if cut <= 0:
            cut = 1
        head, tail = remaining[:cut], remaining[cut:]
        # 优先在标点/空格处断开，读起来不别扭
        for sep in ("，", "。", "；", "、", " ", ",", ";"):
            position = head.rfind(sep)
            if position >= max(2, cut // 3):
                head, tail = head[:position + 1], head[position + 1:] + tail
                break
        lines.append(head)
        remaining = tail.lstrip()
    if remaining:
        # 超过 max_lines：把余下内容并进最后一行（交给缩字号/截断处理）
        lines[-1] = lines[-1] + remaining
    return lines


def _draw_lines(draw, xy, lines, font, fill, *, anchor: str | None, line_h: int) -> None:
    """多行绘制：垂直方向按 `line_h` 依次排（anchor 仅作用于第一行基线）"""
    x, y = xy
    for index, line in enumerate(lines):
        draw.text((x, y + index * line_h), line, font=font, fill=fill, anchor=anchor)


def _rounded(draw, box, radius: int, fill):
    draw.rounded_rectangle(box, radius=radius, fill=fill)


def _wrap_width(image, margin_left: int) -> int:
    return int(image.width - margin_left - image.width * 0.08)


def render_info_image(base_image: bytes | None, copy: dict, *, layout: str = "",
                      size: tuple[int, int] | None = None,
                      brand_color: tuple[int, int, int] | None = None,
                      typography: dict | None = None) -> tuple[bytes, dict]:
    """把文案排版到（可选）底图上，返回 `(JPEG 字节, 说明)`

    - `base_image` 为空时用纯色版式底（也完全可用，适合"只需要信息版面"的图）；
    - **有底图且未指定 `size` 时保持底图分辨率**（实测事故：2048 底图被强制缩到 1200，
      信息图比摄影图糊一截）；
    - `copy["blocked"]=True` 时**不绘制**，直接原样返回并给出原因（事实边界优先）；
    - `typography`（`config/image.yaml → typography`）控制字号倍率/条目上限/配色/页脚；
    - 说明里带 `layout/items/font/truncated` 等，便于产物与前端展示"这张图的字是怎么来的"。
    """
    try:
        from PIL import Image, ImageDraw
    except ImportError:  # pragma: no cover - 本机已装
        return base_image or b"", {"ok": False, "reason": "未安装 Pillow，无法本地排版"}

    layout = (layout or copy.get("layout") or "top_title_bullets").strip()
    if copy.get("blocked"):
        return base_image or b"", {"ok": False, "reason": copy.get("reason") or "文案缺少事实依据",
                                   "blocked": True, "layout": layout}

    from src.core.config import clean_typography

    style = clean_typography(typography)
    clipped: list[str] = []

    if base_image:
        try:
            image = Image.open(io.BytesIO(base_image)).convert("RGB")
        except Exception as exc:  # noqa: BLE001 — 底图坏了就退回纯色版式，不整体失败
            image = Image.new("RGB", size or DEFAULT_INFO_CANVAS, PALETTE["panel"])
            base_image = b""
            _ = exc
    else:
        image = Image.new("RGB", size or DEFAULT_INFO_CANVAS, PALETTE["panel"])
    if size:
        image = image.resize(size)

    font_path = (available_fonts() or [""])[0]
    scale = float(style["font_scale"])
    title_font = _font(max(20, int(image.width // 18 * scale)))
    item_font = _font(max(16, int(image.width // 30 * scale)))
    small_font = _font(max(12, int(image.width // 42 * scale)))
    if title_font is None or item_font is None:
        return base_image or b"", {"ok": False,
                                   "reason": "系统里找不到中文字体，无法本地排版（可安装思源黑体/微软雅黑）",
                                   "layout": layout}

    draw = ImageDraw.Draw(image)

    def draw_text(xy, text, font, fill, **kwargs):
        """统一入口：所有文字绘制都上报截断（排版结果可核对）"""
        _draw_text(draw, xy, text, font, fill, clipped=clipped, **kwargs)

    # 折行高度：标题条内可放两行；列表项内两行不超过行距（否则会压到下一行）
    title_line_h = int((getattr(title_font, "size", 0) or 0) * 1.15)
    item_line_h = int((getattr(item_font, "size", 0) or 0) * 1.05)

    def _rgb(value: str) -> tuple[int, int, int]:
        return (int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16))

    accent = brand_color or _rgb(style["brand_color"])
    secondary = _rgb(style["accent_color"])
    width, height = image.size
    pad = int(width * 0.06)

    title = str(copy.get("title") or "")
    # 条目上限 = 用户设置 ∩ 槽位条目上限 ∩ **该版式能放下的条数**（放不下的会被截断/省略）
    capacity = LAYOUT_CAPACITY.get(layout, MAX_ITEMS)
    limit = min(int(style["max_items"]), MAX_ITEMS, capacity)
    all_items = [str(item) for item in (copy.get("items") or [])]
    items = all_items[:limit]
    if len(all_items) > limit:
        clipped.append(f"条目超过上限（{len(all_items)} > {limit}，"
                       f"版式 {layout} 最多 {capacity} 条），已省略 "
                       f"{len(all_items) - limit} 条")
    footer = str(copy.get("footer") or "") if style["show_footer"] else ""
    drawn_items = 0

    if layout in ("top_title_bullets", "audience_panel", "ingredient_list"):
        header_h = int(height * 0.13)
        _rounded(draw, (0, 0, width, header_h), 0, accent)
        draw_text((pad, header_h // 2), title, title_font, (255, 255, 255),
                   label="标题", max_width=width - 2 * pad, anchor="lm", line_h=title_line_h)
        top = header_h + int(height * 0.05)
        line_h = int(height * 0.075)
        for index, item in enumerate(items):
            y = top + index * line_h
            if y + line_h > height - pad:
                break
            box = int(line_h * 0.52)
            # 文字底卡：底图不一定给足留白（拼多多式设计底），有卡才读得清
            _rounded(draw, (pad - int(width * 0.012), y - int(line_h * 0.06),
                            width - pad + int(width * 0.012),
                            y + max(box, item_line_h * 2) + int(line_h * 0.02)), 14,
                     PALETTE["card"])
            _rounded(draw, (pad, y, pad + box, y + box), 8,
                     accent if index % 2 == 0 else secondary)
            draw_text((pad + box // 2, y + box // 2), str(index + 1), small_font,
                       (255, 255, 255), anchor="mm")
            draw_text((pad + box + int(width * 0.025), y + box // 2), item, item_font,
                       PALETTE["ink"], label="条目",
                       max_width=_wrap_width(image, pad + box + int(width * 0.05)),
                       anchor="lm", line_h=item_line_h)
            drawn_items += 1

    elif layout == "benefit_grid":
        header_h = int(height * 0.13)
        _rounded(draw, (0, 0, width, header_h), 0, accent)
        draw_text((pad, header_h // 2), title, title_font, (255, 255, 255),
                   label="标题", max_width=width - 2 * pad, anchor="lm", line_h=title_line_h)
        columns = 2 if len(items) > 2 else 1
        rows = (len(items) + columns - 1) // columns or 1
        grid_top = header_h + int(height * 0.05)
        cell_w = (width - pad * 2 - (columns - 1) * pad // 2) // columns
        cell_h = min(int(height * 0.2),
                     (height - grid_top - pad - (rows - 1) * int(height * 0.03)) // rows)
        for index, item in enumerate(items):
            row, column = divmod(index, columns)
            x = pad + column * (cell_w + pad // 2)
            y = grid_top + row * (cell_h + int(height * 0.03))
            if y + cell_h > height - pad:
                break
            _rounded(draw, (x, y, x + cell_w, y + cell_h), 18, PALETTE["card"])
            _rounded(draw, (x, y, x + int(cell_w * 0.04), y + cell_h), 6,
                     accent if index % 2 == 0 else secondary)
            draw_text((x + int(cell_w * 0.09), y + int(cell_h * 0.22)), item, item_font,
                       PALETTE["ink"], label="条目", max_width=int(cell_w * 0.82),
                       line_h=item_line_h)
            drawn_items += 1

    elif layout == "spec_table":
        draw_text((pad, pad), title, title_font, PALETTE["title"], label="标题",
                   max_width=width - 2 * pad, line_h=title_line_h)
        y = pad + int(height * 0.12)
        row_h = int(height * 0.1)
        for item in items:
            if y + row_h > height - pad:
                break
            _rounded(draw, (pad - int(width * 0.01), y, width - pad + int(width * 0.01),
                            y + row_h), 10, PALETTE["card"])
            draw.line((pad, y, width - pad, y), fill=PALETTE["line"], width=2)
            label, _, value = item.partition("：")
            draw_text((pad, y + row_h // 2), label, item_font, PALETTE["muted"],
                       max_width=int(width * 0.32), anchor="lm")
            draw_text((pad + int(width * 0.36), y + row_h // 2), value or label,
                       item_font, PALETTE["ink"],
                       max_width=int(width * 0.5), anchor="lm", line_h=item_line_h)
            y += row_h
            drawn_items += 1
        draw.line((pad, y, width - pad, y), fill=PALETTE["line"], width=2)

    elif layout == "step_list":
        draw_text((pad, pad), title, title_font, PALETTE["title"], label="标题",
                   max_width=width - 2 * pad, line_h=title_line_h)
        y = pad + int(height * 0.14)
        step_h = int(height * 0.14)
        circle = int(height * 0.06)
        for index, item in enumerate(items):
            if y + step_h > height - pad:
                break
            center = (pad + circle // 2, y + circle // 2)
            draw.ellipse((center[0] - circle // 2, center[1] - circle // 2,
                          center[0] + circle // 2, center[1] + circle // 2), fill=accent)
            draw_text(center, str(index + 1), small_font, (255, 255, 255), anchor="mm")
            if index < len(items) - 1:
                draw.line((center[0], center[1] + circle // 2,
                           center[0], y + step_h - circle // 4), fill=PALETTE["line"], width=3)
            draw_text((pad + circle + int(width * 0.04), y + circle // 2), item,
                       item_font, PALETTE["ink"],
                       max_width=_wrap_width(image, pad + circle + int(width * 0.05)),
                       anchor="lm", line_h=item_line_h)
            y += step_h
            drawn_items += 1

    elif layout == "cert_badge":
        header_h = int(height * 0.13)
        _rounded(draw, (0, 0, width, header_h), 0, secondary)
        draw_text((pad, header_h // 2), title, title_font, (255, 255, 255),
                   label="标题", max_width=width - 2 * pad, anchor="lm", line_h=title_line_h)
        badge_h = int(height * 0.16)
        y = header_h + int(height * 0.06)
        for item in items:
            if y + badge_h > height - pad:
                break
            _rounded(draw, (pad, y, width - pad, y + badge_h), 20, PALETTE["card"])
            draw.rectangle((pad, y, pad + int(width * 0.012), y + badge_h), fill=secondary)
            draw_text((pad + int(width * 0.06), y + badge_h // 2), item, item_font,
                       PALETTE["ink"], label="条目",
                       max_width=width - 2 * pad - int(width * 0.08), anchor="lm",
                       line_h=item_line_h)
            y += badge_h + int(height * 0.025)
            drawn_items += 1

    else:  # compare_two_column 及其他未知模板 → 走两栏
        draw_text((pad, pad), title, title_font, PALETTE["title"],
                   label="标题", max_width=width - 2 * pad, line_h=title_line_h)
        column_w = (width - pad * 3) // 2
        top = pad + int(height * 0.13)
        for index, item in enumerate(items[:capacity]):
            column = index % 2
            x = pad + column * (column_w + pad)
            y = top + (index // 2) * int(height * 0.22)
            _rounded(draw, (x, y, x + column_w, y + int(height * 0.18)), 16,
                     PALETTE["card"] if column == 0 else (247, 252, 249))
            draw_text((x + int(column_w * 0.08), y + int(height * 0.045)), item, item_font,
                       PALETTE["ink"], label="条目", max_width=int(column_w * 0.84),
                       line_h=item_line_h)
            drawn_items += 1

    if footer:
        draw_text((pad, height - pad // 2), footer, small_font, PALETTE["muted"],
                   max_width=width - 2 * pad, anchor="lm")

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=92, optimize=True)
    return buffer.getvalue(), {
        "ok": True, "layout": layout, "items": drawn_items,
        "font": os.path.basename(font_path), "size": f"{width}x{height}",
        "title": title, "footer": footer,
        # 排版参数与"哪些文字被截断/省略"：让用户能据此调字号、缩短文案
        "typography": style,
        "truncated": clipped,
    }


def compose_info_image(base_image: bytes | None, copy: dict, slot: dict | None = None,
                       *, size: tuple[int, int] | None = None,
                       typography: dict | None = None) -> tuple[bytes | None, dict]:
    """按槽位信息取模板并排版（`slot` 里的 `layout`/`aspect` 生效；`typography` 控制样式）

    **不再按 1:1 把底图缩到 1200×1200**：有底图就保持底图分辨率（信息图与摄影图同样清晰）；
    只有"没有底图"时才用 `DEFAULT_INFO_CANVAS`。显式传 `size` 时仍按 `size` 缩放（脚本用）。
    """
    slot = slot if isinstance(slot, dict) else {}
    layout = str(slot.get("layout") or "").strip()
    return render_info_image(base_image, copy, layout=layout, size=size, typography=typography)


def layout_options() -> list[str]:
    """可用版式模板（前端下拉/文档用）"""
    return ["top_title_bullets", "benefit_grid", "ingredient_list", "audience_panel",
            "spec_table", "step_list", "cert_badge", "compare_two_column"]


def palette_preview() -> dict[str, list[int]]:
    return {key: list(value) for key, value in PALETTE.items()}


def _unused(_: Any) -> None:  # pragma: no cover - 保留类型提示用
    return None
