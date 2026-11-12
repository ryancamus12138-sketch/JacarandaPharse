"""计算字段的公式（ADR 0035）：有的字段不该让模型去单据上找，而是由别的字段算出来。

例如酒店账单的晚数 = 离店日期 − 入住日期；明细的金额 = 数量 × 单价；总额 = sum(items.amount)。
单据上 Rooms 一栏的 1 是房间数，让模型找"晚数"它可能抓错；算出来的不会错，出处就是参与计算的字段。

公式只能写这些（不执行任意代码，用 Python 的语法树逐个节点检查）：
- 字段 ID：check_in、total；表格的列在"每一行的公式"里直接写列名（quantity * unit_price）
- 数字、+ - * /、括号
- 日期相减得到相差几天（check_out - check_in）
- 函数：sum(items.amount)、count(items)、round(x)、round(x, 2)、abs(x)、min(a, b, …)、max(a, b, …)
- 日期范围（ADR 0039）：period.start、period.end 是日期，period.days 是一共几天（首尾都算）
文字字段不能参与计算。算的结果是数字。
"""

from __future__ import annotations

import ast
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from jacara.core.daterange import parse_date_range
from jacara.core.textvalue import parse_date

Kind = str
"""表达式的类型："number" 或 "date"。"""

_RANGE_PARTS = {"start": "date", "end": "date", "days": "number"}
"""日期范围字段能取的部分（ADR 0039）。"""
_RANGE_NAMES = {"start": "开始", "end": "结束", "days": "天数"}

_AGGREGATES = {"sum", "count"}
_FUNCTIONS = {"round", "abs", "min", "max"}
_SYMBOLS = {ast.Add: "+", ast.Sub: "−", ast.Mult: "×", ast.Div: "÷"}


class FormulaError(ValueError):
    """公式写得不对。说明直接给人看。"""


@dataclass(frozen=True)
class Formula:
    """检查过的一条公式。"""

    text: str
    tree: ast.expr
    fields: frozenset[str]
    """用到的普通字段（check_in）。"""
    columns: frozenset[str]
    """用到的同一行的列（每一行的公式才有）。"""
    tables: frozenset[str]
    """用到的整张表（sum(items.amount)、count(items)）。"""

    def readable(self, labels: Mapping[str, str]) -> str:
        """给人看的写法：字段 ID 换成中文名，check_out - check_in → 离店日期 − 入住日期。"""
        return _readable(self.tree, labels)


@dataclass(frozen=True)
class Scope:
    """公式里能用哪些名字，以及它们的类型。"""

    fields: Mapping[str, str]
    """普通字段 ID → 类型（string、number、date）。"""
    tables: Mapping[str, Mapping[str, str]]
    """表名 → {列名 → 类型}。"""
    row: Mapping[str, str] | None = None
    """每一行的公式：同一行的列名 → 类型。普通字段的公式是 None。"""


def parse_formula(text: str, scope: Scope) -> Formula:
    """检查公式：语法、用到的名字是否存在、类型能不能算。写错了抛 FormulaError。"""
    source = text.strip()
    if not source:
        raise FormulaError("公式是空的")
    if len(source) > 500:
        raise FormulaError("公式太长了（最多 500 个字）")
    try:
        tree = ast.parse(source, mode="eval").body
    except SyntaxError as err:
        raise FormulaError(f"公式写得不对：{source}") from err
    used: dict[str, set[str]] = {"fields": set(), "columns": set(), "tables": set()}
    kind = _check(tree, scope, used)
    if kind != "number":
        raise FormulaError("公式算出来要是数字（两个日期相减得到天数）")
    return Formula(
        text=source,
        tree=tree,
        fields=frozenset(used["fields"]),
        columns=frozenset(used["columns"]),
        tables=frozenset(used["tables"]),
    )


def _check(node: ast.expr, scope: Scope, used: dict[str, set[str]]) -> Kind:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise FormulaError(f"公式里只能写数字，不能写 {node.value!r}")
        return "number"
    if isinstance(node, ast.Name):
        return _name(node.id, scope, used)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        if _check(node.operand, scope, used) != "number":
            raise FormulaError("日期前面不能加正负号")
        return "number"
    if isinstance(node, ast.BinOp) and type(node.op) in _SYMBOLS:
        left = _check(node.left, scope, used)
        right = _check(node.right, scope, used)
        if left == right == "number":
            return "number"
        if left == right == "date" and isinstance(node.op, ast.Sub):
            return "number"  # 两个日期相减 = 相差几天
        raise FormulaError("日期只能和日期相减（得到天数），不能和数字一起算")
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
        return _call(node.func.id, node.args, scope, used)
    if isinstance(node, ast.Attribute):
        if isinstance(node.value, ast.Name) and _kind_of(node.value.id, scope) == "date_range":
            if node.attr not in _RANGE_PARTS:
                raise FormulaError(f"日期范围只能取 .start、.end、.days，不能取 .{node.attr}")
            if scope.row is not None and node.value.id in scope.row:
                used["columns"].add(node.value.id)
            else:
                used["fields"].add(node.value.id)
            return _RANGE_PARTS[node.attr]
        raise FormulaError("表格的列要放在 sum() 里，例如 sum(items.amount)")
    raise FormulaError("公式里只能用字段、数字、+ - * /、括号和 sum、count、round、abs、min、max")


def _kind_of(name: str, scope: Scope) -> str | None:
    if scope.row is not None and name in scope.row:
        return scope.row[name]
    return scope.fields.get(name)


def _name(name: str, scope: Scope, used: dict[str, set[str]]) -> Kind:
    if scope.row is not None and name in scope.row:
        kind = scope.row[name]
        used["columns"].add(name)
    elif name in scope.fields:
        kind = scope.fields[name]
        used["fields"].add(name)
    elif name in scope.tables:
        raise FormulaError(f"{name} 是一张表，要写成 sum({name}.列名) 或 count({name})")
    else:
        raise FormulaError(f"没有叫 {name} 的字段")
    if kind == "date_range":
        raise FormulaError(f"{name} 是日期范围，要写 {name}.start、{name}.end 或 {name}.days")
    if kind not in ("number", "date"):
        raise FormulaError(f"{name} 是文字字段，不能参与计算")
    return kind


def _call(func: str, args: Sequence[ast.expr], scope: Scope, used: dict[str, set[str]]) -> Kind:
    if func in _AGGREGATES:
        if scope.row is not None:
            raise FormulaError(f"每一行的公式里不能用 {func}()")
        if len(args) != 1:
            raise FormulaError(f"{func}() 里只能写一项")
        arg = args[0]
        if func == "count":
            if not (isinstance(arg, ast.Name) and arg.id in scope.tables):
                raise FormulaError("count() 里写表名，例如 count(items)")
            used["tables"].add(arg.id)
            return "number"
        if not (
            isinstance(arg, ast.Attribute)
            and isinstance(arg.value, ast.Name)
            and arg.value.id in scope.tables
        ):
            raise FormulaError("sum() 里写 表名.列名，例如 sum(items.amount)")
        columns = scope.tables[arg.value.id]
        if arg.attr not in columns:
            raise FormulaError(f"表 {arg.value.id} 没有叫 {arg.attr} 的列")
        if columns[arg.attr] != "number":
            raise FormulaError(f"{arg.value.id}.{arg.attr} 不是数字，不能求和")
        used["tables"].add(arg.value.id)
        return "number"
    if func not in _FUNCTIONS:
        raise FormulaError(f"没有 {func}() 这个函数，能用的有 sum、count、round、abs、min、max")
    if func == "round" and len(args) == 2:
        if not (isinstance(args[1], ast.Constant) and isinstance(args[1].value, int)):
            raise FormulaError("round(x, n) 的 n 要写整数，表示保留几位小数")
    elif func in ("round", "abs") and len(args) != 1:
        raise FormulaError(f"{func}() 里写一项，round 可以再加保留几位小数")
    elif func in ("min", "max") and len(args) < 2:
        raise FormulaError(f"{func}() 里至少写两项")
    for arg in args:
        if _check(arg, scope, used) != "number":
            raise FormulaError(f"{func}() 只能算数字")
    return "number"


# —— 计算 ——


class Missing(Exception):
    """参与计算的值没有（原文没有、读不出来）。"""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name


@dataclass(frozen=True)
class Inputs:
    """计算时用的值。都是文字或数字（和数据库、抽取结果一样），按类型读。"""

    fields: Mapping[str, Any]
    tables: Mapping[str, Sequence[Mapping[str, Any]]]
    """表名 → 每一行 {列名 → 值}（已经去掉删掉的行）。"""
    row: Mapping[str, Any] | None = None
    day_first: bool | None = None
    """拆日期范围时，数字日期是不是 日/月（按模板的国家，ADR 0039）。"""


@dataclass(frozen=True)
class Outcome:
    value: float | None
    missing: str | None = None
    """算不出来时，缺的是哪个字段。"""
    problem: str | None = None
    """算不出来的其他原因（除以 0）。"""


def evaluate(formula: Formula, inputs: Inputs) -> Outcome:
    """按当前的值算一次。缺值、除以 0 时 value 是 None，并说明原因。"""
    try:
        result = _eval(formula.tree, inputs)
    except Missing as err:
        return Outcome(None, missing=err.name)
    except ZeroDivisionError:
        return Outcome(None, problem="除以 0")
    if not isinstance(result, (int, float)) or not math.isfinite(result):
        return Outcome(None, problem="算出来不是数字")
    return Outcome(round(float(result), 6))


def _eval(node: ast.expr, inputs: Inputs) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if inputs.row is not None and node.id in inputs.row:
            return _value(node.id, inputs.row[node.id])
        return _value(node.id, inputs.fields.get(node.id))
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        name = node.value.id
        raw = inputs.row[name] if inputs.row is not None and name in inputs.row else None
        if raw is None:
            raw = inputs.fields.get(name)
        if raw is None or raw == "":
            raise Missing(name)
        found = parse_date_range(str(raw), inputs.day_first).range
        if found is None:
            raise Missing(name)
        part = found.days if node.attr == "days" else getattr(found, node.attr)
        if part is None:
            raise Missing(name)
        return float(part) if node.attr == "days" else part
    if isinstance(node, ast.UnaryOp):
        operand = _eval(node.operand, inputs)
        return -operand if isinstance(node.op, ast.USub) else operand
    if isinstance(node, ast.BinOp):
        left, right = _eval(node.left, inputs), _eval(node.right, inputs)
        if isinstance(left, date) and isinstance(right, date):
            return (left - right).days
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        return left / right
    assert isinstance(node, ast.Call) and isinstance(node.func, ast.Name)  # noqa: S101 — parse_formula 已检查
    func = node.func.id
    if func == "count":
        assert isinstance(node.args[0], ast.Name)  # noqa: S101
        return len(inputs.tables.get(node.args[0].id, ()))
    if func == "sum":
        arg = node.args[0]
        assert isinstance(arg, ast.Attribute) and isinstance(arg.value, ast.Name)  # noqa: S101
        name = f"{arg.value.id}.{arg.attr}"
        rows = inputs.tables.get(arg.value.id, ())
        return sum(_number(name, row.get(arg.attr)) for row in rows)
    values = [_eval(a, inputs) for a in node.args]
    if func == "round":
        return round(values[0], values[1] if len(values) > 1 else 0)
    if func == "abs":
        return abs(values[0])
    return min(values) if func == "min" else max(values)


def _value(name: str, raw: Any) -> float | date:
    if raw is None or raw == "":
        raise Missing(name)
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw)
    text = str(raw)
    try:
        return float(text)
    except ValueError:
        pass
    try:
        return date.fromisoformat(text)
    except ValueError:
        parsed = parse_date(text)
        if parsed is None:
            raise Missing(name) from None
        return parsed


def _number(name: str, raw: Any) -> float:
    """求和用：parse_formula 已检查这一列是数字。"""
    value = _value(name, raw)
    if isinstance(value, date):
        raise Missing(name)
    return value


def _readable(node: ast.expr, labels: Mapping[str, str]) -> str:
    if isinstance(node, ast.Constant):
        return f"{node.value:g}" if isinstance(node.value, float) else str(node.value)
    if isinstance(node, ast.Name):
        return labels.get(node.id, node.id)
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        key = f"{node.value.id}.{node.attr}"
        part = labels.get(key) or _RANGE_NAMES.get(node.attr, node.attr)
        return f"{labels.get(node.value.id, node.value.id)}的{part}"
    if isinstance(node, ast.UnaryOp):
        return f"-{_readable(node.operand, labels)}"
    if isinstance(node, ast.BinOp):
        left, right = _readable(node.left, labels), _readable(node.right, labels)
        if isinstance(node.left, ast.BinOp) and _weaker(node.left.op, node.op):
            left = f"({left})"
        if isinstance(node.right, ast.BinOp) and not _weaker(node.op, node.right.op):
            right = f"({right})"
        return f"{left} {_SYMBOLS[type(node.op)]} {right}"
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        args = "，".join(_readable(a, labels) for a in node.args)
        if node.func.id == "sum":
            return f"{args}合计"
        if node.func.id == "count":
            return f"{args}的行数"
        names = {"sum": "合计", "count": "行数", "round": "四舍五入", "abs": "绝对值"}
        names |= {"min": "最小", "max": "最大"}
        return f"{names.get(node.func.id, node.func.id)}({args})"
    return ast.unparse(node)


def _weaker(inner: ast.operator, outer: ast.operator) -> bool:
    """inner 的优先级比 outer 低（+ − 在 × ÷ 里面要加括号）。"""
    low = (ast.Add, ast.Sub)
    return isinstance(inner, low) and not isinstance(outer, low)


def order(formulas: Mapping[str, Formula]) -> list[str]:
    """按依赖排好计算顺序：先算别人要用的。互相引用（a 用 b、b 用 a）抛 FormulaError。"""
    done: list[str] = []
    visiting: set[str] = set()

    def visit(name: str) -> None:
        if name in done:
            return
        if name in visiting:
            raise FormulaError(f"公式互相引用了：{name}")
        visiting.add(name)
        formula = formulas[name]
        for dep in formula.fields | formula.columns:
            if dep in formulas:
                visit(dep)
        visiting.discard(name)
        done.append(name)

    for name in formulas:
        visit(name)
    return done


# —— 自定义规则的条件（ADR 0035）——

_COMPARE = {
    ast.Eq: "=",
    ast.NotEq: "≠",
    ast.Lt: "<",
    ast.LtE: "≤",
    ast.Gt: ">",
    ast.GtE: "≥",
}
TOLERANCE = 0.005
"""金额比较的容差：两边差不到半分钱算相等（和内置规则一样）。"""


@dataclass(frozen=True)
class Condition:
    """一条规则的条件：左边 比较 右边，例如 sum(items.amount) == total、check_out > check_in。"""

    text: str
    left: Formula
    op: type[ast.cmpop]
    right: Formula

    @property
    def fields(self) -> frozenset[str]:
        return self.left.fields | self.right.fields

    @property
    def columns(self) -> frozenset[str]:
        return self.left.columns | self.right.columns

    @property
    def tables(self) -> frozenset[str]:
        return self.left.tables | self.right.tables

    def readable(self, labels: Mapping[str, str]) -> str:
        return f"{self.left.readable(labels)} {_COMPARE[self.op]} {self.right.readable(labels)}"


def parse_condition(text: str, scope: Scope) -> Condition:
    """检查规则的条件：只能有一个比较（== != < <= > >=），两边是数字，或者两边都是日期。"""
    source = text.strip()
    if not source:
        raise FormulaError("条件是空的")
    if len(source) > 500:
        raise FormulaError("条件太长了（最多 500 个字）")
    try:
        tree = ast.parse(source, mode="eval").body
    except SyntaxError as err:
        raise FormulaError(f"条件写得不对：{source}") from err
    if not isinstance(tree, ast.Compare) or len(tree.ops) != 1:
        raise FormulaError(
            "条件要写成 左边 比较 右边，例如 sum(items.amount) == total（比较用 == != < <= > >=）"
        )
    op = type(tree.ops[0])
    if op not in _COMPARE:
        raise FormulaError("比较只能用 == != < <= > >=")
    sides = []
    kinds = []
    for node in (tree.left, tree.comparators[0]):
        used: dict[str, set[str]] = {"fields": set(), "columns": set(), "tables": set()}
        kinds.append(_check(node, scope, used))
        sides.append(
            Formula(
                ast.unparse(node),
                node,
                frozenset(used["fields"]),
                frozenset(used["columns"]),
                frozenset(used["tables"]),
            )
        )
    if kinds[0] != kinds[1]:
        raise FormulaError("比较的两边要都是数字，或者都是日期")
    return Condition(source, sides[0], op, sides[1])


@dataclass(frozen=True)
class Verdict:
    passed: bool | None
    """True 通过、False 不通过、None 缺值没法检查（跳过）。"""
    left: float | date | None = None
    right: float | date | None = None


def check_condition(condition: Condition, inputs: Inputs) -> Verdict:
    """按当前的值检查一次条件。数字相等允许差半分钱。"""
    try:
        left = _eval(condition.left.tree, inputs)
        right = _eval(condition.right.tree, inputs)
    except (Missing, ZeroDivisionError):
        return Verdict(None)
    op = condition.op
    if isinstance(left, date) or isinstance(right, date):
        diff = 0.0 if left == right else (1.0 if left > right else -1.0)
    else:
        diff = float(left) - float(right)
        if abs(diff) < TOLERANCE:
            diff = 0.0
    passed = {
        ast.Eq: diff == 0,
        ast.NotEq: diff != 0,
        ast.Lt: diff < 0,
        ast.LtE: diff <= 0,
        ast.Gt: diff > 0,
        ast.GtE: diff >= 0,
    }[op]
    return Verdict(passed, left, right)
