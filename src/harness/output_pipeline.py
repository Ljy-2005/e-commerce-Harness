"""输出验证管道 — Schema 校验 + 字段完整性 + 业务规则"""

from dataclasses import dataclass, field
from typing import Optional


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

        for field in required:
            if field not in output or output[field] is None:
                result.missing_fields.append(field)
                result.errors.append(f"{agent_name} 缺少必要字段: {field}")

        result.passed = len(result.errors) == 0
        return result


class FieldCompletenessChecker:
    """字段完整性检查 — 关键字段不能为空字符串/空列表"""

    def check(self, agent_name: str, output: dict) -> OutputResult:
        result = OutputResult()

        checks = {
            "商品分析员": {
                "category": lambda v: bool(v and v.strip()),
                "features": lambda v: isinstance(v, list) and len(v) > 0,
            },
            "提示词生成员": {
                "main_image.prompt": lambda v: bool(v and v.strip()) if isinstance(v, dict) else bool(v),
            },
            "审查员": {
                "overall_score": lambda v: isinstance(v, (int, float)) and 0 <= v <= 100,
                "verdict": lambda v: v in ("pass", "retry", "fail"),
            },
        }

        checks_for = checks.get(agent_name, {})
        for field, validator in checks_for.items():
            value = output
            for part in field.split("."):
                if isinstance(value, dict):
                    value = value.get(part)
                else:
                    value = None
                    break

            if not validator(value):
                result.errors.append(f"{agent_name}.{field} 验证失败: {value}")
                result.passed = False

        return result


class BusinessRuleValidator:
    """业务规则校验"""

    def validate(self, agent_name: str, output: dict) -> OutputResult:
        result = OutputResult()

        if agent_name == "审查员":
            score = output.get("overall_score", 0)
            if not (0 <= score <= 100):
                result.errors.append(f"overall_score 超出范围: {score}")
            verdict = output.get("verdict", "")
            if verdict not in ("pass", "retry", "fail"):
                result.errors.append(f"无效 verdict: {verdict}")

        elif agent_name == "商品分析员":
            confidence = output.get("confidence_score", 0)
            if not (0 <= confidence <= 100):
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
        """完整输出验证管道"""
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
