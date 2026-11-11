"""审核之后的"当前"结果：跳过删掉的行，表格按显示顺序重新编号，规则按当前值重新检查。

抽取时的规则结果（extractions.checks）存下来不改；审核时改了值、增删了行，
"商品金额合计"这些规则要按现在的值再算一次，审核页面和导出看的都是这个。
"""

from __future__ import annotations

from contextlib import suppress
from typing import cast

from jacara.models import ExtractedValue, Extraction
from jacara.plugins.extractors.base import ExtractedField, FieldType, cell_name, split_cell_name
from jacara.plugins.validators import RuleValidator
from jacara.plugins.validators.base import Issue, ValidationResult
from jacara.templates import TemplateError, load_template


def current_values(extraction: Extraction) -> list[tuple[ExtractedValue, str]]:
    """当前的字段和它的显示名：按显示顺序，跳过删掉的行；表格的格子按显示顺序重新编号。

    例如删掉第 2 行后，原来的 items.3.amount 显示名变成 items.2.amount；
    插在第 1 行后面的新行（名字可能是 items.9.*）显示名是 items.2.*。
    """
    numbering: dict[str, dict[int, int]] = {}
    result: list[tuple[ExtractedValue, str]] = []
    for value in sorted(extraction.values, key=lambda v: (v.sort_key, v.position)):
        if value.deleted:
            continue
        cell = split_cell_name(value.name)
        if cell is None:
            result.append((value, value.name))
            continue
        table, index, column = cell
        rows = numbering.setdefault(table, {})
        rows.setdefault(index, len(rows) + 1)
        result.append((value, cell_name(table, rows[index], column)))
    return result


def typed_value(type_: str, raw: str | None) -> str | float | None:
    """数据库里的值都是文字；数字还原成数字，规则才能计算。"""
    if type_ == "number" and raw is not None:
        with suppress(ValueError):
            return float(raw)
    return raw


def current_checks(extraction: Extraction) -> ValidationResult:
    """按当前值重新检查规则。字段名用显示名（第几行和页面上一致）。

    模板文件找不到了（改名、删了），就用抽取时存下的结果。
    """
    try:
        rules = load_template(extraction.template).rules
    except TemplateError:
        from jacara.pipeline.load import stored_checks

        return stored_checks(extraction.checks)
    fields = [
        ExtractedField(name, cast(FieldType, v.type), typed_value(v.type, v.current_value))
        for v, name in current_values(extraction)
    ]
    return RuleValidator(rules).validate(fields)


def current_checks_json(extraction: Extraction) -> dict[str, object]:
    """给接口用：issues 里的字段名换回真实的字段名（前端按真实名字找到格子）。"""
    real = {name: v.name for v, name in current_values(extraction)}
    checks = current_checks(extraction)
    return {
        "passed": list(checks.passed),
        "skipped": list(checks.skipped),
        "issues": [_issue_json(i, real) for i in checks.issues],
    }


def _issue_json(issue: Issue, real: dict[str, str]) -> dict[str, object]:
    return {
        "rule": issue.rule,
        "title": issue.title,
        "message": issue.message,
        "fields": [real.get(f, f) for f in issue.fields],
        "severity": issue.severity,
    }
