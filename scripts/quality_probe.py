"""真机质量探针 —— 用真实方舟跑「文+图」出一张，量化验收成图质量

## 它验什么（用户三点要求 + 本轮改动）

1. **参考图真的送上去了**（`image` 参数被上游接受，不是被静默忽略）；
2. **`watermark:false` 真的生效**（去掉实测存在的右下角「AI生成」水印）；
3. **背景是不是纯白 #FFFFFF**（本地体检：边缘平均亮度 ≥ 250）；
4. **商品身份有没有保住**（与上传原图的主体相似度；太低 = 退化成"凭文字想象商品"）；
5. **是否只是复制原图**（整图高度相似 + 背景未变 = 没按提示词重绘）；
6. **包装文字是否逐字一致**（把原图与生成图一起交给视觉模型逐项比对）。

## 花费

**未标定，以供应商控制台为准** —— 方舟 Seedream（`doubao-seedream-*`）没有可核对的
公开价目表，本仓库不再按 DALL·E 时代的常量（0.04/张）猜金额，界面上它一律显示
"未标定（N 张图）"；本脚本只出 **1 张**图 + 1 次视觉比对（比对用的文本模型若已标定，
金额见会话/审计里的"已标定部分"）。想知道确切花费，请查方舟控制台账单，
或用设置页的「实测标定」把本账号实测单价写回 `config/pricing.yaml`。用法：

```
python scripts/quality_probe.py                       # 用上次真实会话的上传原图
python scripts/quality_probe.py --image 路径.png       # 指定原图
python scripts/quality_probe.py --slot main_scene     # 换个槽位
python scripts/quality_probe.py --dry-run             # 不花钱：只打印提示词与参数
```
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.core.config import image_settings, resolve_image_size  # noqa: E402
from src.core.platforms import slot_label  # noqa: E402
from src.harness.image_prompt import compose_prompt, effective_strategy  # noqa: E402
from src.harness.image_quality import (  # noqa: E402
    compare_to_reference,
    inspect_image,
    decode_reference,
)
from src.harness.reference_images import collect_references  # noqa: E402
from src.harness.set_plan import platform_slot_plan  # noqa: E402

OUT_ROOT = ROOT / "output" / "quality-probe"
DEFAULT_CHECKPOINT = ROOT / "data" / "checkpoints" / "72d5ef86831c4f99.json"

# 视觉比对用的提示词（拿原图与生成图逐项核对，只输出 JSON）
COMPARE_PROMPT = """你在做电商成图的质量验收。我给你两张图：
- 图一：用户上传的**真实商品图**（基准）
- 图二：AI 根据图一生成的候选图

请**只依据图像本身**逐项比对，输出 JSON：
{
  "brand_text": {"expected": "图一上的品牌原文", "observed": "图二上的品牌文字", "status": "same/different/missing/unreadable"},
  "product_name": {"expected": "", "observed": "", "status": "..."},
  "spec_text": {"expected": "", "observed": "", "status": "..."},
  "certifications": {"expected": "", "observed": "", "status": "..."},
  "packaging_graphics": {"status": "same/different/missing/unreadable", "note": "包装图案/配色/形制是否一致"},
  "background": {"observed": "背景是什么颜色", "is_pure_white": true},
  "any_text_at_all": true,
  "verdict": "pass/retry/fail",
  "top_issues": ["问题"],
  "fix_direction": ["可执行修改方向"]
}
status 取值：same 一致 / different 不一致 / missing 缺失 / unreadable 看不清（按策略虚化也算）。"""


def load_reference(path_arg: str) -> tuple[bytes, str]:
    """取参考图字节：命令行路径 → 上次真实会话 checkpoint 里的上传原图"""
    if path_arg:
        data = Path(path_arg).read_bytes()
        return data, str(Path(path_arg).name)
    if DEFAULT_CHECKPOINT.exists():
        payload = json.loads(DEFAULT_CHECKPOINT.read_text(encoding="utf-8"))
        images = (payload.get("task") or {}).get("product_images") or []
        if images:
            return base64.b64decode(images[0]), f"{DEFAULT_CHECKPOINT.name} 的上传原图"
    raise SystemExit("找不到参考图：请用 --image 指定，或确认 checkpoint 存在")


def build_slot(slot_id: str, platform: str) -> dict:
    """按平台档案构造一个槽位（与提示词生成员产出的结构一致）"""
    slots = platform_slot_plan(platform)
    slot_id = slot_id if slot_id in slots else (slots[0] if slots else "main_white")
    presets = {
        "main_white": {
            "prompt": "商品本体正面平视，纯白背景 #FFFFFF，柔和均匀商业摄影布光，"
                      "主体居中、占比 85% 以上，画面干净无道具",
            "composition": "主体居中，正面平视，占比 85% 以上",
            "background": "#FFFFFF", "aspect": "1:1",
        },
        "main_scene": {
            "prompt": "商品本体置于简洁生活场景（原木桌面，清晨自然光斜射，环境克制）",
            "composition": "主体居中偏下，环境交代充足", "background": "", "aspect": "1:1",
        },
    }
    slot = dict(presets.get(slot_id, presets["main_white"]))
    slot.update({"slot_id": slot_id, "role": slot_label(slot_id), "text_in_image": False})
    return slot


async def ask_vision(reference: bytes, generated: bytes) -> dict:
    """把原图与生成图交给视觉模型逐项比对（Mock/无 Key 时跳过）"""
    from src.providers import get_provider_registry
    from src.harness.vision_payload import sniff_mime

    provider, model = get_provider_registry().resolve(["vision"], "审查员")
    if provider is None or getattr(provider, "name", "") == "mock":
        return {"skipped": "视觉 Provider 未就绪（Mock），跳过文字比对"}
    parts = []
    for data in (reference, generated):
        uri = f"data:{sniff_mime(data)};base64,{base64.b64encode(data).decode()}"
        parts.append({"type": "image_url", "image_url": {"url": uri}})
    result = await provider.chat_with_vision(
        messages=[{"role": "user", "content": [{"type": "text", "text": COMPARE_PROMPT}, *parts]}],
        model=model,
    )
    if isinstance(result, dict) and result.get("error"):
        return {"error": str(result["error"])[:300]}
    content = result.get("content")
    return content if isinstance(content, dict) else {"raw": str(content)[:2000]}


async def main() -> int:
    parser = argparse.ArgumentParser(description="真机质量探针（会花钱：1 张 ≈ ¥0.2）")
    parser.add_argument("--image", default="", help="参考图路径（默认用上次会话的上传原图）")
    parser.add_argument("--platform", default="taobao")
    parser.add_argument("--slot", default="main_white")
    parser.add_argument("--route", default="ark")
    parser.add_argument("--model", default="")
    parser.add_argument("--size", default="")
    parser.add_argument("--dry-run", action="store_true", help="只打印参数与提示词，不调用生图")
    parser.add_argument("--skip-vision", action="store_true", help="跳过视觉文字比对")
    args = parser.parse_args()

    reference, source_name = load_reference(args.image)
    options = image_settings()
    slot = build_slot(args.slot, args.platform)
    references, ref_notes = collect_references(
        [base64.b64encode(reference).decode()], limit=int(options["max_references"]))
    strategy = effective_strategy(options["text_strategy"], has_references=bool(references),
                                  text_in_image=bool(slot.get("text_in_image")))
    prompt = compose_prompt(prompt=slot["prompt"], composition=slot.get("composition", ""),
                            background=slot.get("background", ""), aspect=slot.get("aspect", ""),
                            text_in_image=bool(slot.get("text_in_image")),
                            text_strategy=options["text_strategy"], has_references=bool(references))

    from src.providers import get_provider_registry
    provider = get_provider_registry().get_image(args.route)
    if provider is None or getattr(provider, "name", "") == "mock":
        raise SystemExit(f"路由 {args.route} 的 Image Provider 未就绪（未配置凭据？）")
    size = args.size or resolve_image_size(getattr(provider, "default_size", ""))
    model = args.model or "doubao-seedream-5-0-260128"

    print("=" * 78)
    print(f"参考图   : {source_name}（{len(reference) // 1024}KB）")
    print(f"路由/模型: {args.route} / {model}")
    print(f"尺寸     : {size}｜文字策略: {options['text_strategy']} → 生效 {strategy}")
    print(f"水印     : watermark={bool(options['watermark'])}（False = 关掉平台水印）")
    print(f"参考图   : {len(references)} 张｜{'; '.join(ref_notes) or '无说明'}")
    print(f"槽位     : {slot['slot_id']}（{slot['role']}）")
    print("-" * 78)
    print("提示词   :", prompt)
    print("=" * 78)
    if args.dry_run:
        print("（--dry-run：未调用生图，未花钱）")
        return 0

    started = time.perf_counter()
    result = await provider.generate(prompt=prompt, size=size, model=model,
                                     reference_images=references or None,
                                     options={"watermark": bool(options["watermark"])})
    elapsed = time.perf_counter() - started
    if isinstance(result, dict) and result.get("error"):
        print(f"❌ 生图失败（{elapsed:.1f}s）：{result['error']}")
        return 2

    # 取图像字节（方舟回 URL；24h 内有效，必须立刻下载）
    data = b""
    if result.get("base64_data"):
        data = base64.b64decode(result["base64_data"])
    if not data and str(result.get("image_url") or "").startswith("data:"):
        data = base64.b64decode(str(result["image_url"]).partition(",")[2])
    if not data and result.get("image_url"):
        import httpx
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            resp = await client.get(result["image_url"])
            resp.raise_for_status()
            data = resp.content
    if not data:
        print("❌ 上游未返回可用图像数据")
        return 2

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out_dir = OUT_ROOT / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    gen_path = out_dir / f"{slot['slot_id']}.jpg"
    ref_path = out_dir / "reference.png"
    gen_path.write_bytes(data)
    ref_path.write_bytes(reference)

    report: dict = {
        "timestamp": stamp,
        "source_image": source_name,
        "route": args.route, "model": result.get("model_used") or model, "size": size,
        "latency_s": round(elapsed, 1),
        "cost_usd": result.get("cost_usd"),
        "text_strategy": options["text_strategy"], "effective_strategy": strategy,
        "slot_id": slot["slot_id"],
        "prompt": prompt,
        "reference_count": result.get("reference_count"),
        "ignored_params": result.get("ignored_params"),
        "request_params": result.get("request_params"),
        "reference_notes": ref_notes,
        "files": {"generated": str(gen_path.relative_to(ROOT)),
                  "reference": str(ref_path.relative_to(ROOT))},
    }
    report["quality"] = inspect_image(data, thresholds=options.get("quality"))
    report["compare"] = compare_to_reference(reference, data, options.get("quality"))
    if not args.skip_vision:
        report["vision"] = await ask_vision(reference, data)

    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                         encoding="utf-8")

    quality = report["quality"]
    compare = report["compare"]
    print(f"✅ 出图完成（{elapsed:.1f}s，${report['cost_usd']}）→ {report['files']['generated']}")
    print(f"   参考图确实入体: reference_count={report['reference_count']}"
          f"｜被忽略参数={report['ignored_params'] or '无'}")
    print(f"   尺寸/格式: {quality.get('width')}×{quality.get('height')} {quality.get('format')}"
          f"（{quality.get('bytes', 0) // 1024}KB）")
    print(f"   背景纯白: {'✅' if quality.get('is_white_bg') else '❌'}"
          f" 边缘均值 {quality.get('edge_mean')}（阈值 250）")
    print(f"   无水印  : {'✅' if not quality.get('watermark_suspected') else '❌'}"
          f" 右下区偏移 {quality.get('watermark_zone_delta')}")
    print(f"   商品身份: 相似度 {compare.get('identity_similarity')}"
          f"（阈值 0.45）{'✅' if not compare.get('identity_lost') else '❌ 疑似凭文字想象商品'}")
    print(f"   未复制原图: {'✅' if not compare.get('is_near_copy') else '❌ 疑似直接复制'}"
          f"（整图相似度 {compare.get('global_similarity')}，背景变化 {compare.get('background_shift')}）")
    for issue in (quality.get("issues") or []) + (compare.get("issues") or []):
        print(f"   · {issue}")
    vision = report.get("vision") or {}
    if vision.get("verdict"):
        print(f"   视觉比对: verdict={vision['verdict']}"
              f"｜品牌 {vision.get('brand_text', {}).get('status')}"
              f"（图一 {vision.get('brand_text', {}).get('expected')!r}"
              f" → 图二 {vision.get('brand_text', {}).get('observed')!r}）")
        for issue in (vision.get("top_issues") or [])[:5]:
            print(f"   · {issue}")
    elif vision:
        print(f"   视觉比对: {vision}")
    print(f"\n报告：{out_dir / 'report.json'}")
    print(f"看图：{report['files']['reference']}  vs  {report['files']['generated']}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
