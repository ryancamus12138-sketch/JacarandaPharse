"""Text-matching aligner: locate a value in the source text line by line.

- Numbers: numbers in the source are parsed and compared numerically (core/numbers.py), so 9.0
  matches 9.00, 78.5 and 1,234.50 are recognized;
- Dates: date-like spans in the source are parsed and compared (core/dates.py), so 25/12/2018 and
  25 DEC 18 are recognized.
- The line named by the model is searched first, then the whole text.
- Text fields tolerate small OCR errors (sufficient similarity counts as fuzzy); numbers and
  dates must match exactly, down to the last digit."""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Protocol

from jacara.core.dates import date_pattern
from jacara.core.i18n import join_notes, stored
from jacara.core.numbers import number_pattern
from jacara.plugins.aligners.base import AlignedField, Evidence
from jacara.plugins.extractors.base import ExtractedField, ExtractionResult, split_cell_name
from jacara.plugins.ocr.base import OcrLine, OcrPage, Point
from jacara.plugins.ocr.layout import OcrRow, group_rows


class Span(Protocol):
    """A located span: re.Match or core.numbers.NumberMatch."""

    def start(self) -> int: ...

    def end(self) -> int: ...


class Finder(Protocol):
    """Search rule: a regular expression (dates, times) or core.numbers.NumberPattern (numbers)."""

    def search(self, text: str, /) -> Span | None: ...


def _squash(text: str) -> str:
    """Ignore case and whitespace when comparing text: OCR often adds or drops spaces."""
    return re.sub(r"\s+", "", text).lower()


_TIME = re.compile(r"^\s*(\d{1,2}):(\d{2})(?::(\d{2}))?\s*$")
_AMPM = r"\s*[ap]\.?\s*m\b\.?"


def time_pattern(value: str) -> re.Pattern[str] | None:
    """Possible notations of a time (14:05, 20:13:39) on a document; None if not a time.

        Templates often ask the model for 24-hour times while documents print 8:13:39 PM; both
    match. A value without seconds matches source text with or without seconds. 24-hour notations
    must not be followed by AM/PM (8:13 PM is not 08:13)."""
    m = _TIME.match(value)
    if m is None:
        return None
    hour, minute, second = int(m[1]), m[2], m[3]
    if hour > 23 or int(minute) > 59:
        return None
    sec = f":{second}" if second else r"(?::\d{2})?"
    h12 = hour % 12 or 12
    half = "p" if hour >= 12 else "a"
    clock24 = rf"(?<!\d)0?{hour}:{minute}{sec}(?![\d:])(?!{_AMPM})"
    clock12 = rf"(?<!\d)0?{h12}:{minute}{sec}\s*{half}\.?\s*m\b\.?"
    return re.compile(rf"{clock24}|{clock12}", re.IGNORECASE)


def _tidy(text: str) -> str:
    """Remove extra spaces before commas and periods, common in PDFs as in "August 3 , 2014"."""
    return re.sub(r"\s+(?=[,.])", "", text)


def _best_similarity(needle: str, haystack: str) -> float:
    """Similarity (0–1) of the best-matching span of haystack to needle."""
    if not needle or not haystack:
        return 0.0
    if needle in haystack:
        return 1.0
    size = len(needle)
    if size >= len(haystack):
        return SequenceMatcher(None, needle, haystack).ratio()
    return max(
        SequenceMatcher(None, needle, haystack[i : i + size]).ratio()
        for i in range(len(haystack) - size + 1)
    )


class TextMatchAligner:
    name = "text_match"

    def __init__(self, min_similarity: float = 0.85, min_confidence: float = 0.8) -> None:
        self.min_similarity = min_similarity
        """Text fields with similarity below this are not found."""
        self.min_confidence = min_confidence
        """Evidence with OCR confidence below this requires manual confirmation even when found."""

    def align(self, page: OcrPage, result: ExtractionResult) -> tuple[AlignedField, ...]:
        rows = group_rows(page)
        return tuple(self._align_field(field, rows) for field in result.fields)

    def _align_field(self, field: ExtractedField, rows: tuple[OcrRow, ...]) -> AlignedField:
        if field.value is None:
            return AlignedField(field, "empty", None, False, stored("模型说原文里没有这个字段"))

        # Lines named by the model: when the value wraps, from the first to the last line (row to
        # row_end).
        last = field.row_end or field.row
        claimed = [r for r in rows if field.row is not None and field.row <= r.index <= (last or 0)]
        taken = {r.index for r in claimed}
        others = [r for r in rows if r.index not in taken]
        if field.row is not None:
            # Search near the line named by the model first: the same price (9.99) often appears
            # several times on a receipt.
            others.sort(key=lambda r: abs(r.index - (field.row or 0)))

        clock = time_pattern(str(field.value)) if field.type == "string" else None
        if clock is not None:
            # Times: source 8:13:39 PM and model 20:13:39 count as equal.
            evidence = self._find_exact(clock, claimed + others)
        elif field.type in ("string", "date_range"):
            span = (last - field.row + 1) if field.row is not None and last is not None else 1
            evidence = self._find_text(str(field.value), claimed + others, span)
        elif field.type == "number" and isinstance(field.value, float):
            loose = number_pattern(field.value, loose=True)
            strict = number_pattern(field.value, loose=False)
            evidence = self._find_exact(loose, claimed) or self._find_exact(strict, others)
        else:
            pattern = date_pattern(field.value)
            evidence = self._find_exact(pattern, claimed + others)

        if evidence is None:
            return AlignedField(
                field, "not_found", None, True, stored("原文里找不到这个值，可能是模型编的或改写了")
            )

        notes: list[str] = []
        if evidence.similarity < 1.0:
            notes.append(
                stored(
                    "原文里只有相似的文字（相似度 {similarity:.2f}）",
                    similarity=evidence.similarity,
                )
            )
        slack = _row_slack(field)
        if field.row is not None and not (
            field.row - slack <= evidence.row <= (last or field.row) + slack
        ):
            where = (
                stored("第 {row}～{last} 行", row=field.row, last=last)
                if last != field.row
                else stored("第 {row} 行", row=field.row)
            )
            notes.append(
                stored("模型说在{where}，实际在第 {row} 行", where=where, row=evidence.row)
            )
        if evidence.confidence < self.min_confidence:
            notes.append(
                stored("原文这里 OCR 置信度只有 {confidence:.2f}", confidence=evidence.confidence)
            )
        if field.type == "number" and isinstance(field.value, float):
            sign = _sign_mismatch(field.value, evidence)
            if sign:
                notes.append(sign)

        status = "matched" if evidence.similarity == 1.0 else "fuzzy"
        return AlignedField(field, status, evidence, bool(notes), join_notes(*notes))

    def _find_exact(self, pattern: Finder, rows: list[OcrRow]) -> Evidence | None:
        for row in rows:
            if pattern.search(_tidy(row.text)):
                lines = (
                    tuple(
                        _clip_to(ln, pattern.search(ln.text))
                        for ln in row.lines
                        if pattern.search(_tidy(ln.text))
                    )
                    or row.lines
                )
                return Evidence(row.index, row.text, lines, similarity=1.0)
        return None

    def _find_text(self, value: str, rows: list[OcrRow], claimed_span: int = 1) -> Evidence | None:
        """Text fields: search line by line; long text such as addresses may wrap, so 2–4
        consecutive lines are also tried combined.

                The order of rows is the priority (the line named by the model first, then
        nearest to farthest). On equal similarity, fewer lines and earlier rows win, so short
        text is not matched far away merely because a 4-line combination contains it."""
        needle = _squash(value)
        by_index = {r.index: r for r in rows}
        best: tuple[float, int, int, list[OcrRow]] | None = None
        """(similarity, -line count, -priority, lines): larger is better."""
        # When the model says the value spans several lines, try at least that many (at most
        # _MAX_CLAIMED_SPAN).
        max_span = max(_MAX_SPAN, min(claimed_span, _MAX_CLAIMED_SPAN))
        for order, start in enumerate(rows):
            for size in range(1, max_span + 1):
                span = [
                    by_index[i] for i in range(start.index, start.index + size) if i in by_index
                ]
                if len(span) < size:
                    break
                score = _best_similarity(needle, _span_text(needle, span))
                key = (score, -size, -order)
                if score >= self.min_similarity and (best is None or key > best[:3]):
                    best = (*key, span)
                if score == 1.0:
                    break  # Combining more lines only makes the text longer.
            if best is not None and best[:2] == (1.0, -1):
                break  # Exact match on a single line: nothing later can be better.
        if best is None:
            return None
        score, _, _, span = best
        lines = _lines_for(needle, tuple(ln for r in span for ln in r.lines))
        if len(lines) == 1:
            # Only part of the block is the value (Sanjay in "Guest Name: Sanjay"): box only the
            # value.
            found = re.search(re.escape(value.strip()), lines[0].text, re.IGNORECASE)
            lines = (_clip_to(lines[0], found),)
        row_text = " / ".join(r.text for r in span)
        return Evidence(span[0].index, row_text, lines, round(score, 3))


def _span_text(needle: str, span: list[OcrRow]) -> str:
    """Text of several lines combined for comparison.

        One line: the whole line as is (as before). Several lines (wrapped product names,
    addresses): only the blocks related to the value are kept from each line; otherwise the
    quantity and amount after the first line of a product name ("Room Charges Deluxe  1
    1939.00") would sit between the two parts of the name and the combination would not match."""
    if len(span) == 1:
        return _squash(span[0].text)
    return _squash("".join(ln.text for r in span for ln in _lines_for(needle, r.lines)))


def _sign_mismatch(value: float, evidence: Evidence) -> str | None:
    """Sign mismatch: the source says "6.00-" (discount, refund) while the value is a positive 6.0,
    or vice versa.

        Evidence search ignores signs (receipts write negatives in too many ways), so the sign is
    checked separately."""
    if value == 0:
        return None
    number = re.escape(f"{abs(value):.2f}")
    text = " ".join(line.text for line in evidence.lines)
    negative = re.search(rf"(-\s*{number}|\(\s*{number}\s*\)|{number}\s*-)(?![\d])", text)
    if negative and value > 0:
        return stored("原文是 {value}（负数，多半是折扣），值却是正的", value=negative[0].strip())
    if not negative and value < 0 and re.search(rf"(?<![\d.]){number}(?![\d])", text):
        return stored("原文是 {value:.2f}（正数），值却是负的", value=abs(value))
    return None


def _row_slack(field: ExtractedField) -> int:
    """Allowed line number deviation. A line-item row often occupies two lines (name above, quantity
    and price below) while the model gives one line number, so table cells may be off by one;
    regular fields must be on the same line."""
    return 1 if split_cell_name(field.name) else 0


_MAX_SPAN = 4
"""Maximum number of lines a text field may span."""

_MAX_CLAIMED_SPAN = 8
"""When the model says a value spans several lines (long addresses, long product names), the maximum
number of lines tried."""


MIN_PIECE = 3
"""Minimum characters for a text block to count as part of the value: otherwise "1" (number of
rooms) would count as part of "OYO 4189 Resort" and the evidence box would stretch from the name
to the room count at the right edge. Short blocks identical to the value (for example the value
"1") are exempt."""


def _lines_for(needle: str, lines: tuple[OcrLine, ...]) -> tuple[OcrLine, ...]:
    """Select only the text blocks of a line related to the value so the highlight is precise; fall
    back to the whole line."""
    picked: list[OcrLine] = []
    for line in lines:
        text = _squash(line.text)
        if not text:
            continue
        long_enough = len(text) >= MIN_PIECE
        part = long_enough and (text in needle or _best_similarity(text, needle) >= 0.8)
        if text == needle or part or needle in text:
            picked.append(line)
    return tuple(picked) or lines


def _clip_to(line: OcrLine, found: Span | None) -> OcrLine:
    """When the value is only part of a text block, estimate the box of that part from its character
    position within the block (cut proportionally along the block's top and bottom edges).

        "Date: 31/12/2017" is one block and the value 31/12/2017 is its last 10 characters: only
    that part is boxed. The proportional estimate is exact for monospaced fonts and slightly off
    for proportional ones, but far more precise than boxing the whole block including the label.
    Only the box changes; the text remains the whole block (the minus sign of "6.00-" and the
    source value need the whole block). Returned unchanged when not found or when the value is
    the whole block."""
    text = line.text
    if found is None or not text or (found.start() == 0 and found.end() >= len(text)):
        return line
    f0, f1 = found.start() / len(text), found.end() / len(text)
    tl, tr, br, bl = line.polygon

    def at(a: Point, b: Point, f: float) -> Point:
        return Point(a.x + (b.x - a.x) * f, a.y + (b.y - a.y) * f)

    polygon = (at(tl, tr, f0), at(tl, tr, f1), at(bl, br, f1), at(bl, br, f0))
    return OcrLine(text=text, polygon=polygon, confidence=line.confidence)
