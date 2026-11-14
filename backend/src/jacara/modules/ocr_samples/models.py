"""OCR sample and team settings tables.

ocr_samples          One sample: a line crop (in object storage) + the text exactly as on the
image + what OCR read at the time.
team_settings        Per-team settings: the "collect OCR correction samples" switch, split seed
and version.
doc_group_members    Group of each business document (file + first page of the document).
split_assignments    Split of each group in a split version: train, validation, locked test or
quarantined.
doc_pair_decisions   Confirmed decisions on whether two documents are the same (PDF and photo
pairing).
ocr_sample_exports   Samples, revisions and splits of each export; duplicates found later are
recorded here.
det_pages            Detection samples: all text boxes on a full-page image, adjusted and
confirmed by a person and exported for detection models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from jacara.core.db import Base, JsonColumn, OwnedMixin, TimestampMixin, new_id


class TeamSetting(Base, TimestampMixin):
    __tablename__ = "team_settings"

    team_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    """Setting name, for example collect_ocr_samples."""
    value: Mapped[Any] = mapped_column(JsonColumn)


class OcrSample(Base, OwnedMixin, TimestampMixin):
    __tablename__ = "ocr_samples"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"), index=True)
    value_id: Mapped[uuid.UUID | None]
    """Field review the sample came from."""
    revision_id: Mapped[uuid.UUID | None]
    """Revision (collected when the review was saved)."""
    page: Mapped[int]
    """Page of the file (1-based)."""
    unit_page: Mapped[int] = mapped_column(default=1)
    """First page of this document in the file: together with document_id identifies the business
    document."""
    revision: Mapped[int] = mapped_column(default=1)
    """Number of transcription revisions (from 1), recorded in export manifests to identify the
    label version used for training."""
    kind: Mapped[str] = mapped_column(String(8))
    """line: a clicked OCR block, cropped by its four corners; box: a selected region, cropped by
    the box."""
    polygon: Mapped[list[Any]] = mapped_column(JsonColumn)
    """Four crop corners [[x, y] × 4] (pixels of the page image; PDFs are rendered at 216 DPI). For
    region selections, the corners of the box."""
    transcription: Mapped[str] = mapped_column(Text)
    """Transcription: the text exactly as on the image; the training label."""
    ocr_text: Mapped[str | None] = mapped_column(Text)
    """What OCR read at the time. Empty (None) when the box contained no OCR text block."""
    category: Mapped[str] = mapped_column(String(16))
    """rec_error (misread), correct (correctly read line), missed (not detected), unclear (cause
    undetermined)."""
    error_tags: Mapped[list[Any]] = mapped_column(JsonColumn, default=list)
    template: Mapped[str] = mapped_column(String(200), default="")
    """Template name (used in exports), for example "Receipt"."""
    field: Mapped[str] = mapped_column(String(200), default="")
    """Field ID (table cells without the row number: items.amount)."""
    field_label: Mapped[str] = mapped_column(String(200), default="")
    source_format: Mapped[str] = mapped_column(String(8))
    """pdf or image."""
    ocr_engine: Mapped[str] = mapped_column(String(128), default="")
    """Engine and model versions of the first OCR pass."""
    crop_key: Mapped[str | None] = mapped_column(String(255))
    """Object storage key of the crop. Empty if cropping failed."""
    crop_sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    width: Mapped[int | None]
    height: Mapped[int | None]
    origin: Mapped[str] = mapped_column(String(12), default="review", server_default="review")
    """Origin: review (collected during review), backfill (recovered from earlier corrections;
    requires verification)."""
    status: Mapped[str] = mapped_column(String(12), default="ready")
    """ready (exportable), failed (cropping failed; retryable), withdrawn (not exported)."""
    error: Mapped[str] = mapped_column(Text, default="")
    """Cause of the cropping failure."""


class DocGroupMember(Base, OwnedMixin, TimestampMixin):
    """Group of a business document. Samples of one group always land in the same split (training or
    validation)."""

    __tablename__ = "doc_group_members"
    __table_args__ = (UniqueConstraint("document_id", "first_page"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    group_id: Mapped[uuid.UUID] = mapped_column(index=True)
    """Group id: a random UUID without business data. Shared by a PDF and a photo once confirmed to
    be the same document."""
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"), index=True)
    first_page: Mapped[int]
    """First page of this document in the file."""


class SplitAssignment(Base, TimestampMixin):
    """Split of a group in a split version. Assigned by hash and stored when the group first enters
    a dataset, then only read from here."""

    __tablename__ = "split_assignments"

    team_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    version: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    split: Mapped[str] = mapped_column(String(16))
    """train, val, test_locked (locked test set), quarantined (not exported)."""
    reason: Mapped[str] = mapped_column(Text, default="")
    """Reason for this split: hash assignment, administrator change, quarantine after merging
    already-exported groups, …"""
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PairDecision(Base, OwnedMixin, TimestampMixin):
    """Whether two business documents are the same (confirmed by a person). same: merged; different:
    not the same and no longer proposed."""

    __tablename__ = "doc_pair_decisions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    a_document: Mapped[uuid.UUID] = mapped_column(index=True)
    a_page: Mapped[int]
    b_document: Mapped[uuid.UUID] = mapped_column(index=True)
    b_page: Mapped[int]
    decision: Mapped[str] = mapped_column(String(12))


class OcrSampleExport(Base, OwnedMixin, TimestampMixin):
    """One export: which samples, which revisions and which splits. Cross-split duplicates found
    later are recorded in affected."""

    __tablename__ = "ocr_sample_exports"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(12))
    """train_val (training and validation) or test (locked test set)."""
    split_version: Mapped[int]
    filters: Mapped[dict[str, Any]] = mapped_column(JsonColumn)
    counts: Mapped[dict[str, Any]] = mapped_column(JsonColumn)
    """Groups and lines per split."""
    items: Mapped[list[Any]] = mapped_column(JsonColumn)
    """[{sample, revision, group, split}]"""
    affected: Mapped[list[Any]] = mapped_column(JsonColumn, default=list)
    """Problems found after export: [{group, reason, at}], for example the group was later merged
    with another, so one copy may be in training and the other in validation."""


class DetPage(Base, OwnedMixin, TimestampMixin):
    """Detection sample: all text boxes on one page. Following the PPOCRLabel workflow:
    pre-annotated with OCR results, adjusted by a person and confirmed per page; only confirmed
    pages are exported. The image is not stored separately but re-rendered with page_png on
    export (the same image used for line crops)."""

    __tablename__ = "det_pages"
    __table_args__ = (UniqueConstraint("document_id", "page"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"), index=True)
    extraction_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("extractions.id"))
    """Extraction used for pre-annotation (OCR results of that run)."""
    page: Mapped[int]
    """Page of the file (1-based)."""
    unit_page: Mapped[int]
    """First page of this document in the file: together with document_id identifies the business
    document group."""
    width: Mapped[int]
    height: Mapped[int]
    boxes: Mapped[list[Any]] = mapped_column(JsonColumn)
    """[{"points": [[x, y] × 4], "text": "text on the image", "ignore": false, "origin": …}], where
    origin is ocr, sample, missed or manual. Coordinates are pixels of the page image (page_png).
    Ignored boxes are exported as ###."""
    status: Mapped[str] = mapped_column(String(12), default="draft")
    """draft (not confirmed), confirmed (exported), excluded (page not used)."""
    revision: Mapped[int] = mapped_column(default=1)
    """Incremented on every change. Saves must carry the revision seen; a mismatch (someone just
    changed it) is rejected."""
    ocr_engine: Mapped[str] = mapped_column(String(255), default="")
    """OCR engine and models used for pre-annotation."""
    confirmed_by: Mapped[uuid.UUID | None]
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
