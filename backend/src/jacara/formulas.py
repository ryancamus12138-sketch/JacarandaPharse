"""Formulas for computed fields: some fields should be derived from other fields rather than located
on the document by the model.

Examples: nights on a hotel bill = check-out date − check-in date; line amount = quantity × unit
price; total = sum(items.amount). The 1 in a Rooms column is the number of rooms, and a model
asked for "nights" may pick it up; a computed value cannot make that mistake, and its evidence is
the fields used in the computation.

Formulas may contain only the following (no arbitrary code; every node of the Python syntax tree
is checked):
- Field IDs: check_in, total; table columns are referenced by name in per-row formulas (quantity
  * unit_price)
- Numbers, + - * /, parentheses
- Date subtraction yields the difference in days (check_out - check_in)
- Functions: sum(items.amount), count(items), round(x), round(x, 2), abs(x), min(a, b, …), max(a,
  b, …)
- Date ranges: period.start and period.end are dates; period.days is the number of days
  (inclusive)
Text fields cannot be used in computations. The result is a number."""

from __future__ import annotations

import ast
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from jacara.core.daterange import parse_date_range
from jacara.core.i18n import N_, joined, stored
from jacara.core.textvalue import parse_date

Kind = str
"""Expression type: "number" or "date"."""

_RANGE_PARTS = {"start": "date", "end": "date", "days": "number"}
"""Parts available on a date range field."""
_RANGE_NAMES = {"start": N_("开始"), "end": N_("结束"), "days": N_("天数")}

_AGGREGATES = {"sum", "count"}
_FUNCTIONS = {"round", "abs", "min", "max"}
_SYMBOLS = {ast.Add: "+", ast.Sub: "−", ast.Mult: "×", ast.Div: "÷"}


class FormulaError(ValueError):
    """Invalid formula. The message is shown to the user as is."""


@dataclass(frozen=True)
class Formula:
    """A validated formula."""

    text: str
    tree: ast.expr
    fields: frozenset[str]
    """Regular fields referenced (check_in)."""
    columns: frozenset[str]
    """Columns of the same row referenced (per-row formulas only)."""
    tables: frozenset[str]
    """Whole tables referenced (sum(items.amount), count(items))."""

    def readable(self, labels: Mapping[str, str]) -> str:
        """Human-readable form: field IDs replaced by display names, check_out - check_in →
        Check-out date − Check-in date. Returned as a stored message; display it with render()."""
        return _readable(self.tree, labels)


@dataclass(frozen=True)
class Scope:
    """Names available to a formula and their types."""

    fields: Mapping[str, str]
    """Regular field ID → type (string, number, date)."""
    tables: Mapping[str, Mapping[str, str]]
    """Table name → {column name → type}."""
    row: Mapping[str, str] | None = None
    """Per-row formulas: column name in the same row → type. None for formulas on regular fields."""


def parse_formula(text: str, scope: Scope) -> Formula:
    """Validate a formula: syntax, that referenced names exist, and that types are compatible.
    Raises FormulaError on errors."""
    source = text.strip()
    if not source:
        raise FormulaError(stored("公式为空"))
    if len(source) > 500:
        raise FormulaError(stored("公式过长（最多 500 个字符）"))
    try:
        tree = ast.parse(source, mode="eval").body
    except SyntaxError as err:
        raise FormulaError(stored("公式有误：{source}", source=source)) from err
    used: dict[str, set[str]] = {"fields": set(), "columns": set(), "tables": set()}
    kind = _check(tree, scope, used)
    if kind != "number":
        raise FormulaError(stored("公式结果须为数字（两个日期相减得到天数）"))
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
            raise FormulaError(
                stored("公式中只能使用数字，不能使用 {value}", value=repr(node.value))
            )
        return "number"
    if isinstance(node, ast.Name):
        return _name(node.id, scope, used)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        if _check(node.operand, scope, used) != "number":
            raise FormulaError(stored("日期前不能添加正负号"))
        return "number"
    if isinstance(node, ast.BinOp) and type(node.op) in _SYMBOLS:
        left = _check(node.left, scope, used)
        right = _check(node.right, scope, used)
        if left == right == "number":
            return "number"
        if left == right == "date" and isinstance(node.op, ast.Sub):
            return "number"  # Subtracting two dates gives the difference in days.
        raise FormulaError(stored("日期只能与日期相减（得到天数），不能与数字运算"))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
        return _call(node.func.id, node.args, scope, used)
    if isinstance(node, ast.Attribute):
        if isinstance(node.value, ast.Name) and _kind_of(node.value.id, scope) == "date_range":
            if node.attr not in _RANGE_PARTS:
                raise FormulaError(
                    stored("日期范围只能使用 .start、.end、.days，不能使用 .{attr}", attr=node.attr)
                )
            if scope.row is not None and node.value.id in scope.row:
                used["columns"].add(node.value.id)
            else:
                used["fields"].add(node.value.id)
            return _RANGE_PARTS[node.attr]
        raise FormulaError(stored("表格的列需放在 sum() 中，例如 sum(items.amount)"))
    raise FormulaError(
        stored("公式中只能使用字段、数字、+ - * /、括号及 sum、count、round、abs、min、max")
    )


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
        raise FormulaError(
            stored("{name} 是表格，需写作 sum({name}.列名) 或 count({name})", name=name)
        )
    else:
        raise FormulaError(stored("没有叫 {name} 的字段", name=name))
    if kind == "date_range":
        raise FormulaError(
            stored(("{name} 是日期范围，需写作 {name}.start、{name}.end 或 {name}.days"), name=name)
        )
    if kind not in ("number", "date"):
        raise FormulaError(stored("{name} 是文字字段，不能参与计算", name=name))
    return kind


def _call(func: str, args: Sequence[ast.expr], scope: Scope, used: dict[str, set[str]]) -> Kind:
    if func in _AGGREGATES:
        if scope.row is not None:
            raise FormulaError(stored("逐行公式中不能使用 {func}()", func=func))
        if len(args) != 1:
            raise FormulaError(stored("{func}() 只能包含一个参数", func=func))
        arg = args[0]
        if func == "count":
            if not (isinstance(arg, ast.Name) and arg.id in scope.tables):
                raise FormulaError(stored("count() 中需填写表名，例如 count(items)"))
            used["tables"].add(arg.id)
            return "number"
        if not (
            isinstance(arg, ast.Attribute)
            and isinstance(arg.value, ast.Name)
            and arg.value.id in scope.tables
        ):
            raise FormulaError(stored("sum() 中需填写 表名.列名，例如 sum(items.amount)"))
        columns = scope.tables[arg.value.id]
        if arg.attr not in columns:
            raise FormulaError(
                stored("表 {table} 中没有名为 {attr} 的列", table=arg.value.id, attr=arg.attr)
            )
        if columns[arg.attr] != "number":
            raise FormulaError(
                stored("{table}.{attr} 不是数字，不能求和", table=arg.value.id, attr=arg.attr)
            )
        used["tables"].add(arg.value.id)
        return "number"
    if func not in _FUNCTIONS:
        raise FormulaError(
            stored(("不支持函数 {func}()，可用函数：sum、count、round、abs、min、max"), func=func)
        )
    if func == "round" and len(args) == 2:
        if not (isinstance(args[1], ast.Constant) and isinstance(args[1].value, int)):
            raise FormulaError(stored("round(x, n) 中的 n 需为整数，表示保留的小数位数"))
    elif func in ("round", "abs") and len(args) != 1:
        raise FormulaError(stored("{func}() 需包含一个参数；round 可另加保留的小数位数", func=func))
    elif func in ("min", "max") and len(args) < 2:
        raise FormulaError(stored("{func}() 至少需要两个参数", func=func))
    for arg in args:
        if _check(arg, scope, used) != "number":
            raise FormulaError(stored("{func}() 仅适用于数字", func=func))
    return "number"


# -- Evaluation --


class Missing(Exception):
    """An input value is missing (absent from the source or unreadable)."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name


@dataclass(frozen=True)
class Inputs:
    """Values used for computation. All are strings or numbers (as in the database and extraction
    results) and are parsed by type."""

    fields: Mapping[str, Any]
    tables: Mapping[str, Sequence[Mapping[str, Any]]]
    """Table name → rows of {column name → value} (deleted rows already removed)."""
    row: Mapping[str, Any] | None = None
    day_first: bool | None = None
    """Whether numeric dates are day/month when splitting date ranges (from the template
    country)."""


@dataclass(frozen=True)
class Outcome:
    value: float | None
    missing: str | None = None
    """Field whose value is missing when the result cannot be computed."""
    problem: str | None = None
    """Other reason the result cannot be computed (division by zero)."""


def evaluate(formula: Formula, inputs: Inputs) -> Outcome:
    """Evaluate once with the current values. value is None for missing inputs or division by zero,
    with the reason."""
    try:
        result = _eval(formula.tree, inputs)
    except Missing as err:
        return Outcome(None, missing=err.name)
    except ZeroDivisionError:
        return Outcome(None, problem=stored("除以 0"))
    if not isinstance(result, (int, float)) or not math.isfinite(result):
        return Outcome(None, problem=stored("计算结果不是数字"))
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
    # Validated by parse_formula.
    assert isinstance(node, ast.Call) and isinstance(node.func, ast.Name)  # noqa: S101
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
    """For sums: parse_formula has already verified that this column is numeric."""
    value = _value(name, raw)
    if isinstance(value, date):
        raise Missing(name)
    return value


def _readable(node: ast.expr, labels: Mapping[str, str]) -> str:
    """Formula with display names, as a stored message (rendered in the reader's language)."""
    if isinstance(node, ast.Constant):
        return f"{node.value:g}" if isinstance(node.value, float) else str(node.value)
    if isinstance(node, ast.Name):
        return labels.get(node.id, node.id)
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        key = f"{node.value.id}.{node.attr}"
        part = labels.get(key) or (
            stored(_RANGE_NAMES[node.attr]) if node.attr in _RANGE_NAMES else node.attr
        )
        return stored("{table}的{part}", table=labels.get(node.value.id, node.value.id), part=part)
    if isinstance(node, ast.UnaryOp):
        return stored("-{operand}", operand=_readable(node.operand, labels))
    if isinstance(node, ast.BinOp):
        left, right = _readable(node.left, labels), _readable(node.right, labels)
        if isinstance(node.left, ast.BinOp) and _weaker(node.left.op, node.op):
            left = stored("({inner})", inner=left)
        if isinstance(node.right, ast.BinOp) and not _weaker(node.op, node.right.op):
            right = stored("({inner})", inner=right)
        return stored(
            "{left} {operator} {right}", left=left, operator=_SYMBOLS[type(node.op)], right=right
        )
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        args = _listing([_readable(a, labels) for a in node.args])
        if node.func.id == "sum":
            return stored("{args}合计", args=args)
        if node.func.id == "count":
            return stored("{args}的行数", args=args)
        names = {"round": N_("四舍五入"), "abs": N_("绝对值"), "min": N_("最小"), "max": N_("最大")}
        if node.func.id in names:
            return stored("{function}({args})", function=stored(names[node.func.id]), args=args)
        return stored("{function}({args})", function=node.func.id, args=args)
    return ast.unparse(node)


def _listing(items: list[str]) -> str:
    """Function arguments joined with the argument separator of the reader's language."""
    return joined(ARGUMENT_SEPARATOR, items)


ARGUMENT_SEPARATOR = N_("，")
"""Separator between function arguments in readable formulas (Chinese "，", English ", ")."""


def _weaker(inner: ast.operator, outer: ast.operator) -> bool:
    """Whether inner binds less tightly than outer (+ and − inside × and ÷ need parentheses)."""
    low = (ast.Add, ast.Sub)
    return isinstance(inner, low) and not isinstance(outer, low)


def order(formulas: Mapping[str, Formula]) -> list[str]:
    """Order formulas by dependency so that inputs are computed first. Circular references (a uses
    b, b uses a) raise FormulaError."""
    done: list[str] = []
    visiting: set[str] = set()

    def visit(name: str) -> None:
        if name in done:
            return
        if name in visiting:
            raise FormulaError(stored("公式存在循环引用：{name}", name=name))
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


# -- Conditions of custom rules --

_COMPARE = {
    ast.Eq: "=",
    ast.NotEq: "≠",
    ast.Lt: "<",
    ast.LtE: "≤",
    ast.Gt: ">",
    ast.GtE: "≥",
}
TOLERANCE = 0.005
"""Tolerance for amount comparisons: values within half a cent are equal (same as the built-in
rules)."""


@dataclass(frozen=True)
class Condition:
    """Condition of a rule: left comparison right, for example sum(items.amount) == total, check_out
    > check_in."""

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
        """Human-readable condition as a stored message (see Formula.readable)."""
        return stored(
            "{left} {operator} {right}",
            left=self.left.readable(labels),
            operator=_COMPARE[self.op],
            right=self.right.readable(labels),
        )


def parse_condition(text: str, scope: Scope) -> Condition:
    """Validate a rule condition: exactly one comparison (== != < <= > >=), with both sides numbers
    or both sides dates."""
    source = text.strip()
    if not source:
        raise FormulaError(stored("条件为空"))
    if len(source) > 500:
        raise FormulaError(stored("条件过长（最多 500 个字符）"))
    try:
        tree = ast.parse(source, mode="eval").body
    except SyntaxError as err:
        raise FormulaError(stored("条件有误：{source}", source=source)) from err
    if not isinstance(tree, ast.Compare) or len(tree.ops) != 1:
        raise FormulaError(
            stored(
                "条件格式应为 左侧 比较符 右侧，例如 sum(items.amount) == tot"
                "al（比较符：== != < <= > >=）"
            )
        )
    op = type(tree.ops[0])
    if op not in _COMPARE:
        raise FormulaError(stored("比较符只能使用 == != < <= > >="))
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
        raise FormulaError(stored("比较的两侧须同为数字或同为日期"))
    return Condition(source, sides[0], op, sides[1])


@dataclass(frozen=True)
class Verdict:
    passed: bool | None
    """True passes, False fails, None cannot be checked because of missing values (skipped)."""
    left: float | date | None = None
    right: float | date | None = None


def check_condition(condition: Condition, inputs: Inputs) -> Verdict:
    """Evaluate the condition once with the current values. Numeric equality allows half a cent of
    difference."""
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
