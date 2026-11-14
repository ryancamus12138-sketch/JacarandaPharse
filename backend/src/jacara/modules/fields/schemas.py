"""Public schemas of field values and revisions."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from jacara.core.i18n import Rendered, render_candidates


class SuggestionOut(BaseModel):
    """Carry over a review from the previous run: confirm confirms the current value; edit applies
    the previously edited value."""

    action: Literal["confirm", "edit"]
    value: str | None
    run: int
    """Source run."""


class RangeOut(BaseModel):
    """Split date range, computed in code from the current value."""

    start: str | None
    end: str | None
    days: int | None
    """Number of days, inclusive."""
    problem: str | None
    """Not splittable, day/month ambiguous, or end before start; null when there is no problem."""


class ValueOut(BaseModel):
    """A field. value is the model value; current_value is the current post-review value."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    type: str
    value: str | None
    """Value extracted by the model; never changes."""
    rejected_value: str | None = None
    """Value given by the model but not verifiable in the source and therefore not filled. A
    reviewer can accept it with one click."""
    current_value: str | None = None
    """Current value: result of the latest review, or value if never reviewed. This is what the UI
    shows."""
    status: str
    needs_review: bool
    """Whether the value was flagged for confirmation at extraction (never changes)."""
    reviewed: bool = False
    """Edited or confirmed by a person."""
    pending_review: bool = False
    """Awaiting confirmation: needs_review and not yet handled. This is what the UI highlights."""
    note: Rendered
    evidence: dict[str, Any] | None
    """Evidence found at extraction; never changes."""
    current_evidence: dict[str, Any] | None = None
    """Current evidence: the selected blocks if selected from source text, otherwise evidence. Drawn
    on the page image."""
    candidates: Annotated[dict[str, Any] | None, AfterValidator(render_candidates)] = None
    """Candidates: {"items": [...]}, differing values from different sources, offered for
    selection."""
    origin: str = "model"
    """model: extracted by the model; manual: entered manually; computed: calculated by a template
    formula."""
    deleted: bool = False
    """The row containing this cell was deleted (tables only)."""
    evidence_state: str = "empty"
    """Evidence: verified (matches the source), pending (doubtful), missing (not found), empty
    (absent from the source)."""
    source_value: str | None = None
    """Source value: the value parsed from the evidence text, present only when it differs from the
    current value ("Use source value"). Document details only."""
    check_state: str | None = None
    """Validation: ok, warn (violates a warning rule), fail (violates an error rule). Computed from
    the current value; document details only."""
    level: str | None = None
    """Color: red, yellow, grey, green (evidence × validation × reviewed). Document details only."""
    label: str | None = None
    """Display name: the label in the template; in key-value discovery the label on the document.
    Document details only."""
    names: dict[str, str] = {}
    """Names in other languages: language code → name. Languages not defined in the template are
    absent. Document details only."""
    suggestion: SuggestionOut | None = None
    """After reprocessing: results reviewed in the previous run that can be carried over. Document
    details only."""
    range: RangeOut | None = None
    """Start and end of date range fields. Document details only."""


class AdoptOut(BaseModel):
    confirmed: int
    edited: int


class RevisionIn(BaseModel):
    """A review. Edits carry a new value and a reason; confirmations need only the action, and the
    reason is optional."""

    action: Literal["edit", "confirm"]
    """edit: change to value; confirm: the current value is correct."""
    value: str | None = None
    """New value. Used only by edit; null means the field is indeed absent from the source. With
    source_lines and no value, the value is parsed from the selected text."""
    reason: str = Field(default="", max_length=500)
    """Reason for the change. Required for edits so that the history remains understandable
    (optional when selecting from source text)."""
    source_lines: list[int] | None = Field(default=None, max_length=50)
    """Select from source text: indices of the selected text blocks in this page's OCR result
    (0-based, see /pages/{n}/ocr). They become the new evidence and the value is parsed from
    their text."""
    source_box: list[float] | None = Field(default=None, min_length=4, max_length=4)
    """Region selection: the region [x0, y0, x1, y1] drawn on the page image (the value confirmed
    after re-recognition goes in value). It becomes the new evidence."""
    transcription: str | None = Field(default=None, max_length=500)
    """Transcription: the text exactly as it appears on the image. Only with a single source block
    (one entry in source_lines) or a region selection. When selecting from source text without
    value, the value is parsed from it. When the team has OCR sample collection enabled, an OCR
    sample is stored as well."""
    page: int | None = Field(default=None, ge=1)
    """Page of the file for source text or region selection (a document may span several pages).
    Defaults to the first page of the document."""


class ConfirmManyIn(BaseModel):
    """Confirm several fields at once (for example a whole line-item row, or all flagged fields)."""

    ids: list[uuid.UUID] = Field(min_length=1, max_length=1000)
    reason: str = Field(default="", max_length=500)


class ConfirmManyOut(BaseModel):
    confirmed: int
    """Number of fields confirmed. Deleted rows are skipped."""
    skipped: int


class RowIn(BaseModel):
    """Add a table row. after is the row to insert after (the row number, e.g. 3 in items.3.amount);
    0 inserts at the top; omitted appends at the end."""

    after: int | None = None
    reason: str = Field(default="", max_length=500)


class RowChange(BaseModel):
    """Delete or restore a row. Reason required."""

    reason: str = Field(default="", max_length=500)


class RevisionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    seq: int
    action: str
    new_value: str | None
    reason: Rendered
    evidence: dict[str, Any] | None = None
    """New evidence when selected from source text."""
    user_id: uuid.UUID
    """Actor. Always the same person in personal mode; identifies who changed what in team mode."""
    created_at: datetime


class ValueHistory(BaseModel):
    """Complete history of a field: model value plus every review."""

    value: ValueOut
    revisions: list[RevisionOut]
