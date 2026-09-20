"""多图审查结果的归一化（A43，真实会话实测发现）

真实会话（`72d5ef86831c4f99`）里审查员**干得很好但不合规**：它按协调者要求"对每张图输出评分"，
返回了逐变体结构

```json
{"results": [{"variant": "variant_1", "overall_score": 62.4,
              "dimension_scores": {...}, "top_issues": [...],
              "verdict": "fail", "needs_human_review": true}, ...]}
```

顶层**没有** `overall_score` / `verdict` → A33 的门禁（正确地）判为"无法解析"→ 转人工。
审查内容本身是有效的（它逐字读出了生成图里被臆造的 `NUTRIVA®` 品牌、缺失的 GMP 条、AI 水印），
所以缺的是"把逐变体结果汇总成顶层判定"这一步。

归一化规则（幂等，可用于 Agent 内部与引擎门禁两处）：
- 顶层已有合法 `verdict` → 原样返回（不改动任何字段）
- 否则若存在非空的 `results[]`：分数取**平均**，判定取**最严重**（fail > retry > pass），
  `top_issues` 合并去重（上限 10），`needs_human_review` 取任一为真，
  并保留 `results` 供界面逐张展示
"""

from src.harness.output_pipeline import is_number

VERDICT_SEVERITY = {"pass": 0, "retry": 1, "fail": 2}
VALID_VERDICTS = tuple(VERDICT_SEVERITY)
MAX_TOP_ISSUES = 10


def _worst_verdict(verdicts) -> str:
    worst = ""
    for verdict in verdicts:
        if verdict in VERDICT_SEVERITY and (
                not worst or VERDICT_SEVERITY[verdict] > VERDICT_SEVERITY[worst]):
            worst = verdict
    return worst


def normalize_review_payload(review: dict) -> tuple[dict, str]:
    """把逐变体审查结果汇总为顶层判定

    Returns: `(归一化后的 review, 说明)`；无需归一化时说明为 `""`（原对象原样返回）。
    """
    if not isinstance(review, dict):
        return review, ""
    if review.get("verdict") in VALID_VERDICTS:
        return review, ""

    results = review.get("results")
    if not isinstance(results, list) or not results:
        return review, ""
    entries = [r for r in results if isinstance(r, dict)]
    if not entries:
        return review, ""

    scores = [r.get("overall_score") for r in entries if is_number(r.get("overall_score"))]
    verdict = _worst_verdict(r.get("verdict") for r in entries)

    merged = dict(review)
    if scores:
        merged["overall_score"] = round(sum(scores) / len(scores), 1)
    if verdict:
        merged["verdict"] = verdict

    issues: list = []
    for entry in entries:
        for issue in (entry.get("top_issues") or []):
            if issue not in issues:
                issues.append(issue)
    if issues:
        merged["top_issues"] = issues[:MAX_TOP_ISSUES]

    praises: list = []
    for entry in entries:
        for praise in (entry.get("top_praises") or []):
            if praise not in praises:
                praises.append(praise)
    if praises:
        merged["top_praises"] = praises[:MAX_TOP_ISSUES]

    # 逐变体维度分取平均（缺失的维度不参与）
    dimensions: dict[str, float] = {}
    for entry in entries:
        for key, value in (entry.get("dimension_scores") or {}).items():
            if is_number(value):
                dimensions.setdefault(key, [])
                dimensions[key].append(value)
    if dimensions:
        merged["dimension_scores"] = {k: round(sum(v) / len(v), 1) for k, v in dimensions.items()}

    if any(bool(r.get("needs_human_review")) for r in entries):
        merged["needs_human_review"] = True

    detail = (f"逐变体审查结果已汇总：{len(entries)} 张图，"
              f"分数={'/'.join(str(s) for s in scores) if scores else '未给出'}"
              f"→ 平均 {merged.get('overall_score', '未给出')}，"
              f"判定取最严重 = {verdict or '未知'}")
    return merged, detail
