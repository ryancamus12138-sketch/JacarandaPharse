"""Export extension point: write processing results to files others can use directly (Excel, CSV,
…).

Exporters receive finished results only; they never recognize, extract or judge, only decide the
output format."""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from jacara.core.i18n import tr
from jacara.plugins.aligners.base import AlignedField
from jacara.plugins.extractors.base import ExtractionResult
from jacara.plugins.ocr.base import OcrPage
from jacara.plugins.validators.base import ValidationResult


@dataclass(frozen=True)
class RangeSetting:
    """Export format of a date range field."""

    export: str = "normalized"
    """normalized (uniform format), original (source text), columns (separate start and end
    columns)."""
    separator: str = " ~ "
    day_first: bool | None = None
    """Whether numeric dates are day/month (from the template country)."""


@dataclass(frozen=True)
class Review:
    """Manual review result (the latest). Exports show this value instead of the model value."""

    action: str
    """edit or confirm (the original value is correct)."""
    value: str | None
    """Post-review value (as text, as stored in the database)."""
    reason: str
    reviewed_at: datetime


if TYPE_CHECKING:
    from jacara.structure.graph import PageStructure


@dataclass(frozen=True)
class DocumentReport:
    """All results of one document after the full pipeline; the only input of exporters."""

    source: str
    """Original file name, for example receipt.jpg."""

    extraction: ExtractionResult
    aligned: tuple[AlignedField, ...]
    checks: ValidationResult

    ocr_engine: str
    """OCR engine and version, for example rapidocr 3.10.0."""

    processed_at: datetime

    page: int = 1
    """First page of this document, 1-based."""

    last_page: int | None = None
    """Last page of this document. None for a single page."""

    pages: tuple[OcrPage, ...] = ()
    """Stacked documents: the OCR result of each page (stored page by page). Empty for a single
    page, which uses ocr_page."""

    page_starts: tuple[float, ...] = ()
    """Stacked documents: y offset of each page on the tall page, used to convert evidence back to
    each page's coordinates when storing."""

    text_source: str = "ocr"
    """Source of the page text: ocr or pdf-text (PDF text layer)."""

    template: str = "receipt"
    """Template name (templates/*.toml)."""

    template_version: int = 1
    """Template version, so older results remain attributable to the version used after the template
    changes."""

    reviews: Mapping[str, Review] = field(default_factory=dict)
    """Field name → manual review result. Empty right after processing (as in CLI exports)."""

    ocr_page: OcrPage | None = None
    """OCR result of this page. Present after a pipeline run and stored for click-to-fill on the
    review page; unused by exporters."""

    structure: PageStructure | None = None
    """Structure of this page (text block indices, layout regions, tables, label → value pairs).
    Stored in the database; unused by exporters."""

    labels: Mapping[str, str] = field(default_factory=dict)
    """Field ID → display name (label in the template), used as export column headers. Table columns
    as items.amount. Field IDs are used when absent (CLI, legacy data)."""

    manual: frozenset[str] = frozenset()
    """Manual-entry fields (set in the template not to be extracted by the model), stored with
    origin = manual."""
    computed: frozenset[str] = frozenset()
    """Fields computed by formula, stored with origin = computed."""

    hidden: frozenset[str] = frozenset()
    """Fields, tables (items) and table columns (items.tax) set not to export in the template; their
    columns are omitted."""

    ranges: Mapping[str, RangeSetting] = field(default_factory=dict)
    """Date range fields: field ID (table columns as items.period) → export settings."""

    template_version_id: uuid.UUID | None = None
    """Version of my template picked by automatic selection. None when the template was chosen at
    upload (the document's template is used)."""
    classified: Mapping[str, Any] | None = None
    """Automatic selection details: what was chosen, why, and the candidates. None without automatic
    selection."""

    @property
    def pages_label(self) -> str:
        """ "Page 3" or "Pages 3–4"."""
        last = self.last_page or self.page
        return (
            tr("第 {page} 页", page=self.page)
            if last == self.page
            else tr("第 {first}–{last} 页", first=self.page, last=last)
        )


class Exporter(Protocol):
    """Exporter interface: any class with name and suffix attributes and an export method is an
    exporter."""

    name: str
    suffix: str
    """File suffix, for example .xlsx."""

    def export(self, reports: Sequence[DocumentReport], path: Path) -> None:
        """Write the results of one or more documents to path."""
        ...
