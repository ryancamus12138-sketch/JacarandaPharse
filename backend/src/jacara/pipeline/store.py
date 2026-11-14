"""Store pipeline results in the database.

Plugins never touch the database: they return DocumentReport and this module stores it."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

from sqlalchemy.orm import Session

from jacara.core.owner import PERSONAL_OWNER, Owner
from jacara.models import Document, ExtractedValue, Extraction
from jacara.pipeline.pages import local_box
from jacara.plugins.aligners.base import AlignedField
from jacara.plugins.exporters.base import DocumentReport
from jacara.plugins.ocr.base import OcrPage
from jacara.plugins.validators.base import ValidationResult


def file_sha256(path: Path) -> str:
    """Content fingerprint. Files may be large, so they are read in chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def save_document(
    session: Session,
    path: Path,
    reports: Sequence[DocumentReport],
    owner: Owner = PERSONAL_OWNER,
) -> Document:
    """For the CLI: create a document record and store the extraction of every page with it. The
    caller commits."""
    document = Document(
        filename=path.name,
        sha256=file_sha256(path),
        size_bytes=path.stat().st_size,
        page_count=page_count(reports),
        status="done",
        user_id=owner.user_id,
        team_id=owner.team_id,
    )
    session.add(document)
    add_extractions(session, document, reports)
    return document


def page_count(reports: Sequence[DocumentReport]) -> int:
    """Page count of the file: the last page of the last document (a document may span several
    pages)."""
    return max((r.last_page or r.page for r in reports), default=0)


def add_extractions(
    session: Session,
    document: Document,
    reports: Sequence[DocumentReport],
    *,
    run: int = 1,
    template_version_id: uuid.UUID | None = None,
    run_reason: str | None = None,
    ocr_snapshot: str | None = None,
) -> None:
    """Attach the extraction of each page to an existing document. Used by background processing of
    uploaded files.

        Extractions have the same ownership as the document (same user and team). run is the run
    number. ocr_snapshot is the storage key of this run's OCR snapshot, shared by all documents
    of the run."""
    who = {"user_id": document.user_id, "team_id": document.team_id}
    for report in reports:
        e = report.extraction
        extraction = Extraction(
            document=document,
            page=report.page,
            last_page=report.last_page,
            source=report.text_source,
            ocr_engine=report.ocr_engine,
            model=e.model,
            backend=e.backend,
            prompt_version=e.prompt_version,
            template=report.template,
            template_version=report.template_version,
            # With automatic selection, each document uses its own template; otherwise the template
            # chosen on the document.
            template_version_id=report.template_version_id
            if report.classified is not None
            else template_version_id,
            classified=dict(report.classified) if report.classified is not None else None,
            vision=e.vision,
            run=run,
            run_reason=run_reason,
            duration_ms=e.duration_ms,
            raw_output=e.raw_output,
            checks=checks_json(report.checks),
            ocr=report_ocr_json(report),
            structure=report.structure.to_json() if report.structure else None,
            model_input=dict(e.sent) if e.sent else None,
            ocr_snapshot=ocr_snapshot,
            **who,
        )
        session.add(extraction)
        flagged = report.checks.fields_with_issues()
        for position, field in enumerate(report.aligned):
            row = _value_row(field, position, extraction, field.field.name in flagged, who)
            row.evidence = _evidence_json(field, report)
            row.candidates = candidates_json(field, report)
            if field.field.name in report.manual:
                # Manual entry: awaiting input, not reported absent by the model.
                row.origin = "manual"
                row.needs_review = False
            elif field.field.name in report.computed:
                row.origin = "computed"  # Computed by formula.
            session.add(row)

    # Have the database assign IDs and check constraints now, so errors surface immediately.
    session.flush()


def _value_row(
    field: AlignedField,
    position: int,
    extraction: Extraction,
    rule_failed: bool,
    who: dict[str, Any],
) -> ExtractedValue:
    value = field.field.value
    rejected = field.status == "rejected" and value is not None
    return ExtractedValue(
        extraction=extraction,
        position=position,
        sort_key=float(position),
        name=field.field.name,
        label=field.field.label,
        type=field.field.type,
        # Rejected by cross-checking: the value is stored empty and the model value is recorded
        # separately.
        value=None if value is None or rejected else str(value),
        rejected_value=str(value) if rejected else None,
        row_no=field.field.row,
        status=field.status,
        needs_review=field.needs_review or rule_failed,
        note=field.note,
        **who,
    )


def _evidence_json(field: AlignedField, report: DocumentReport) -> dict[str, Any] | None:
    """Evidence: page (page number in the file) and box on that page. For stacked documents, boxes
    on the tall page are converted back to their page's coordinates; boxes spanning two pages
    keep only the part on the upper page."""
    ev = field.evidence
    if ev is None:
        return None
    page, bbox = report.page, list(ev.bbox)
    lines = ev.lines
    if report.page_starts:
        index, bbox = local_box(ev.bbox, report.page_starts)
        page = report.page + index
        same = [ln for ln in lines if local_box(ln.bbox, report.page_starts)[0] == index]
        if same and len(same) < len(lines):
            boxes = [local_box(ln.bbox, report.page_starts)[1] for ln in same]
            bbox = [
                min(b[0] for b in boxes),
                min(b[1] for b in boxes),
                max(b[2] for b in boxes),
                max(b[3] for b in boxes),
            ]
    return {
        "page": page,
        "row": ev.row,
        "blocks": list(field.field.blocks),
        "row_text": ev.row_text,
        "text": " ".join(line.text for line in lines),
        "bbox": [round(v, 1) for v in bbox],
        "confidence": round(ev.confidence, 4),
        "similarity": ev.similarity,
        **({"quote": field.field.text} if field.field.text else {}),
        **({"text_layer": field.layer} if field.layer else {}),
    }


def candidates_json(field: AlignedField, report: DocumentReport) -> dict[str, Any] | None:
    """Candidates stored as JSON, with boxes converted back to their page's coordinates as for
    evidence."""
    if not field.candidates:
        return None
    items = []
    for c in field.candidates:
        page, box = report.page, list(c.box) if c.box else None
        if box is not None and report.page_starts:
            index, box = local_box(box, report.page_starts)
            page = report.page + index
        items.append(
            {
                "source": c.source,
                "text": c.text,
                "value": c.value,
                "page": page,
                "box": [round(v, 1) for v in box] if box else None,
                "confidence": round(c.confidence, 4) if c.confidence is not None else None,
                "note": c.note,
                "dpi": c.dpi,
                "rule_ok": c.rule_ok,
                "visible": c.visible,
            }
        )
    return {"items": items}


def report_ocr_json(report: DocumentReport) -> dict[str, Any] | None:
    """OCR result of a document: a single-page document stores that page as before; a multi-page
    document stores each page separately ({"pages": [{"page": 3, "width", "height", "lines"},
    …]}, in each page's own coordinates)."""
    if not report.pages:
        return ocr_json(report.ocr_page)
    return {
        "pages": [
            {"page": report.page + k, "engine": p.engine, **(ocr_json(p) or {})}
            for k, p in enumerate(report.pages)
        ]
    }


def ocr_json(page: OcrPage | None) -> dict[str, Any] | None:
    """One page of OCR results as JSON: text, bounding box, four corners and confidence of every
    text block, plus its visual line number.

        Used by click-to-fill on the review page: the clicked block supplies its text and
    position."""
    if page is None:
        return None
    from jacara.plugins.ocr.layout import group_rows

    row_of = {id(line): row.index for row in group_rows(page) for line in row.lines}
    return {
        "width": page.width,
        "height": page.height,
        **({"note": page.models["note"]} if page.models.get("note") else {}),
        **(
            {
                "text_layer": [
                    {"text": ln.text, "box": [round(v, 1) for v in ln.bbox]}
                    for ln in page.text_layer
                ]
            }
            if page.text_layer
            else {}
        ),
        **(
            {
                "forms": [
                    {
                        "name": f.name,
                        "value": f.value,
                        "box": [round(v, 1) for v in f.box],
                        "hidden": f.hidden,
                    }
                    for f in page.forms
                ]
            }
            if page.forms
            else {}
        ),
        "lines": [
            {
                "text": line.text,
                "box": [round(v, 1) for v in line.bbox],
                "polygon": [[round(p.x, 1), round(p.y, 1)] for p in line.polygon],
                "confidence": round(line.confidence, 4),
                "row": row_of.get(id(line)),
            }
            for line in page.lines
        ],
    }


def page_from_json(data: dict[str, Any], engine: str) -> OcrPage:
    """Inverse of ocr_json: restore a page from the stored OCR result without re-running OCR (for
    trial runs)."""
    from jacara.plugins.ocr.base import FormValue, OcrLine, Point

    lines = tuple(
        OcrLine(
            text=line["text"],
            polygon=cast(
                tuple[Point, Point, Point, Point], tuple(Point(x, y) for x, y in line["polygon"])
            ),
            confidence=line["confidence"],
        )
        for line in data.get("lines", [])
        if len(line.get("polygon", [])) == 4
    )
    layer = tuple(
        OcrLine(
            text=line["text"],
            polygon=(
                Point(line["box"][0], line["box"][1]),
                Point(line["box"][2], line["box"][1]),
                Point(line["box"][2], line["box"][3]),
                Point(line["box"][0], line["box"][3]),
            ),
            confidence=1.0,
        )
        for line in data.get("text_layer", [])
        if len(line.get("box", [])) == 4
    )
    forms = tuple(
        FormValue(f["name"], f["value"], tuple(f["box"]), bool(f.get("hidden")))  # type: ignore[arg-type]
        for f in data.get("forms", [])
        if len(f.get("box", [])) == 4
    )
    return OcrPage(
        width=int(data.get("width") or 0),
        height=int(data.get("height") or 0),
        lines=lines,
        engine=engine,
        models={"engine": engine},
        duration_ms=0,
        text_layer=layer,
        forms=forms,
    )


def evidence_from_ocr(lines: list[dict[str, Any]]) -> dict[str, Any]:
    """Text blocks (in ocr_json format) → evidence, in the same format as evidence found during
    extraction."""
    boxes = [line["box"] for line in lines]
    rows = [line["row"] for line in lines if line.get("row") is not None]
    return {
        "row": min(rows) if rows else None,
        "row_text": "  ".join(line["text"] for line in lines),
        "text": " ".join(line["text"] for line in lines),
        "bbox": [
            min(b[0] for b in boxes),
            min(b[1] for b in boxes),
            max(b[2] for b in boxes),
            max(b[3] for b in boxes),
        ],
        "confidence": min(line["confidence"] for line in lines),
        "similarity": 1.0,
    }


def checks_json(checks: ValidationResult) -> dict[str, Any]:
    return {
        "passed": list(checks.passed),
        "skipped": list(checks.skipped),
        "issues": [
            {
                "rule": i.rule,
                "title": i.title,
                "message": i.message,
                "fields": list(i.fields),
                "severity": i.severity,
            }
            for i in checks.issues
        ],
    }
