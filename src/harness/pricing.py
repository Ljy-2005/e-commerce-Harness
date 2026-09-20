"""价格表 —— 「事实优先 + 可标定」的成本口径唯一来源

## 为什么有这个模块（用户质疑，已核实成立）

> "不同的模型的花费是不同的，其次每个模型的花费又会随着时间被各大模型商来回修改，
> 这意味着除非每次模型商修改价格的时候都及时更新修改不然会出现很大的误导"

核实结果：成本链路此前是**硬编码兜底常量**在冒充事实——

- `src/providers/openai.py` 的 `_estimate_cost(size)` 把 DALL·E 3 时代的
  `{"1024x1024": 0.04, "1024x1792": 0.08, "1792x1024": 0.08}` 当价目表，
  未命中尺寸回落 `0.04`；而我们实际用的方舟 `doubao-seedream-5-0-260128`
  出图尺寸是 **2048x2048** → 从来没命中过，每次都按 0.04 记账；
- `src/harness/cost_tracker.py` 对不认识的模型按 `(1.0, 3.0)`/1M tokens 估价 ——
  凭空造钱；`record_image` 未知模型回落 `(0.04, 0.04)`；
- `src/providers/seedream.py` 直接 `return 0.0  # 通常有免费额度`（把假设当事实）。

## 四条规则（用户已批准）

1. **用量是事实，永远显示**（路由/模型/能力/张数/tokens/耗时/成功失败），不依赖价格表；
2. **未标定价格的模型 → 不显示金额**（`None`），界面写"未标定（N 张图）"，
   不再出现 `0.04` / `(1.0,3.0)` / `0.0` 这类伪价格；
3. 有价格才显示 `≈¥0.20`（带 `≈` 与来源/更新日期）；>90 天或与标定偏差 >30% 给提示；
4. **价格表由用户自己改**：`config/pricing.yaml`（实例级、程序写，设置页入口另做）。

## 数据结构

`config/pricing.yaml`（用户填的，**优先于内置参考价**）::

    entries:
      模型 id / 路由/模型 id:
        route: ark            # 可选。缺省：键里带 "/" 则取 "/" 前缀，否则 "any"（任意路由生效）
        unit: image           # image（按张）| 1M_tokens（按 1M tokens）
        in: 0.2               # image = 每张单价；1M_tokens = 输入单价
        out: 0.2              # image 同 in；1M_tokens = 输出单价
        currency: CNY         # CNY | USD
        updated_at: 2026-09-18
        source: 用户填写
        sizes:                # image 可选：按尺寸分档（DALL·E 3 就是算两种价）
          1024x1024: 0.04
          1024x1792: 0.08
        note: 方舟控制台账单核对

内置参考价（`BUILTIN_REFERENCE_PRICES`）**只保留确实查得到公开定价的型号**，
每条都带 `updated_at` 与依据注释；**查不到的一律不写**——尤其
`doubao-seedream-*`（方舟）与 `seedream-*`（旧火山视觉）**一律没有数字**，
这是本轮的核心修复点（它们此前的"价格"是 DALL·E 的常量与 `0.0` 假设）。

## 解析缓存

照抄 `src/core/platforms.py` 的 `_DOC_CACHE`：按 `(路径, mtime_ns, size)` 缓存解析结果。
本仓库踩过"每次访问都重新读+解析 YAML → 一次渲染 10 秒"的坑（槽位契约把访问器
调用次数放大几十倍），所以价格查询这种高频路径必须带 mtime 缓存，**配置改了立刻生效**。
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from src.core.config import _project_root, data_root, load_yaml

PRICING_REL = "config/pricing.yaml"

# 来源标记（界面按它显示"这个数字哪来的"）
SOURCE_USER = "用户填写"
SOURCE_BUILTIN = "内置参考价"
SOURCE_CALIBRATED = "实测标定"

UNITS = ("image", "1M_tokens")
CURRENCIES = ("CNY", "USD")
CAPABILITIES = ("text", "vision", "image")

STALE_DAYS = 90            # 超过即提示"价格可能已过期"
DEVIATION_THRESHOLD = 0.30  # 与实测标定值偏差超过 30% 即提示

_CURRENCY_SYMBOL = {"CNY": "¥", "USD": "$"}

# 解析缓存：key = (路径, mtime_ns, size) → 解析结果（配置改了立刻生效）
_DOC_CACHE: dict[str, tuple[tuple, dict]] = {}


def currency_symbol(currency: str) -> str:
    """货币符号：CNY → ¥，其余 → $（与前端 formatCost 同一套口径）"""
    return _CURRENCY_SYMBOL.get(str(currency or "").upper(), "$")


def _today() -> str:
    return date.today().isoformat()


# ── 内置参考价 ──
#
# 纪律（用户定）：**只保留确实查得到公开定价的型号**，照抄现有代码里的数值，
# 不自己编新数字；每条标注 updated_at 与据以填写的依据；查不到的一律不写。
#
# 为什么全部标 2026-09-18：`updated_at` 的语义是"这份价格表最近一次被核实/填写的日期"
# （见本文件顶部第 3 条规则），内置条目的核实动作发生在本次重构。数值本身取自
# 各厂商公开定价页，逐条注释给出依据与"官方最后一次调价"信息。
#
# ⚠️ 尤其注意 DeepSeek：官方 2026-09-10 已把 `deepseek-v4-flash` 退役、改推
# `deepseek-flash`（V4.1 Flash，$0.30/$1.20 @peak），并把 `deepseek-v4-pro`
# 调到 $0.435/$0.87。本表仍按用户指示照抄仓库既有数值（0.14 / 0.55），
# 只在 note 里留痕 —— 要精确计费请到设置页按官方页面改写（这正是本模块存在的理由）。

BUILTIN_REFERENCE_PRICES: dict[str, dict] = {
    # ── OpenAI 文本（USD / 1M tokens，input/output）──
    # 依据：OpenAI 官方定价页 https://platform.openai.com/docs/pricing （gpt-4o 长期为
    # $2.50 input / $10.00 output；与仓库既有 `_PRICES` 一致）
    "gpt-4o": {"unit": "1M_tokens", "in": 2.50, "out": 10.00, "currency": "USD",
               "updated_at": _today(),
               "note": "OpenAI 官方定价页；仓库既有数值照抄"},
    "gpt-4o-mini": {"unit": "1M_tokens", "in": 0.15, "out": 0.60, "currency": "USD",
                    "updated_at": _today(),
                    "note": "OpenAI 官方定价页；仓库既有数值照抄"},
    "gpt-4.1": {"unit": "1M_tokens", "in": 2.00, "out": 8.00, "currency": "USD",
                "updated_at": _today(),
                "note": "OpenAI 官方定价页；仓库既有数值照抄"},
    "gpt-4.1-mini": {"unit": "1M_tokens", "in": 0.40, "out": 1.60, "currency": "USD",
                     "updated_at": _today(),
                     "note": "OpenAI 官方定价页；仓库既有数值照抄"},
    "o3": {"unit": "1M_tokens", "in": 10.00, "out": 40.00, "currency": "USD",
           "updated_at": _today(),
           "note": "OpenAI 官方定价页；推理模型，思考 token 计入输出"},
    "o4-mini": {"unit": "1M_tokens", "in": 1.10, "out": 4.40, "currency": "USD",
                "updated_at": _today(),
                "note": "OpenAI 官方定价页；仓库既有数值照抄"},
    # ── Anthropic（USD / 1M tokens）──
    # 依据：Anthropic 官方定价页 https://www.anthropic.com/pricing
    "claude-sonnet-4-20250514": {"unit": "1M_tokens", "in": 3.0, "out": 15.0,
                                 "currency": "USD", "updated_at": _today(),
                                 "note": "Anthropic 官方定价页；仓库既有数值照抄"},
    "claude-opus-4-20250514": {"unit": "1M_tokens", "in": 15.0, "out": 75.0,
                               "currency": "USD", "updated_at": _today(),
                               "note": "Anthropic 官方定价页；仓库既有数值照抄"},
    # 仓库既有键名是 `claude-haiku-3-5-20241022`（cost_tracker）与 `claude-haiku-3-5`
    # （anthropic provider）两个写法，都保留，避免改一个漏一个。
    "claude-haiku-3-5-20241022": {"unit": "1M_tokens", "in": 0.80, "out": 4.00,
                                  "currency": "USD", "updated_at": _today(),
                                  "note": "Anthropic 官方定价页；仓库既有数值照抄"},
    "claude-haiku-3-5": {"unit": "1M_tokens", "in": 0.80, "out": 4.00,
                         "currency": "USD", "updated_at": _today(),
                         "note": "Anthropic 官方定价页（上一条的别名写法）"},
    # ── DeepSeek（USD / 1M tokens）──
    # ⚠️ 官方已于 2026-09-10 调整：`deepseek-v4-flash` 退役、改推 `deepseek-flash`
    # （V4.1 Flash，$0.30/$1.20 @peak，off-peak 减半）；`deepseek-v4-pro` 现为
    # $0.435/$0.87。下面照抄仓库既有数值（用户在需求里明确"照抄现有代码里的数值即可，
    # 不要自己编新数字"），**要精确计费请在设置页按官方页面改**。
    # 依据：https://api-docs.deepseek.com/quick_start/pricing/
    "deepseek-chat": {"unit": "1M_tokens", "in": 0.14, "out": 0.14, "currency": "USD",
                      "updated_at": _today(),
                      "note": "V3 旧别名（仓库既有值 ≈¥1/1M）；官方已调价，请核对"},
    "deepseek-v4-flash": {"unit": "1M_tokens", "in": 0.14, "out": 0.14,
                          "currency": "USD", "updated_at": _today(),
                          "note": "仓库既有值 ≈¥1/1M；官方 2026-09-10 已退役该 id 并调价，请核对"},
    "deepseek-v4-pro": {"unit": "1M_tokens", "in": 0.55, "out": 0.55, "currency": "USD",
                        "updated_at": _today(),
                        "note": "仓库既有值；官方现行 $0.435/$0.87，请核对"},
    "deepseek-v4-flash-vision-exp": {"unit": "1M_tokens", "in": 0.14, "out": 0.14,
                                     "currency": "USD", "updated_at": _today(),
                                     "note": "仓库既有值；官方已并入 V4.1 Flash 计费，请核对"},
    # ── 通义千问（USD / 1M tokens，按 ¥1 ≈ $0.14 折算）──
    # 依据：阿里云百炼（Model Studio）官方定价 https://help.aliyun.com/zh/model-studio/qwen-max
    "qwen-max": {"unit": "1M_tokens", "in": 5.60, "out": 5.60, "currency": "USD",
                 "updated_at": _today(),
                 "note": "仓库既有值（¥40/1M × 0.14，输入输出同价）；官方按输入长度分档"},
    "qwen-plus": {"unit": "1M_tokens", "in": 0.28, "out": 0.28, "currency": "USD",
                  "updated_at": _today(), "note": "仓库既有值（¥2/1M × 0.14）"},
    "qwen-vl-max": {"unit": "1M_tokens", "in": 5.60, "out": 5.60, "currency": "USD",
                    "updated_at": _today(), "note": "仓库既有值（与 qwen-max 同档）"},
    "qwen-vl-plus": {"unit": "1M_tokens", "in": 0.42, "out": 0.42, "currency": "USD",
                     "updated_at": _today(), "note": "仓库既有值（¥3/1M × 0.14）"},
    # ── 图像（USD / 张）──
    # 依据：OpenAI 官方定价页（DALL·E 3 按尺寸分档：1024x1024 = $0.04；
    # 1024x1792 / 1792x1024 = $0.08。**这个"0.04"只属于 DALL·E 3**，
    # 此前被当成所有未知模型/未知尺寸的兜底价 —— 这正是本轮要修的误导）
    "dall-e-3": {"unit": "image", "in": 0.04, "out": 0.04, "currency": "USD",
                 "updated_at": _today(),
                 "note": "OpenAI 官方定价页（standard 档，按尺寸见 sizes）",
                 "sizes": {"1024x1024": 0.04, "1024x1792": 0.08, "1792x1024": 0.08}},
    "dall-e-2": {"unit": "image", "in": 0.02, "out": 0.02, "currency": "USD",
                 "updated_at": _today(),
                 "note": "OpenAI 官方定价页（1024x1024 standard）",
                 "sizes": {"1024x1024": 0.02}},
    # FLUX 依据：Black Forest Labs 官方定价（flux-pro-1.1 / flux.1-dev ≈ $0.05/张），
    # 与仓库既有 flux provider 写死的 0.05 一致。
    "flux.1-dev": {"unit": "image", "in": 0.05, "out": 0.05, "currency": "USD",
                   "updated_at": _today(), "note": "BFL 官方定价 ≈$0.05/张；仓库既有值"},
    "flux-pro-1.1": {"unit": "image", "in": 0.05, "out": 0.05, "currency": "USD",
                     "updated_at": _today(), "note": "BFL 官方定价 ≈$0.05/张；仓库既有值"},

    # ── 故意缺席（**不要**给它们加数字）──
    #
    # doubao-seedream-5-0-260128 / doubao-seedream-5-0-pro-260628 /
    # doubao-seedream-4-5-251128 / doubao-seedream-4-0-250828（火山方舟）
    # seedream-5.0 / seedream-4.0（旧版火山视觉）
    #   → 方舟计费按"张"且随活动/免费额度变化，我们没有可核对的公开价目表；
    #     此前它们的"价格"分别是 DALL·E 的 0.04 与 `return 0.0`（免费额度假设），
    #     两个都是误导。现在一律 `resolve_price(...) is None`，
    #     界面显示"未标定（N 张图）"，金额请以供应商控制台账单为准
    #     （或用设置页的"实测标定"把本账号实测单价写回来）。
}

# 命中内置表即视为"内置参考价"，用户表里的同键覆盖它（用户优先）
_BUILTIN_KEYS = set(BUILTIN_REFERENCE_PRICES)


def _split_key(key: str) -> tuple[str, str]:
    """把价格表键拆成 (route, model)

    - 带 `/`：`ark/doubao-seedream-5-0-260128`、`any/gpt-4o` → 前缀是路由；
    - 不带：`gpt-4o` → `("any", "gpt-4o")`，即"任意路由下都生效"。
    """
    text = str(key or "").strip()
    if "/" in text:
        route, model = text.split("/", 1)
        return route.strip().lower(), model.strip()
    return "any", text


def _entries() -> dict[str, dict]:
    """合并后的条目表：内置在前，用户填写覆盖（用户优先）"""
    merged: dict[str, dict] = {
        key: {**value, "source": SOURCE_BUILTIN}
        for key, value in BUILTIN_REFERENCE_PRICES.items()
    }
    for key, value in (_user_entries() or {}).items():
        if isinstance(value, dict):
            merged[key] = dict(value)
    return merged


def _user_entries() -> dict[str, dict]:
    """`config/pricing.yaml` 的 entries 段（带 mtime 缓存；读不到返回 {}）"""
    path = _project_root() / PRICING_REL
    try:
        stat = path.stat()
        key = (str(path), stat.st_mtime_ns, stat.st_size)
    except OSError:
        return {}
    cached = _DOC_CACHE.get("entries")
    if cached and cached[0] == key:
        return cached[1]
    try:
        raw = load_yaml(PRICING_REL)
    except Exception:  # noqa: BLE001 — 坏 YAML 不该让成本链路整体崩掉
        raw = {}
    entries = raw.get("entries") if isinstance(raw, dict) else {}
    cleaned = {str(k): dict(v) for k, v in (entries or {}).items() if isinstance(v, dict)}
    _DOC_CACHE["entries"] = (key, cleaned)
    return cleaned


def price_table() -> dict:
    """解析后的价格表（带 mtime 缓存的合并结果）

    Returns:
        `{"entries": {键: {...}}, "path": "config/pricing.yaml",
          "user_entry_count": int, "builtin_entry_count": int}`
    """
    entries = _entries()
    return {
        "entries": entries,
        "path": PRICING_REL,
        "user_entry_count": len(_user_entries() or {}),
        "builtin_entry_count": len(BUILTIN_REFERENCE_PRICES),
    }


def _candidate_keys(route: str, model: str) -> list[str]:
    """查询顺序：精确路由 → 路由内按键名 → 任意路由

    用户在设置页既可以写 `any/gpt-4o`（所有走 gpt-4o 的路由都用它），
    也可以写 `openrouter/gpt-4o`（只覆盖某条路由），后者优先。
    """
    route = str(route or "").strip().lower()
    model = str(model or "").strip()
    keys: list[str] = []
    if route and model:
        keys.append(f"{route}/{model}")
        keys.append(f"any/{model}")
    if model:
        keys.append(model)
    if route:
        keys.append(f"{route}/any")
        keys.append(f"{route}/*")
    return keys


def _as_price(value) -> float | None:
    """价格数值：非数值/负数/NaN → None（**绝不回落默认价**）"""
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number < 0:   # NaN / 负数
        return None
    return number


def resolve_price(route: str, model: str, capability: str,
                  size: str = "", usage: dict | None = None) -> dict | None:
    """查价：命中返回单价字典，**查不到返回 None**（这是刻意的，别改成兜底价）

    Args:
        route: 路由 id（`ark` / `openai` / `deepseek` / 自定义服务商 id；空 = 任意）
        model: 模型 id（provider 实际下发的那个）
        capability: `text` / `vision` / `image`
        size: 图像按尺寸分档时用（如 `dall-e-3` 的 1024x1792）
        usage: 兼容别名 —— 若调用方只有 usage 字典，可取其中的 `size`

    Returns:
        `{"unit", "in", "out", "currency", "source", "updated_at"}`；查不到 → `None`
        （图像条目还会带上 `sizes`，供调用方展示分档依据）
    """
    if not size and isinstance(usage, dict):
        size = str(usage.get("size") or "")
    table = _entries()
    entry = None
    for key in _candidate_keys(route, model):
        candidate = table.get(key)
        if isinstance(candidate, dict):
            entry = candidate
            break
    if entry is None:
        return None

    unit = str(entry.get("unit") or "").strip()
    if unit not in UNITS:
        return None
    # 能力与计价单位必须自洽：图像按张、文本/视觉按 tokens。
    # 不自洽（例如给文本模型查 image 价）说明表里写错了 → 当作查不到，
    # 免得拿错误的单位算出"看起来正常"的金额。
    if capability == "image" and unit != "image":
        return None
    if capability in ("text", "vision") and unit != "1M_tokens":
        return None

    currency = str(entry.get("currency") or "USD").upper()
    if currency not in CURRENCIES:
        currency = "USD"
    source = str(entry.get("source") or SOURCE_BUILTIN)
    updated_at = str(entry.get("updated_at") or "")[:10]

    sizes = entry.get("sizes")
    if isinstance(sizes, dict) and sizes:
        # 按尺寸分档（DALL·E 3 两种价）：命中该尺寸用档位价，未命中用条目基准价；
        # **不回落任何第三方常量**（此前 unknown-size → 0.04 就是这么来的）
        price = _as_price(sizes.get(size))
        if price is None:
            price = _as_price(entry.get("in"))
        if price is None:
            return None
        resolved = {
            "unit": "image", "in": price, "out": price,
            "currency": currency, "source": source, "updated_at": updated_at,
            "sizes": {str(k): _as_price(v) for k, v in sizes.items()},
            "size": size,
            "note": str(entry.get("note") or ""),
        }
        return resolved

    in_price = _as_price(entry.get("in"))
    out_price = _as_price(entry.get("out"))
    if in_price is None and out_price is None:
        return None
    if in_price is None:
        in_price = out_price
    if out_price is None:
        out_price = in_price
    return {
        "unit": unit, "in": in_price, "out": out_price,
        "currency": currency, "source": source, "updated_at": updated_at,
        "note": str(entry.get("note") or ""),
    }


def _usage_line(capability: str, images: int, tokens_in: int, tokens_out: int) -> str:
    """用量可读串（**用量是事实，永远能显示**）"""
    if capability == "image" or images:
        return f"{images} 张图"
    return f"{tokens_in} in + {tokens_out} out"


def _to_number(value) -> int:
    if isinstance(value, bool) or value is None:
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _is_stale(updated_at: str, *, today: date | None = None) -> bool:
    """`updated_at` 距今天超过 STALE_DAYS 天 → 过期（解析不出日期按"未知"处理，不报过期）"""
    text = str(updated_at or "")[:10]
    if not text:
        return False
    try:
        when = datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return False
    now = today or date.today()
    return (now - when).days > STALE_DAYS


def estimate(route: str, model: str, capability: str, usage: dict) -> dict:
    """估算一次调用的金额（**查不到价格时 amount 必须是 `None`**）

    Args:
        usage: `{"images": int, "tokens_in": int, "tokens_out": int, "size": str?}`

    Returns:
        `{"amount": float|None, "currency": str, "source": str, "updated_at": str,
          "basis": str, "stale": bool}`
        - 有价：`basis` 形如 `"2 张图 × ¥0.20/张"` / `"1200 in + 300 out"`
        - 无价：`basis` 形如 `"2 张图（价格未标定）"`、`amount is None`
    """
    usage = usage if isinstance(usage, dict) else {}
    images = _to_number(usage.get("images"))
    tokens_in = _to_number(usage.get("tokens_in"))
    tokens_out = _to_number(usage.get("tokens_out"))
    size = str(usage.get("size") or "")

    is_image = capability == "image" or (images > 0 and tokens_in == 0 and tokens_out == 0)
    usage_text = _usage_line("image" if is_image else capability, images, tokens_in, tokens_out)

    price = resolve_price(route, model, "image" if is_image else capability, size=size)
    if price is None:
        return {
            "amount": None,
            "currency": "CNY",
            "source": "",
            "updated_at": "",
            "basis": f"{usage_text}（价格未标定）",
            "stale": False,
        }

    symbol = currency_symbol(price["currency"])
    if is_image:
        count = images or 1
        unit_price = float(price["in"])
        amount = round(unit_price * count, 6)
        basis = f"{count} 张图 × {symbol}{unit_price:g}/张"
    else:
        amount = round((tokens_in / 1_000_000) * float(price["in"])
                       + (tokens_out / 1_000_000) * float(price["out"]), 6)
        basis = usage_text
    return {
        "amount": amount,
        "currency": price["currency"],
        "source": price["source"],
        "updated_at": price["updated_at"],
        "basis": basis,
        "stale": _is_stale(price["updated_at"]),
    }


# ── 白名单校验 + 写盘 ──

_ALLOWED_KEYS = {"route", "unit", "in", "out", "currency", "updated_at", "source",
                 "sizes", "note", "measured_unit", "measured_at"}
_ROUTE_RE_MAX = 32


def _valid_route(route: str) -> bool:
    text = str(route or "").strip().lower()
    if text in ("any", ""):
        return text == "any"
    return 1 < len(text) <= _ROUTE_RE_MAX and all(
        ch.isalnum() or ch in "-_" for ch in text) and text[0].isalpha()


def _clean_entry(key: str, raw: dict) -> dict:
    """校验并规整一条价格（非法值 raise ValueError —— 绝不静默写入垃圾）"""
    if not isinstance(raw, dict):
        raise ValueError(f"价格条目 '{key}' 必须是对象")
    unknown = sorted(set(raw) - _ALLOWED_KEYS)
    if unknown:
        raise ValueError(f"价格条目 '{key}' 含不支持的字段: {', '.join(unknown)}")

    route, model = _split_key(key)
    if "route" in raw:
        route = str(raw.get("route") or "").strip().lower() or "any"
    if not _valid_route(route):
        raise ValueError(f"价格条目 '{key}' 的 route 非法（1-32 位小写字母/数字/-/_）")
    if not model:
        raise ValueError(f"价格条目 '{key}' 缺少模型 id")

    unit = str(raw.get("unit") or "").strip()
    if unit not in UNITS:
        raise ValueError(f"价格条目 '{key}' 的 unit 必须是 {' 或 '.join(UNITS)}")

    cleaned: dict = {"unit": unit}
    for field in ("in", "out"):
        if field not in raw:
            continue
        value = _as_price(raw.get(field))
        if value is None:
            raise ValueError(f"价格条目 '{key}' 的 {field} 必须是非负数字")
        cleaned[field] = value
    if "in" not in cleaned and "out" not in cleaned:
        raise ValueError(f"价格条目 '{key}' 至少要给 in 或 out")

    currency = str(raw.get("currency") or "USD").upper()
    if currency not in CURRENCIES:
        raise ValueError(f"价格条目 '{key}' 的 currency 必须是 {' 或 '.join(CURRENCIES)}")
    cleaned["currency"] = currency

    updated_at = str(raw.get("updated_at") or "").strip()[:10] or _today()
    try:
        datetime.strptime(updated_at, "%Y-%m-%d")
    except ValueError:
        raise ValueError(f"价格条目 '{key}' 的 updated_at 必须是 YYYY-MM-DD")
    cleaned["updated_at"] = updated_at

    source = str(raw.get("source") or SOURCE_USER).strip() or SOURCE_USER
    if source not in (SOURCE_USER, SOURCE_BUILTIN, SOURCE_CALIBRATED):
        raise ValueError(f"价格条目 '{key}' 的 source 非法: {source}")
    cleaned["source"] = source

    if "sizes" in raw:
        sizes = raw.get("sizes")
        if not isinstance(sizes, dict) or not sizes:
            raise ValueError(f"价格条目 '{key}' 的 sizes 必须是非空对象（尺寸 → 单价）")
        cleaned_sizes: dict[str, float] = {}
        for dim, value in sizes.items():
            price = _as_price(value)
            if price is None:
                raise ValueError(f"价格条目 '{key}' 的 sizes.{dim} 必须是非负数字")
            cleaned_sizes[str(dim).strip()] = price
        cleaned["sizes"] = cleaned_sizes

    if "measured_unit" in raw:
        measured = _as_price(raw.get("measured_unit"))
        if measured is None:
            raise ValueError(f"价格条目 '{key}' 的 measured_unit 必须是非负数字")
        cleaned["measured_unit"] = measured
    if "measured_at" in raw:
        measured_at = str(raw.get("measured_at") or "").strip()[:10]
        try:
            datetime.strptime(measured_at, "%Y-%m-%d")
        except ValueError:
            raise ValueError(f"价格条目 '{key}' 的 measured_at 必须是 YYYY-MM-DD")
        cleaned["measured_at"] = measured_at

    note = str(raw.get("note") or "").strip()
    if note:
        cleaned["note"] = note

    cleaned["route"] = route
    return cleaned


def _entry_key(route: str, model: str) -> str:
    route = str(route or "").strip().lower() or "any"
    model = str(model or "").strip()
    return model if route == "any" else f"{route}/{model}"


def save_prices(entries: dict) -> dict:
    """写入 `config/pricing.yaml`（白名单校验；非法值 raise ValueError）

    Args:
        entries: `{键: 条目}`，键可以是 `gpt-4o`（任意路由）或 `ark/doubao-...`；
                 条目字段见本模块文档（unit/in/out/currency/updated_at/source/sizes/note）。

    Returns:
        写入并重新解析后的价格表（`price_table()` 的结果）
    """
    import yaml as _yaml

    if not isinstance(entries, dict) or not entries:
        raise ValueError("prices 必须是非空对象（键 = 模型 id 或 路由/模型 id）")

    current = _user_entries()
    stored: dict[str, dict] = {k: dict(v) for k, v in current.items()}
    for key, raw in entries.items():
        name = str(key or "").strip()
        if not name:
            raise ValueError("价格条目的键（模型 id）不能为空")
        cleaned = _clean_entry(name, raw)
        # 键统一成"规范化写法"：任意路由用裸模型名，指定路由用 route/model
        stored[_entry_key(cleaned.pop("route"), name if "/" not in name
                          else name.split("/", 1)[1])] = cleaned

    path = _project_root() / PRICING_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "_comment": ("价格表（实例级，程序写；设置页可改）。"
                     "unit: image=按张 / 1M_tokens=按百万 tokens；"
                     "in/out: image 为每张单价，1M_tokens 为输入/输出单价；"
                     "currency: CNY|USD；sizes: 按尺寸分档（可选）。"
                     "未列出的模型一律显示“未标定”，不估算金额。"),
        "entries": stored,
    }
    path.write_text(_yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
                    encoding="utf-8")
    # 缓存按 mtime 失效即可生效，但**写入方自己清一次**更稳：同一秒内连续两次写入
    # 且字节数相同时（测试与批量标定都会发生），(mtime_ns, size) 可能判不出变化。
    _DOC_CACHE.clear()
    return price_table()


def calibrate(route: str, model: str, capability: str, actual_amount: float,
              usage: dict) -> dict:
    """用"本次实际花费 + 本次用量"反推单价并写入，`source="实测标定"`

    这是规则 3 里"与标定偏差 >30% 给提示"的基准值来源：把本次实测单价一并存进
    条目的 `measured_unit`/`measured_at`，之后价格表若被改成偏差 >30% 的值，
    `staleness()` 会提示。
    """
    usage = usage if isinstance(usage, dict) else {}
    amount = _as_price(actual_amount)
    if amount is None:
        raise ValueError("actual_amount 必须是非负数字（本次实际花费）")
    capability = str(capability or "").strip().lower()
    if capability not in CAPABILITIES:
        raise ValueError(f"capability 必须是 {'/'.join(CAPABILITIES)}")

    is_image = capability == "image"
    if is_image:
        count = _to_number(usage.get("images")) or 1
        unit_price = round(amount / count, 8)
    else:
        tokens_in = _to_number(usage.get("tokens_in"))
        tokens_out = _to_number(usage.get("tokens_out"))
        if tokens_in + tokens_out <= 0:
            raise ValueError("按 tokens 标定必须给出 tokens_in / tokens_out")
        # 只给了总额时按输出价反推（输入价沿用现值），保证单值可解
        existing = resolve_price(route, model, capability) or {}
        in_price = _as_price(existing.get("in")) or 0.0
        out_price = ((amount - (tokens_in / 1_000_000) * in_price)
                     / (tokens_out / 1_000_000)) if tokens_out else 0.0
        if out_price < 0:
            # 输入价已经超过实际总额 → 用总量平均反推，避免写出负价
            in_price = out_price = round(amount / ((tokens_in + tokens_out) / 1_000_000), 8)
        unit_price = round(out_price, 8)

    entry: dict = {
        "unit": "image" if is_image else "1M_tokens",
        "currency": str(usage.get("currency") or "USD").upper(),
        "source": SOURCE_CALIBRATED,
        "updated_at": _today(),
        "measured_unit": unit_price,
        "measured_at": _today(),
        "note": str(usage.get("note") or "由实测账单/控制台账单反推"),
    }
    if is_image:
        entry["in"] = entry["out"] = unit_price
        size = str(usage.get("size") or "")
        if size:
            entry["sizes"] = {size: unit_price}
    else:
        entry["in"] = round(in_price, 8)
        entry["out"] = round(unit_price, 8)
    if entry["currency"] not in CURRENCIES:
        entry["currency"] = "USD"

    save_prices({_entry_key(route, model): entry})
    return resolve_price(route, model, capability) or {}


def staleness() -> list[dict]:
    """过期/偏差提示：`[{"route", "model", "reason", "updated_at"}]`

    - 超过 STALE_DAYS（90 天）没更新 → "价格已 N 天未更新"
    - 与实测标定值偏差 >DEVIATION_THRESHOLD（30%）→ "与实测标定偏差 X%"
    """
    notices: list[dict] = []
    today = date.today()
    for key, entry in _entries().items():
        if not isinstance(entry, dict):
            continue
        route, model = _split_key(key)
        updated_at = str(entry.get("updated_at") or "")[:10]
        if _is_stale(updated_at, today=today):
            try:
                days = (today - datetime.strptime(updated_at, "%Y-%m-%d").date()).days
            except ValueError:
                days = 0
            notices.append({
                "route": route, "model": model,
                "reason": f"价格已 {days} 天未更新（>{STALE_DAYS} 天），请核对官方定价",
                "updated_at": updated_at,
            })
        measured = _as_price(entry.get("measured_unit"))
        current = _as_price(entry.get("in"))
        if measured and current is not None and measured > 0:
            deviation = abs(current - measured) / measured
            if deviation > DEVIATION_THRESHOLD:
                notices.append({
                    "route": route, "model": model,
                    "reason": (f"价格表值 {current:g} 与实测标定值 {measured:g} "
                               f"偏差 {deviation * 100:.0f}%（>{int(DEVIATION_THRESHOLD * 100)}%）"),
                    "updated_at": updated_at,
                })
    return notices


def _capability_for(entry: dict, route: str = "") -> str:
    """审计条目 → 能力（用于设置页列候选）

    审计行里没有 capability 字段，按可靠度依次判断：
    1. 模型名里的强信号（`seedream`/`dall-e`/`flux`/`kolors` → image；`vision`/`vl-` → vision）；
    2. 路由表声明的能力（该路由只有一种能力时直接采用）；
    3. 兜底 `text`。
    """
    text = " ".join(str(entry.get(k) or "") for k in ("action", "capability", "model")).lower()
    if _to_number(entry.get("images")) > 0 or any(
            hint in text for hint in ("image", "生图", "dall-e", "seedream", "flux", "kolors", "t2i")):
        return "image"
    if "vision" in text or "vl-" in text or "vl_" in text or "vision-exp" in text:
        return "vision"
    if route:
        try:
            from src.providers.routes import BUILTIN_BY_ROUTE
            spec = BUILTIN_BY_ROUTE.get(route)
            caps = list(getattr(spec, "capabilities", []) or [])
            # 只有"该路由**只**有视觉能力"时才敢断言 vision；
            # deepseek 这类 text+vision 双能力路由必须以模型名为准
            # （deepseek-v4-flash 是文本模型，不能因为路由支持 vision 就算视觉）
            if caps == ["image"]:
                return "image"
            if caps == ["vision"]:
                return "vision"
        except Exception:  # noqa: BLE001 — 路由表不可用不影响聚合
            pass
    return "text"


def observed_models() -> list[dict]:
    """从审计日志聚合"实际用过的 路由 × 模型 × 能力"，给设置页列候选

    读 `data_root()/audit` 下的 `audit-*.jsonl`；**读不到就返回 `[]`（不抛）**。
    """
    try:
        audit_dir = Path(data_root()) / "audit"
        files = sorted(audit_dir.glob("audit-*.jsonl"))
    except Exception:  # noqa: BLE001 — 目录不可读不该让设置页崩
        return []
    if not files:
        return []

    aggregated: dict[tuple[str, str, str], dict] = {}
    for path in files:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                lines = handle.readlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(entry, dict):
                continue
            model = str(entry.get("model") or "").strip()
            if not model or model == "unknown":
                continue
            route = str(entry.get("provider") or "").strip().lower()
            capability = _capability_for(entry, route)
            bucket = aggregated.setdefault((route, model, capability), {
                "route": route, "model": model, "capability": capability,
                "calls": 0, "tokens": 0, "images": 0,
                "priced": resolve_price(route, model, capability) is not None,
                "last_seen": "",
            })
            bucket["calls"] += 1
            bucket["tokens"] += _to_number(entry.get("tokens"))
            bucket["images"] += _to_number(entry.get("images"))
            stamp = str(entry.get("timestamp") or "")
            if stamp > bucket["last_seen"]:
                bucket["last_seen"] = stamp
    return sorted(aggregated.values(),
                  key=lambda item: (item["route"], item["model"], item["capability"]))


def currency_of(route: str, model: str, capability: str) -> str:
    """该 路由×模型×能力 的货币（查不到 → CNY，界面口径）"""
    price = resolve_price(route, model, capability)
    return price["currency"] if price else "CNY"


def _reset_cache() -> None:
    """清解析缓存（测试用；生产走 mtime 自动失效）"""
    _DOC_CACHE.clear()


__all__ = [
    "PRICING_REL",
    "BUILTIN_REFERENCE_PRICES",
    "SOURCE_USER", "SOURCE_BUILTIN", "SOURCE_CALIBRATED",
    "STALE_DAYS", "DEVIATION_THRESHOLD",
    "price_table", "resolve_price", "estimate", "observed_models",
    "save_prices", "calibrate", "staleness", "currency_symbol", "currency_of",
    "pricing_path",
]


def pricing_path() -> Path:
    """`config/pricing.yaml` 的绝对路径（设置页可展示"写到哪儿"）

    路径经 `_project_root()` 解析，所以 `ECOMM_PROJECT_ROOT`（测试/挂载卷重定向）同样生效。
    """
    return _project_root() / PRICING_REL
