"""Schemas of the OCR samples API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, Field

from jacara.core.i18n import Rendered, render_items

Category = Literal["rec_error", "correct", "missed", "unclear"]
Split = Literal["train", "val", "test_locked", "quarantined"]
ExportKind = Literal["train_val", "test"]
Status = Literal["ready", "failed", "withdrawn", "pending"]
"""pending: recovered from earlier corrections; becomes ready (exportable) after verification."""
SourceFormat = Literal["pdf", "image"]


class SampleSettings(BaseModel):
    collect: bool
    """Whether OCR correction sample collection is enabled."""
    can_change: bool = False
    """Whether the current user may change the switch (administrators)."""


class SampleSettingsIn(BaseModel):
    collect: bool


class SampleOut(BaseModel):
    id: uuid.UUID
    created_at: datetime
    document_id: uuid.UUID
    filename: str
    page: int
    kind: str
    transcription: str
    ocr_text: str | None
    category: Category
    error_tags: list[str]
    template: str
    field: str
    field_label: str
    source_format: SourceFormat
    ocr_engine: str
    width: int | None
    height: int | None
    status: Status
    error: Rendered
    origin: str = "review"
    """review (collected during review), backfill (recovered from earlier corrections), confirmed
    (correctly read lines collected automatically)."""
    reviewed_value: str | None = None
    """Backfilled samples: the value the reviewer entered at the time (a reference during
    verification; not necessarily the transcription)."""


class VerifyIn(BaseModel):
    """Approve several pending samples at once ("Approve page" on the samples page)."""

    ids: list[uuid.UUID] = Field(min_length=1, max_length=200)


class VerifyOut(BaseModel):
    verified: int


class BackfillOut(BaseModel):
    """Result of backfilling samples from earlier corrections."""

    created: int
    skipped: list[Excluded]


class SampleList(BaseModel):
    items: list[SampleOut]
    total: int
    """Total matching the filters (before pagination)."""
    categories: dict[str, int]
    """Count per category among non-withdrawn samples (unaffected by filters)."""
    statuses: dict[str, int]
    """Count per status (unaffected by filters)."""


class SampleIn(BaseModel):
    """Edit one sample on the samples page: transcription, category, withdraw (withdrawn) or restore
    (ready). Omit unchanged fields. Changing a pending sample to ready approves it."""

    transcription: str | None = Field(default=None, max_length=500)
    category: Category | None = None
    status: Literal["ready", "withdrawn"] | None = None


class Excluded(BaseModel):
    reason: str
    count: int


class SplitCount(BaseModel):
    groups: int
    """Groups (independent business documents)."""
    rows: int
    """Lines (samples)."""


class Evidence(BaseModel):
    """Whether the validation set is large enough to support conclusions."""

    level: Literal["process", "thin", "trend", "none"]
    """process: only checks the workflow; thin: small; trend: shows overall trends; none: no
    validation set this time."""
    message: str


class Distribution(BaseModel):
    """Lines in training and validation by template, category and origin (distribution only; groups
    are never moved to balance proportions)."""

    by: Literal["template", "category", "source_format"]
    key: str
    train: int = 0
    val: int = 0
    test: int = 0


class ExportPreview(BaseModel):
    kind: ExportKind = "train_val"
    included: int
    """Number of entries to export."""
    categories: dict[str, int]
    """Count per category among exported entries."""
    excluded: list[Excluded]
    """Entries not exported, by reason."""
    splits: dict[str, SplitCount] = {}
    """Groups and lines in train and val (or test)."""
    split_version: int = 1
    evidence: Evidence | None = None
    val_category_groups: dict[str, int] = {}
    """Number of groups per category in the validation set (200 lines from 3 invoices are still
    evidence from only 3 groups)."""
    distributions: list[Distribution] = []
    paddle_minimum: int = 5000
    """Recommended minimum number of lines for fine-tuning a PaddleOCR recognition model."""


class GroupMemberOut(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    filename: str
    first_page: int
    source_format: str
    samples: int


class GroupOut(BaseModel):
    group_id: uuid.UUID
    code: str
    """Group id in export manifests (prefixed g_, free of business data)."""
    split: Split
    reason: Rendered
    members: list[GroupMemberOut]
    samples: int
    exported_as: list[str]
    """Splits in which it has been exported."""


class GroupList(BaseModel):
    items: list[GroupOut]
    split_version: int
    counts: dict[str, int]
    """Groups per split."""


class SplitIn(BaseModel):
    split: Split


class UnitRef(BaseModel):
    document_id: uuid.UUID
    page: int = Field(ge=1)
    """First page of this document in the file."""


class UnitOut(BaseModel):
    document_id: uuid.UUID
    filename: str
    page: int
    last_page: int
    template: str
    values: dict[str, str]
    """Values used for pairing: document number, date, amount, currency, issuer."""
    group_id: uuid.UUID | None = None
    samples: int = 0


class PairOut(BaseModel):
    a: UnitOut
    b: UnitOut


class PairIn(BaseModel):
    a: UnitRef
    b: UnitRef
    decision: Literal["same", "different"]


class PairResult(BaseModel):
    group_id: uuid.UUID | None
    split: Split | None
    quarantined: bool
    affected_exports: int
    message: str


def _render_affected(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return render_items(items, "reason")


class ExportRecordOut(BaseModel):
    id: uuid.UUID
    created_at: datetime
    kind: Literal["train_val", "test", "det", "det_test"]
    """det, det_test: detection sample exports."""
    split_version: int
    counts: dict[str, Any]
    filters: dict[str, Any]
    affected: Annotated[list[dict[str, Any]], AfterValidator(_render_affected)]


# ---- Detection samples ----

DetStatus = Literal["none", "draft", "confirmed", "excluded"]
"""none: never saved (pre-annotation shown when opened); draft: saved, not confirmed; confirmed:
exported; excluded: page not used."""


class DetBox(BaseModel):
    points: list[list[float]] = Field(min_length=4, max_length=4)
    """Four corners [[x, y] × 4] in page image pixels, clockwise from top left."""
    text: str = Field(default="", max_length=500)
    ignore: bool = False
    """Illegible: exported as ### and excluded from training."""
    origin: Literal["ocr", "sample", "missed", "manual"] = "manual"
    """ocr: detected by OCR; sample: transcription corrected during review; missed: region selected
    during review; manual: drawn here."""


class DetPageItem(BaseModel):
    document_id: uuid.UUID
    filename: str
    page: int
    status: DetStatus
    boxes: int
    revision: int
    confirmed_at: datetime | None


class DetPageList(BaseModel):
    items: list[DetPageItem]
    total: int
    counts: dict[str, int]
    """Pages per status; sensitive is the number of pages excluded because of sensitive fields."""


class DetPageOut(BaseModel):
    document_id: uuid.UUID
    filename: str
    page: int
    width: int
    height: int
    status: DetStatus
    revision: int
    boxes: list[DetBox]
    ocr_engine: str
    confirmed_at: datetime | None


class DetPageIn(BaseModel):
    revision: int = Field(ge=0)
    """Revision seen when the page was opened (0 if never saved). Saving is rejected if someone
    changed it since."""
    boxes: list[DetBox] = Field(max_length=2000)


class DetExportPreview(BaseModel):
    kind: ExportKind = "train_val"
    included: int
    boxes: int
    splits: dict[str, SplitCount]
    excluded: dict[str, int]
    split_version: int
