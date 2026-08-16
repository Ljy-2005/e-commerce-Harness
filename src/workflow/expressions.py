"""Workflow 编排层 — 极简表达式求值

只支持三种上下文 + 基本运算，零模板引擎依赖：
- $inputs.x          实例化输入
- $steps.<node>.outputs.k   上游节点产出
- $ctx.k             运行期变量（retries 等）

运算符：== != >= <= > < in / not in、and / or / not、括号、列表字面量 [a, b]
"""

import re

_TOKEN_RE = re.compile(
    r"""\s*(?:
        (?P<num>-?\d+(?:\.\d+)?)
      | (?P<str>"[^"]*"|'[^']*')
      | (?P<list>\[[^\]]*\])
      | (?P<path>\$[A-Za-z_][\w]*\.(?:[A-Za-z_][\w]*\.)*[A-Za-z_][\w]*)
      | (?P<op>==|!=|>=|<=|>|<|\bnot\s+in\b|\bin\b|\band\b|\bor\b|\bnot\b|\(|\))
      | (?P<word>[A-Za-z_][\w]*)
    )""",
    re.VERBOSE,
)

# 允许的路径根
_ALLOWED_ROOTS = ("inputs", "steps", "ctx")


def resolve_path(path: str, scope: dict):
    """解析 $inputs.x.y 路径，缺失返回 None（不抛异常）"""
    body = path[1:]  # 去掉 $
    parts = body.split(".")
    if parts[0] not in _ALLOWED_ROOTS:
        raise ValueError(f"不允许的路径根: {parts[0]}（仅 inputs/steps/ctx）")
    current = scope.get(parts[0])
    for p in parts[1:]:
        if isinstance(current, dict):
            current = current.get(p)
        else:
            return None
    return current


class _Token:
    __slots__ = ("kind", "value")
    def __init__(self, kind, value):
        self.kind = kind
        self.value = value
    def __repr__(self):
        return f"{self.kind}:{self.value}"


def _tokenize(expr: str) -> list[_Token]:
    tokens = []
    pos = 0
    while pos < len(expr):
        m = _TOKEN_RE.match(expr, pos)
        if not m or m.end() == pos:
            raise ValueError(f"表达式无法解析（位置 {pos}）: {expr[pos:pos+12]}...")
        pos = m.end()
        kind = m.lastgroup
        if kind is None:
            continue
        val = m.group().strip()  # 去掉 \s* 前缀吞入的空格
        if kind == "str":
            val = val[1:-1]
        elif kind == "num":
            val = float(val) if "." in val else int(val)
        elif kind == "list":
            val = [s.strip().strip('"').strip("'") for s in val[1:-1].split(",") if s.strip()]
        elif kind == "word":
            val = str(val)  # 裸词 → 字符串字面量（如 verdict == retry）
        tokens.append(_Token(kind, val))
    return tokens


class _Parser:
    def __init__(self, tokens, scope):
        self.tokens = tokens
        self.pos = 0
        self.scope = scope

    def peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def next(self):
        t = self.peek()
        self.pos += 1
        return t

    def parse(self):
        if not self.tokens:
            return True  # 空表达式恒真
        value = self.parse_or()
        if self.peek() is not None:
            raise ValueError(f"表达式存在多余内容: {self.peek()}")
        return value

    def parse_or(self):
        left = self.parse_and()
        while self.peek() and self.peek().value == "or":
            self.next()
            right = self.parse_and()
            left = bool(left) or bool(right)
        return left

    def parse_and(self):
        left = self.parse_not()
        while self.peek() and self.peek().value == "and":
            self.next()
            right = self.parse_not()
            left = bool(left) and bool(right)
        return left

    def parse_not(self):
        if self.peek() and self.peek().value == "not":
            self.next()
            return not bool(self.parse_not())
        return self.parse_comparison()

    def parse_comparison(self):
        left = self.parse_primary()
        t = self.peek()
        if t and t.value in ("==", "!=", ">=", "<=", ">", "<", "in", "not in"):
            self.next()
            right = self.parse_primary()
            return _compare(t.value, left, right)
        return left

    def parse_primary(self):
        t = self.peek()
        if t is None:
            raise ValueError("表达式不完整")
        if t.kind == "op" and t.value == "(":
            self.next()
            v = self.parse_or()
            closing = self.next()
            if closing is None or closing.value != ")":
                raise ValueError("缺少右括号")
            return v
        if t.kind in ("num", "str", "list", "word"):
            self.next()
            return t.value
        if t.kind == "path":
            self.next()
            return resolve_path(t.value, self.scope)
        raise ValueError(f"无法识别的标记: {t}")


def _compare(op: str, left, right):
    if op == "==":
        return left == right
    if op == "!=":
        return left != right
    # 缺失值（None）参与大小比较 → 恒 False，不抛异常
    if left is None or right is None:
        return False
    if op == ">":
        return left > right
    if op == "<":
        return left < right
    if op == ">=":
        return left >= right
    if op == "<=":
        return left <= right
    if op == "in":
        try:
            return left in right
        except TypeError:
            return False
    if op == "not in":
        try:
            return left not in right
        except TypeError:
            return True
    raise ValueError(f"未知运算符: {op}")


def eval_expr(expr: str, scope: dict):
    """求值极简表达式。scope = {"inputs": ..., "steps": ..., "ctx": ...}"""
    if not isinstance(expr, str) or not expr.strip():
        return True
    return _Parser(_tokenize(expr), scope).parse()


_INTERP_RE = re.compile(r"\{([$A-Za-z_][\w$.]*)\}")


def render_string(template: str, scope: dict) -> str:
    """把 {inputs.platform} / {steps.analyze.outputs.category} 插值替换为字符串"""
    def repl(m):
        ref = m.group(1)
        if ref.startswith("$"):
            value = resolve_path(ref, scope)
        else:
            parts = ref.split(".")
            if parts and parts[0] in ("inputs", "steps", "ctx"):
                value = resolve_path("$" + ref, scope)
            else:
                value = scope.get("inputs", {}).get(ref)
        return str(value) if value is not None else m.group(0)
    return _INTERP_RE.sub(repl, template)


def build_scope(job_inputs: dict, step_outputs: dict, ctx: dict) -> dict:
    """构造求值 scope。step_outputs = {node_name: outputs}

    表达式 `$steps.<node>.outputs.<key>` 与之对应。
    """
    return {
        "inputs": job_inputs,
        "steps": {node: {"outputs": outputs} for node, outputs in step_outputs.items()},
        "ctx": ctx,
    }
