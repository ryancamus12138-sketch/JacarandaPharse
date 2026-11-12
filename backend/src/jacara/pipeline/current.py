"""审核之后的"当前"结果：跳过删掉的行，表格按显示顺序重新编号，规则按当前值重新检查。

抽取时的规则结果（extractions.checks）存下来不改；审核时改了值、增删了行，
"商品金额合计"这些规则要按现在的值再算一次，审核页面和导出看的都是这个。
"""

from __future__ import annotations

import uuid
from contextlib import suppress
from typing import Any, Literal, cast

from jacara.core.textvalue import signed_number
from jacara.models import ExtractedValue, Extraction, TemplateVersion
from jacara.plugins.extractors.base import (
    DISCOVER,
    ExtractedField,
    FieldType,
    cell_name,
    split_cell_name,
)
from jacara.plugins.validators.base import Issue, ValidationResult
from jacara.templates import Template, TemplateError, from_definition, load_template


def template_from_version(version: TemplateVersion) -> Template:
    """我的模板的一个版本 → Template。名字用模板的 id（存进 extractions.template）。"""
    record = version.template
    return from_definition(
        version.definition,
        str(record.id),
        version.version,
        record.title,
        record.category,
        record.description,
    )


def template_of(extraction: Extraction) -> Template | None:
    """这一页抽取时用的模板（ADR 0030）。

    - 我的模板：按记下的版本号还原，模板以后再改也不影响这份文档；
    - 内置模板：按名字读 templates/*.toml；
    - 键值对发现，或者模板文件找不到了（改名、删了）：None。
    """
    if extraction.template_def is not None:
        return template_from_version(extraction.template_def)
    if extraction.template == DISCOVER:
        return None
    try:
        return load_template(extraction.template)
    except TemplateError:
        return None


def labels_of(extraction: Extraction) -> dict[str, str]:
    """字段 ID → 中文名（审核页面、导出用）。表格的格子按"表名.列名"找。

    键值对发现的字段用单据上的原名。模板里没有的字段（模板文件找不到了）不在里面。
    """
    template = template_of(extraction)
    labels = template.labels() if template else {}
    labels.update({v.name: v.label for v in extraction.values if v.label})
    return labels


def label_for(labels: dict[str, str], name: str) -> str | None:
    """一个字段的中文名：items.2.amount 找"items.amount"，再加上第几行。"""
    if name in labels:
        return labels[name]
    cell = split_cell_name(name)
    if cell and f"{cell[0]}.{cell[2]}" in labels:
        return labels[f"{cell[0]}.{cell[2]}"]
    return None


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

    键值对发现没有规则；模板文件找不到了（改名、删了），就用抽取时存下的结果。
    """
    template = template_of(extraction)
    if template is None:
        from jacara.pipeline.load import stored_checks

        return stored_checks(extraction.checks)
    fields = [
        ExtractedField(name, cast(FieldType, v.type), typed_value(v.type, v.current_value))
        for v, name in current_values(extraction)
    ]
    return template.validator().validate(fields)


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


CheckState = Literal["ok", "warn", "fail"]
Level = Literal["red", "yellow", "grey", "green"]
LEVEL_ORDER: dict[str, int] = {"red": 0, "yellow": 1, "grey": 2, "green": 3}
"""审核页面"按状态排序"的顺序：先红、再黄、再灰、最后绿。"""


def level_of(value: ExtractedValue, check: CheckState) -> Level:
    """字段的颜色（ADR 0028）：证据 × 校验，再看人审核过没有。

    - 红：当前值违反了"错误"级的规则；或者原文里找不到、还没人看过
    - 黄：违反了"警告"级的规则；或者出处有疑点、还没人看过
    - 灰：模型说原文没有、还没人看过（或者这一行删掉了）
    - 绿：其他（原文里找到且一致，或者人确认、修改过）

    规则是按当前值重新算的，人确认过也照样标红：值对上了原文、规则却不通过，
    说明单据本身对不上，要人知道。
    """
    if value.deleted:
        return "grey"
    if check == "fail":
        return "red"
    evidence = value.evidence_state
    if not value.reviewed and evidence == "missing":
        return "red"
    if check == "warn" or (not value.reviewed and evidence == "pending"):
        return "yellow"
    if not value.reviewed and evidence == "empty":
        return "grey"
    return "green"


def field_states(
    extraction: Extraction, checks: dict[str, object]
) -> dict[uuid.UUID, tuple[CheckState, Level]]:
    """每个字段的校验状态和颜色。checks 是 current_checks_json 的结果（真实字段名）。"""
    worst: dict[str, CheckState] = {}
    for issue in cast(list[dict[str, Any]], checks["issues"]):
        state: CheckState = "fail" if issue["severity"] == "error" else "warn"
        for name in issue["fields"]:
            if worst.get(name) != "fail":
                worst[name] = state
    result: dict[uuid.UUID, tuple[CheckState, Level]] = {}
    for value in extraction.values:
        check: CheckState = "ok" if value.deleted else worst.get(value.name, "ok")
        result[value.id] = (check, level_of(value, check))
    return result


def evidence_text(value: ExtractedValue, ocr_lines: list[dict[str, Any]] | None) -> str | None:
    """抽取时出处那几块文字。新处理的文档存在 evidence.text 里；更新前处理的没有存，
    就用这一页的 OCR 结果，取中心点落在出处框里的文字块（不改数据库里的出处）。"""
    evidence = value.evidence or {}
    if "text" in evidence:
        return cast(str | None, evidence["text"])
    bbox = evidence.get("bbox")
    if not ocr_lines or not bbox:
        return None
    x0, y0, x1, y1 = bbox
    inside = [
        line["text"]
        for line in ocr_lines
        if x0 - 1 <= (line["box"][0] + line["box"][2]) / 2 <= x1 + 1
        and y0 - 1 <= (line["box"][1] + line["box"][3]) / 2 <= y1 + 1
    ]
    return " ".join(inside) or None


def wants_source(value: ExtractedValue) -> bool:
    """这个字段可能有"原文值"：相似匹配的文字字段，或者找到了出处的数字。"""
    if value.deleted or value.value is None or value.evidence is None:
        return False
    return (value.type == "string" and value.status == "fuzzy") or (
        value.type == "number" and value.status == "matched"
    )


def source_value(value: ExtractedValue, text: str | None) -> str | None:
    """原文值：按出处文字的写法得到的值，和当前值不一样时才有。审核时"采用原文值"用。

    - 文字字段：只是相似匹配（fuzzy）时，原文那几块的文字。
    - 数字：原文里同一个数字带着负号（折扣 6.00-），值却是正的；或者反过来。
      不会拿同一行别的数字顶替（"2 @ 3.00 6.00" 里单价 3.0 不会变成 6.0）。
    - 日期不给：25/03/18 是日/月/年还是月/日/年，单看原文判断不了。
    """
    if not text or not wants_source(value):
        return None
    candidate: str | None = None
    if value.type == "string":
        candidate = " ".join(text.split()) or None
    else:
        with suppress(ValueError):
            signed = signed_number(text, float(cast(str, value.value)))
            current = None if value.current_value is None else float(value.current_value)
            if signed is not None and signed != current:
                candidate = str(signed)
    return candidate if candidate != value.current_value else None


Suggestion = tuple[Literal["confirm", "edit"], str | None, int]
"""（confirm 或 edit，建议的值，来自第几轮）。"""


def previous_extraction(document: Any, extraction: Extraction) -> Extraction | None:
    """同一页在上一轮（比现在这轮早、最近的一轮）的抽取结果。"""
    earlier = [
        e for e in document.all_extractions if e.page == extraction.page and e.run < extraction.run
    ]
    return max(earlier, key=lambda e: e.run, default=None)


def suggestions(current: Extraction, previous: Extraction | None) -> dict[uuid.UUID, Suggestion]:
    """重新处理之后，上一轮人审核过的字段能不能直接沿用（ADR 0030）。

    同一个字段（字段 ID 一样）、出处在图上差不多同一个位置，而且：
    - 这次抽出的值 = 上次审核后的值 → 建议"确认"；
    - 这次抽出的值 = 上次模型抽错、被人改掉的那个值 → 建议"改成上次人改的值"。
    其他情况（值不一样、位置不一样）不建议，要人重新看。
    """
    if previous is None:
        return {}
    before = {v.name: v for v in previous.values if v.reviewed and not v.deleted}
    result: dict[uuid.UUID, Suggestion] = {}
    for value in current.values:
        old = before.get(value.name)
        if old is None or value.reviewed or value.deleted or value.value is None:
            continue
        if not _near(value.evidence, old.current_evidence):
            continue
        if value.value == old.current_value:
            result[value.id] = ("confirm", old.current_value, previous.run)
        elif value.value == old.value and old.revisions[-1].action == "edit":
            result[value.id] = ("edit", old.current_value, previous.run)
    return result


def _near(a: dict[str, Any] | None, b: dict[str, Any] | None) -> bool:
    """两个出处框的中心离得够近（不超过框高的两倍再加 10 像素）。都没有出处也算。"""
    if a is None or b is None:
        return a is None and b is None
    ax0, ay0, ax1, ay1 = a["bbox"]
    bx0, by0, bx1, by1 = b["bbox"]
    distance = ((ax0 + ax1 - bx0 - bx1) ** 2 + (ay0 + ay1 - by0 - by1) ** 2) ** 0.5 / 2
    return distance <= 2 * max(ay1 - ay0, by1 - by0) + 10
