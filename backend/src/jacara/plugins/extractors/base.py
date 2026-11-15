"""Field extraction extension point: defines what to extract and the shape of the result.

Extractors receive OCR results and fill values according to a field list. Evidence alignment and
rule validation depend only on these types, regardless of whether values come from an LLM, rules
or future methods."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol

from jacara.plugins.ocr.base import OcrPage

FieldType = Literal["string", "number", "date", "date_range"]
"""date_range: a date range whose value is copied verbatim from the source; start and end dates are
split out in code."""


class ExtractionError(RuntimeError):
    """The model answer is not valid JSON or lacks fields."""


@dataclass(frozen=True)
class FieldSpec:
    """A field to extract: name, type, description for the model."""

    name: str
    type: FieldType
    description: str


@dataclass(frozen=True)
class TableSpec:
    """A table to extract (for example line items): every row has the same columns; the number of
    rows varies."""

    name: str
    description: str
    columns: tuple[FieldSpec, ...]


_CELL = re.compile(r"^([a-z][a-z0-9_]*)\.(\d+)\.([a-z][a-z0-9_]*)$")


def cell_name(table: str, index: int, column: str) -> str:
    """Field name of a table cell: items.2.amount is the amount in row 2 of the line items. Rows
    start at 1.

        Every table cell is treated as a regular field, so evidence, rule validation, manual
    review and history need no separate implementation."""
    return f"{table}.{index}.{column}"


def split_cell_name(name: str) -> tuple[str, int, str] | None:
    """Inverse of cell_name: items.2.amount → ("items", 2, "amount"). None for regular fields."""
    m = _CELL.match(name)
    return (m[1], int(m[2]), m[3]) if m else None


@dataclass(frozen=True)
class ExtractedField:
    """An extracted field. value None means not found in the source.

        row is the line the model claims holds the value; only a hint, verified by evidence
    alignment."""

    name: str
    """Field name. Table cells are named like items.2.amount (see cell_name)."""
    type: FieldType
    value: str | float | None
    row: int | None = None
    label: str | None = None
    """Label on the document (key-value discovery only; for example kv.3 is "Check-in")."""
    row_end: int | None = None
    """Line number of the last line when the value spans several lines (wrapped product names,
    addresses). None for a single line."""
    blocks: tuple[int, ...] = ()
    """Text blocks (indices) the model says the value came from; evidence alignment verifies against
    them."""
    text: str | None = None
    """The value exactly as written in the source: numbers, dates and date ranges only; for example
    value 2014-08-03 with text "August 3 , 2014". Evidence alignment searches the source for text
    first, then checks that value agrees with it."""


@dataclass(frozen=True)
class ExtractionResult:
    """Result of one extraction and its provenance.

        raw_output is the raw model answer and is always kept (original values are never
    overwritten). model, backend and prompt_version enable tracing: the same input may yield
    different results with another model or prompt."""

    fields: tuple[ExtractedField, ...]
    raw_output: str
    model: str
    backend: str
    prompt_version: str
    duration_ms: int
    vision: str | None = None
    """Why the model was shown the page image in this extraction; None if it was not."""
    sent: Mapping[str, Any] | None = None
    """Model input of this extraction: text block indices, characters sent, number of images, number
    of past correction examples, the document text sent and its sha256. Used to diagnose missed
    values: whether the value is in the text sent tells OCR problems from extraction problems."""


class Extractor(Protocol):
    """Extractor interface: any class with a name attribute and an extract method is an
    extractor."""

    name: str

    def extract(
        self,
        page: OcrPage,
        fields: Sequence[FieldSpec],
        tables: Sequence[TableSpec] = (),
        structure: PageStructure | None = None,
        images: Sequence[bytes] = (),
    ) -> ExtractionResult:
        """Extract the given fields and tables from one page of OCR results. Every table cell is
        returned as a separate field.

                structure is the page structure (text block indices, tables, label → value
        pairs); when omitted, a rule-based analysis is run once. images are page images (PNG, one
        per page); when given, the model extracts while viewing them."""
        ...

    def discover(self, page: OcrPage, structure: PageStructure | None = None) -> ExtractionResult:
        """Without a field list, find every "label: value" pair on the document. Field names are
        kv.1, kv.2, …; label is the label on the document."""
        ...


if TYPE_CHECKING:
    from jacara.structure.graph import PageStructure

DISCOVER = "discover"
""""Template name" for key-value discovery, stored in extractions.template to indicate that no
template was used."""

AUTO = "auto"
""""Automatic" selected at upload: each document picks the best match from my templates."""


def kv_name(index: int) -> str:
    """Field name of the index-th (1-based) pair in key-value discovery."""
    return f"kv.{index}"
