"""算计算字段（ADR 0035）：抽取之后算一次；审核时参与计算的值改了，再算一次。

两处用同一份代码，所以处理时算出的值和审核后重算的值一定一致。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from jacara.formulas import Formula, Inputs, Outcome, evaluate
from jacara.plugins.extractors.base import cell_name, split_cell_name
from jacara.templates import Template


@dataclass(frozen=True)
class Computed:
    """算出来的一个字段（表格的格子是 items.2.amount 这样的名字）。"""

    name: str
    value: float | None
    note: str
    """给人看的：按什么公式算的、算出多少；算不出来时说缺了什么。"""
    formula: str
    """给人看的公式（中文名）。"""


def compute(template: Template, values: Mapping[str, Any]) -> list[Computed]:
    """按模板的公式算出所有计算字段。values 是现在的值：字段名 → 值（文字或数字，没有就是 None），
    表格的格子用 items.2.amount 这样的名字，删掉的行不要放进来。

    先算每一行的公式（明细的金额 = 数量 × 单价），再算普通字段（总额 = sum(items.amount)），
    算出的值马上能给后面的公式用。
    """
    formulas = template.formulas()
    if not formulas:
        return []
    labels = template.labels()
    current: dict[str, Any] = dict(values)
    results: list[Computed] = []

    for table, columns in formulas.columns.items():
        # 每一行的公式里写的是列名：列名优先（明细的 date 和单据的 date 可能重名）
        row_labels = {
            **labels,
            **{c: labels.get(f"{table}.{c}", c) for c in _columns(template, table)},
        }
        for index in _rows(current, table):
            row = _row(current, table, index)
            for column, formula in columns.items():
                name = cell_name(table, index, column)
                outcome = evaluate(formula, Inputs(current, {}, row, template.day_first))
                done = _result(name, formula, outcome, row_labels)
                results.append(done)
                current[name] = done.value
                row[column] = done.value

    tables = {
        t.name: [_row(current, t.name, i) for i in _rows(current, t.name)] for t in template.tables
    }
    for name, formula in formulas.fields.items():
        outcome = evaluate(formula, Inputs(current, tables, day_first=template.day_first))
        done = _result(name, formula, outcome, labels)
        results.append(done)
        current[name] = done.value
    return results


def _result(name: str, formula: Formula, outcome: Outcome, labels: Mapping[str, str]) -> Computed:
    readable = formula.readable(labels)
    if outcome.value is not None:
        return Computed(name, outcome.value, f"按公式算：{readable} = {outcome.value:g}", readable)
    if outcome.missing is not None:
        missing = labels.get(outcome.missing, outcome.missing)
        if "." in outcome.missing:
            table, column = outcome.missing.split(".", 1)
            missing = f"{labels.get(table, table)}的{labels.get(outcome.missing, column)}"
        reason = f"「{missing}」没有值"
    else:
        reason = outcome.problem or "算不出来"
    return Computed(name, None, f"按公式算（{readable}），但{reason}，算不出来", readable)


def _columns(template: Template, table: str) -> list[str]:
    return [c.name for t in template.tables if t.name == table for c in t.columns]


def _rows(values: Mapping[str, Any], table: str) -> list[int]:
    """表里有哪几行（按行号）。"""
    rows = {cell[1] for name in values if (cell := split_cell_name(name)) and cell[0] == table}
    return sorted(rows)


def _row(values: Mapping[str, Any], table: str, index: int) -> dict[str, Any]:
    """一行的每一列：列名 → 值。"""
    return {
        cell[2]: value
        for name, value in values.items()
        if (cell := split_cell_name(name)) and cell[0] == table and cell[1] == index
    }
