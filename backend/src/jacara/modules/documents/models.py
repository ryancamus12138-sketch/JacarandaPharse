"""Document and extraction tables.

documents     An uploaded file (image or PDF)
extractions   One extraction of one page of a document: model, prompt, rule check results, raw
model answer

Reprocessing: each processing pass is a run. Earlier runs are kept for inspection;
documents.current_run points to the run in use, and Document.extractions contains only that run."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, Text, and_
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, foreign, mapped_column, relationship

from jacara.core.db import Base, OwnedMixin, TimestampMixin, new_id

if TYPE_CHECKING:
    from jacara.modules.fields.models import ExtractedValue
    from jacara.modules.templates.models import TemplateVersion


class Document(Base, OwnedMixin, TimestampMixin):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    filename: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    """Content fingerprint: uploading the same file twice yields the same fingerprint, which detects
    duplicates."""
    size_bytes: Mapped[int]
    page_count: Mapped[int]
    storage_key: Mapped[str | None] = mapped_column(String(512))
    """Location of the original file in object storage. Present only for files uploaded through the
    API."""
    content_type: Mapped[str | None] = mapped_column(String(100))
    """Media type, for example image/jpeg, application/pdf."""
    status: Mapped[str] = mapped_column(String(16), default="uploaded", server_default="uploaded")
    """Processing status: uploaded (queued) → processing → done / failed."""
    error: Mapped[str | None] = mapped_column(Text)
    """Human-readable cause of a processing failure. Empty on success or before processing."""
    stage: Mapped[str | None] = mapped_column(Text)
    """Human-readable current step, for example "Recognizing text" or "Extracting page 2 / 3". Empty
    when not processing."""
    progress: Mapped[int | None]
    """Processing progress 0–100. Empty when not processing."""
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_seconds: Mapped[int] = mapped_column(default=0, server_default="0")
    """Seconds spent reviewing this document (only while the review page is open and in use, summed
    across sessions)."""
    timings: Mapped[dict[str, Any] | None] = mapped_column(
        JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql")
    )
    """Step durations of the latest run: OCR read_ms, waiting for the model wait_ms, model
    extract_ms, and split (whether the two-stage pipeline was used). While processing it also
    holds the hand-off attempt."""
    """Time processing finished (success or failure). Used for "processed today" on the
    dashboard."""
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """Time "Complete review" was clicked."""
    skipped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """Time "Skip for now" was clicked during review. Skipped documents are excluded from "Next";
    cleared when unskipped or reviewed."""
    skip_reason: Mapped[str | None] = mapped_column(Text)
    """Reason for skipping (optional)."""
    skipped_by: Mapped[uuid.UUID | None]
    """User who skipped it."""

    template_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("template_versions.id")
    )
    """Template version for the next run. Empty: the built-in receipt template (documents uploaded
    before templates were versioned)."""
    discover: Mapped[bool] = mapped_column(default=False, server_default="false")
    """Next run uses no template and extracts all key-value pairs on the document (the first step of
    creating a template from a sample)."""
    auto: Mapped[bool] = mapped_column(default=False, server_default="false")
    """Next run selects the template automatically: each document picks the best match from my
    templates."""
    vision: Mapped[str | None] = mapped_column(String(8))
    """Whether the next run shows the page image to the model: always or off; empty follows
    JACARA_VISION."""
    pdf_read: Mapped[str | None] = mapped_column(String(8))
    """How the next run reads PDFs: image renders pages for OCR, text reads only the text layer;
    empty follows JACARA_PDF_READ."""
    folder: Mapped[str | None] = mapped_column(String(255), index=True)
    """Source folder in a batch import (relative path, such as "2026-10 trip/hotel"). Lists group
    and filter by it."""
    split: Mapped[str] = mapped_column(String(200), default="auto", server_default="auto")
    """How many documents a file contains: auto detects, one treats the whole file as one document,
    pages makes each page a document, or explicit page ranges ("1-2, 3, 4-6"). Applied on the
    next run."""
    current_run: Mapped[int] = mapped_column(default=1, server_default="1")
    """Run whose results are currently used."""
    run_reason: Mapped[str | None] = mapped_column(Text)
    """Reason for the next run (entered when reprocessing); recorded on that run's extractions once
    processed."""
    claim: Mapped[str | None] = mapped_column(String(32))
    """Token of the worker run that holds this document, set when a worker claims it and cleared
    when the user stops or restarts processing. A worker writes progress and results only while
    the token is still its own (a fencing token), so a stopped run can never overwrite a newer
    one."""

    extractions: Mapped[list[Extraction]] = relationship(
        primaryjoin=lambda: and_(
            Document.id == foreign(Extraction.document_id),
            Extraction.run == Document.current_run,
        ),
        order_by="Extraction.page",
        viewonly=True,
    )
    """Extraction results of the current run, one per document."""
    all_extractions: Mapped[list[Extraction]] = relationship(
        back_populates="document", order_by="[Extraction.run, Extraction.page]"
    )
    """Extraction results of all runs (for history)."""


class Extraction(Base, OwnedMixin, TimestampMixin):
    __tablename__ = "extractions"
    __table_args__ = (
        # Created by migration c7d2a9e4f1b8 but never declared on the model; without it `alembic
        # check` reports an extra index.
        Index("ix_extractions_document_run", "document_id", "run"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"), index=True)
    page: Mapped[int]
    """First page of this document, 1-based."""
    last_page: Mapped[int | None]
    """Last page of this document: set only when a document spans several pages; empty for
    single-page documents. A file may contain several documents, each with its own extraction."""

    # Provenance
    source: Mapped[str] = mapped_column(String(32))
    """Source of the page text: ocr or pdf-text."""
    ocr_engine: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(128))
    backend: Mapped[str] = mapped_column(String(32))
    prompt_version: Mapped[str] = mapped_column(String(32))
    template: Mapped[str] = mapped_column(String(64))
    """Template name, for example receipt (templates/receipt.toml)."""
    template_version: Mapped[int] = mapped_column(default=1, server_default="1")
    """Template version number. Documents processed before templates were versioned are recorded as
    1."""
    duration_ms: Mapped[int]
    run: Mapped[int] = mapped_column(default=1, server_default="1")
    """Run number."""
    run_reason: Mapped[str | None] = mapped_column(Text)
    """Reason for reprocessing (empty for the first run)."""
    template_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("template_versions.id")
    )
    """Version of my template used. Empty for built-in templates and key-value discovery, which are
    looked up by template name."""
    classified: Mapped[dict[str, Any] | None] = mapped_column(
        JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql")
    )
    """How the template was selected automatically: {"picked", "reason", "by", "candidates"}. Empty
    when the template was chosen at upload."""
    vision: Mapped[str | None] = mapped_column(Text)
    """Why the model was shown the page image for this document; empty if it was not."""

    raw_output: Mapped[str] = mapped_column(Text)
    """Raw model answer, stored verbatim."""
    checks: Mapped[dict[str, Any]]
    """Rule check results: passed, skipped and failed rules with messages."""
    ocr: Mapped[dict[str, Any] | None] = mapped_column(
        # none_as_null: a missing OCR result is stored as SQL NULL (not JSON null), so that `jacara
        # backfill-ocr` can find rows with "ocr IS NULL".
        JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql"),
        deferred=True,
    )
    """OCR result of this page: {"lines": [{"text", "box": [x0, y0, x1, y1], "polygon",
    "confidence"}]}. Used for click-to-fill during review. Loaded from the database only when
    needed (deferred). Absent for documents processed before 2026-11."""

    structure: Mapped[dict[str, Any] | None] = mapped_column(
        JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql"),
        deferred=True,
    )
    """Structure of this page: text blocks, lines, layout regions, tables, label → value pairs;
    format in structure/graph.py. Shown in the layout view of the review page and reused by trial
    runs. Absent for documents processed before 2026-11-14."""

    model_input: Mapped[dict[str, Any] | None] = mapped_column(
        JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql"),
        deferred=True,
    )
    """Model input of this run: {"blocks": [block indices], "total_blocks", "chars", "sha256",
    "text" (document text), "images", "lessons"}. Absent for documents processed before this was
    recorded."""
    ocr_snapshot: Mapped[str | None] = mapped_column(String(512))
    """Location in object storage of the complete OCR output of this run. Documents of the same run
    share one file. Used by replay (`jacara eval replay`) instead of re-running OCR."""

    document: Mapped[Document] = relationship(back_populates="all_extractions")
    template_def: Mapped[TemplateVersion | None] = relationship(lazy="selectin")
    """Template version used (the row referenced by template_version_id)."""
    values: Mapped[list[ExtractedValue]] = relationship(
        back_populates="extraction",
        order_by="[ExtractedValue.sort_key, ExtractedValue.position]",
    )
