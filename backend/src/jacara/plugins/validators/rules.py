"""按规则清单逐条检查的校验器，以及收据常用的规则。

新增一条规则：写一个检查函数，再在 RECEIPT_RULES 里加一行 Rule(...)。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from jacara.plugins.extractors.base import ExtractedField
from jacara.plugins.validators.base import Issue, Rule, ValidationResult, Values

TOLERANCE = 0.005
"""金额比较的容差：差不到半分钱就算相等（小数在电脑里有微小误差）。"""


def _num(values: Values, name: str) -> float:
    """取出数字字段。规则只在字段都有值时才运行，抽取器也保证数字字段是 float。"""
    value = values[name]
    if not isinstance(value, float):
        raise TypeError(f"字段 {name} 应该是数字，实际是 {value!r}")
    return value


def _money(value: float) -> str:
    return f"{value:,.2f}"


def check_change(values: Values) -> str | None:
    cash, total, change = _num(values, "cash"), _num(values, "total"), _num(values, "change")
    expected = cash - total
    if abs(expected - change) < TOLERANCE:
        return None
    return (
        f"现金 {_money(cash)} − 总额 {_money(total)} = {_money(expected)}，"
        f"但找零是 {_money(change)}"
    )


def check_rounding(values: Values) -> str | None:
    subtotal, rounding = _num(values, "subtotal"), _num(values, "rounding")
    total = _num(values, "total")
    expected = subtotal + rounding
    if abs(expected - total) < TOLERANCE:
        return None
    return (
        f"小计 {_money(subtotal)} + 调整 {_money(rounding)} = {_money(expected)}，"
        f"但总额是 {_money(total)}"
    )


def check_cash_covers_total(values: Values) -> str | None:
    cash, total = _num(values, "cash"), _num(values, "total")
    if cash + TOLERANCE >= total:
        return None
    return f"现金 {_money(cash)} 比总额 {_money(total)} 还少"


def check_positive_total(values: Values) -> str | None:
    total = _num(values, "total")
    return None if total > 0 else f"总额是 {_money(total)}，应该大于 0"


def check_date_exists(values: Values) -> str | None:
    try:
        date.fromisoformat(str(values["date"]))
    except ValueError:
        return f"日期 {values['date']} 不存在"
    return None


def check_date_not_future(values: Values) -> str | None:
    try:
        day = date.fromisoformat(str(values["date"]))
    except ValueError:
        return None  # 不存在的日期由上一条规则负责
    if day <= date.today():
        return None
    return f"日期 {day} 在今天之后"


RECEIPT_RULES: tuple[Rule, ...] = (
    Rule("total_positive", "总额大于 0", ("total",), check_positive_total),
    Rule("date_exists", "日期真实存在", ("date",), check_date_exists),
    Rule("date_not_future", "日期不在未来", ("date",), check_date_not_future, "warning"),
    Rule("rounding", "小计 + 调整 = 总额", ("subtotal", "rounding", "total"), check_rounding),
    Rule("cash_covers_total", "现金不少于总额", ("cash", "total"), check_cash_covers_total),
    Rule("change", "现金 − 总额 = 找零", ("cash", "total", "change"), check_change),
)


class RuleValidator:
    name = "rules"

    def __init__(self, rules: Sequence[Rule] = RECEIPT_RULES) -> None:
        self.rules = tuple(rules)

    def validate(self, fields: Sequence[ExtractedField]) -> ValidationResult:
        values: Values = {f.name: f.value for f in fields}
        issues: list[Issue] = []
        passed: list[str] = []
        skipped: list[str] = []
        for rule in self.rules:
            if any(values.get(name) is None for name in rule.needs):
                skipped.append(rule.id)
                continue
            message = rule.check(values)
            if message is None:
                passed.append(rule.id)
            else:
                issues.append(Issue(rule.id, rule.title, message, rule.needs, rule.severity))
        return ValidationResult(tuple(issues), tuple(passed), tuple(skipped))
