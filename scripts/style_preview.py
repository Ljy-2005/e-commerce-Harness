"""风格档案库预览 —— **零成本**看清"哪一张会用哪条设计档案"

用户对花钱的纪律是"先看清单再决定要不要花钱"（每一轮真实调用都要单独批准）。这个脚本
把检索结果与**实际会注入提示词的话术**原样打印出来，不调用任何模型、不写任何文件：

```
# 某个品类在某个平台上，逐槽位命中了哪条档案（并打印注入块）
python scripts/style_preview.py --platform pinduoduo --category 保健品

# 只看指定的几个槽位
python scripts/style_preview.py --platform amazon --slots main_white,main_detail

# 全平台自检：覆盖率（每个槽位都至少 1 条）+ 与槽位契约的冲突（应为 0）
python scripts/style_preview.py --check

# 机器可读（给别的脚本/前端用）
python scripts/style_preview.py --platform taobao --category 美妆 --json
```

退出码：0 正常；1 自检发现"有槽位没命中档案"或"档案与槽位契约冲突"。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.core.platforms import (  # noqa: E402
    background_policy,
    list_platforms,
    platform_detail_slots,
    platform_slots,
    slot_kind,
    slot_label,
)
from src.harness import style_library as sl  # noqa: E402


def _slot_dicts(platform: str, subset: list[str] | None = None) -> list[dict]:
    ids = [str(s) for s in platform_slots(platform)] + \
          [str(s) for s in platform_detail_slots(platform)]
    if subset:
        unknown = [slot for slot in subset if slot not in ids]
        if unknown:
            print(f"⚠️ 这些槽位不在 {platform} 的平台档案里：{unknown}（仍会尝试检索）")
        ids = subset
    return [{"slot_id": slot_id, "kind": slot_kind(slot_id)} for slot_id in ids]


def print_library(lib: dict) -> None:
    stats = sl.library_stats(lib)
    print(f"档案库    : {stats['path']}（{'存在' if stats['exists'] else '不存在'}）")
    print(f"可用档案  : {stats['entries']} 条｜锚点 {stats['anchors']} 条"
          f"｜未启用/被丢弃 {stats['dropped']} 条")
    print(f"生效策略  : {'开' if stats['enabled'] else '关'}"
          f"｜整套最多 {stats['max_entries']} 种风格｜锚点最多 {stats['max_anchors']} 条")
    if lib.get("dropped"):
        print("被丢弃的条目（**不会进提示词**）：")
        for item in lib["dropped"]:
            print(f"  - {item['id']}：{'；'.join(item['reasons'])}")
    for anchor in lib.get("anchors") or []:
        print(f"  锚点「{anchor['name']}」：{anchor.get('taste_verdict', '')[:60]}")


def run_preview(args) -> int:
    lib = sl.load_library()
    print("=" * 78)
    print_library(lib)
    print("=" * 78)
    slots = _slot_dicts(args.platform, args.slots)
    analysis = {"category": args.category} if args.category else {}
    result = sl.select_by_slot(slots, analysis=analysis, platform=args.platform,
                               policy=args.policy or None)
    print(f"平台      : {args.platform}（背景策略：{background_policy(args.platform)}）")
    print(f"品类      : {args.category or '（未给：只命中通用档案）'}")
    print("-" * 78)
    for slot in slots:
        slot_id = slot["slot_id"]
        picked = result["slots"].get(slot_id) or []
        kind = "信息图底图" if slot["kind"] == "info" else "纯摄影"
        names = "、".join(f"「{entry['name']}」" for entry in picked) or "（无）"
        print(f"{slot_id:<20} {slot_label(slot_id):<10} {kind}  →  {names}")
    print("-" * 78)
    block = sl.render_slot_block(result)
    print(block or "（没有可注入的档案）")
    anchors = sl.render_anchor_block(result.get("anchors"))
    if anchors:
        print()
        print(anchors)
    if result.get("notes"):
        print("-" * 78)
        for note in result["notes"]:
            print(f"提示：{note}")
    if args.json:
        payload = {"summary": sl.usage_snapshot(result), "block": block}
        out = ROOT / "output" / "style-preview.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nJSON 已写入 {out.relative_to(ROOT)}")
    return 0


def run_check() -> int:
    """全平台自检：每个槽位至少 1 条档案 + 与槽位 forbid 零冲突

    区分两种冲突（用户 2026-09-20）：
    - **内置档案**冲突 = 配置错误 → 自检失败（退出码 1）；
    - **用户词条**冲突 = 你在"风格词库"里导入的参考套图做法与本平台槽位契约不一致
      （例：浅粉渐层 vs 首图必须纯白）→ 只作**警告**打印，不改变退出码；
      注入时仍然"槽位契约优先"，但你应该知道这件事。
    """
    lib = sl.load_library()
    print("=" * 78)
    print_library(lib)
    print("=" * 78)
    problems = 0
    warnings = 0
    for item in list_platforms():
        slug = item["slug"]
        slots = _slot_dicts(slug)
        result = sl.select_by_slot(slots, analysis={"category": "保健品"}, platform=slug)
        empty = [slot["slot_id"] for slot in slots if not result["slots"].get(slot["slot_id"])]
        conflicts: list[dict] = []
        for entry in lib["entries"]:
            applicable = sl.applicable_slots(entry, slug, haystack="保健品 3C数码 美妆")
            for conflict in sl.entry_conflicts(entry, applicable):
                conflicts.append({"entry": entry["id"], "builtin": entry.get("source") != "用户导入",
                                  **conflict})
        hard = [item for item in conflicts if item["builtin"]]
        soft = [item for item in conflicts if not item["builtin"]]
        per_slot = {len(picked) for picked in result["slots"].values()}
        status = "✅" if not empty and not hard else "❌"
        print(f"{status} {slug:<12} 槽位 {len(slots):>2}｜命中 "
              f"{sum(1 for picked in result['slots'].values() if picked):>2}"
              f"｜未命中 {len(empty)}｜每张注入 {sorted(per_slot)} 条"
              f"｜内置冲突 {len(hard)}｜用户词条冲突 {len(soft)}")
        for slot_id in empty:
            print(f"     · 未命中：{slot_id}（{slot_label(slot_id)}）")
        for conflict in hard[:6]:
            print(f"     · 冲突：{conflict['entry']} × {conflict['slot_id']} → {conflict['rule']}")
        for conflict in soft[:6]:
            print(f"     ⚠️ 你的词条 {conflict['entry']} 与 {conflict['slot_id']} 的契约不一致："
                  f"{conflict['rule']}（注入时槽位契约优先）")
        problems += len(empty) + len(hard)
        warnings += len(soft)
    # 用户词条的套图结构覆盖差（"我给的是一套图片"—— 看它覆盖了本平台哪几张）
    for coverage in sl.sequence_coverage(sl.select_by_slot(
            _slot_dicts("taobao"), platform="taobao")):
        print(f"🎨 套图结构「{coverage['name']}」：{coverage['ref_count']} 张角色"
              f"（对得上 {len(coverage['matched'])} 个槽位"
              f"｜本平台缺 {len(coverage['missing_in_ref'])}"
              f"｜本平台没有的角色 {len(coverage['extra_in_ref'])}）")
    print("=" * 78)
    if warnings:
        print(f"⚠️ {warnings} 项「用户词条 vs 槽位契约」的不一致（不影响自检；"
              "注入时槽位契约优先，可在「风格词库」里改词条的适用范围）")
    if problems:
        print(f"❌ 自检未通过：{problems} 项问题需要修配置")
        return 1
    print("✅ 自检通过：所有平台的所有槽位都能命中档案，且内置档案与槽位契约零冲突")
    return 0


def main() -> int:
    # Windows 控制台默认 GBK：✅/❌ 这类字符会直接抛 UnicodeEncodeError（脚本崩在自检里）
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 — 老 Python/被重定向时不强求
        pass
    parser = argparse.ArgumentParser(description="风格档案库预览（零成本，不调用任何模型）")
    parser.add_argument("--platform", default="pinduoduo")
    parser.add_argument("--category", default="", help="品类关键词（如 保健品 / 美妆 / 3C）")
    parser.add_argument("--slots", default="", help="只预览这些槽位（逗号分隔）")
    parser.add_argument("--policy", default="", help="覆盖背景策略（white_required/white_preferred/design_allowed）")
    parser.add_argument("--check", action="store_true", help="全平台自检（覆盖率 + 契约冲突）")
    parser.add_argument("--json", action="store_true", help="把注入块与摘要写到 output/style-preview.json")
    args = parser.parse_args()

    args.slots = [piece.strip() for piece in args.slots.replace("，", ",").split(",")
                  if piece.strip()]
    if args.check:
        return run_check()
    return run_preview(args)


if __name__ == "__main__":
    sys.exit(main())
