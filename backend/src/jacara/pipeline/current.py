"""Current post-review results: deleted rows skipped, tables renumbered in display order, rules
re-evaluated on the current values.

Rule results at extraction time (extractions.checks) are stored and never changed; when values
are edited or rows added or removed during review, rules such as "line amounts add up" are
re-evaluated on the current values. Both the review page and exports use these results."""

from __future__ import annotations

import uuid
from contextlib import suppress
from dataclasses import replace
from typing import Any, Literal, cast

from jacara.core.i18n import language as reader_language
from jacara.core.i18n import tr
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
    """A version of my template → Template. The name is the template id (stored in
    extractions.template)."""
    record = version.template
    template = from_definition(
        version.definition,
        str(record.id),
        version.version,
        record.title,
        record.category,
        record.description,
    )
    # Tags live on the template, not in versions; automatic selection must use the current domains
    # and country.
    return replace(
        template,
        domains=tuple(record.domains or template.domains),
        countries=tuple(record.countries) if record.domains else template.countries,
    )


def template_of(extraction: Extraction) -> Template | None:
    """Template used for extracting this page.

    - My templates: restored from the recorded version, so later template changes do not
      affect this document;
    - Built-in templates: read from templates/*.toml by name;
    - Key-value discovery, or the template file no longer exists (renamed or deleted): None."""
    if extraction.template_def is not None:
        return template_from_version(extraction.template_def)
    if extraction.template == DISCOVER:
        return None
    try:
        return load_template(extraction.template)
    except TemplateError:
        return None


def template_title(extraction: Extraction, template: Template | None = None) -> str:
    """Display name of the template used for this page, in the reader's language when the template
    has a name in it (otherwise its primary language). Lists across templates use it; the template
    itself is identified by extraction.template (its id or file name), never by this name.
    template: the result of template_of(extraction) when the caller already has it."""
    if extraction.template == DISCOVER:
        return tr("抽取所有键值对")
    if template is None:
        template = template_of(extraction)
    return template.title_in(reader_language()) if template else extraction.template


def labels_of(extraction: Extraction, language: str | None = None) -> dict[str, str]:
    """Field ID → display name (for the review page and exports). Table cells are looked up as
    "table.column". Lists across templates pass reader_language() for names in the reader's
    language.

        Uses the default language; with language set, the template's names in that language,
    falling back to the default. Key-value discovery fields use the label on the document. Fields
    absent from the template (template file missing) are omitted."""
    template = template_of(extraction)
    labels = template.labels(language) if template else {}
    labels.update({v.name: v.label for v in extraction.values if v.label})
    return labels


def label_for(labels: dict[str, str], name: str) -> str | None:
    """Display name of one field: items.2.amount looks up "items.amount" and appends the row
    number."""
    if name in labels:
        return labels[name]
    cell = split_cell_name(name)
    if cell and f"{cell[0]}.{cell[2]}" in labels:
        return labels[f"{cell[0]}.{cell[2]}"]
    return None


def current_values(extraction: Extraction) -> list[tuple[ExtractedValue, str]]:
    """Current fields with their display names: in display order, deleted rows skipped, table cells
    renumbered in display order.

        For example, after deleting row 2, the former items.3.amount is displayed as
    items.2.amount; a new row inserted after row 1 (possibly named items.9.*) is displayed as
    items.2.*."""
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
    """Values in the database are text; numbers are restored so rules can compute with them."""
    if type_ == "number" and raw is not None:
        with suppress(ValueError):
            return float(raw)
    return raw


def current_checks(extraction: Extraction) -> ValidationResult:
    """Re-evaluate rules on the current values. Fields use display names (row numbers match the
    page).

        Key-value discovery has no rules; if the template file no longer exists (renamed or
    deleted), the results stored at extraction are used."""
    template = template_of(extraction)
    if template is None:
        from jacara.pipeline.load import stored_checks

        return stored_checks(extraction.checks)
    fields = [
        ExtractedField(name, cast(FieldType, v.type), typed_value(v.type, v.current_value))
        for v, name in current_values(extraction)
    ]
    result = template.validator().validate(fields)
    if not any(f.unique for f in template.fields):
        return result
    duplicates = duplicate_issues(extraction, template)
    return ValidationResult(
        result.issues + tuple(duplicates),
        result.passed + (() if duplicates else (DUPLICATE,)),
        result.skipped,
    )


DUPLICATE = "duplicate"
"""Id of the duplicate check: a field marked "must be unique" in the template has the same value on
another document."""


def _same(text: str | None) -> str:
    """Comparison key: whitespace removed, case-insensitive ("INV 001" equals "inv001")."""
    return "".join((text or "").split()).upper()


def duplicate_issues(extraction: Extraction, template: Template) -> list[Issue]:
    """Possible duplicate claims: warn when a field marked "must be unique" (invoice number, bill
    number) has the same current value on another document in the same team (another file, or
    another document in the same file).

        Pairs where both documents have totals that differ are excluded (different merchants may
    reuse numbers). Deleted rows and earlier runs are ignored."""
    from sqlalchemy import or_, select
    from sqlalchemy.orm import object_session

    from jacara.models import Document, ValueRevision

    session = object_session(extraction)
    if session is None:
        return []
    mine = {v.name: v for v in extraction.values if not v.deleted}
    total = mine["total"].current_value if "total" in mine else None
    issues: list[Issue] = []
    for field in (f for f in template.fields if f.unique):
        value = mine.get(field.name)
        if value is None or not (value.current_value or "").strip():
            continue
        wanted = value.current_value
        candidates = session.scalars(
            select(ExtractedValue)
            .join(Extraction, ExtractedValue.extraction_id == Extraction.id)
            .join(Document, Extraction.document_id == Document.id)
            .where(
                ExtractedValue.name == field.name,
                Extraction.run == Document.current_run,
                Extraction.id != extraction.id,
                Document.team_id == extraction.team_id,
                or_(
                    ExtractedValue.value == wanted,
                    ExtractedValue.id.in_(
                        select(ValueRevision.value_id).where(ValueRevision.new_value == wanted)
                    ),
                ),
            )
        ).all()
        hits: list[str] = []
        for other in candidates:
            if other.deleted or _same(other.current_value) != _same(wanted):
                continue
            other_total = next(
                (v.current_value for v in other.extraction.values if v.name == "total"), None
            )
            if total and other_total and _differs(total, other_total):
                continue
            hits.append(_where(other.extraction, extraction))
        if hits:
            label = field.label
            shown = tr("；").join(hits[:3])
            if len(hits) > 3:
                shown = tr("{places} 等 {n} 处", places=shown, n=len(hits))
            issues.append(
                Issue(
                    DUPLICATE,
                    tr("可能重复报销"),
                    tr(
                        "{label}「{value}」和{places}一样，请确认是不是同一张单据传了两次",
                        label=label,
                        value=wanted,
                        places=shown,
                    ),
                    (field.name,),
                    "warning",
                )
            )
    return issues


def _differs(a: str, b: str) -> bool:
    try:
        return abs(float(a) - float(b)) >= 0.005
    except ValueError:
        return _same(a) != _same(b)


def _where(other: Extraction, mine: Extraction) -> str:
    """Location of the other document: "page n of this file" within the same file, otherwise file
    name and upload date."""
    pages = page_range(other)
    if other.document_id == mine.document_id:
        return tr("这个文件的{pages}", pages=pages)
    day = other.document.created_at.strftime("%Y-%m-%d")
    return tr(
        "《{filename}》{pages}（{day} 上传）",
        filename=other.document.filename,
        pages=pages,
        day=day,
    )


def page_range(extraction: Extraction) -> str:
    """Pages occupied by a document: "page 3", "pages 3–4"."""
    last = getattr(extraction, "last_page", None) or extraction.page
    return (
        tr("第 {page} 页", page=extraction.page)
        if last == extraction.page
        else tr("第 {first}–{last} 页", first=extraction.page, last=last)
    )


def current_checks_json(extraction: Extraction) -> dict[str, object]:
    """For the API: field names in issues are mapped back to real field names (the frontend locates
    cells by real name)."""
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
"""Order of "sort by status" on the review page: red, yellow, grey, then green."""


def level_of(value: ExtractedValue, check: CheckState) -> Level:
    """Field color: evidence × validation, then whether a person has reviewed it.

        - Red: the current value violates an error-level rule, or it was not found in the source
          and nobody has reviewed it
        - Yellow: violates a warning-level rule, or the evidence is doubtful and nobody has
          reviewed it
        - Grey: the model reported it absent and nobody has reviewed it (or the row was deleted)
        - Green: everything else (found and consistent in the source, or confirmed or edited by a
          person)

        Rules are re-evaluated on the current values, so a confirmed value can still be red: when
    the value matches the source but a rule fails, the document itself is inconsistent and a
    person should know."""
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
    """Validation status and color of each field. checks is the output of current_checks_json (real
    field names)."""
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
    """Text of the evidence blocks at extraction. Newer documents store it in evidence.text; for
    older ones, the text blocks of this page's OCR result whose center lies inside the evidence
    box are used (the stored evidence is not changed)."""
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
    """Whether this field can have a source value: fuzzy-matched text fields, or numbers with
    evidence."""
    if value.deleted or value.value is None or value.evidence is None:
        return False
    return (value.type == "string" and value.status == "fuzzy") or (
        value.type == "number" and value.status == "matched"
    )


def source_value(value: ExtractedValue, text: str | None) -> str | None:
    """Source value: the value as written in the evidence text, present only when it differs from
    the current value. Used by "use source value" during review.

        - Text fields: the text of the source blocks, only for fuzzy matches.
        - Numbers: the same number in the source carries a minus sign (discount 6.00-) while the
          value is positive, or vice versa. Another number on the line is never substituted (in
          "2 @ 3.00 6.00", the unit price 3.0 does not become 6.0).
        - Dates are not offered: whether 25/03/18 is day/month/year or month/day/year cannot be
          decided from the source alone."""
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
"""(confirm or edit, suggested value, source run)."""


def previous_extraction(document: Any, extraction: Extraction) -> Extraction | None:
    """Extraction of the same page in the previous run (the most recent run before the current
    one)."""
    earlier = [
        e for e in document.all_extractions if e.page == extraction.page and e.run < extraction.run
    ]
    return max(earlier, key=lambda e: e.run, default=None)


def suggestions(current: Extraction, previous: Extraction | None) -> dict[uuid.UUID, Suggestion]:
    """After reprocessing, whether fields reviewed in the previous run can be carried over.

        Same field (same field ID) with evidence at about the same position on the image, and:
        - the new extracted value equals the previous post-review value → suggest "confirm";
        - the new extracted value equals the value the model got wrong last time and a person
          corrected → suggest "apply the previous correction".
        Otherwise (different value or position) nothing is suggested and a person must look
    again."""
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
    """Whether the centers of two evidence boxes are close enough (at most twice the box height plus
    10 pixels). Also true when neither has evidence."""
    if a is None or b is None:
        return a is None and b is None
    ax0, ay0, ax1, ay1 = a["bbox"]
    bx0, by0, bx1, by1 = b["bbox"]
    distance = ((ax0 + ax1 - bx0 - bx1) ** 2 + (ay0 + ay1 - by0 - by1) ** 2) ** 0.5 / 2
    return distance <= 2 * max(ay1 - ay0, by1 - by0) + 10
