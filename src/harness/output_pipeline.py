"""输出验证管道 — Schema 校验 + 字段完整性 + 业务规则

第三轮审计 B1-5：本模块处理的是 **LLM 原始输出**（不可信输入），任何一条校验
都不允许抛异常——此前 `BusinessRuleValidator` 直接做 `0 <= "85" <= 100` 比较，
字符串分数 → `TypeError`，调用处（engine）无 try → **整轮群聊失败**。
现在：所有比较先做类型判定（`bool` 也不算数字），`OutputPipeline.validate`
再加一层兜底 try/except，保证"校验器只返回结果，不抛异常"。
"""

from dataclasses import dataclass, field


def is_number(value) -> bool:
    """真正的数字判定（排除 bool：`True <= 100` 成立但不是合法分数）"""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def is_non_blank_str(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


@dataclass
class OutputResult:
    passed: bool = True
    errors: list[str] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)


class SchemaValidator:
    """验证 Agent 输出是否符合预期的 Schema"""

    # 每个 Agent 输出的必要字段
    REQUIRED_FIELDS = {
        "商品分析员": ["category", "features", "target_audience", "confidence_score"],
        "品类专项分析员": ["marketing_angles"],
        "提示词生成员": ["main_image", "scene_images"],
        "生图员": ["images"],
        "审查员": ["overall_score", "dimension_scores", "verdict"],
        "合规审查员": ["passed", "risk_level"],
    }

    def validate(self, agent_name: str, output: dict) -> OutputResult:
        """验证输出包含必要字段"""
        result = OutputResult()
        required = self.REQUIRED_FIELDS.get(agent_name, [])

        if not required:
            return result  # 无 Schema 要求的 Agent 直接通过

        if not isinstance(output, dict):
            result.errors.append(f"{agent_name} 输出不是 dict 类型")
            result.passed = False
            return result

        for fld in required:
            if fld not in output or output[fld] is None:
                result.missing_fields.append(fld)
                result.errors.append(f"{agent_name} 缺少必要字段: {fld}")

        result.passed = len(result.errors) == 0
        return result


class FieldCompletenessChecker:
    """字段完整性检查 — 关键字段不能为空字符串/空列表"""

    def check(self, agent_name: str, output: dict) -> OutputResult:
        result = OutputResult()

        if not isinstance(output, dict):
            result.errors.append(f"{agent_name} 输出不是 dict 类型")
            result.passed = False
            return result

        checks = {
            "商品分析员": {
                "category": is_non_blank_str,
                "features": lambda v: isinstance(v, list) and len(v) > 0,
            },
            "提示词生成员": {
                # B1-5：此前写成 `bool(v.strip()) if isinstance(v, dict) else bool(v)`，
                # 把 dict 当字符串 strip() → AttributeError；prompt 必须是**非空字符串**
                "main_image.prompt": is_non_blank_str,
            },
            "审查员": {
                "overall_score": lambda v: is_number(v) and 0 <= v <= 100,
                "verdict": lambda v: v in ("pass", "retry", "fail"),
            },
        }

        checks_for = checks.get(agent_name, {})
        for fld, validator in checks_for.items():
            value = output
            for part in fld.split("."):
                if isinstance(value, dict):
                    value = value.get(part)
                else:
                    value = None
                    break

            if not validator(value):
                result.errors.append(f"{agent_name}.{fld} 验证失败: {value}")
                result.passed = False

        return result


class BusinessRuleValidator:
    """业务规则校验"""

    def validate(self, agent_name: str, output: dict) -> OutputResult:
        result = OutputResult()

        if not isinstance(output, dict):
            result.errors.append(f"{agent_name} 输出不是 dict 类型")
            result.passed = False
            return result

        if agent_name == "审查员":
            score = output.get("overall_score", 0)
            if not is_number(score):
                result.errors.append(f"overall_score 类型非法: {score!r}")
            elif not (0 <= score <= 100):
                result.errors.append(f"overall_score 超出范围: {score}")
            verdict = output.get("verdict", "")
            if verdict not in ("pass", "retry", "fail"):
                result.errors.append(f"无效 verdict: {verdict}")

        elif agent_name == "商品分析员":
            confidence = output.get("confidence_score", 0)
            if not is_number(confidence):
                result.errors.append(f"confidence_score 类型非法: {confidence!r}")
            elif not (0 <= confidence <= 100):
                result.errors.append(f"confidence_score 超出范围: {confidence}")

        elif agent_name == "合规审查员":
            risk = output.get("risk_level", "")
            if risk not in ("low", "medium", "high"):
                result.errors.append(f"无效 risk_level: {risk}")

        result.passed = len(result.errors) == 0
        return result


class OutputPipeline:
    """输出验证管道

    阶段: Schema 校验 → 字段完整性 → 业务规则
    """

    def __init__(self):
        self.schema = SchemaValidator()
        self.completeness = FieldCompletenessChecker()
        self.business = BusinessRuleValidator()

    def validate(self, agent_name: str, output: dict) -> OutputResult:
        """完整输出验证管道（第三轮审计 B1-5：保证不抛异常）

        校验对象是不可信的 LLM 输出：任何未预期的类型/结构都只能变成
        `passed=False` + 可读 errors，绝不能把异常抛给调用方（否则整轮群聊失败）。
        """
        try:
            result = self.schema.validate(agent_name, output)
            if not result.passed:
                return result

            completeness = self.completeness.check(agent_name, output)
            if not completeness.passed:
                result.errors.extend(completeness.errors)
                result.passed = False

            business = self.business.validate(agent_name, output)
            if not business.passed:
                result.errors.extend(business.errors)
                result.passed = False

            return result
        except Exception as e:  # noqa: BLE001 — 兜底：校验器不得让调用方崩
            return OutputResult(passed=False,
                                errors=[f"{agent_name} 输出校验异常: {type(e).__name__}: {e}"])
