"""Learning from corrections: include past manual corrections for the same template as examples in
the extraction prompt.

The model is not trained; the prompt gains a section on common past errors:
- Only reviewed documents, their current run, and fields whose model value was corrected;
- Sensitive fields (names, medical record numbers, phone numbers, …) are excluded: personal data
  never enters another document's prompt;
- The document itself is excluded (when reprocessing);
- Selection: most-corrected fields first, at most 2 recent examples per field, at most 8 in
  total;
- Examples only describe likely errors, and the model is told explicitly that this document's
  values must follow its own source text.
The prompt version is recorded as extract-v8+lessonsN (N examples), for traceability.

Curated examples (`JACARA_LEARN_MODE=curated`; recent above remains the default):
- Only corrections where the model was at fault (per error attribution): OCR misreads, OCR misses
  and correct model values dropped by checks cannot be fixed by telling the model and are not
  used;
- Corrections of the same field and error kind are merged into one with an occurrence count;
  frequent and recent first;
- Examples are stale and unused when the field's type or description changed in a newer template
  version;
- When an issuer (series) has at least 3 reviewed documents, its own examples are used first,
  then those of the template;
- Sentences follow fixed patterns per error kind instead of model-written summaries (small local
  models summarize rules unreliably).
Whether curated beats recent should be checked with `jacara eval replay` on the locked test set
before switching."""

from __future__ import annotations

import json
import uuid
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from jacara.modules.insights.kinds import KIND_NAMES, error_kind

LIMIT = 8
"""Maximum number of examples: more would consume context and invite copying."""
PER_FIELD = 2
SCAN = 200
"""Maximum number of recent reviewed documents considered."""

NOTE = """以前人工审核同一种单据时改过下面这些地方（是别的单据上的例子，只说明容易错在哪里；
这张单据的值一律按它自己的原文，不要照抄例子里的值）：
{lines}"""


@dataclass(frozen=True)
class Lesson:
    field: str
    label: str
    before: str | None
    after: str | None
    kind: str
    series: str | None = None
    """Series (issuer) this example comes from."""
    stage: str | None = None
    """Stage at fault (error attribution; a person's override takes precedence)."""
    sig: str | None = None
    """Definition of the field in the template at the time (type, description); stale once the
    template changes."""
    document: str | None = None
    count: int = 1
    """Number of identical corrections merged during curation."""


MODES = ("recent", "curated")
MIN_SERIES = 3
"""Reviewed documents a series needs before its own examples are used (following ABBYY: at least
3)."""
SKIP_STAGES = frozenset({"ocr_misread", "ocr_uncovered", "matching", "mixed"})
"""Errors not caused by the model: telling the model cannot fix them, so they are not examples."""


def field_sig(template: object, field: str) -> str | None:
    """Current definition of this field in the template (table columns as items.amount): type +
    description. None if not found."""
    if template is None:
        return None
    for f in template.fields:  # type: ignore[attr-defined]
        if f.name == field:
            return f"{f.type}|{f.description}"
    table, _, column = field.partition(".")
    for t in template.tables:  # type: ignore[attr-defined]
        if t.name == table:
            for c in t.columns:
                if c.name == column:
                    return f"{c.type}|{c.description}"
    return None


def curate(
    lessons: Iterable[Lesson],
    text: str = "",
    template: object = None,
    limit: int = LIMIT,
    per_field: int = PER_FIELD,
) -> list[Lesson]:
    """Curated examples. lessons are ordered newest first; text is this document's OCR text (for
    series detection); template is the template used now (to drop stale examples)."""
    from jacara.pipeline.series import series_in

    usable = [
        lesson
        for lesson in lessons
        if lesson.stage not in SKIP_STAGES
        and (
            template is None
            or lesson.sig is None
            or lesson.sig == field_sig(template, lesson.field)
        )
    ]
    # Series: only those with at least MIN_SERIES documents, and only when this document's text
    # mentions it.
    docs: dict[str, set[str | None]] = {}
    for lesson in usable:
        if lesson.series:
            docs.setdefault(lesson.series, set()).add(lesson.document)
    known = [key for key, found in docs.items() if len(found) >= MIN_SERIES]
    mine = series_in(text, known) if text and known else None
    # Merge: one entry per field and error kind, keeping the newest example and counting
    # occurrences.
    merged: dict[tuple[bool, str, str], Lesson] = {}
    order: list[tuple[bool, str, str]] = []
    for lesson in usable:
        key = (lesson.series == mine and mine is not None, lesson.field, lesson.kind)
        if key in merged:
            merged[key] = replace(merged[key], count=merged[key].count + 1)
        else:
            merged[key] = lesson
            order.append(key)
    ranked = sorted(order, key=lambda k: (not k[0], -merged[k].count, order.index(k)))
    kept: list[Lesson] = []
    used: Counter[str] = Counter()
    for key in ranked:
        lesson = merged[key]
        if used[lesson.field] >= per_field:
            continue
        kept.append(lesson)
        used[lesson.field] += 1
        if len(kept) >= limit:
            break
    return kept


def lesson_line(lesson: Lesson) -> str:
    times = f"（同样的错出现过 {lesson.count} 次）" if lesson.count > 1 else ""
    if lesson.kind == "missing":
        return (
            f"- {lesson.label}：模型没有给，原文其实有（例如「{lesson.after}」），要仔细找{times}"
        )
    if lesson.kind == "extra":
        return f"- {lesson.label}：模型给了「{lesson.before}」，原文其实没有，应该给 null{times}"
    reason = KIND_NAMES.get(lesson.kind, "")
    line = f"- {lesson.label}：模型写成「{lesson.before}」，人改成「{lesson.after}」（{reason}）"
    return line + times


def pick(lessons: Iterable[Lesson], limit: int = LIMIT, per_field: int = PER_FIELD) -> list[Lesson]:
    """Most-corrected fields first, at most per_field per field (lessons ordered newest first)."""
    lessons = list(lessons)
    counts = Counter(lesson.field for lesson in lessons)
    kept: list[Lesson] = []
    used: Counter[str] = Counter()
    for field, _ in counts.most_common():
        for lesson in lessons:
            if lesson.field == field and used[field] < per_field:
                kept.append(lesson)
                used[field] += 1
        if len(kept) >= limit:
            break
    return kept[:limit]


def note_for(lessons: Sequence[Lesson]) -> str:
    """Examples → a prompt section. Empty string when there are none."""
    if not lessons:
        return ""
    return NOTE.format(lines="\n".join(lesson_line(lesson) for lesson in lessons))


def _generic(name: str) -> str:
    from jacara.plugins.extractors.base import split_cell_name

    cell = split_cell_name(name)
    return f"{cell[0]}.{cell[2]}" if cell else name


def from_database(
    session: Session, team_id: uuid.UUID, template: str, exclude: uuid.UUID | None = None
) -> list[Lesson]:
    """Corrections in reviewed documents of this team and template (extractions.template), newest
    first."""
    from jacara.modules.documents.models import Document, Extraction
    from jacara.modules.fields.models import ExtractedValue
    from jacara.pipeline.current import label_for, labels_of, template_of

    query = (
        select(Document)
        .where(Document.team_id == team_id, Document.status == "reviewed")
        .options(
            selectinload(Document.extractions)
            .selectinload(Extraction.values)
            .selectinload(ExtractedValue.revisions)
        )
        .order_by(Document.reviewed_at.desc())
        .limit(SCAN)
    )
    if exclude is not None:
        query = query.where(Document.id != exclude)
    from jacara.modules.ocr_samples.groups import locked_units
    from jacara.pipeline.series import series_key

    # Documents in the locked test split are never sources of examples.
    locked = locked_units(session, team_id)
    edited: list[tuple[ExtractedValue, Extraction, object, str, str, str | None]] = []
    for document in session.scalars(query):
        for extraction in document.extractions:
            if extraction.template != template or (document.id, extraction.page) in locked:
                continue
            tpl = template_of(extraction)
            hidden = _sensitive(tpl)
            labels = labels_of(extraction)
            series = series_key(
                {v.name: v.current_value for v in extraction.values if not v.deleted}
            )
            for value in extraction.values:
                if value.origin != "model" or value.deleted or not value.revisions:
                    continue
                field = _generic(value.name)
                if field in hidden:
                    continue
                if (value.value or "").strip() == (value.current_value or "").strip():
                    continue
                label = (label_for(labels, value.name) or value.label or value.name).split(" · ")[0]
                edited.append((value, extraction, tpl, field, label, series))
    stages = _stages(session, [item[:3] for item in edited])
    found: list[tuple[object, Lesson]] = []
    for value, extraction, tpl, field, label, series in edited:
        before, after = value.value, value.current_value
        lesson = Lesson(
            field,
            label,
            before,
            after,
            error_kind(value.type, before, after),
            series=series,
            stage=stages.get(value.id),
            sig=field_sig(tpl, field),
            document=str(extraction.document_id),
        )
        found.append((value.revisions[-1].created_at, lesson))
    found.sort(key=lambda item: item[0], reverse=True)  # type: ignore[arg-type, return-value]
    return [lesson for _, lesson in found]


def _stages(session: Session, items: Sequence[tuple[Any, Any, Any]]) -> dict[uuid.UUID, str]:
    """Stage at fault for each corrected field: a person's override if any, otherwise attributed
    from evidence on the fly (not written to the database)."""
    from jacara.modules.insights.attribution import attribute
    from jacara.modules.insights.models import ErrorAttribution
    from jacara.modules.ocr_samples.models import OcrSample

    ids = [value.id for value, _, _ in items]
    if not ids:
        return {}
    overridden = {
        a.value_id: a.override_stage
        for a in session.scalars(
            select(ErrorAttribution).where(
                ErrorAttribution.value_id.in_(ids), ErrorAttribution.override_stage.is_not(None)
            )
        )
        if a.override_stage
    }
    samples: dict[uuid.UUID, list[Any]] = {}
    for s in session.scalars(
        select(OcrSample).where(OcrSample.value_id.in_(ids), OcrSample.status == "ready")
    ):
        if s.value_id is not None:
            samples.setdefault(s.value_id, []).append(s)
    stages: dict[uuid.UUID, str] = {}
    for value, extraction, tpl in items:
        if value.id in overridden:
            stages[value.id] = overridden[value.id]
            continue
        revision = value.revisions[-1].id
        mine = [s for s in samples.get(value.id, []) if s.revision_id == revision]
        stages[value.id] = attribute(value, extraction, mine, tpl).stage
    return stages


def _sensitive(template: object) -> set[str]:
    if template is None:
        return set()
    names = {f.name for f in template.fields if f.sensitive}  # type: ignore[attr-defined]
    for table in template.tables:  # type: ignore[attr-defined]
        names |= {f"{table.name}.{c.name}" for c in table.columns if c.sensitive}
    return names


def from_dataset(path: Path) -> dict[str, list[tuple[str, Lesson]]]:
    """Exported review data (train.jsonl) → template → [(document id, example)]. Used for paired
    evaluation; a document never receives its own examples (leave-one-out)."""
    by_template: dict[str, list[tuple[str, Lesson]]] = {}
    for line in path.read_text("utf-8").splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        for change in entry.get("corrections", []):
            field = _generic(change["field"])
            lesson = Lesson(field, field, change.get("before"), change.get("after"), change["kind"])
            by_template.setdefault(entry["template"], []).append((entry["document"], lesson))
    return by_template
