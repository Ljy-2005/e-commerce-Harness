"""审美锚点工具 —— 把"就要这种感觉"的照片转成**文字判词**（写进风格档案库）

用户 2026-09-18 指定「风格词库」时的原话是"导入照片 → 命名 → 让专门的 agent 分析这组照片的风格"。
界面版在「风格词库」页里（`data/style_library/`，词条 + 锚点一起产出）；这个脚本是无界面/自动化的
同一条链路，用来把**已有图片文件**直接转成 `config/style_library.yaml` 的 `anchors:` 条目
（审核优化员与成图审查员的打分准绳）。

```
# 1) 零成本：只打印可手填的锚点模板（不调用任何模型）
python scripts/style_anchor.py --print

# 2) 用一张参考图生成判词（**会花 1 次视觉调用**，默认只打印、不写文件）
python scripts/style_anchor.py --images 参考.jpg --name "冷白实验室感"

# 3) 确认满意后写入 config/style_library.yaml（自动备份 .bak）
python scripts/style_anchor.py --images 参考.jpg --name "冷白实验室感" --append

# 4) 只看会送什么、不花钱
python scripts/style_anchor.py --images 参考.jpg --name "X" --dry-run
```

安全边界（与「风格词库」一致，别改）：
- 照片**只用于风格分析**，不落 `data/inputs/`、不作生图参考图，脚本也不复制/保存图片；
- 判词由「风格档案员」产出后经 `sanitize_entry()` 清洗：品牌/成分/认证/色值会被剔除并**如实打印**；
- `--append` 只改 `config/style_library.yaml` 的 `anchors:` 段（先备份），不碰其它内容。
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.harness.style_library import STYLE_LIBRARY_REL, sanitize_entry  # noqa: E402

MAX_IMAGES = 6


def _yaml_quote(text: str) -> str:
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _slug(text: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "_", str(text or "")).strip("_")
    return (cleaned or "anchor")[:24].lower()


def render_anchor_yaml(payload: dict, name: str, note: str = "") -> str:
    """把锚点渲染成 YAML 片段（**只输出判词相关内容**，不含任何品牌/色值）"""
    anchor_id = f"anchor_{_slug(name)}"
    lines = [
        "anchors:",
        f"  - id: {anchor_id}",
        f"    name: {_yaml_quote(name)}",
        f"    source: {_yaml_quote(note or '用户锚点')}",
    ]
    verdict = payload.get("taste_verdict") or payload.get("style_words") or payload.get("summary") or ""
    lines.append("    taste_verdict: >-")
    lines.append(f"      {verdict or '（未取到判词：请手填「这张图的感觉」）'}")
    for key, label in (("reward_points", "要有的"), ("avoid_points", "要避免的")):
        items = payload.get(key) or []
        if items:
            lines.append(f"    {key}: [{', '.join(_yaml_quote(item) for item in items)}]")
    applies = payload.get("applies_to") or {}
    kinds = applies.get("kinds") if isinstance(applies, dict) else None
    categories = (applies or {}).get("categories") if isinstance(applies, dict) else None
    lines.append("    applies_to:")
    lines.append(f"      kinds: [{', '.join(kinds or ['photo'])}]")
    lines.append(f"      categories: [{', '.join(_yaml_quote(item) for item in (categories or []))}]")
    return "\n".join(lines)


def template() -> str:
    """零成本模板（`--print`）：字段与 `--append` 写入的结构完全一致"""
    return """# 把下面这段贴进 config/style_library.yaml 的 anchors: 段（或直接跑本脚本 --append）
anchors:
  - id: anchor_我的锚点
    name: "我的锚点"
    source: "用户锚点（手写）"
    taste_verdict: >-
      用一句白话写清"这张图给你的感觉"：明度偏亮还是偏暗、留白多不多、
      光平不平（有没有投影）、饱和度倾向、边缘高光的形态、色系几种。
      **不要写品牌/成分/认证/色值**（会被剔除）。
    reward_points: ["要有的1", "要有的2"]
    avoid_points: ["要避免的1", "要避免的2"]
    applies_to:
      kinds: [photo]        # photo=摄影槽位｜info=信息图底图；空=都适用
      categories: []        # 品类关键词（空=任何品类都参与打分）
"""


async def analyze(images: list[Path], name: str, hint: str) -> dict:
    """调「风格档案员」做一次风格分析（**会产生 1 次视觉调用**）"""
    from src.providers import get_provider_registry
    from src.agents.style_archivist import StyleArchivistAgent
    from src.harness.vision_payload import sniff_mime

    provider, model = get_provider_registry().resolve(["vision"], "风格档案员")
    if provider is None or getattr(provider, "name", "") == "mock":
        raise SystemExit("视觉 Provider 未就绪（Mock 或未配置 Key）——"
                         "请在设置页配置视觉模型，或先 --print 手写判词（零成本）")
    agent = StyleArchivistAgent(provider=provider)
    if model:
        agent.model_name = model
    payloads = [base64.b64encode(path.read_bytes()).decode() for path in images[:MAX_IMAGES]]
    session = {
        "tenant_id": "default", "artifacts": {}, "turn_count": 0,
        "task": {"reference_images": payloads, "style_name": name, "hint": hint,
                 "applies_to": {"kinds": ["photo"]}},
    }
    _ = sniff_mime  # 图片 MIME 由 vision_payload 内部嗅探（这里只做依赖可见性）
    return await agent.execute("把这组照片拆成风格档案与审美判词", session)


def append_to_library(block: str) -> Path:
    """把锚点块写进 config/style_library.yaml 的 `anchors:` 段（**追加在段尾**，先备份）

    为什么不用 `yaml.safe_dump` 重写整个文件：那会把配置文件里的注释（=文档）全部清掉，
    本仓库已有教训。这里只做**行级插入**，其余内容一字不动。
    """
    path = ROOT / STYLE_LIBRARY_REL
    if not path.exists():
        raise SystemExit(f"找不到 {STYLE_LIBRARY_REL}")
    text = path.read_text(encoding="utf-8")
    body = block.splitlines()
    if body and body[0].strip() == "anchors:":
        body = body[1:]                                # 去掉自带的一级键

    if re.search(r"(?m)^anchors:\s*\[\]\s*$", text):
        # 占位空列表 → 换成真实锚点（保留原缩进层级）
        updated = re.sub(r"(?m)^anchors:\s*\[\]\s*$", "anchors:\n" + "\n".join(body), text)
    elif re.search(r"(?m)^anchors:\s*$", text):
        lines = text.splitlines()
        start = next(index for index, line in enumerate(lines)
                     if re.match(r"^anchors:\s*$", line)) + 1
        end = start
        while end < len(lines):
            line = lines[end]
            if not line.strip() or line.startswith((" ", "\t")) or line.lstrip().startswith("#"):
                end += 1
                continue
            break
        updated = "\n".join(lines[:end] + body + lines[end:]) + "\n"
    else:
        updated = text.rstrip("\n") + "\n\n" + block.strip() + "\n"

    backup = path.with_suffix(path.suffix + ".bak")
    backup.write_text(text, encoding="utf-8")
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(updated, encoding="utf-8")
    tmp.replace(path)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="把参考图转成审美锚点判词（可能花钱）")
    parser.add_argument("--images", nargs="*", default=[], help=f"参考图（≤{MAX_IMAGES} 张）")
    parser.add_argument("--name", default="", help="给这个锚点起个名字（也是它在界面上的标识）")
    parser.add_argument("--hint", default="", help="侧重说明（如：更冷、更干净一点）")
    parser.add_argument("--print", action="store_true", dest="print_template",
                        help="零成本：只打印可手填的锚点模板")
    parser.add_argument("--append", action="store_true",
                        help="写入 config/style_library.yaml（默认只打印，不写文件）")
    parser.add_argument("--dry-run", action="store_true", help="只打印将要做什么，不调用模型")
    args = parser.parse_args()

    if args.print_template or not args.images:
        print(template())
        if not args.print_template:
            print("（未给 --images：以上为零成本模板；给图会调 1 次视觉模型生成判词）")
        return 0

    images = [Path(item) for item in args.images[:MAX_IMAGES]]
    missing = [str(path) for path in images if not path.exists()]
    if missing:
        raise SystemExit(f"图片不存在：{', '.join(missing)}")
    name = args.name.strip()
    if not name:
        raise SystemExit("请用 --name 给这个锚点起个名字（例：--name \"冷白实验室感\"）")

    print("=" * 78)
    print(f"参考图  : {len(images)} 张 → {[path.name for path in images]}")
    print(f"锚点名  : {name}")
    print("花费    : 1 次视觉调用（按 token 计费；未标定价格时不显示金额）")
    print("落盘    : 照片**不会**被保存，也不会成为生图参考图（只用于本次风格分析）")
    print("=" * 78)
    if args.dry_run:
        print("（--dry-run：未调用模型，未花钱）")
        return 0

    result = asyncio.run(analyze(images, name, args.hint))
    if isinstance(result, dict) and result.get("error"):
        print(f"❌ 分析失败：{result['error']}")
        usage = result.get("usage") or {}
        if usage.get("maybe_billed"):
            print("   ⚠️ 这次失败**可能已经产生费用**（上游可能已受理），请以控制台账单为准")
        return 2

    cleaned, removed = sanitize_entry(result)
    usage = result.get("usage") or {}
    payload = {**cleaned, "applies_to": {"kinds": ["photo"], "categories": []}}
    note = f"用户锚点（视觉转写 · {usage.get('model') or 'vision'}）"
    block = render_anchor_yaml(payload, name, note=note)

    print("用量    : "
          f"{usage.get('calls', 0)} 次调用 · {usage.get('images', 0)} 张图 · "
          f"{usage.get('elapsed_ms', 0)}ms · {usage.get('model') or '—'}")
    if removed:
        print(f"已剔除  : {len(removed)} 处（品牌/成分/认证/色值，不进判词）")
        for item in removed[:6]:
            print(f"   · {item}")
    if payload.get("name_suggestions"):
        print("候选名  : " + "、".join(payload["name_suggestions"]))
    print("-" * 78)
    print(block)
    print("-" * 78)

    if args.append:
        path = append_to_library(block)
        print(f"✅ 已写入 {path.relative_to(ROOT)}（原文件备份为 .yaml.bak）")
        print("   生效方式：重新导入/重跑提示词阶段即会引用；也可 "
              "python scripts/style_preview.py --check 复核")
    else:
        print("（默认只打印，未写文件；确认无误后加 --append 写入 config/style_library.yaml）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
