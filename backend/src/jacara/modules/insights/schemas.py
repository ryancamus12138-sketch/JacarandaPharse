"""API schemas for statistics and data export."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class KindCount(BaseModel):
    kind: str
    name: str
    """Display name: decimal point, day/month swapped, …"""
    count: int


class FieldStat(BaseModel):
    """One field of one template (table columns are aggregated, for example "Line items ·
    Amount")."""

    template: str
    template_title: str
    field: str
    label: str
    total: int
    """Occurrences of this field in reviewed documents."""
    edited: int
    """Number of corrections (value differs from the model value)."""
    silent: int
    """Corrected but not flagged at extraction (wrong without a warning)."""
    rate: float
    kinds: list[KindCount]


class TemplateStat(BaseModel):
    template: str
    template_title: str
    documents: int
    fields: int
    edited: int
    silent: int
    rate: float


class Correction(BaseModel):
    """A recent manual correction. Values of sensitive fields are not shown."""

    document_id: uuid.UUID
    filename: str
    template_title: str
    label: str
    before: str | None
    after: str | None
    kind: str
    kind_name: str
    flagged: bool
    """Whether it was flagged at extraction."""
    reason: str
    at: datetime
    attribution_id: uuid.UUID | None = None
    stage: str | None = None
    """Stage at fault; a person's override takes precedence."""
    stage_name: str = ""
    stage_reason: str = ""
    """Reason for the attribution (one sentence)."""
    overridden: bool = False
    """Overridden by a person."""
    auto_stage: str | None = None
    """Automatically determined stage (differs from stage when overridden)."""
    evidence: list[dict[str, Any]] = []
    """Evidence (omitted for sensitive fields)."""


class StageCount(BaseModel):
    stage: str
    name: str
    count: int


class AttributionIn(BaseModel):
    """Override: stage null reverts to the automatic attribution."""

    stage: str | None
    note: str = Field(default="", max_length=500)


class AttributionOut(BaseModel):
    id: uuid.UUID
    stage: str
    auto_stage: str
    overridden: bool
    note: str


class CorrectionStats(BaseModel):
    documents: int
    """Number of reviewed documents."""
    fields: int
    edited: int
    confirmed: int
    silent: int
    rate: float
    """Manual correction rate = edited / fields."""
    review_seconds: float | None = None
    """Average review seconds per document (only documents with recorded review time; time with the
    review page open and in use)."""
    timed_documents: int = 0
    """Number of documents with recorded review time."""
    kinds: list[KindCount]
    stages: list[StageCount] = []
    """Stages at fault for corrected fields."""
    templates: list[TemplateStat]
    by_field: list[FieldStat]
    recent: list[Correction]
