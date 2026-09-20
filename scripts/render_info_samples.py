"""信息图样例渲染器 —— 用某个会话的真实商品信息，本地排版出信息图（**零花费**）

用途（用户追问"为什么全是白底商品图"之后新增的能力）：
- 想看排版效果、调字号/配色/版式时，**不必花钱重新生图** —— 直接拿已有会话的商品信息重排；
- 真实出图后，如果只想改文案或版式，也可以只重排文字层。

```
python scripts/render_info_samples.py                       # 用最近一个会话
python scripts/render_info_samples.py --session f01d3ba404584b2c
python scripts/render_info_samples.py --base output/quality-probe/<ts>/main_white.jpg
python scripts/render_info_samples.py --out output/info-samples --size 1200x1200
```

产物：`{输出目录}/{槽位}.jpg` + `manifest.json`（每张的标题/条目/页脚/版式/字体，
以及 blocked 槽位的原因）。**信息图的文字只来自已确认事实**，缺依据的槽位会给出
"要补什么素材"，不会产出编造的图。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.core.config import image_settings  # noqa: E402
from src.core.platforms import slot_kind, slot_layout  # noqa: E402
from src.harness.image_compose import available_fonts, render_info_image  # noqa: E402
from src.harness.set_plan import normalize_set_plan  # noqa: E402
from src.harness.slot_copy import build_slot_copy  # noqa: E402

CHECKPOINT_DIR = ROOT / "data" / "checkpoints"


def latest_checkpoint() -> Path:
    files = sorted(CHECKPOINT_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        raise SystemExit(f"没有找到任何 checkpoint（{CHECKPOINT_DIR}）")
    return files[0]


def load_session(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="本地渲染信息图样例（零花费）")
    parser.add_argument("--session", default="", help="会话 id（对应 data/checkpoints/<id>.json）")
    parser.add_argument("--out", default="output/info-samples", help="输出目录")
    parser.add_argument("--base", default="", help="底图路径（不给则用纯色版式底）")
    parser.add_argument("--size", default="1200x1200", help="输出尺寸，默认 1200x1200")
    parser.add_argument("--all-slots", action="store_true",
                        help="渲染槽位目录里所有信息槽位（默认只渲染该会话 set_plan 里的）")
    parser.add_argument("--user-copy", default="",
                        help='手工补充的事实（JSON），例如 \'{"usage": ["每日 2 粒"], '
                             '"compare": ["升級版 vs 普通版"]}\'——用于成分表/用法等包装正面看不到的信息')
    parser.add_argument("--user-copy-file", default="",
                        help="同上，但从 JSON 文件读取（Windows 命令行里引号容易被 shell 吃掉）")
    parser.add_argument("--typography-file", default="",
                        help='临时覆盖排版样式（JSON），例如 {"font_scale": 1.3, "max_items": 4, '
                             '"brand_color": "#B02020"}——用于不动配置试效果')
    args = parser.parse_args()

    checkpoint = (CHECKPOINT_DIR / f"{args.session}.json") if args.session else latest_checkpoint()
    if not checkpoint.exists():
        raise SystemExit(f"找不到 checkpoint：{checkpoint}")
    session = load_session(checkpoint)
    artifacts = session.get("artifacts") or {}
    identity = artifacts.get("product_identity") or {}
    analysis = artifacts.get("analysis") or {}
    platform = (session.get("task") or {}).get("platform", "")

    # 事实来源优先级与真实出图一致：会话里已补充的 `task.product_facts` → 命令行覆盖
    user_copy: dict = {}
    session_facts = (session.get("task") or {}).get("product_facts")
    if isinstance(session_facts, dict):
        user_copy.update({key: value for key, value in session_facts.items() if value})
    raw_copy = args.user_copy
    if args.user_copy_file:
        override_path = Path(args.user_copy_file)
        if not override_path.exists():
            raise SystemExit(f"--user-copy-file 不存在：{override_path}")
        raw_copy = override_path.read_text(encoding="utf-8")
    if raw_copy:
        try:
            parsed = json.loads(raw_copy)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"--user-copy 必须是 JSON 对象：{exc}")
        if not isinstance(parsed, dict):
            raise SystemExit("--user-copy 必须是 JSON 对象")
        user_copy.update(parsed)

    try:
        width, height = (int(part) for part in args.size.lower().split("x"))
        size = (width, height)
    except Exception:
        raise SystemExit("--size 形如 1200x1200")

    plan = normalize_set_plan(artifacts.get("prompts") or {}, platform=platform) or {}
    slot_ids = [slot["slot_id"] for slot in plan.get("slots", []) if slot.get("kind") == "info"]
    if args.all_slots or not slot_ids:
        from src.core.platforms import load_platforms
        catalog = set()
        for profile in load_platforms().values():
            catalog.update(profile.get("slots") or [])
            catalog.update(profile.get("detail_slots") or [])
        slot_ids = [slot for slot in sorted(catalog) if slot_kind(slot) == "info"]

    base_bytes = Path(args.base).read_bytes() if args.base else None
    out_dir = ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    # 排版样式走 config/image.yaml 的 typography（设置页可改），与真实出图**同一套参数**
    typography = image_settings().get("typography")
    if args.typography_file:
        from src.core.config import clean_typography
        override_path = Path(args.typography_file)
        if not override_path.exists():
            raise SystemExit(f"--typography-file 不存在：{override_path}")
        try:
            raw_override = json.loads(override_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"--typography-file 必须是 JSON：{exc}")
        typography = clean_typography({**typography, **(raw_override or {})})

    print(f"会话    : {checkpoint.name}")
    print(f"平台    : {platform or '(未指定)'}｜底图: {args.base or '纯色版式底'}")
    print(f"字体    : {available_fonts() or '⚠️ 未找到中文字体，无法本地排版'}")
    print(f"排版    : 字号×{typography.get('font_scale')}｜最多 {typography.get('max_items')} 条"
          f"｜主色 {typography.get('brand_color')}｜页脚 {'开' if typography.get('show_footer') else '关'}")
    print(f"身份卡  : {identity.get('brand') or '(空)'} / {identity.get('product_name') or '(空)'}")
    print("-" * 74)

    manifest = {"session": checkpoint.stem, "platform": platform, "size": args.size,
                "typography": typography,
                "generated_at": datetime.now(timezone.utc).isoformat(), "slots": []}
    for slot_id in slot_ids:
        copy = build_slot_copy(slot_id, identity=identity, analysis=analysis, user_copy=user_copy)
        data, meta = render_info_image(base_bytes, copy, layout=slot_layout(slot_id) or None,
                                       size=size, typography=typography)
        entry = {"slot_id": slot_id, "layout": slot_layout(slot_id), "title": copy.get("title"),
                 "items": copy.get("items"), "footer": copy.get("footer"),
                 "blocked": bool(copy.get("blocked")), "reason": copy.get("reason") or "",
                 "file": "", "compose": meta}
        if meta.get("ok") and data:
            path = out_dir / f"{slot_id}.jpg"
            path.write_bytes(data)
            entry["file"] = path.relative_to(ROOT).as_posix()
            print(f"✅ {slot_id:20s} {slot_layout(slot_id):18s} 条目{meta.get('items')} "
                  f"→ {path.name}（{len(data) // 1024}KB）")
        else:
            print(f"⛔ {slot_id:20s} {slot_layout(slot_id):18s} 未生成：{meta.get('reason')}")
        manifest["slots"].append(entry)

    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                           encoding="utf-8")
    ok = sum(1 for slot in manifest["slots"] if slot["file"])
    blocked = sum(1 for slot in manifest["slots"] if slot["blocked"])
    print("-" * 74)
    print(f"完成：{ok} 张已渲染，{blocked} 张因缺素材被拦下 → {out_dir}")
    print(f"清单：{(out_dir / 'manifest.json').relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
