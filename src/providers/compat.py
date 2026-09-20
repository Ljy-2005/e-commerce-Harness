"""OpenAI 兼容端点的共用调用与"输出守卫"（A32 / A33）

DeepSeek / OpenAI / Qwen / 火山方舟 / 自定义服务商都走 `POST {base}/chat/completions`，
响应结构一致；此前四个 provider 各写一份解析，且**都缺失**同一批保护：

1. `content` 为空串时回落 `{"text": ""}` 当成功——推理模型（DeepSeek V4）的思考 token
   也计入 `max_tokens`，实测 `finish_reason=length` + `reasoning_tokens=4097` +
   `content=""`，于是 Agent"输出为空"却算成功（品类专项分析员连续两轮空输出）；
2. `finish_reason=length` 造成的 JSON 截断只表现为"缺少必要字段"，用户看不懂；
3. 代码围栏 JSON 解析失败（见 `json_parse`）；
4. `choices` 缺失时 `data["choices"][0]` 直接 KeyError。

这里统一：解析 → 空/截断/非法结构一律变成可读错误，并在截断时**自动把预算提升 4 倍
重试一次**（上限 `MAX_TOKENS_CEILING`）。
"""

from typing import Callable

from src.providers.base import provider_error
from src.providers.json_parse import parse_json_loose

DEFAULT_TIMEOUT_S = 120.0      # 推理模型单次 20–30s 很常见（实测 28.5s）
MAX_TOKENS_CEILING = 32768     # 自动升级的上限，避免把上游打爆


def _usage(data: dict) -> tuple[int, int, int, int]:
    """返回 (tokens_in, tokens_out, tokens_total, reasoning_tokens)"""
    usage = data.get("usage") or {}
    tokens_in = int(usage.get("prompt_tokens") or 0)
    tokens_out = int(usage.get("completion_tokens") or 0)
    total = int(usage.get("total_tokens") or (tokens_in + tokens_out))
    details = usage.get("completion_tokens_details") or {}
    reasoning = int(details.get("reasoning_tokens") or 0)
    return tokens_in, tokens_out, total, reasoning


def _message(data: dict) -> tuple[str, str, str]:
    """返回 (content, finish_reason, reasoning_content)；结构非法时 content 为空"""
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return "", "", ""
    choice = choices[0] if isinstance(choices[0], dict) else {}
    message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
    content = message.get("content")
    reasoning = message.get("reasoning_content")
    return (content if isinstance(content, str) else "",
            str(choice.get("finish_reason") or ""),
            reasoning if isinstance(reasoning, str) else "")


def _truncation_error(label: str, budget: int, reasoning_tokens: int,
                      reasoning_text: str, *, json_expected: bool) -> str:
    spent = (f"其中思考约 {reasoning_tokens} tokens"
             if reasoning_tokens else f"其中思考约 {len(reasoning_text)} 字")
    kind = "JSON 被截断（不完整）" if json_expected else "输出为空"
    return (f"{label} 输出被截断：max_tokens={budget} 已用尽（{spent}，finish_reason=length），"
            f"{kind}；请在 model_mapping/路由配置里调大 max_tokens，或让任务描述更精简")


def _error_with_reasoning(label: str, finish_reason: str, reasoning: str) -> str:
    detail = f"（finish_reason={finish_reason or '未知'}）"
    if reasoning.strip():
        detail += f"；模型只产出了思考内容：{reasoning.strip()[:200]}"
    return f"{label} 返回空内容{detail}"


def parse_completion(
    data: dict,
    *,
    label: str,
    json_mode: bool = False,
    parse_json: bool = False,
    budget: int = 0,
    reasoning_tokens: int = 0,
) -> tuple[dict | None, str, str]:
    """从响应体里取业务内容

    Returns: `(content, error, finish_reason)`；`content` 非空表示成功。
    """
    content_str, finish_reason, reasoning = _message(data)
    wants_json = bool(json_mode or parse_json)

    if not content_str.strip():
        if finish_reason == "length":
            return None, _truncation_error(label, budget, reasoning_tokens, reasoning,
                                           json_expected=wants_json), finish_reason
        return None, _error_with_reasoning(label, finish_reason, reasoning), finish_reason

    if wants_json:
        parsed = parse_json_loose(content_str)
        if parsed is None:
            if finish_reason == "length":
                return None, _truncation_error(label, budget, reasoning_tokens, reasoning,
                                               json_expected=True), finish_reason
            return ({"raw": content_str} if json_mode else {"text": content_str}), "", finish_reason
        return parsed, "", finish_reason

    return {"text": content_str}, "", finish_reason


async def openai_compatible_chat(
    *,
    base_url: str,
    api_key: str,
    body: dict,
    label: str,
    model: str = "",
    timeout: float = DEFAULT_TIMEOUT_S,
    json_mode: bool = False,
    parse_json: bool = False,
    cost_fn: Callable[[str, int, int], float] | None = None,
    allow_budget_upgrade: bool = True,
) -> dict:
    """调用 OpenAI 兼容 `/chat/completions`，带输出守卫与一次性预算升级"""
    import httpx

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    budget = body.get("max_tokens")
    budget = budget if isinstance(budget, int) and budget > 0 else 0
    attempted_upgrade = False
    last: dict = {"error": f"{label} 调用失败（无响应）"}

    for _ in range(2):
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(f"{base_url}/chat/completions", headers=headers, json=body)

        if resp.status_code != 200:
            err = provider_error(label, resp, base_url, api_key)
            if attempted_upgrade:
                # 升级预算被上游拒绝（如模型不支持该上限）：把两次原因都带上
                err["error"] = f"{last.get('error', '')}；自动提升预算重试也被拒绝：{err['error']}"
            return err

        data = resp.json()
        tokens_in, tokens_out, total, reasoning_tokens = _usage(data)
        content, error, finish_reason = parse_completion(
            data, label=label, json_mode=json_mode, parse_json=parse_json,
            budget=budget, reasoning_tokens=reasoning_tokens,
        )

        envelope = {
            "tokens_used": total,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cost_usd": cost_fn(model, tokens_in, tokens_out) if cost_fn else 0.0,
        }

        if content is not None:
            return {"content": content, **envelope}

        last = {"error": error, **envelope}
        if (allow_budget_upgrade and not attempted_upgrade
                and finish_reason == "length" and budget
                and min(budget * 4, MAX_TOKENS_CEILING) > budget):
            attempted_upgrade = True
            budget = min(budget * 4, MAX_TOKENS_CEILING)
            body = {**body, "max_tokens": budget}
            continue
        return last

    return last
