"""Define a template by selecting regions on a sample: the value inside the box, the nearby label,
its position on the page and its likely type.

Uses only the stored OCR result of this page (coordinates match the page image); when no text was
recognized inside the box, the caller re-recognizes the region."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from jacara.core.daterange import parse_date_range
from jacara.core.i18n import N_, tr
from jacara.core.textvalue import parse_date, parse_number

Box = Sequence[float]

_NUMBER_ONLY = re.compile(
    r"(?:[A-Za-z]{2,3}\.?\s+)?[-+(]?\s*[$€£¥₹]?\s*\d+(?:[,\s]\d{3})*(?:\.\d+)?\)?"
    r"\s*(?:%|[A-Za-z]{1,4}\.?)?"
)
"""The whole text is one number (optionally with currency or unit): 1,234.50, $4.11, RM 9.00;
INV-0042 and 2018-12-25 do not qualify."""


def _cover(line: Box, box: Box) -> float:
    """Share of a text block's area inside the box."""
    w = min(line[2], box[2]) - max(line[0], box[0])
    h = min(line[3], box[3]) - max(line[1], box[1])
    area = (line[2] - line[0]) * (line[3] - line[1])
    return (w * h) / area if w > 0 and h > 0 and area > 0 else 0.0


def lines_in(lines: Sequence[dict[str, Any]], box: Box) -> list[dict[str, Any]]:
    """Text blocks with more than half their area inside the box, in reading order."""
    inside = [ln for ln in lines if _cover(ln["box"], box) >= 0.5]
    return sorted(inside, key=lambda ln: (round(ln["box"][1] / 10), ln["box"][0]))


def pair_for(pairs: Sequence[Any], box: Box) -> Any | None:
    """The label → value pair from layout analysis that the box selects: the value box lies mostly
    inside the box and the box is mostly this value. None if nothing matches (no guessing). pairs
    are StructurePair objects (with key and value_box)."""
    best: tuple[float, Any] | None = None
    for pair in pairs:
        value = pair.value_box
        w = min(value[2], box[2]) - max(value[0], box[0])
        h = min(value[3], box[3]) - max(value[1], box[1])
        if w <= 0 or h <= 0:
            continue
        inter = w * h
        of_value = inter / max(1.0, (value[2] - value[0]) * (value[3] - value[1]))
        of_box = inter / max(1.0, (box[2] - box[0]) * (box[3] - box[1]))
        if of_value >= 0.6 and of_box >= 0.4 and (best is None or of_value + of_box > best[0]):
            best = (of_value + of_box, pair)
    return None if best is None else best[1]


def _bare(text: str) -> str:
    return re.sub(r"[\s:：*#]+$", "", str(text)).strip()


def guess_type(text: str) -> str:
    """number if it looks like a number, date_range for a date range, date for a date, otherwise
    string."""
    text = text.strip()
    if not text:
        return "string"
    digits = re.sub(r"\D", "", text)
    code_like = re.fullmatch(r"\d+", text) is not None and (
        text.startswith("0") or len(digits) >= 9
    )
    if code_like:
        # Account and reference numbers (00030340067212, 296664039561) would lose leading zeros as
        # numbers.
        return "string"
    if _NUMBER_ONLY.fullmatch(text) and parse_number(text) is not None and not parse_date(text):
        return "number"
    found = parse_date_range(text).range
    # Requires an explicit range separator (a hyphen surrounded by spaces, ~, 至, to); the hyphens
    # in 2018-12-25 do not count.
    joined = re.search(r"\s[-–—]\s|[~～至到]|\bto\b|\bthrough\b", text, re.IGNORECASE)
    if found is not None and found.start != found.end and joined:
        return "date_range"
    if parse_date(text) is not None:
        return "date"
    return "string"


POSITIONS = (
    (N_("左上角"), N_("上方中部"), N_("右上角")),
    (N_("左侧中部"), N_("中间"), N_("右侧中部")),
    (N_("左下角"), N_("下方中部"), N_("右下角")),
)
"""Names of the cells of a 3 × 3 grid over the page, by row and column."""


def position(box: Box, width: float, height: float) -> str:
    """Position of the box on the page: top left, top center, top right, … (the page is divided into
    a 3 × 3 grid)."""
    cx = (box[0] + box[2]) / 2 / max(width, 1)
    cy = (box[1] + box[3]) / 2 / max(height, 1)
    row = 0 if cy < 1 / 3 else 1 if cy < 2 / 3 else 2
    col = 0 if cx < 1 / 3 else 1 if cx < 2 / 3 else 2
    return tr(POSITIONS[row][col])


def field_id(label: str, used: set[str], n: int) -> str:
    """Guess a field ID from the label: English labels are converted (Invoice No. → invoice_no);
    others are numbered field_3."""
    base = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:40]
    if not re.fullmatch(r"[a-z][a-z0-9_]*", base or ""):
        base = f"field_{n}"
    candidate = base
    k = 2
    while candidate in used:
        candidate = f"{base}_{k}"
        k += 1
    used.add(candidate)
    return candidate


def hint(where: str, label: str | None, side: str | None) -> str:
    """Position hint for the model, for example: top right of the document, right of "Invoice
    No"."""
    if label:
        return tr("在单据{where}，「{label}」{side}", where=where, label=label, side=side or "")
    return tr("在单据{where}", where=where)
