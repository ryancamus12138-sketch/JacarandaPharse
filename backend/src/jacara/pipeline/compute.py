"""Compute calculated fields: once after extraction, and again during review whenever an input value
changes.

Both use the same code, so values computed during processing always equal values recomputed after
review."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from jacara.core.i18n import stored
from jacara.formulas import Formula, Inputs, Outcome, evaluate
from jacara.plugins.extractors.base import cell_name, split_cell_name
from jacara.templates import Template


@dataclass(frozen=True)
class Computed:
    """One computed field (table cells use names like items.2.amount)."""

    name: str
    value: float | None
    note: str
    """Human-readable: the formula used and the result; when not computable, what is missing."""
    formula: str
    """Human-readable formula (with display names)."""


def compute(template: Template, values: Mapping[str, Any]) -> list[Computed]:
    """Compute all calculated fields from the template formulas. values holds the current values:
    field name → value (text or number, None if absent); table cells use names like
    items.2.amount, and deleted rows must be excluded.

        Per-row formulas run first (line amount = quantity × unit price), then regular fields
    (total = sum(items.amount)); each result is immediately available to later formulas."""
    formulas = template.formulas()
    if not formulas:
        return []
    labels = template.labels()
    current: dict[str, Any] = dict(values)
    results: list[Computed] = []

    for table, columns in formulas.columns.items():
        # Per-row formulas reference column names, which take precedence (a line-item date and the
        # document date may share a name).
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
        return Computed(
            name,
            outcome.value,
            stored("按公式算：{readable} = {value:g}", readable=readable, value=outcome.value),
            readable,
        )
    if outcome.missing is not None:
        missing = labels.get(outcome.missing, outcome.missing)
        if "." in outcome.missing:
            table, column = outcome.missing.split(".", 1)
            missing = stored(
                "{table}的{column}",
                table=labels.get(table, table),
                column=labels.get(outcome.missing, column),
            )
        reason = stored("「{missing}」没有值", missing=missing)
    else:
        reason = outcome.problem or stored("算不出来")
    return Computed(
        name,
        None,
        stored("按公式算（{readable}），但{reason}，算不出来", readable=readable, reason=reason),
        readable,
    )


def _columns(template: Template, table: str) -> list[str]:
    return [c.name for t in template.tables if t.name == table for c in t.columns]


def _rows(values: Mapping[str, Any], table: str) -> list[int]:
    """Row numbers present in the table."""
    rows = {cell[1] for name in values if (cell := split_cell_name(name)) and cell[0] == table}
    return sorted(rows)


def _row(values: Mapping[str, Any], table: str, index: int) -> dict[str, Any]:
    """Every column of one row: column name → value."""
    return {
        cell[2]: value
        for name, value in values.items()
        if (cell := split_cell_name(name)) and cell[0] == table and cell[1] == index
    }
