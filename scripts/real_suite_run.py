"""真实套图验收 —— 一次命令跑完整轮真实会话并给出交付物报告（**会花钱**）

用途（A61-A68 之后的最终验收）：把**用户上传的真实商品图**（正面 + 背面/成分表等，最多 4 张）
灌进真实链路，跑完「分析 → 提示词 → 按槽位出图（文+图）→ 本地排版信息图 → 审查（拿原图比对）→ 合规」，
然后汇总：每个槽位出没出、哪些因缺素材被拦下、体检数值、审查的原图比对结论、**实际花费**。

```
# 先估算（不花钱）：只打印将要上送的文件与平台槽位（价格未标定时打印张数而不是金额）
python scripts/real_suite_run.py --images 正面.jpg 背面.jpg --platform pinduoduo --dry-run

# 真跑（默认：已标定金额超 $3 中止 + 出图张数超 --max-images 中止；0 表示不限）
python scripts/real_suite_run.py --images 正面.jpg 背面.jpg --platform pinduoduo --max-images 6
```

设计要点：
- **张数硬闸门（事实量）**：`--max-images` 按"本轮实际出图张数"计数并在超限时中止/报警。
  金额依赖价格表，未标定的模型（方舟 Seedream 等）**不计入** `cost_so_far`，
  只靠 `--max-cost` 会在"未标定 + 循环出图"时放任烧钱，所以要有这条不依赖价格表的闸门；
- **成本闸门（估算）**：轮询时若 `cost_so_far`（**已标定部分**）超过 `--max-cost`，
  立即 cancel 会话并报告；未标定的调用不计入金额、单独计数并打印；
- **不自动重跑**：会话进入终态或人工审批即停，把决定权交回用户（此前实测协调者会自己重跑一轮，成本翻倍）；
- 报告落盘 `output/real-suite/<时间戳>/report.json`，含逐槽位明细与省钱线索。
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.core.config import image_settings, load_models_config  # noqa: E402
from src.harness.pricing import estimate  # noqa: E402

DEFAULT_BASE = "http://127.0.0.1:8000"
OUT_ROOT = ROOT / "output" / "real-suite"
DEFAULT_MAX_COST = 3.0        # USD：超过即中止（真实一轮拼多多套图约 $0.4-0.8）
DEFAULT_MAX_IMAGES = 0        # 张数硬闸门（事实量）；0 = 不限，建议按预算显式设一个


def resolve_image_route() -> tuple[str, str]:
    """当前配置下"生图会用哪个 路由/模型"（给预估费用用；读不到返回 ("", "")）

    取值顺序与 `ProviderRegistry.resolve(["image"])` 一致：
    `agent_overrides.生图员.image` → `capabilities.image.default`。
    """
    try:
        models = load_models_config() or {}
        override = ((models.get("agent_overrides") or {}).get("生图员") or {}).get("image")
        spec = override or ((models.get("capabilities") or {}).get("image") or {}).get("default", "")
    except Exception:  # noqa: BLE001 — 配置读不到就不预估，不影响主流程
        return "", ""
    text = str(spec or "").strip()
    if "/" in text:
        route, model = text.split("/", 1)
        return route.strip(), model.strip()
    return "", text


def _multipart(fields: dict, files: list[tuple[str, Path]]) -> tuple[bytes, str]:
    """手搓最小 multipart（不引第三方库）：fields=文本字段，files=[(字段名, 路径)]"""
    boundary = "----real" + uuid.uuid4().hex[:16]
    body = bytearray()
    for name, value in fields.items():
        body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode()
    for name, path in files:
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; "
                 f"filename=\"{path.name}\"\r\nContent-Type: {ctype}\r\n\r\n").encode()
        body += path.read_bytes() + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    return bytes(body), f"multipart/form-data; boundary={boundary}"


def _request(url: str, *, data: bytes | None = None, headers: dict | None = None,
             method: str = "GET", timeout: int = 60):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = resp.read().decode("utf-8")
    return json.loads(payload) if payload else {}


def cost_exceeded(cost: float, limit: float) -> bool:
    """成本闸门：limit<=0 表示不限

    ⚠️ 这是**按价格表估算**的闸门：未标定价格的模型（方舟 Seedream 等）不计入金额，
    所以它不能单独当"花钱上限"用 —— 张数硬闸门 `images_exceeded` 才是事实量。
    """
    return limit > 0 and cost >= limit


def images_exceeded(count: int, limit: int) -> bool:
    """张数硬闸门（**事实量**）：limit<=0 表示不限

    为什么要有它：金额依赖价格表，未标定的模型（方舟 Seedream 没有可核对的公开价目表）
    根本不计入 `cost_so_far`，只靠 `--max-cost` 会在"未标定 + 循环出图"时**放任烧钱**。
    张数来自会话产物里实际出图的张数，是事实，不受价格表影响。
    """
    return limit > 0 and count >= limit


def summarize_session(session: dict) -> dict:
    """把会话汇总成可读报告（纯函数，便于测试）

    金额口径：`cost_usd` 只含**已标定**部分；未标定的调用次数单独给
    `cost_unknown_calls`（界面提示"另有 N 次未标定，金额以供应商控制台为准"）。
    """
    artifacts = session.get("artifacts") or {}
    images = artifacts.get("images") or []
    coverage = artifacts.get("set_plan_coverage") or {}
    identity = artifacts.get("product_identity") or {}
    quality = artifacts.get("quality_report") or {}
    review = artifacts.get("review") or {}
    compliance = artifacts.get("compliance") or {}
    lint = artifacts.get("prompt_lint") or {}
    prompt_review = artifacts.get("prompt_review") or {}
    try:
        unknown_calls = int(session.get("cost_unknown_calls") or 0)
    except (TypeError, ValueError):
        unknown_calls = 0
    return {
        "session_id": session.get("session_id", ""),
        "status": session.get("status", ""),
        "turns": session.get("turn_count", 0),
        "cost_usd": round(float(session.get("cost_so_far") or 0.0), 4),
        "cost_unknown_calls": unknown_calls,
        "images_count": len(images),
        "identity": {k: identity.get(k) for k in
                     ("status", "brand", "product_name", "spec", "source", "confidence",
                      "brand_palette")},
        "coverage": coverage,
        "images": [{
            "slot_id": img.get("slot_id") or img.get("prompt_name"),
            "number": img.get("prompt_number"),
            "saved_path": img.get("saved_path", ""),
            "size": f"{(img.get('quality') or {}).get('width')}x{(img.get('quality') or {}).get('height')}",
            "text_status": img.get("text_status", ""),
            "text_reason": img.get("text_reason", ""),
            "reference_count": (img.get("generation_params") or {}).get("reference_count"),
            "white_bg": (img.get("quality") or {}).get("is_white_bg"),
            "watermark": (img.get("quality") or {}).get("watermark_suspected"),
            "identity_similarity": ((img.get("quality") or {}).get("reference_compare") or {})
            .get("identity_similarity"),
            # 逐张提示词与审美改写痕迹（用户要能核对"第几张是怎么写的"）
            "revised_by_reviewer": bool(img.get("revised_by_reviewer")),
            "aesthetic_score": (prompt_review.get("scores") or {}).get(img.get("slot_id")),
            "prompt_notes": img.get("prompt_notes") or [],
            "prompt_text": img.get("prompt_text", ""),
        } for img in images],
        "quality": {k: quality.get(k) for k in
                    ("count", "white_bg_ok", "watermark_free", "identity_lost",
                     "near_copy", "blocked_slots")},
        "prompt_lint": {k: lint.get(k) for k in ("checked", "expected", "digest")}
        | {"errors": len(lint.get("errors") or []), "warnings": len(lint.get("warnings") or []),
           "findings": (lint.get("findings") or [])[:12]},
        "prompt_review": {k: prompt_review.get(k) for k in
                          ("status", "verdict", "threshold", "revised_slots",
                           "refine_rejected", "scores", "message")},
        "review": {k: review.get(k) for k in
                   ("verdict", "overall_score", "reference_compared", "needs_human_review",
                    "dimension_scores", "reviewed_slots", "unreviewed_slots")},
        "fidelity_findings": (review.get("fidelity_findings") or [])[:12],
        "review_issues": (review.get("top_issues") or [])[:8],
        "compliance": {k: compliance.get(k) for k in ("passed", "risk_level")},
        "errors": [str(entry.get("error"))[:160] for entry in (session.get("error_history") or [])],
    }


def run_session(args, headers: dict, *, slots: list[str], out_dir: Path) -> dict:
    """跑一轮真实会话（上传图 → 分析 → 提示词+体检+审美审核 → 出图 → 审查 → 合规）

    `slots` 非空时只出这些槽位（最小付费冒烟）；成本闸门超过 `--max-cost` 立刻中止并取消会话。
    """
    body, content_type = _multipart({
        "product_info": args.product_info or "真实商品（上传原图）",
        "platform": args.platform,
        "category_hint": args.category,
        "mode": "serial",
        "slots": ",".join(slots),
    }, [("files", p) for p in args.images])
    created = _request(f"{args.base_url}/api/sessions", data=body, method="POST",
                       headers={**headers, "Content-Type": content_type}, timeout=180)
    session_id = created["session_id"]
    print(f"会话已创建：{session_id}", flush=True)

    deadline = time.time() + args.timeout
    started = time.time()
    last_line = ""
    session: dict = {}
    while time.time() < deadline:
        try:
            session = _request(f"{args.base_url}/api/sessions/{session_id}", headers=headers)
        except urllib.error.URLError as exc:
            print(f"  轮询失败（{exc}），10s 后重试", flush=True)
            time.sleep(10)
            continue
        cost = float(session.get("cost_so_far") or 0)
        images = (session.get("artifacts") or {}).get("images") or []
        unknown = int(session.get("cost_unknown_calls") or 0)
        line = (f"[{int(time.time() - started):4d}s] status={session.get('status')} "
                f"turns={session.get('turn_count')} 图{len(images)} 张 "
                f"已标定≈${cost:.4f}" + (f"（另有 {unknown} 次未标定）" if unknown else ""))
        if line != last_line:
            print(line, flush=True)
            last_line = line
        if images_exceeded(len(images), args.max_images):
            print(f"⛔ 已达张数上限 {args.max_images} 张（当前 {len(images)} 张，事实量、"
                  "与价格表无关）→ 取消会话止损", flush=True)
            try:
                _request(f"{args.base_url}/api/sessions/{session_id}/interject",
                         data=json.dumps({"content": "出图张数已达上限，请停止"}).encode(),
                         headers={**headers, "Content-Type": "application/json"}, method="POST")
            except Exception:  # noqa: BLE001 — 止损尽力而为
                pass
            break
        if cost_exceeded(cost, args.max_cost):
            print(f"⛔ 已达成本上限 ${args.max_cost:.2f}（按价格表估算的已标定部分 "
                  f"${cost:.4f}{f'，另有 {unknown} 次未标定' if unknown else ''}）→ 取消会话止损",
                  flush=True)
            try:
                _request(f"{args.base_url}/api/sessions/{session_id}/interject",
                         data=json.dumps({"content": "成本已达上限，请停止"}).encode(),
                         headers={**headers, "Content-Type": "application/json"}, method="POST")
            except Exception:  # noqa: BLE001 — 止损尽力而为
                pass
            break
        if session.get("status") in ("completed", "failed", "waiting_human"):
            print(f"TERMINAL: {session['status']}", flush=True)
            break
        time.sleep(10)
    else:
        print("TIMEOUT：整轮超时（会话仍在跑，请到界面处理）", flush=True)

    report = summarize_session(session)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                         encoding="utf-8")
    return report


def print_report(report: dict, out_dir: Path) -> None:
    print("\n=== 交付物报告 ===")
    ident = report["identity"]
    print(f"商品身份: {ident.get('status')}｜{ident.get('brand')} / {ident.get('product_name')}"
          f"（{ident.get('source')}, 置信度 {ident.get('confidence')}）")
    palette = ident.get("brand_palette") or {}
    if palette.get("primary"):
        print(f"品牌色系: 主色 {palette.get('primary')}｜辅色 {palette.get('secondary')}"
              f"｜背景 {palette.get('background')}（{palette.get('evidence') or '取自包装'}）")
    lint = report.get("prompt_lint") or {}
    print(f"提示词   : 体检 {lint.get('checked')}/{lint.get('expected')} 张"
          f"｜硬伤 {lint.get('errors')}｜建议 {lint.get('warnings')}")
    prompt_review = report.get("prompt_review") or {}
    print(f"审美审核 : {prompt_review.get('status')}｜{prompt_review.get('verdict')}"
          f"｜阈值 {prompt_review.get('threshold')}"
          f"｜已改写 {prompt_review.get('revised_slots') or '无'}")
    for finding in (lint.get("findings") or [])[:6]:
        mark = "❌" if finding.get("level") == "error" else "⚠️"
        print(f"  {mark} {finding.get('message')}")
    coverage = report["coverage"] or {}
    print(f"套图覆盖: {coverage.get('produced')}/{coverage.get('expected')}"
          f"｜缺 {coverage.get('missing_slots') or '无'}"
          f"｜缺素材被拦 {coverage.get('blocked_slots') or '无'}")
    for img in report["images"]:
        flag = {"composed": "图文已排版", "blocked": "缺素材·未生成"}.get(img["text_status"], "")
        score = img.get("aesthetic_score")
        print(f"  · 第{img.get('number')}张 {str(img['slot_id']):20s} {img['size']:>9} "
              f"参考图{img['reference_count']} 白底={img['white_bg']} 水印={img['watermark']} "
              f"身份={img['identity_similarity']} 审美={score}{'（已改写）' if img.get('revised_by_reviewer') else ''} {flag}")
    quality = report["quality"]
    print(f"本地体检: 白底合格={quality.get('white_bg_ok')}｜无水印={quality.get('watermark_free')}"
          f"｜身份丢失={quality.get('identity_lost') or '无'}")
    review = report["review"]
    print(f"审查    : verdict={review.get('verdict')} score={review.get('overall_score')}"
          f"｜维度={review.get('dimension_scores')}")
    print(f"         拿到原图比对={review.get('reference_compared')}"
          f"｜已审 {review.get('reviewed_slots') or '—'}"
          f"｜未审 {review.get('unreviewed_slots') or '无'}"
          f"｜需人工={review.get('needs_human_review')}")
    for finding in report["fidelity_findings"][:6]:
        print(f"  · [{finding.get('status')}] {finding.get('element')}: "
              f"{str(finding.get('expected'))[:20]} → {str(finding.get('observed'))[:20]}")
    for issue in report["review_issues"][:4]:
        print(f"  ⚠ {str(issue)[:120]}")
    print(f"合规    : {report['compliance']}")
    unknown = report.get("cost_unknown_calls") or 0
    print(f"\n实际花费: 已标定部分 ≈${report['cost_usd']}"
          f"｜出图 {report.get('images_count', 0)} 张"
          + (f"｜另有 {unknown} 次未标定（价格表里没有该模型，金额以供应商控制台账单为准）"
             if unknown else ""))
    print(f"报告    : {(out_dir / 'report.json').relative_to(ROOT)}")
    print("提示    : 会话在界面里可点 approve（收下这套）/ retry（再跑一轮，会再花钱）/ reject")


def _set_prompt_review(headers: dict, base_url: str, enabled: bool) -> bool:
    """开关"出图前提示词审美审核"（A/B 用）；失败返回 False（不阻断）"""
    try:
        _request(f"{base_url}/api/settings/chat",
                 data=json.dumps({"require_prompt_review": bool(enabled)}).encode(),
                 headers={**headers, "Content-Type": "application/json"}, method="POST")
        return True
    except Exception as exc:  # noqa: BLE001 — 设置失败不阻断主流程，但要如实打印
        print(f"⚠️ 无法切换提示词审核开关（{exc}）——本轮按当前设置执行", flush=True)
        return False


def _set_style_library(headers: dict, base_url: str, enabled: bool) -> bool:
    """开关"风格档案注入"（A/B 用：`--style-library off,on`）；失败返回 False（不阻断）

    档案是**逐槽位**注入「提示词生成员」与「提示词审核优化员」的设计原型
    （`config/style_library.yaml` 内置 + 用户在「🎨 风格词库」里导入的）。
    关掉它就能回答"这套档案到底有没有让审美上去"——否则永远只是我的说法。
    """
    try:
        _request(f"{base_url}/api/settings/chat",
                 data=json.dumps({"style_library_enabled": bool(enabled)}).encode(),
                 headers={**headers, "Content-Type": "application/json"}, method="POST")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️ 无法切换风格档案开关（{exc}）——本轮按当前设置执行", flush=True)
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="真实套图验收（会花钱）")
    parser.add_argument("--images", nargs="+", required=True,
                        help="上传的真实商品图（正面 + 背面/成分表等，最多 4 张）")
    parser.add_argument("--platform", default="pinduoduo")
    parser.add_argument("--base-url", default=DEFAULT_BASE)
    parser.add_argument("--product-info", default="")
    parser.add_argument("--category", default="")
    parser.add_argument("--slots", default="",
                        help="只出这些槽位（逗号分隔，如 main_white,main_selling_point）"
                             "→ 最小付费冒烟（2 张约 $0.08）")
    parser.add_argument("--variants", default="",
                        help="`draft,refined`：跑两轮做 A/B —— draft 关闭「提示词审美改写」、"
                             "refined 开启（会花两轮的钱，约 2 倍）")
    parser.add_argument("--style-library", default="",
                        help="`off,on`：跑两轮做 A/B —— 关闭/开启「风格档案逐槽位注入」"
                             "（与 --variants 可叠加，每叠加一档就多一倍花费）")
    parser.add_argument("--max-cost", type=float, default=DEFAULT_MAX_COST,
                        help=f"成本上限（按价格表估算、**未标定模型不计**；USD，"
                             f"默认 {DEFAULT_MAX_COST}；0 = 不限）。要防烧钱请配合 --max-images")
    parser.add_argument("--max-images", type=int, default=0,
                        help="出图张数硬闸门（**事实量**，按本轮实际出图张数计；"
                             "默认 0 = 不限。未标定价格时 --max-cost 形同虚设，靠它兜底）")
    parser.add_argument("--timeout", type=int, default=1800, help="整轮等待上限（秒）")
    parser.add_argument("--api-key", default="", help="服务配了 ECOMM_API_KEY 时填")
    parser.add_argument("--dry-run", action="store_true", help="只打印将要做什么，不发起请求")
    args = parser.parse_args()

    args.images = [Path(p) for p in args.images]
    missing = [str(p) for p in args.images if not p.exists()]
    if missing:
        raise SystemExit(f"图片不存在：{', '.join(missing)}")
    if len(args.images) > 4:
        raise SystemExit("最多 4 张参考图（方舟多参考图上限内；更多请先合并/挑选）")

    from src.core.platforms import platform_detail_slots, platform_slots, slot_kind

    main_slots = platform_slots(args.platform)
    detail_slots = platform_detail_slots(args.platform)
    subset = [piece.strip() for piece in args.slots.replace("，", ",").split(",") if piece.strip()]
    unknown = [slot for slot in subset if slot not in main_slots + detail_slots]
    if unknown:
        print(f"⚠️ 这些槽位不在 {args.platform} 的平台档案里：{unknown}（仍会尝试，"
              "若提示词里没有该槽位会被跳过）")
    variants = [piece.strip() for piece in args.variants.replace("，", ",").split(",")
                if piece.strip()]
    style_library = [piece.strip() for piece in args.style_library.replace("，", ",").split(",")
                     if piece.strip()]
    for name in style_library:
        if name not in ("on", "off"):
            raise SystemExit(f"--style-library 只接受 on/off，收到 {name!r}")
    rounds = max(1, len(variants)) * max(1, len(style_library))
    est_images = len(subset) if subset else len(main_slots) + len(detail_slots)

    print("=" * 78)
    print(f"平台      : {args.platform}（主图 {len(main_slots)} + 详情 {len(detail_slots)}）")
    print(f"参考图    : {len(args.images)} 张 → {[p.name for p in args.images]}")
    if subset:
        print(f"槽位子集  : " + "、".join(
            f"{s}{'(图文)' if slot_kind(s) == 'info' else ''}" for s in subset))
    else:
        print("套图角色  : " + "、".join(
            f"{s}{'(图文)' if slot_kind(s) == 'info' else ''}" for s in main_slots + detail_slots))
    if variants:
        print(f"A/B 轮次  : {variants}（draft=关闭审美改写 / refined=开启）")
    if style_library:
        print(f"风格档案  : {style_library}（off=不注入 / on=逐槽位注入设计档案与锚点）"
              f" → 共 {rounds} 轮")
    print(f"成本闸门  : " + ("不限" if args.max_cost <= 0 else f"${args.max_cost:.2f} 即中止")
          + "（按价格表估算、未标定模型不计、每轮独立计）")
    print(f"张数闸门  : " + ("不限" if args.max_images <= 0 else f"{args.max_images} 张即中止")
          + "（事实量，按本轮实际出图张数计）")

    # 预估价：**按价格表逐模型查**，查不到就只说张数（绝不拿 DALL·E 时代的 0.04 冒充）
    size = image_settings().get("size", "")
    route, model = resolve_image_route()
    estimated = estimate(route, model, "image", {"images": est_images, "size": size}) \
        if route and model else None
    if estimated and estimated.get("amount") is not None:
        print(f"预计花费  : 出图 {est_images} 张 ≈ {estimated['currency']}"
              f"{estimated['amount']:.2f}／轮 × {rounds} 轮 ≈ "
              f"{estimated['currency']}{estimated['amount'] * rounds:.2f}"
              f"（{estimated['basis']}，来源 {estimated['source']}"
              f"{'｜' + estimated['updated_at'] if estimated.get('updated_at') else ''}）")
    else:
        print(f"预计花费  : **未标定**（{route or '?'} / {model or '?'} 在价格表里没有条目）"
              f"—— 本轮预计出图 {est_images} 张 × {rounds} 轮 = {est_images * rounds} 张，"
              "金额请以供应商控制台账单为准（可用 --max-images 设张数上限）")
        if args.max_cost > 0 and args.max_images <= 0:
            print("⚠️ 该模型价格未标定 → --max-cost 不会生效（未标定金额不计入 cost_so_far）；"
                  "建议显式加 --max-images，否则本轮等于没有花钱上限")
    print("=" * 78)
    if args.dry_run:
        print("（--dry-run：未发起任何请求，未花钱）")
        return 0

    headers = {"X-API-Key": args.api_key} if args.api_key else {}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    root_dir = OUT_ROOT / stamp
    tags = variants or [""]
    style_tags = style_library or [""]
    reports: dict[str, dict] = {}
    for tag in tags:
        for style_tag in style_tags:
            # 轮次目录名：只跑一种组合时保持 `draft`/`refined`/根目录的历史习惯
            label = tag or ""
            if style_tag:
                label = f"{tag}-style_{style_tag}".strip("-")
            if tag:
                enabled = tag == "refined"
                if _set_prompt_review(headers, args.base_url, enabled):
                    print(f"── 轮次 {label}：提示词审美改写 = {'开启' if enabled else '关闭'}",
                          flush=True)
            if style_tag:
                style_enabled = style_tag == "on"
                if _set_style_library(headers, args.base_url, style_enabled):
                    print(f"   风格档案注入 = {'开启' if style_enabled else '关闭'}", flush=True)
            out_dir = (root_dir / label) if label else root_dir
            reports[label] = run_session(args, headers, slots=subset, out_dir=out_dir)
            print_report(reports[label], out_dir)

    if len(reports) > 1:
        print("\n=== 轮次对比 ===")
        for tag, report in reports.items():
            review = report.get("review") or {}
            dims = review.get("dimension_scores") or {}
            aesthetic = round(sum(float(v) for v in dims.values()) / len(dims), 1) if dims else "—"
            unknown_calls = report.get("cost_unknown_calls") or 0
            cost_text = f"已标定≈${report['cost_usd']}"
            if unknown_calls:
                cost_text += f"（另有 {unknown_calls} 次未标定）"
            print(f"{tag:8s} 花费 {cost_text}｜审查均分 {review.get('overall_score')}"
                  f"｜维度均分 {aesthetic}｜身份相似度 "
                  f"{[img['identity_similarity'] for img in report['images']]}")
        print("（并排判定：请打开 output/real-suite/<时间戳>/draft 与 /refined 下的图）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
