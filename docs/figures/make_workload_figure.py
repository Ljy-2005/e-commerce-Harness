# -*- coding: utf-8 -*-
"""项目工作量分解图生成器。

仿照学位论文「系统功能结构图 / 系统模块设计图」版式绘制两张图：

* ``工作量分解图-黑白版.png``  —— 仿图 4-1（黑白细线、宋体、直角）
* ``工作量分解图-彩色版.png``  —— 仿图 4-2（彩色圆角、白底彩边、微软雅黑）

同时导出 ``项目工作量清单.md`` / ``项目工作量清单.csv``。

改数字只需要改下面的 ``GROUPS``，然后重跑：

    python docs/figures/make_workload_figure.py
"""
from __future__ import annotations

import csv
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.font_manager import FontProperties  # noqa: E402
from matplotlib.patches import FancyBboxPatch, Rectangle  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))

ROOT_TITLE = "电商商品图生成多智能体系统"
CAPTION_BW = "图 4-3  项目工作量分解图（单位：人天）"
CAPTION_COLOR = "图 4-4  项目工作量分解图（单位：人天）"

# --------------------------------------------------------------------------
# 工作量数据：(工作项, 人天, 交付物 / 规模依据)
# --------------------------------------------------------------------------
GROUPS: list[dict] = [
    {
        "name": "需求与总体设计",
        "color": "#6E9C6B",
        "items": [
            ("需求分析与用例建模", 3, "需求规格、用例图与业务流程，22 篇设计文档"),
            ("系统架构与分层设计", 3, "六层架构（智能体/模型/治理/编排/服务/前端）与接口契约"),
            ("数据模型与接口契约", 2, "会话/任务/事件/成本四类表结构，OpenAPI 快照"),
            ("技术选型与可行性验证", 2, "FastAPI + React + SQLite 选型对比与最小原型"),
        ],
    },
    {
        "name": "智能体与模型接入",
        "color": "#4F86C6",
        "items": [
            ("九大智能体实现", 6, "需求分析/品类/风格/提示词/合规/审查等 9 个智能体，1,202 行"),
            ("七家模型接入适配", 6, "OpenAI/DeepSeek/Anthropic/Seedream/Qwen/FLUX/Mock，1,434 行"),
            ("能力路由与降级链", 3, "按文生图/图生图/文本/视觉四类能力路由，三级降级"),
            ("提示词工程与调优", 4, "7 套系统提示词模板（205 行）与迭代评测"),
            ("智能体记忆与工具链", 2, "JSONL 长期记忆、租户隔离召回、工具调用编排"),
            ("双模式联调与验证", 2, "Mock 全链路确定性回归 + 真实 API 灰度验证"),
        ],
    },
    {
        "name": "编排与治理层",
        "color": "#E5B93C",
        "items": [
            ("模板语言与求值引擎", 4, "YAML 模板 DSL + 安全表达式求值，7 套内置模板"),
            ("事件溯源与断点续跑", 4, "SQLite 事件表 + 检查点，任意步骤恢复，2,130 行"),
            ("人工审批与时限矩阵", 4, "自动/人工双模式、审批时限矩阵、超时升级"),
            ("批量调度与并发治理", 4, "批量任务队列、并发上限、成本闸门与取消保护"),
            ("熔断限流与成本审计", 4, "熔断/令牌桶/重试/超时四件套 + 成本计量与审计日志，1,618 行"),
        ],
    },
    {
        "name": "服务端与前端应用",
        "color": "#E08A45",
        "items": [
            ("服务接口与实时推送", 6, "REST 25+ 端点 + WebSocket 事件流，1,796 行"),
            ("前端工作台十二页", 9, "会话、批量、模板、看板等 12 个页面，2,753 行"),
            ("组件库与设计系统", 5, "玻璃拟态设计语言与 12 个复用组件，2,941 行样式与逻辑"),
            ("密钥与模型设置管理", 4, "多厂商密钥、自定义端点与模型目录、校验与脱敏"),
            ("看板报表与批量任务", 3, "成本/成功率/审批统计看板与批量调度界面"),
            ("一键启动与部署脚本", 2, "start.py 一键启动、Docker 与 CI 流水线"),
        ],
    },
    {
        "name": "测试与交付",
        "color": "#A35D57",
        "items": [
            ("单元与集成测试", 8, "后端 608 例 / 8,388 行，覆盖率 83%"),
            ("端到端冒烟与回归", 4, "7 场景 E2E 冒烟脚本 + 真实 API 回归套件"),
            ("性能与资源治理测试", 3, "并发、限流、检查点与资源回收专项测试"),
            ("持续集成与容器交付", 2, "GitHub Actions 三作业流水线 + Docker 镜像"),
            ("文档与运维手册", 3, "22 篇设计/模块文档（3,544 行）与交接指南"),
        ],
    },
]

# --------------------------------------------------------------------------
# 版式常量（单位：英寸）
# --------------------------------------------------------------------------
PITCH = 0.86          # 叶节点列间距
LEAF_W = 0.62         # 叶节点框宽
LEAF_H = 2.80         # 叶节点框高
GROUP_GAP = 0.55      # 组间空隙
MAX_ROW = 5           # 单行最多叶节点数，超出则折成两行

ROOT_H, ROOT_W = 1.05, 5.60
GROUP_H = 0.78
Y_TOP = 10.55
ROOT_CY = Y_TOP - ROOT_H / 2 - 0.10
BUS_Y = ROOT_CY - ROOT_H / 2 - 0.45
GROUP_CY = BUS_Y - 0.62
BUS0_Y = GROUP_CY - GROUP_H / 2 - 0.50
LEAF0_TOP = BUS0_Y - 0.13
BUS1_Y = LEAF0_TOP - LEAF_H - 0.32
LEAF1_TOP = BUS1_Y - 0.13
CAPTION_Y = (LEAF1_TOP - LEAF_H) / 2 if True else 0.0

MARGIN_X = 0.70


def _split_rows(items: list) -> list[list[int]]:
    n = len(items)
    if n <= MAX_ROW:
        return [list(range(n))]
    half = (n + 1) // 2
    return [list(range(half)), list(range(half, n))]


def _layout(groups):
    """为每个组计算列区间与行划分，返回 (布局, 画布宽)。"""
    cursor = MARGIN_X
    for g in groups:
        rows = _split_rows(g["items"])
        ncols = max(len(r) for r in rows)
        span = ncols * PITCH
        g["_rows"] = rows
        g["_x0"] = cursor
        g["_span"] = span
        g["_cx"] = cursor + span / 2
        # 每行叶节点的中心 x
        g["_centers"] = []
        for r in rows:
            start = cursor + (span - len(r) * PITCH) / 2
            g["_centers"].append([start + i * PITCH + PITCH / 2 for i in range(len(r))])
        cursor += span + GROUP_GAP
    width = cursor - GROUP_GAP + MARGIN_X
    return width


def _box(ax, cx, cy, w, h, *, color_style, fc, ec, lw, rounded):
    if rounded:
        p = FancyBboxPatch(
            (cx - w / 2, cy - h / 2), w, h,
            boxstyle="round,pad=0,rounding_size=0.10",
            linewidth=lw, edgecolor=ec, facecolor=fc,
            mutation_aspect=1.0, zorder=3,
        )
    else:
        p = Rectangle((cx - w / 2, cy - h / 2), w, h,
                      linewidth=lw, edgecolor=ec, facecolor=fc, zorder=3)
    ax.add_patch(p)


def _line(ax, x1, y1, x2, y2, color, lw):
    ax.plot([x1, x2], [y1, y2], color=color, lw=lw, solid_capstyle="butt", zorder=2)


def _text_w(s: str, fontsize: float) -> float:
    """粗略估算文字宽度（英寸）：汉字按 1 em，半角按 0.55 em。"""
    em = sum(1.0 if ord(c) > 0x2E80 else 0.55 for c in s)
    return em * fontsize / 72.0


def draw(color_style: bool, outfile: str) -> None:
    groups = [dict(g) for g in GROUPS]
    width = _layout(groups)
    height = Y_TOP
    fig = plt.figure(figsize=(width, height), dpi=140)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, width)
    ax.set_ylim(0, height)
    ax.axis("off")

    if color_style:
        fam = FontProperties(family="Microsoft YaHei")
        ink = "#1F1F1F"
        root_fc, root_ec, root_tc = "#C0504D", "#B04946", "#FFFFFF"
        leaf_ec_lw, group_lw, stem_lw = 1.6, 1.2, 1.4
        bus_color = "#C0504D"
        caption = CAPTION_COLOR
        rounded = True
    else:
        fam = FontProperties(family="SimSun")
        ink = "#000000"
        root_fc, root_ec, root_tc = "white", "black", "black"
        leaf_ec_lw, group_lw, stem_lw = 0.9, 0.9, 0.9
        bus_color = "black"
        caption = CAPTION_BW
        rounded = False

    total = sum(d for g in groups for _, d, _ in g["items"])

    # --- 根节点 -----------------------------------------------------------
    root_cx = width / 2
    _box(ax, root_cx, ROOT_CY, ROOT_W, ROOT_H,
         color_style=color_style, fc=root_fc, ec=root_ec,
         lw=1.6 if color_style else 1.1, rounded=rounded)
    ax.text(root_cx, ROOT_CY + ROOT_H * 0.16, ROOT_TITLE, ha="center", va="center",
            fontproperties=fam, fontsize=19, color=root_tc,
            fontweight="bold" if color_style else "normal", zorder=4)
    ax.text(root_cx, ROOT_CY - ROOT_H * 0.26, f"合计 {total} 人天", ha="center", va="center",
            fontproperties=fam, fontsize=11.5, color=root_tc, zorder=4)

    # --- 根到各组的分发线 -------------------------------------------------
    first_cx = groups[0]["_cx"]
    last_cx = groups[-1]["_cx"]
    _line(ax, root_cx, ROOT_CY - ROOT_H / 2, root_cx, BUS_Y, bus_color, stem_lw)
    _line(ax, first_cx, BUS_Y, last_cx, BUS_Y, bus_color, stem_lw)

    # --- 各组 -------------------------------------------------------------
    for g in groups:
        gc = g["color"] if color_style else "black"
        _line(ax, g["_cx"], BUS_Y, g["_cx"], GROUP_CY + GROUP_H / 2, gc, stem_lw)
        sub = sum(d for _, d, _ in g["items"])
        gw = max(
            g["_span"] * 0.62,
            _text_w(g["name"], 15) + 0.34,
            _text_w(f"小计 {sub} 人天", 10) + 0.34,
            1.55,
        )
        gw = min(gw, g["_span"] + GROUP_GAP * 0.55)
        if color_style:
            gfc, gtc, gec = g["color"], ("#3A3226" if g["color"] == "#E5B93C" else "#FFFFFF"), g["color"]
        else:
            gfc, gtc, gec = "white", ink, "black"
        _box(ax, g["_cx"], GROUP_CY, gw, GROUP_H,
             color_style=color_style, fc=gfc, ec=gec,
             lw=group_lw, rounded=rounded)
        ax.text(g["_cx"], GROUP_CY + GROUP_H * 0.16, g["name"], ha="center", va="center",
                fontproperties=fam, fontsize=15, color=gtc,
                fontweight="bold" if color_style else "normal", zorder=4)
        ax.text(g["_cx"], GROUP_CY - GROUP_H * 0.24, f"小计 {sub} 人天", ha="center", va="center",
                fontproperties=fam, fontsize=10, color=gtc, zorder=4)

        # 主干：组框底部 -> 第一行总线（第二行再往下续一段）
        _line(ax, g["_cx"], GROUP_CY - GROUP_H / 2, g["_cx"], BUS0_Y, gc, stem_lw)

        for ri, row in enumerate(g["_rows"]):
            busy = BUS0_Y if ri == 0 else BUS1_Y
            top = LEAF0_TOP if ri == 0 else LEAF1_TOP
            centers = g["_centers"][ri]
            if ri > 0:
                _line(ax, g["_cx"], BUS0_Y, g["_cx"], BUS1_Y, gc, stem_lw)
            _line(ax, centers[0], busy, centers[-1], busy, gc, stem_lw)
            for ci, idx in enumerate(row):
                name, days, _note = g["items"][idx]
                x = centers[ci]
                _line(ax, x, busy, x, top, gc, stem_lw)
                _box(ax, x, top - LEAF_H / 2, LEAF_W, LEAF_H,
                     color_style=color_style, fc="white", ec=gc,
                     lw=leaf_ec_lw, rounded=rounded)
                ax.text(x, top - 0.16, "\n".join(name), ha="center", va="top",
                        fontproperties=fam, fontsize=12.5, color=ink,
                        linespacing=1.12, zorder=4)
                ax.text(x, top - LEAF_H + 0.16, f"{days} 人天", ha="center", va="bottom",
                        fontproperties=fam, fontsize=9.5, color=gc, zorder=4)

    ax.text(width / 2, CAPTION_Y, caption, ha="center", va="center",
            fontproperties=fam, fontsize=15.5, color=ink,
            fontweight="bold" if color_style else "normal")

    fig.savefig(outfile, dpi=140, facecolor="white")
    plt.close(fig)
    print("saved", outfile, f"{width:.2f}x{height:.2f} in")


def export_tables() -> None:
    total = sum(d for g in GROUPS for _, d, _ in g["items"])
    md = [
        "# 项目工作量清单",
        "",
        f"项目：{ROOT_TITLE}　　单位：人天　　合计：**{total} 人天**",
        "",
        "| 工作包 | 工作项 | 交付物 / 规模依据 | 人天 |",
        "| --- | --- | --- | ---: |",
    ]
    for g in GROUPS:
        sub = sum(d for _, d, _ in g["items"])
        md.append(f"| **{g['name']}** | | *小计* | **{sub}** |")
        for name, days, note in g["items"]:
            md.append(f"| | {name} | {note} | {days} |")
    md += [f"| **合计** | | | **{total}** |", ""]
    with open(os.path.join(HERE, "项目工作量清单.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md))

    with open(os.path.join(HERE, "项目工作量清单.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["工作包", "小计", "工作项", "人天", "交付物/规模依据"])
        for g in GROUPS:
            sub = sum(d for _, d, _ in g["items"])
            for name, days, note in g["items"]:
                w.writerow([g["name"], sub, name, days, note])
        w.writerow(["合计", total, "", "", ""])
    print("saved 项目工作量清单.md / .csv", f"total={total} 人天")


if __name__ == "__main__":
    draw(color_style=False, outfile=os.path.join(HERE, "工作量分解图-黑白版.png"))
    draw(color_style=True, outfile=os.path.join(HERE, "工作量分解图-彩色版.png"))
    export_tables()
