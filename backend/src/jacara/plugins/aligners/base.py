"""Evidence alignment extension point: locate the source of every extracted value in the source
text.

When the LLM says "the total is 9.0, on line 17", that is only a claim. Evidence alignment
verifies it against the OCR result: if the value is there, its position and OCR confidence are
recorded; otherwise it is flagged for manual confirmation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from jacara.plugins.extractors.base import ExtractedField, ExtractionResult
from jacara.plugins.ocr.base import OcrLine, OcrPage

AlignStatus = Literal["matched", "fuzzy", "not_found", "empty", "rejected", "computed", "seen"]
"""matched: an identical value was found in the source. fuzzy: only a similar value was found.
not_found: not found in the source; possibly invented by the model. empty: the model reported the
field absent. rejected: the model gave a value, but it is neither in the text blocks it
referenced nor elsewhere in the source, so it is dropped as in LMDX; the stored value is empty
and the model value is recorded separately so a reviewer can accept it with one click. computed:
not taken from the source but calculated from other fields by a template formula; no evidence
box. seen: read by the model from the page image; OCR misread or missed it, so it does not match
the text. The value is kept and flagged for review; if the model named the (misread) blocks, they
are boxed, otherwise there is no evidence box."""


@dataclass(frozen=True)
class Evidence:
    """Evidence of a value: line number, text blocks (with coordinates), similarity."""

    row: int
    row_text: str
    lines: tuple[OcrLine, ...]
    similarity: float

    @property
    def confidence(self) -> float:
        """Lowest OCR confidence among the evidence blocks: the least certain block decides how far
        the value can be trusted."""
        return min(line.confidence for line in self.lines)

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        """Merge the evidence blocks into one box (x0, y0, x1, y1), highlighted on the page image by
        the review page."""
        boxes = [line.bbox for line in self.lines]
        return (
            min(b[0] for b in boxes),
            min(b[1] for b in boxes),
            max(b[2] for b in boxes),
            max(b[3] for b in boxes),
        )


CandidateSource = Literal["ocr", "text_layer", "zoom", "form"]
"""Source of a candidate value: full-page OCR, PDF text layer, zoomed re-recognition, PDF form
field."""

LayerCheck = Literal["none", "absent", "agree", "ambiguous", "conflict"]
"""Result of checking against the PDF text layer: none, no text layer (images, scans); absent, no
text-layer characters at the evidence position (usually images or stamps); agree, the text layer
has the same value (recorded only, no extra credit); ambiguous, no clear match (several lines
nearby); conflict, the text layer differs."""


@dataclass(frozen=True)
class Candidate:
    """Another possible value of a field. Offered for selection only; never replaces the value
    automatically.

        box is the position on the (stacked) page, in OCR coordinates."""

    source: CandidateSource
    text: str
    box: tuple[float, float, float, float] | None = None
    confidence: float | None = None
    value: str | None = None
    """Value normalized by field type (number, date), filled in on "accept"; None if it cannot be
    normalized, in which case text is used."""
    note: str = ""
    dpi: int | None = None
    """Resolution used for zoomed re-recognition."""
    rule_ok: bool | None = None
    """Whether the rules involving this field all pass with this value (a hint only; never applied
    automatically)."""
    visible: bool | None = None
    """Form fields: whether the value is visible on the page (None when it cannot be confirmed)."""


@dataclass(frozen=True)
class AlignedField:
    """An aligned field: the original extraction result + alignment status + evidence + whether
    manual confirmation is needed + reason."""

    field: ExtractedField
    status: AlignStatus
    evidence: Evidence | None
    needs_review: bool
    note: str = ""
    """Human-readable explanation, for example "not found in the source text". Shown directly on the
    review page."""
    candidates: tuple[Candidate, ...] = ()
    """Differing values from different sources: present only for conflicts and missed values; all
    distinct readings are kept."""
    layer: LayerCheck | None = None
    """Result of checking against the PDF text layer. None if not checked."""


class Aligner(Protocol):
    """Evidence alignment interface: any class with a name attribute and an align method is an
    aligner."""

    name: str

    def align(self, page: OcrPage, result: ExtractionResult) -> tuple[AlignedField, ...]:
        """Locate the evidence of each field in the OCR result, in the order of result.fields."""
        ...
