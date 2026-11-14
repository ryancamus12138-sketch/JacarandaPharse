"""Document persistence. All SQL queries live here; services never call session.execute directly.

All queries filter by team_id: a team never sees another team's data."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import ColumnElement, and_, exists, func, or_, select
from sqlalchemy.orm import Session, selectinload

from jacara.models import Document, ExtractedValue, Extraction, ValueRevision


@dataclass(frozen=True)
class DocumentFilter:
    """Document list filters. All empty means everything."""

    statuses: tuple[str, ...] = ()
    """Statuses (multiple allowed): uploaded, processing, done, failed, reviewed."""
    template: str | None = None
    """Template used for extraction (matches if any page used it)."""
    since: datetime | None = None
    """Uploaded at or after (inclusive)."""
    until: datetime | None = None
    """Uploaded before (exclusive)."""
    search: str = ""
    """File name contains this text (case-insensitive)."""
    pending_only: bool = False
    """Only documents with fields awaiting confirmation."""
    value_search: str = ""
    """Field values contain this text (any field of the current run, case-insensitive), for example
    part of an invoice number or merchant name."""
    field: str | None = None
    """Used with value_search: search only this field (field ID, for example invoice_no)."""
    skipped: bool | None = None
    """True: skipped only; False: exclude skipped; None: all."""
    folder: str | None = None
    """Batch-import folder: this folder and its subfolders."""


def _like(text: str) -> str:
    """Treat % and _ in user input as literal characters, not wildcards."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


ORDER = (Document.created_at.desc(), Document.id)
"""List order: upload time descending (newest first). Previous / next follow the same order."""


class DocumentRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, document: Document) -> Document:
        self.session.add(document)
        self.session.flush()
        return document

    def get(self, document_id: uuid.UUID, team_id: uuid.UUID) -> Document | None:
        """Load a document with its extractions, field values and review records in one query (no
        N+1 queries)."""
        query = (
            select(Document)
            .where(Document.id == document_id, Document.team_id == team_id)
            .options(
                selectinload(Document.extractions)
                .selectinload(Extraction.values)
                .selectinload(ExtractedValue.revisions)
            )
        )
        return self.session.scalars(query).one_or_none()

    def get_many(self, document_ids: list[uuid.UUID], team_id: uuid.UUID) -> list[Document]:
        """Load several documents (for export) with extractions, fields and review records
        preloaded. Order is not guaranteed."""
        query = (
            select(Document)
            .where(Document.id.in_(document_ids), Document.team_id == team_id)
            .options(
                selectinload(Document.extractions)
                .selectinload(Extraction.values)
                .selectinload(ExtractedValue.revisions)
            )
        )
        return list(self.session.scalars(query))

    def delete(self, document: Document) -> None:
        """Delete a document and all its results from the database:
                error attributions → review records → fields → detection samples → extractions of
        every run → comments → OCR samples → document."""
        from sqlalchemy import delete

        from jacara.models import Comment, DetPage, ErrorAttribution, OcrSample

        extractions = select(Extraction.id).where(Extraction.document_id == document.id)
        values = select(ExtractedValue.id).where(ExtractedValue.extraction_id.in_(extractions))
        self.session.execute(
            delete(ErrorAttribution).where(ErrorAttribution.document_id == document.id)
        )
        self.session.execute(delete(ValueRevision).where(ValueRevision.value_id.in_(values)))
        self.session.execute(
            delete(ExtractedValue).where(ExtractedValue.extraction_id.in_(extractions))
        )
        self.session.execute(delete(DetPage).where(DetPage.document_id == document.id))
        self.session.execute(delete(Extraction).where(Extraction.document_id == document.id))
        self.session.execute(delete(Comment).where(Comment.document_id == document.id))
        self.session.execute(delete(OcrSample).where(OcrSample.document_id == document.id))
        self.session.execute(delete(Document).where(Document.id == document.id))
        self.session.expunge(document)

    def folders(self, team_id: uuid.UUID) -> list[tuple[str, int]]:
        """Batch-import folders with their document counts, sorted by name."""
        rows = self.session.execute(
            select(Document.folder, func.count())
            .where(Document.team_id == team_id, Document.folder.is_not(None))
            .group_by(Document.folder)
            .order_by(Document.folder)
        ).all()
        return [(str(folder), int(count)) for folder, count in rows]

    def first_with_hash(self, team_id: uuid.UUID, sha256: str) -> Document | None:
        """The earliest uploaded document in the same team with identical content."""
        return self.session.scalars(
            select(Document)
            .where(Document.team_id == team_id, Document.sha256 == sha256)
            .order_by(Document.created_at)
            .limit(1)
        ).first()

    def same_files(
        self, team_id: uuid.UUID, documents: list[Document]
    ) -> dict[uuid.UUID, list[Document]]:
        """For each document: other documents in the same team with identical content (same sha256),
        earliest first."""
        hashes = {d.sha256 for d in documents}
        if not hashes:
            return {}
        found = self.session.scalars(
            select(Document)
            .where(Document.team_id == team_id, Document.sha256.in_(hashes))
            .order_by(Document.created_at)
        ).all()
        by_hash: dict[str, list[Document]] = {}
        for d in found:
            by_hash.setdefault(d.sha256, []).append(d)
        return {d.id: [o for o in by_hash.get(d.sha256, []) if o.id != d.id] for d in documents}

    def page(
        self, team_id: uuid.UUID, limit: int, offset: int, filters: DocumentFilter | None = None
    ) -> tuple[list[Document], int]:
        """One page of documents in upload time descending order, and the total number matching the
        filters."""
        where = [Document.team_id == team_id, *self._conditions(filters or DocumentFilter())]
        query = select(Document).where(*where).order_by(*ORDER).limit(limit).offset(offset)
        total = self.session.scalar(select(func.count()).select_from(Document).where(*where))
        return list(self.session.scalars(query)), total or 0

    def _conditions(self, f: DocumentFilter) -> list[ColumnElement[bool]]:
        conditions: list[ColumnElement[bool]] = []
        if f.statuses:
            conditions.append(Document.status.in_(f.statuses))
        if f.template:
            conditions.append(
                exists().where(
                    Extraction.document_id == Document.id,
                    Extraction.run == Document.current_run,
                    Extraction.template == f.template,
                )
            )
        if f.since is not None:
            conditions.append(Document.created_at >= f.since)
        if f.until is not None:
            conditions.append(Document.created_at < f.until)
        if f.search.strip():
            text = _like(f.search.strip())
            conditions.append(Document.filename.ilike(f"%{text}%", escape="\\"))
        if f.pending_only:
            conditions.append(exists().where(*self._pending_value()))
        if f.value_search.strip():
            text = _like(f.value_search.strip())
            found = [
                Extraction.document_id == Document.id,
                Extraction.run == Document.current_run,
                ExtractedValue.extraction_id == Extraction.id,
                # Present in the model value or in any reviewed value (any revision counts).
                or_(
                    ExtractedValue.value.ilike(f"%{text}%", escape="\\"),
                    exists().where(
                        ValueRevision.value_id == ExtractedValue.id,
                        ValueRevision.new_value.ilike(f"%{text}%", escape="\\"),
                    ),
                ),
            ]
            if f.field:
                found.append(ExtractedValue.name == f.field)
            conditions.append(exists().where(*found))
        if f.folder:
            name = f.folder.strip("/")
            conditions.append(
                or_(Document.folder == name, Document.folder.like(f"{_like(name)}/%", escape="\\"))
            )
        if f.skipped is True:
            conditions.append(Document.skipped_at.is_not(None))
        elif f.skipped is False:
            conditions.append(Document.skipped_at.is_(None))
        return conditions

    @staticmethod
    def _pending_value() -> list[ColumnElement[bool]]:
        """Condition for "this document has a field awaiting confirmation": the field is flagged and
        has no review record."""
        reviewed = exists().where(ValueRevision.value_id == ExtractedValue.id)
        return [
            Extraction.document_id == Document.id,
            Extraction.run == Document.current_run,
            ExtractedValue.extraction_id == Extraction.id,
            ExtractedValue.needs_review.is_(True),
            ~reviewed,
        ]

    def neighbors(
        self, document: Document, statuses: tuple[str, ...]
    ) -> tuple[uuid.UUID | None, uuid.UUID | None, int]:
        """Previous and next document in list order (restricted to these statuses), and the number
        of documents in these statuses excluding this one."""
        same = [
            Document.team_id == document.team_id,
            Document.status.in_(statuses),
            Document.id != document.id,
            Document.skipped_at.is_(None),  # Skipped documents are excluded from "Next".
        ]
        # The list is ordered by created_at descending, then id ascending: later in the list means
        # uploaded earlier, or at the same time with a larger id.
        later = or_(
            Document.created_at < document.created_at,
            and_(Document.created_at == document.created_at, Document.id > document.id),
        )
        following = self.session.scalar(
            select(Document.id).where(*same, later).order_by(*ORDER).limit(1)
        )
        preceding = self.session.scalar(
            select(Document.id)
            .where(*same, ~later)
            .order_by(Document.created_at.asc(), Document.id.desc())
            .limit(1)
        )
        count = self.session.scalar(select(func.count()).select_from(Document).where(*same))
        return preceding, following, count or 0

    def status_counts(self, team_id: uuid.UUID) -> dict[str, int]:
        query = (
            select(Document.status, func.count())
            .where(Document.team_id == team_id)
            .group_by(Document.status)
        )
        return {status: n for status, n in self.session.execute(query)}

    def skipped_count(self, team_id: uuid.UUID) -> int:
        """Number of skipped documents awaiting review."""
        query = select(func.count()).where(
            Document.team_id == team_id, Document.status == "done", Document.skipped_at.is_not(None)
        )
        return self.session.scalar(query.select_from(Document)) or 0

    def processed_since(self, team_id: uuid.UUID, since: datetime) -> int:
        """Number of documents processed successfully since since (failures excluded)."""
        query = select(func.count()).where(
            Document.team_id == team_id,
            Document.processed_at >= since,
            Document.status.in_(("done", "reviewed")),
        )
        return self.session.scalar(query) or 0

    def reviewed_since(self, team_id: uuid.UUID, since: datetime) -> int:
        """Number of documents reviewed since since."""
        query = select(func.count()).where(
            Document.team_id == team_id,
            Document.reviewed_at >= since,
            Document.status == "reviewed",
        )
        return self.session.scalar(query) or 0

    def in_progress(self, team_id: uuid.UUID, limit: int) -> list[Document]:
        """Queued and processing documents: processing first, then by upload time (first uploaded,
        first processed)."""
        query = (
            select(Document)
            .where(Document.team_id == team_id, Document.status.in_(("uploaded", "processing")))
            .order_by((Document.status == "processing").desc(), Document.created_at, Document.id)
            .limit(limit)
        )
        return list(self.session.scalars(query))

    def failed(self, team_id: uuid.UUID, limit: int) -> list[Document]:
        query = (
            select(Document)
            .where(Document.team_id == team_id, Document.status == "failed")
            .order_by(*ORDER)
            .limit(limit)
        )
        return list(self.session.scalars(query))

    def most_pending(self, team_id: uuid.UUID, limit: int) -> list[tuple[Document, int]]:
        """Documents awaiting review with the most fields awaiting confirmation (ties: earliest
        upload first)."""
        reviewed = exists().where(ValueRevision.value_id == ExtractedValue.id)
        pending = func.count(ExtractedValue.id)
        query = (
            select(Document, pending)
            .join(Extraction, Extraction.document_id == Document.id)
            .join(ExtractedValue, ExtractedValue.extraction_id == Extraction.id)
            .where(
                Document.team_id == team_id,
                Document.status == "done",
                Document.skipped_at.is_(None),  # Skipped documents are not included in reminders.
                Extraction.run == Document.current_run,
                ExtractedValue.needs_review,
                ~reviewed,
            )
            .group_by(Document.id)
            .order_by(pending.desc(), Document.created_at, Document.id)
            .limit(limit)
        )
        return [(d, n) for d, n in self.session.execute(query)]

    def pending_fields(self, team_id: uuid.UUID) -> int:
        """Total number of fields awaiting confirmation in documents awaiting review."""
        reviewed = exists().where(ValueRevision.value_id == ExtractedValue.id)
        query = (
            select(func.count(ExtractedValue.id))
            .join(Extraction, ExtractedValue.extraction_id == Extraction.id)
            .join(Document, Extraction.document_id == Document.id)
            .where(
                Document.team_id == team_id,
                Document.status == "done",
                Extraction.run == Document.current_run,
                ExtractedValue.needs_review,
                ~reviewed,
            )
        )
        return self.session.scalar(query) or 0

    def average_page_ms(self, team_id: uuid.UUID, since: datetime) -> int | None:
        """Average time per page of recent processing (extraction time of one page, excluding queue
        time)."""
        query = select(func.avg(Extraction.duration_ms)).where(
            Extraction.team_id == team_id, Extraction.created_at >= since
        )
        value = self.session.scalar(query)
        return None if value is None else int(value)

    def stage_seconds(self, team_id: uuid.UUID, since: datetime) -> dict[str, float] | None:
        """Average seconds per step for recently processed documents: OCR, waiting for the model,
        model."""
        rows = self.session.scalars(
            select(Document.timings).where(
                Document.team_id == team_id,
                Document.processed_at >= since,
                Document.status.in_(("done", "reviewed")),
                Document.timings.is_not(None),
            )
        ).all()
        found: dict[str, list[float]] = {"read": [], "wait": [], "extract": []}
        for timings in rows:
            for step in found:
                value = (timings or {}).get(f"{step}_ms")
                if isinstance(value, int | float):
                    found[step].append(value / 1000)
        if not found["extract"]:
            return None
        return {
            step: round(sum(values) / len(values), 1) for step, values in found.items() if values
        }

    def review_counts(self, document_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
        """Number of fields awaiting confirmation per document: flagged and without any review
        record."""
        if not document_ids:
            return {}
        reviewed = exists().where(ValueRevision.value_id == ExtractedValue.id)
        query = (
            select(Extraction.document_id, func.count(ExtractedValue.id))
            .join(ExtractedValue, ExtractedValue.extraction_id == Extraction.id)
            .join(Document, Extraction.document_id == Document.id)
            .where(
                Extraction.document_id.in_(document_ids),
                Extraction.run == Document.current_run,
                ExtractedValue.needs_review,
                ~reviewed,
            )
            .group_by(Extraction.document_id)
        )
        return {doc_id: count for doc_id, count in self.session.execute(query)}
