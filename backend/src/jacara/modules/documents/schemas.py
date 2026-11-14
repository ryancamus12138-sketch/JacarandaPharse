"""Public schemas of the documents API (request and response bodies).

Kept separate from models.py: not every database column is exposed to the frontend (for example
storage_key, user_id), and not everything the frontend needs is a column (for example
review_count is computed)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from jacara.core.i18n import N_, Rendered, render_checks, render_classified
from jacara.modules.fields.schemas import ValueOut

__all__ = [
    "Dashboard",
    "DocumentDetail",
    "DocumentNeighbors",
    "DocumentOut",
    "DocumentPage",
    "ExtractionOut",
    "SentOut",
    "SentText",
    "ValueOut",
]
"""ValueOut moved to the fields module; re-exported here so existing imports keep working."""


class DeleteIn(BaseModel):
    """Delete several documents at once."""

    ids: list[uuid.UUID] = Field(min_length=1, max_length=1000)


class ReviewTimeIn(BaseModel):
    """Review time reported by the review page: active seconds in this interval (reported every 30
    seconds and once more when leaving the page)."""

    seconds: int = Field(ge=1, le=600)


class ReprocessManyIn(BaseModel):
    """Reprocess the selected documents with the same settings. Omitted settings keep each
    document's previous value."""

    ids: list[uuid.UUID] = Field(min_length=1, max_length=1000)
    template: str | None = None
    """Id of one of my templates, discover, or auto. Omitted: each document uses the latest version
    of its current template (auto-selected documents are re-selected)."""
    split: Literal["auto", "one", "pages"] | None = None
    """Number of documents per file. Omitted: same as last time (page ranges are not allowed for
    multiple documents)."""
    vision: Literal["always", "off", "default"] | None = "default"
    """Whether to show the page image to the model. default follows the system setting; omitted
    (null): same as last time."""
    pdf_read: Literal["image", "text", "default"] | None = "default"
    """How to read PDFs (PDF only). default follows the system setting; omitted (null): same as last
    time."""
    reason: Literal[
        "template_changed", "wrong_template", "bad_result", "model_changed", "batch", "other"
    ] = "batch"
    note: str = Field(default="", max_length=500)


class ReprocessManyOut(BaseModel):
    queued: int
    """Number of documents queued."""
    skipped: list[str] = []
    """Documents not processed (being processed, still queued, or missing the original file), one
    line each: file name + reason."""


class DeleteOut(BaseModel):
    deleted: int
    skipped: list[str] = []
    """Documents not deleted (being processed), one line each: file name + reason."""


class FolderOut(BaseModel):
    """A batch-import folder."""

    folder: str
    count: int


class SameFile(BaseModel):
    """Another document with identical content."""

    id: uuid.UUID
    filename: str
    created_at: datetime


class DocumentOut(BaseModel):
    """One item of the document list."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    content_type: str | None
    size_bytes: int
    page_count: int
    status: str
    """uploaded (queued) → processing → done / failed; after manual review of a done document,
    "Complete review" sets reviewed."""
    error: Rendered | None = None
    """Failure cause. Only when status is failed."""
    created_at: datetime
    review_count: int = 0
    """Number of fields still awaiting confirmation (edited or confirmed fields excluded)."""
    stage: Rendered | None = None
    """Current step, for example "Extracting page 2 / 3". Only while processing."""
    progress: int | None = None
    """Processing progress 0–100. Only while processing."""
    processed_at: datetime | None = None
    """Time processing finished (success or failure)."""
    reviewed_at: datetime | None = None
    """Time review was completed."""
    current_run: int = 1
    """Run whose results are currently used."""
    discover: bool = False
    """The next (or current) run uses no template and extracts all key-value pairs."""
    auto: bool = False
    """The next (or current) run selects the template automatically."""
    vision: str | None = None
    """Whether the model sees the page image: always, off; null follows the system setting."""
    pdf_read: str | None = None
    """How PDFs are read: image renders pages for OCR, text reads only the text layer; null follows
    the system setting."""
    skipped_at: datetime | None = None
    """Time "Skip for now" was clicked during review; null if not skipped."""
    skip_reason: str | None = None
    """Reason for skipping (optional)."""
    open_comments: int = 0
    """Number of unresolved comments."""
    folder: str | None = None
    """Source folder in a batch import."""
    split: str = "auto"
    """Number of documents per file: auto, one, pages, or page ranges."""
    same_file: list[SameFile] = []
    """The same file (identical content) was uploaded before: those documents."""
    duplicate_skipped: bool = False
    """"Skip duplicate files" was selected for the batch import; no new document was created and the
    earlier one is returned."""


class SentOut(BaseModel):
    """Summary of the model input of this run. The full text is too long for the details response;
    use the /sent endpoint."""

    blocks: int
    """Number of text blocks sent to the model."""
    total_blocks: int
    """Total number of text blocks on this page (or on the pages forming this document)."""
    chars: int
    """Number of characters of document text sent."""
    images: int
    """Number of page images attached."""
    lessons: int
    """Number of past correction examples included."""
    has_snapshot: bool
    """Whether the OCR snapshot of this run was stored (required for replay)."""


class SentText(BaseModel):
    """Document text and text block indices sent to the model."""

    blocks: list[int]
    text: str
    sha256: str


class ExtractionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    page: int
    """First page of this document."""
    last_page: int | None = None
    """Last page of this document; null when it occupies a single page."""
    source_notes: list[str] = []
    """Special conditions while reading text, for example "The PDF text layer is garbled; OCR used
    for this page"."""
    source: str
    model: str
    prompt_version: str
    template: str
    """Built-in template name, id of one of my templates, or discover (no template; all key-value
    pairs extracted)."""
    template_version: int
    template_title: str = ""
    """Template display name."""
    classified: Annotated[dict[str, Any] | None, AfterValidator(render_classified)] = None
    """How the template was selected automatically: picked, reason, by, candidates."""
    vision: Rendered | None = None
    """Why the model was shown the page image; null if it was not."""
    run: int = 1
    run_reason: Rendered | None = None
    labels: dict[str, str] = {}
    """Field ID → display name. Table columns are written as items.amount and table names as
    items."""
    languages: list[str] = []
    """Languages available for field labels; the first is the default. Empty when the template has a
    single language."""
    label_sets: dict[str, dict[str, str]] = {}
    """Labels in other languages: language code → {field ID → name}, same format as labels."""
    checks: Annotated[dict[str, Any], AfterValidator(render_checks)]
    """Rule results at extraction time; never change."""
    current_checks: dict[str, Any] | None = None
    """Rule results re-evaluated on the current post-review values (deleted rows excluded). Shown on
    the review page."""
    values: list[ValueOut]
    sent: SentOut | None = None
    """Model input of this run. null for documents processed before this was recorded."""


class RunOut(BaseModel):
    """A processed run."""

    run: int
    template: str
    template_title: str
    template_version: int
    reason: Rendered | None
    created_at: datetime
    current: bool


class DocumentDetail(DocumentOut):
    """Complete information of a document: the document itself plus the extraction result of each
    page."""

    extractions: list[ExtractionOut]
    runs: list[RunOut] = []
    """Every processed run (newest first). Contains one entry even when there is only one run."""
    viewing_run: int = 1
    """Run of extractions. When it is not current_run, the results are read-only and cannot be
    reviewed."""
    suggestions: int = 0
    """Number of fields whose review from the previous run can be carried over ("Accept previous
    confirmations")."""


REPROCESS_REASONS: dict[str, str] = {
    "template_changed": N_("模板已修改"),
    "wrong_template": N_("模板选择错误"),
    "bad_result": N_("结果不理想，重新运行"),
    "model_changed": N_("已更换模型或 OCR"),
    "batch": N_("批量重新处理"),
    "other": N_("其他"),
}
"""Reprocessing reasons (display names shown in audit log and history)."""


class ReprocessIn(BaseModel):
    """Reprocess: run a new pass with another template (or the latest version of the same template).
    Earlier results are kept for inspection."""

    template: str | None = None
    """Id of one of my templates, discover, or auto (automatic selection). Omitted: the latest
    version of the current template (auto-selected documents are re-selected)."""
    reason: Literal[
        "template_changed", "wrong_template", "bad_result", "model_changed", "batch", "other"
    ]
    note: str = Field(default="", max_length=500)
    """Additional explanation. Required when the reason is "other"."""
    split: str | None = Field(default=None, max_length=200)
    """Number of documents in the file: auto, one, pages or "1-2, 3, 4-6". Omitted: same as last
    time."""
    vision: Literal["always", "off", "default"] | None = None
    """Whether the model sees the page image: always, off, or default (system setting). Omitted:
    same as last time."""
    pdf_read: Literal["image", "text", "default"] | None = None
    """How to read PDFs: image renders pages for OCR (the text layer is used only for
    cross-checking), text reads only the text layer, default follows the system setting. Omitted:
    same as last time."""


class DocumentPage(BaseModel):
    """Paginated list. total is the overall count, used by the frontend for "N items" and
    pagination."""

    items: list[DocumentOut]
    total: int


class OcrLineOut(BaseModel):
    """One text block on the page image. index is its number on this page, used by "select from
    source text"."""

    index: int
    text: str
    box: list[float]
    """Bounding box [x0, y0, x1, y1] in pixels, in page image coordinates."""
    polygon: list[list[float]]
    confidence: float
    row: int | None = None
    """Visual line number (the same line numbers the model sees)."""


class RegionIn(BaseModel):
    """A region of the page: [x0, y0, x1, y1] in page image pixel coordinates."""

    box: list[float] = Field(min_length=4, max_length=4)


class LookIn(BaseModel):
    """Ask the model about a region: the box and, optionally, the target field."""

    box: list[float] = Field(min_length=4, max_length=4)
    label: str | None = Field(default=None, max_length=100)
    """Display name of the target field, for example "Change" or "Row 2 Amount"."""
    type: Literal["string", "number", "date"] | None = None


class LookOut(BaseModel):
    text: str
    """Text read by the model from the image."""
    value: str | None
    """Value of the target field (digits only for numbers, YYYY-MM-DD for dates); null when no field
    was given or the image does not contain it."""
    model: str


class RegionDescription(BaseModel):
    """Template building by region selection: the value inside the box, the nearby label, its
    position on the page and its likely type."""

    text: str
    confidence: float | None
    label: str | None
    """Nearest label to the left of the box (same line) or above it, without the colon; null if
    none."""
    side: str | None
    """Whether the value is to the right of or below the label."""
    type: Literal["string", "number", "date", "date_range"]
    position: str
    """Position on the page: top left, top center, bottom right, …"""
    name: str
    """Field ID guessed from the label (only English labels can be guessed; otherwise field_1 and so
    on, to be renamed)."""
    description: str
    """Position hint for the model, for example: top right of the document, right of "Invoice
    No"."""


class RegionOut(BaseModel):
    """Re-recognition result. An empty text means no text was recognized in the region."""

    text: str
    confidence: float | None
    """Lowest OCR confidence among the recognized text."""


class PageOcr(BaseModel):
    """OCR result of one page. available=false: OCR results were not stored when this page was
    processed (available after reprocessing)."""

    available: bool
    lines: list[OcrLineOut]


class DocumentNeighbors(BaseModel):
    """Previous / next on the review page: the adjacent documents awaiting review in list order;
    null if none."""

    previous: uuid.UUID | None
    next: uuid.UUID | None
    remaining: int
    """Number of other documents awaiting review."""


class DashboardCounts(BaseModel):
    queued: int
    """Queued (uploaded)."""
    processing: int
    to_review: int
    """Processed, awaiting review (done)."""
    failed: int
    reviewed: int
    total: int
    skipped: int = 0
    """Number of skipped documents awaiting review."""
    pending_fields: int
    """Total number of fields awaiting confirmation in documents awaiting review."""
    processed_today: int
    reviewed_today: int
    average_page_seconds: float | None
    """Average processing time per page over the last 7 days (seconds)."""
    stage_seconds: dict[str, float] | None = None
    """Average seconds per step per document over the last 7 days: read (OCR), wait (queue for the
    model), extract (model)."""


class Dashboard(BaseModel):
    """Dashboard: statistic cards, processing queue, items needing attention."""

    counts: DashboardCounts
    queue: list[DocumentOut]
    """Queued and processing documents (processing first, with progress)."""
    failed: list[DocumentOut]
    """Failed documents (most recent)."""
    to_review: list[DocumentOut]
    """Documents awaiting review with the most fields awaiting confirmation (review_count)."""


class SkipIn(BaseModel):
    """Skip this document for now and return to it later."""

    reason: str = Field(default="", max_length=500)
    """Reason for skipping (optional)."""


class StructureRegion(BaseModel):
    kind: str
    """Title, text, table, header, footer, …"""
    box: list[float]


class StructureCell(BaseModel):
    row: int
    col: int
    row_span: int = 1
    col_span: int = 1
    box: list[float]
    text: str


class StructureTable(BaseModel):
    box: list[float]
    rows: int
    cols: int
    by: str
    """model: detected by the table structure model; rule: derived from text positions."""
    cells: list[StructureCell]


class StructurePair(BaseModel):
    key: str
    key_box: list[float]
    value: str
    value_box: list[float]
    rule: str
    """Rule that found the pair: inline, colon, colon_below, header_above, two_column_table,
    label_left."""


class PageStructureOut(BaseModel):
    """Structure of one page, drawn over the page image in the layout view of the review page to
    show what the system detected."""

    available: bool
    """Documents processed before 2026-11-14 have no structure."""
    sources: dict[str, str] = {}
    regions: list[StructureRegion] = []
    tables: list[StructureTable] = []
    pairs: list[StructurePair] = []
