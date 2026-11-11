"""按规则清单逐条检查的校验器，以及收据常用的规则。

新增一条规则：写一个检查函数，在 RECEIPT_RULES 里加一行 Rule(...)，
再把它的编号写进要用它的模板（backend/src/jacara/templates/*.toml 的 rules）。
"""

from __future__ import annotations

import fnmatch
from collections.abc import Sequence
from datetime import date

from jacara.plugins.extractors.base import ExtractedField, split_cell_name
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


ITEM_TOLERANCE = 0.015
"""商品明细的容差：单价 × 数量常有四舍五入，差 1 分钱算对。"""


def _amounts(values: Values) -> list[float]:
    return [
        v
        for k, v in values.items()
        if fnmatch.fnmatch(k, "items.*.amount") and isinstance(v, float)
    ]


def check_items_sum(values: Values) -> str | None:
    """商品金额加起来应该等于小计；模板里没有小计，就和总额比。"""
    amounts = _amounts(values)
    target = "subtotal" if isinstance(values.get("subtotal"), float) else "total"
    expected = _num(values, target)
    added = sum(amounts)
    if abs(added - expected) < ITEM_TOLERANCE:
        return None
    label = "小计" if target == "subtotal" else "总额"
    return (
        f"{len(amounts)} 行商品金额加起来是 {_money(added)}，但{label}是 {_money(expected)}"
        "（可能漏了行、多了行，或者有税、折扣没算进去）"
    )


def check_item_line(values: Values) -> str | None:
    """一行商品：数量 × 单价 = 金额。"""
    quantity, price = _num(values, "quantity"), _num(values, "unit_price")
    amount = _num(values, "amount")
    expected = quantity * price
    # 折扣行的金额是负数（3x 2.00 → -6.00），比较绝对值
    if abs(abs(expected) - abs(amount)) < ITEM_TOLERANCE:
        return None
    return (
        f"数量 {quantity:g} × 单价 {_money(price)} = {_money(expected)}，但金额是 {_money(amount)}"
    )


RECEIPT_RULES: tuple[Rule, ...] = (
    Rule("total_positive", "总额大于 0", ("total",), check_positive_total),
    Rule("date_exists", "日期真实存在", ("date",), check_date_exists),
    Rule("date_not_future", "日期不在未来", ("date",), check_date_not_future, "warning"),
    Rule("rounding", "小计 + 调整 = 总额", ("subtotal", "rounding", "total"), check_rounding),
    Rule("cash_covers_total", "现金不少于总额", ("cash", "total"), check_cash_covers_total),
    Rule("change", "现金 − 总额 = 找零", ("cash", "total", "change"), check_change),
    Rule(
        "items_sum",
        "商品金额合计 = 小计或总额",
        ("items.*.amount", "total"),
        check_items_sum,
        "warning",  # 税、服务费、折扣常常单独列，对不上不一定是错
        flags=("items.*.amount",),  # 只标商品金额，不标总额：总额更可能是对的
    ),
    Rule(
        "item_line",
        "数量 × 单价 = 金额",
        ("quantity", "unit_price", "amount"),
        check_item_line,
        per_row="items",
    ),
)

RULES: dict[str, Rule] = {rule.id: rule for rule in RECEIPT_RULES}
"""规则编号 → 规则。模板文件里按编号选规则。"""


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
            found = self._per_row(rule, values) if rule.per_row else self._whole(rule, values)
            if found is None:
                skipped.append(rule.id)
            elif found:
                issues += found
            else:
                passed.append(rule.id)
        return ValidationResult(tuple(issues), tuple(passed), tuple(skipped))

    def _whole(self, rule: Rule, values: Values) -> list[Issue] | None:
        """整张单据检查一次。返回 None 表示跳过，空列表表示通过。"""
        if not all(_present(values, name) for name in rule.needs):
            return None
        message = rule.check(values)
        if message is None:
            return []
        flagged = tuple(k for p in (rule.flags or rule.needs) for k in _expand(values, p))
        return [Issue(rule.id, rule.title, message, flagged, rule.severity)]

    def _per_row(self, rule: Rule, values: Values) -> list[Issue] | None:
        """表格的每一行单独检查。一行都没法检查（缺列）就算跳过。"""
        table = rule.per_row
        rows: dict[int, Values] = {}
        for name, value in values.items():
            cell = split_cell_name(name)
            if cell and cell[0] == table:
                rows.setdefault(cell[1], {})[cell[2]] = value
        issues: list[Issue] = []
        checked = False
        for index in sorted(rows):
            row = rows[index]
            if any(row.get(name) is None for name in rule.needs):
                continue
            checked = True
            message = rule.check(row)
            if message is not None:
                names = tuple(f"{table}.{index}.{c}" for c in (rule.flags or rule.needs))
                issues.append(
                    Issue(rule.id, rule.title, f"第 {index} 行：{message}", names, rule.severity)
                )
        return issues if checked else None


def _expand(values: Values, pattern: str) -> list[str]:
    """字段名，或 items.*.amount 这样的通配写法 → 实际有值的字段名。"""
    if "*" not in pattern:
        return [pattern]
    return [k for k, v in values.items() if v is not None and fnmatch.fnmatch(k, pattern)]


def _present(values: Values, pattern: str) -> bool:
    if "*" in pattern:
        return bool(_expand(values, pattern))
    return values.get(pattern) is not None
