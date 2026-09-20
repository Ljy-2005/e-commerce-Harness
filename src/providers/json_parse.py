"""宽松 JSON 解析（A33）——LLM 输出是"半结构化"的，不能只认 `json.loads`

实测事故：模型把 JSON 放在 ```json 围栏里（或前面加一句说明）返回，
`json.loads` 直接失败：

- 商品分析员输出被当成纯文本 → `confidence_score` 读成 0 → 系统播报"置信度偏低"，
  协调者白白重跑一轮（多花一次视觉调用）；
- 审查员输出被当成纯文本 → `verdict` 丢失 → 引擎 `result.get("verdict", "pass")`
  **静默按通过放行**，质量门禁彻底失效。

解析顺序：① 原文 ② 去围栏 ③ 文中第一个**括号平衡**的 `{…}`（正确处理字符串内的
花括号与转义）。可选地调用方自己决定失败后如何回落（`{"raw": …}` / `{"text": …}`）。
"""

from typing import Any


def _first_fence_body(text: str) -> str:
    """取第一个 ``` 围栏内的内容（` ```json ` / ` ``` ` 都支持）"""
    start = text.find("```")
    if start < 0:
        return ""
    body_start = text.find("\n", start)
    if body_start < 0:
        return ""
    end = text.find("```", body_start)
    return text[body_start + 1:end if end >= 0 else len(text)]


def strip_code_fence(text: str) -> str:
    """去掉 Markdown 代码围栏；没有围栏时返回 strip 后的原文"""
    if not isinstance(text, str):
        return ""
    if "```" in text:
        body = _first_fence_body(text)
        if body.strip():
            return body.strip()
    return text.strip()


def extract_json_object(text: str) -> str | None:
    """扫描出第一个括号平衡的 `{…}` 片段（识别字符串与转义）"""
    if not isinstance(text, str):
        return None
    for start, char in enumerate(text):
        if char != "{":
            continue
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            current = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif current == "\\":
                    escaped = True
                elif current == '"':
                    in_string = False
                continue
            if current == '"':
                in_string = True
            elif current == "{":
                depth += 1
            elif current == "}":
                depth -= 1
                if depth == 0:
                    return text[start:index + 1]
        return None   # 第一个 { 没有闭合 → 整体截断，直接放弃
    return None


def parse_json_loose(text: Any) -> dict | None:
    """尽最大努力把 LLM 文本解析成 dict；失败返回 None（绝不抛异常）"""
    if not isinstance(text, str) or not text.strip():
        return None

    import json

    candidates = [text.strip()]
    fenced = strip_code_fence(text)
    if fenced and fenced not in candidates:
        candidates.append(fenced)

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(parsed, dict):
            return parsed

    for candidate in candidates:
        snippet = extract_json_object(candidate)
        if not snippet:
            continue
        try:
            parsed = json.loads(snippet)
        except (ValueError, TypeError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None
