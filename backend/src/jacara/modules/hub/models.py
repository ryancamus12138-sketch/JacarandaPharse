"""Dataset library and OCR evaluation tables.

datasets         Datasets: a zip archive (in object storage) plus name, version, kind, source and
per-split counts. Scoped by team.
ocr_eval_runs    One OCR model evaluation: dataset, split, the two models, results and progress.

The model library is not in the database: model files and model cards live in models/hub/ (local
to each machine, like models/selected.json)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from jacara.core.db import Base, JsonColumn, OwnedMixin, TimestampMixin, new_id


class Dataset(Base, OwnedMixin, TimestampMixin):
    __tablename__ = "datasets"
    __table_args__ = (UniqueConstraint("team_id", "name", "version"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(64))
    version: Mapped[int]
    """Storing the same name again in a team increments the version (MLflow registered model
    versioning)."""
    kind: Mapped[str] = mapped_column(String(16))
    """ocr_rec (recognition: line crop + text) or ocr_det (detection: full page + text boxes)."""
    source: Mapped[str] = mapped_column(String(16))
    """samples (OCR sample export), det_pages (detection samples), upload (uploaded)."""
    description: Mapped[str] = mapped_column(Text, default="")
    """Dataset card body: origin, annotation method, caveats."""
    storage_key: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int]
    counts: Mapped[dict[str, Any]] = mapped_column(JsonColumn)
    """Entries per split: {"train": 120, "val": 30, "test": 0, "all": 0}; all is unsplit (PPOCRLabel
    export)."""
    groups: Mapped[dict[str, Any]] = mapped_column(JsonColumn, default=dict)
    """Business document groups per split (only when document_group is present)."""
    files: Mapped[list[Any]] = mapped_column(JsonColumn, default=list)
    """Label files read from the archive and their splits: [{"file": "rec_gt_val.txt", "split":
    "val"}]."""
    problems: Mapped[dict[str, Any]] = mapped_column(JsonColumn, default=dict)
    """Lines skipped on import and why: {"unreadable image": 2}."""
    export_id: Mapped[uuid.UUID | None]
    """For datasets exported from OCR samples: the corresponding export record
    (ocr_sample_exports)."""


class OcrEvalRun(Base, OwnedMixin, TimestampMixin):
    __tablename__ = "ocr_eval_runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    dataset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("datasets.id"), index=True)
    split: Mapped[str] = mapped_column(String(8))
    """Split to evaluate: val, test, train, all."""
    task: Mapped[str] = mapped_column(String(8))
    """rec (recognition) or det (detection), following the dataset kind."""
    arms: Mapped[list[Any]] = mapped_column(JsonColumn)
    """The two models: [{"name": "A", "label": "In use", "det": path or null, "rec": …, "dict": …,
    "sha256": {…}}]. "In use" is resolved to concrete files at creation, so the compared models
    remain known after the selection changes."""
    status: Mapped[str] = mapped_column(String(12), default="queued")
    """queued, running, done, failed."""
    progress: Mapped[str] = mapped_column(String(64), default="")
    summary: Mapped[dict[str, Any] | None] = mapped_column(JsonColumn)
    lines_key: Mapped[str | None] = mapped_column(String(255))
    """Per-item results (object storage, jsonl). Contains document text, i.e. business data."""
    error: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
