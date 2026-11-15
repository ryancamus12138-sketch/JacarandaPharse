"""Load DocumentReport from the database: the inverse of store.py.

Exporters are plugins that never touch the database and accept only DocumentReport. The database
stores documents, extractions, fields and revisions in separate tables; this module reassembles
them into the form exporters expect, adding the latest manual review of each field (reviews)."""

from __future__ import annotations

from contextlib import suppress
from typing import Any, cast

from jacara.models import Document, ExtractedValue, Extraction
from jacara.pipeline.ranges import range_settings
from jacara.plugins.aligners.base import AlignedField, AlignStatus, Evidence
from jacara.plugins.exporters.base import DocumentReport, Review
from jacara.plugins.extractors.base import ExtractedField, ExtractionResult, FieldType
from jacara.plugins.ocr.base import OcrLine, Point
from jacara.plugins.validators.base import Issue, ValidationResult
from jacara.templates import Template


def load_reports(document: Document, language: str | None = None) -> list[DocumentReport]:
    """One DocumentReport per document within a file. Extractions, fields and revisions must be
    loaded first. language: language of field names (column headers) in exports; defaults to the
    default language."""
    from jacara.pipeline.current import page_range

    several = len(document.extractions) > 1
    return [
        _report(
            extraction,
            f"{document.filename} {page_range(extraction)}" if several else document.filename,
            language,
        )
        for extraction in document.extractions
    ]


def _report(extraction: Extraction, source: str, language: str | None = None) -> DocumentReport:
    """Current results: deleted rows are not exported, tables are numbered in display order, rules
    are re-evaluated on post-review values."""
    from jacara.pipeline.current import current_checks, current_values, labels_of, template_of

    current = current_values(extraction)
    fields = tuple(_field(v, name) for v, name in current)
    result = ExtractionResult(
        fields=fields,
        raw_output=extraction.raw_output,
        model=extraction.model,
        backend=extraction.backend,
        prompt_version=extraction.prompt_version,
        duration_ms=extraction.duration_ms,
        vision=extraction.vision,
    )
    aligned = tuple(
        AlignedField(
            field=f,
            # cast: tells the type checker the stored value is one of these literals; no effect at
            # runtime.
            status=cast(AlignStatus, v.status),
            evidence=_evidence(v.current_evidence),
            needs_review=v.needs_review,
            note=v.note,
        )
        for f, (v, _) in zip(fields, current, strict=True)
    )
    reviews = {
        name: Review(
            action=v.revisions[-1].action,
            value=v.revisions[-1].new_value,
            reason=v.revisions[-1].reason,
            reviewed_at=v.revisions[-1].created_at,
        )
        for v, name in current
        if v.revisions
    }
    return DocumentReport(
        source=source,
        extraction=result,
        aligned=aligned,
        checks=current_checks(extraction),
        ocr_engine=extraction.ocr_engine,
        processed_at=extraction.created_at,
        page=extraction.page,
        last_page=extraction.last_page,
        text_source=extraction.source,
        template=extraction.template,
        template_version=extraction.template_version,
        reviews=reviews,
        labels=labels_of(extraction, language),
        hidden=_hidden(template_of(extraction)),
        ranges=range_settings(template_of(extraction)),
    )


def _hidden(template: Template | None) -> frozenset[str]:
    """Fields, tables and table columns set not to export in the template."""
    if template is None:
        return frozenset()
    names = {f.name for f in template.fields if not f.export}
    for t in template.tables:
        if not t.export:
            names.add(t.name)
        names |= {f"{t.name}.{c.name}" for c in t.columns if not c.export}
    return frozenset(names)


def _field(value: ExtractedValue, name: str) -> ExtractedField:
    """Values in the database are text; numbers are restored so rules and exports can treat them as
    numbers.

        Manually added rows have no model value, so the original value is empty; exports use the
    post-review value."""
    raw: str | float | None = value.value
    if value.type == "number" and raw is not None:
        # Keep the value unchanged if it is not numeric (rule validation flags it).
        with suppress(ValueError):
            raw = float(raw)
    quote = (value.evidence or {}).get("quote")
    return ExtractedField(
        name,
        cast(FieldType, value.type),
        raw,
        value.row_no,
        value.label,
        text=quote if isinstance(quote, str) else None,
    )


def _evidence(data: dict[str, Any] | None) -> Evidence | None:
    """Evidence stores only the merged box and the lowest confidence; restore it as a single text
    block with the same bbox and confidence."""
    if data is None:
        return None
    x0, y0, x1, y1 = data["bbox"]
    line = OcrLine(
        text=data["row_text"],
        polygon=(Point(x0, y0), Point(x1, y0), Point(x1, y1), Point(x0, y1)),
        confidence=data["confidence"],
    )
    return Evidence(data.get("row") or 0, data["row_text"], (line,), data["similarity"])


def stored_checks(data: dict[str, Any]) -> ValidationResult:
    return ValidationResult(
        issues=tuple(
            Issue(i["rule"], i["title"], i["message"], tuple(i["fields"]), i["severity"])
            for i in data.get("issues", [])
        ),
        passed=tuple(data.get("passed", [])),
        skipped=tuple(data.get("skipped", [])),
    )
