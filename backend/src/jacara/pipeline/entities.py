"""Detect subjects that disagree across documents.

When a template defines subjects (for example a patient: identifier + name, sex, date of birth)
and the same identifier appears on other documents, the other fields of that subject are
compared; differences are flagged (it is unknown which document is right, so both need a look).
Only documents with the same template, current run and team are compared, at most 50."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from jacara.core.i18n import tr
from jacara.models import Document, ExtractedValue, Extraction
from jacara.templates import Template
from jacara.templates.extras import normalize

ENTITY_CONFLICT = "entity_conflict"
MAX_OTHERS = 50


def _current(extraction: Extraction) -> dict[str, str]:
    return {
        v.name: v.current_value
        for v in extraction.values
        if not v.deleted and v.current_value not in (None, "")
    }


def entity_conflicts(
    session: Session, extraction: Extraction, template: Template
) -> list[dict[str, Any]]:
    """Returns warnings in the same format as issues in current_checks."""
    if not template.entities:
        return []
    mine = _current(extraction)
    labels = template.labels()
    issues: list[dict[str, Any]] = []
    for entity in template.entities:
        key = mine.get(entity.key)
        if key is None:
            continue
        others = session.scalars(
            select(Extraction)
            .join(Document, Extraction.document_id == Document.id)
            .join(ExtractedValue, ExtractedValue.extraction_id == Extraction.id)
            .where(
                Extraction.team_id == extraction.team_id,
                Extraction.template == extraction.template,
                Extraction.run == Document.current_run,
                Extraction.document_id != extraction.document_id,
                ExtractedValue.name == entity.key,
            )
            .options(
                selectinload(Extraction.values).selectinload(ExtractedValue.revisions),
                selectinload(Extraction.document),
            )
            .order_by(Extraction.created_at.desc())
            .limit(MAX_OTHERS * 4)
        ).unique()
        matched = [
            (o, values)
            for o in others
            if normalize(str((values := _current(o)).get(entity.key, ""))) == normalize(key)
        ][:MAX_OTHERS]
        for name in entity.fields:
            if name == entity.key or name not in mine:
                continue
            different = {
                o.document.filename: values[name]
                for o, values in matched
                if name in values and normalize(values[name]) != normalize(mine[name])
            }
            if not different:
                continue
            shown = tr("；").join(
                tr("《{filename}》中为「{value}」", filename=f, value=v)
                for f, v in list(different.items())[:3]
            )
            if len(different) > 3:
                shown = tr("{documents} 等 {n} 份", documents=shown, n=len(different))
            issues.append(
                {
                    "rule": ENTITY_CONFLICT,
                    "title": tr("{label}：与其他单据不一致", label=entity.label),
                    "message": tr(
                        "同一{key_label}「{key}」，{documents}，此处为「{value}」",
                        key_label=labels.get(entity.key, entity.key),
                        key=key,
                        documents=shown,
                        value=mine[name],
                    ),
                    "fields": [name],
                    "severity": "warning",
                }
            )
    return issues
