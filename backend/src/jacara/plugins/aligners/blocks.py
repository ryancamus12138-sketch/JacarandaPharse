"""Block-based evidence alignment (the LMDX approach).

For each value the model names the text blocks it used (by index). Those blocks are checked:
1. The blocks contain the value (sufficient text similarity; numbers and dates must match
   exactly) → the evidence is these blocks. When only part of a block is the value ("Date:
   31/12/2017"), the box covers just that part.
2. The blocks do not contain it (or the model named none) → fall back to a full-page text search
   (TextMatchAligner). If found, the value is kept and flagged: "not in the blocks the model
   referenced; found elsewhere".
3. Not found anywhere on the page → dropped as in LMDX: the stored value is empty, the model
   value is recorded (rejected) and flagged red; a reviewer can accept it with one click after
   checking the source."""

from __future__ import annotations

import re
from dataclasses import replace

from jacara.core.dates import date_pattern
from jacara.core.i18n import join_notes, stored
from jacara.core.numbers import number_pattern
from jacara.plugins.aligners.base import AlignedField, Evidence
from jacara.plugins.aligners.crosscheck import cross_check
from jacara.plugins.aligners.text_match import (
    Finder,
    TextMatchAligner,
    _best_similarity,
    _clip_to,
    _sign_mismatch,
    _squash,
    _tidy,
    time_pattern,
)
from jacara.plugins.extractors.base import ExtractedField, ExtractionResult
from jacara.plugins.ocr.base import OcrPage
from jacara.plugins.ocr.layout import group_rows
from jacara.structure.analyze import analyze_page
from jacara.structure.graph import PageStructure


def _cited(numbers: tuple[int, ...]) -> str:
    return "".join(f"⟨{n}⟩" for n in numbers)


class BlockAligner:
    name = "blocks"

    def __init__(self, min_similarity: float = 0.85, min_confidence: float = 0.8) -> None:
        self.min_similarity = min_similarity
        self.min_confidence = min_confidence
        self.text = TextMatchAligner(min_similarity, min_confidence)
        """Full-page text search used when the blocks named by the model do not match."""

    def align(
        self,
        page: OcrPage,
        result: ExtractionResult,
        structure: PageStructure | None = None,
        day_first: bool | None = None,
    ) -> tuple[AlignedField, ...]:
        """day_first: whether numeric dates are day/month (from the template country); used when
        cross-checking dates."""
        structure = structure or analyze_page(page)
        rows = group_rows(page)
        seen = result.vision is not None
        return tuple(self._align_field(f, structure, rows, seen, day_first) for f in result.fields)

    def _align_field(
        self,
        field: ExtractedField,
        s: PageStructure,
        rows,
        seen: bool = False,
        day_first: bool | None = None,
    ) -> AlignedField:
        if field.value is None:
            return AlignedField(field, "empty", None, False, stored("模型说原文里没有这个字段"))

        if field.text and field.type != "string":
            # The model copied the source text: search for it as written first, then check whether
            # the interpreted value matches.
            quoted = self._quoted(field, s, rows, day_first)
            if quoted is not None:
                return quoted

        if field.blocks:
            evidence = self._in_blocks(field, s)
            if evidence is not None:
                return self._checked(field, evidence)

        # Not in the named blocks (or none named): full-page text search.
        found = self.text._align_field(field, rows)
        if found.status in ("matched", "fuzzy") and found.evidence is not None:
            if not field.blocks and field.row is not None:
                # Line number in the old format (extract-v5): search by line number as before,
                # without an extra note.
                return found
            where = (
                stored(
                    "模型说值在 {cited}，那几块里没有这个值；在别处找到了",
                    cited=_cited(field.blocks),
                )
                if field.blocks
                else stored("模型没说值在哪几块；按文字在原文里找到了")
            )
            note = join_notes(where, found.note)
            return replace(found, needs_review=True, note=note)

        if seen:
            return self._seen(field, s)
        note = (
            stored(
                "模型给了「{shown}」，但它说的文字块 {cited} 里没有、原文里找不到，"
                "按规则没有填（核对原文后可以采用）",
                shown=_shown(field),
                cited=_cited(field.blocks),
            )
            if field.blocks
            else stored(
                "模型给了「{shown}」，但原文里找不到，按规则没有填（核对原文后可以采用）",
                shown=_shown(field),
            )
        )
        return AlignedField(field, "rejected", None, True, note)

    def _quoted(
        self, field: ExtractedField, s: PageStructure, rows, day_first: bool | None
    ) -> AlignedField | None:
        """Locate evidence by the source text copied by the model (text), then cross-check value.
        Returns None if the text is not found, falling back to the value-based search (the model
        may have miscopied a character or two)."""
        text = field.text or ""
        as_text = replace(field, value=text, type="string")
        notes: list[str] = []
        evidence = self._in_blocks(as_text, s) if field.blocks else None
        if evidence is None:
            found = self.text._align_field(replace(as_text, row=None, row_end=None), rows)
            if found.evidence is None or found.status not in ("matched", "fuzzy"):
                return None
            evidence = found.evidence
            if field.blocks:
                notes.append(
                    stored(
                        "模型说值在 {cited}，那几块里没有；在别处找到了", cited=_cited(field.blocks)
                    )
                )
        if evidence.similarity < 1.0:
            notes.append(
                stored(
                    "原文里只有相似的文字（相似度 {similarity:.2f}）",
                    similarity=evidence.similarity,
                )
            )
        if evidence.confidence < self.min_confidence:
            notes.append(
                stored("原文这里 OCR 置信度只有 {confidence:.2f}", confidence=evidence.confidence)
            )
        check = cross_check(field.type, field.value, text, day_first)
        if check.verdict != "ok":
            notes.append(check.note)
        if field.type == "number" and isinstance(field.value, float):
            sign = _sign_mismatch(field.value, evidence)
            if sign:
                notes.append(sign)
        status = "matched" if not notes else "fuzzy"
        return AlignedField(field, status, evidence, bool(notes), join_notes(*notes))

    def _seen(self, field: ExtractedField, s: PageStructure) -> AlignedField:
        """Values from the page image that do not match the text: read by the model from the image.
        The value is kept for review. Blocks the model named as misread by OCR are boxed as
        evidence."""
        words = [w for n in field.blocks if (w := s.word(n)) is not None]
        lines = [ln for n in field.blocks if (ln := s.ocr_line(n)) is not None]
        if words and len(lines) == len(words):
            ocr = " ".join(w.text for w in words)
            evidence = Evidence(
                int(words[0].attrs["row"]), " / ".join(w.text for w in words), tuple(lines), 0.0
            )
            note = stored(
                "看原图读的：OCR 认成「{ocr}」，模型在图上看到的是「{shown}」，请核对原图",
                ocr=ocr,
                shown=_shown(field),
            )
            return AlignedField(field, "seen", evidence, True, note)
        note = stored(
            "看原图读的：OCR 文字里没有「{shown}」（可能是手写、印章或没认出来），请核对原图",
            shown=_shown(field),
        )
        return AlignedField(field, "seen", None, True, note)

    def _in_blocks(self, field: ExtractedField, s: PageStructure) -> Evidence | None:
        """Check the value against the blocks named by the model. Returns the evidence on a match,
        None otherwise."""
        words = [w for n in field.blocks if (w := s.word(n)) is not None]
        lines = [ln for n in field.blocks if (ln := s.ocr_line(n)) is not None]
        if not words or len(lines) != len(words):
            return None
        text = " ".join(w.text for w in words)
        row = int(words[0].attrs["row"])
        row_text = " / ".join(w.text for w in words)
        clock = time_pattern(str(field.value)) if field.type == "string" else None
        if clock is not None:
            # Times: source 8:13:39 PM and the model's 24-hour 20:13:39 match.
            if not clock.search(text):
                return None
            picked = [_clip_to(ln, clock.search(ln.text)) for ln in lines if clock.search(ln.text)]
            return Evidence(row, row_text, tuple(picked or lines), 1.0)
        if field.type in ("string", "date_range"):
            value = str(field.value)
            score = _best_similarity(_squash(value), _squash(text))
            if score < self.min_similarity:
                # The model named extra blocks (a printed "Bill to Address:", "TOTAL AMOUNT DUE" in
                # the neighboring column): compare only the blocks whose text is part of the value.
                kept = [
                    (w, ln)
                    for w, ln in zip(words, lines, strict=True)
                    if _squash(w.text) and _squash(w.text) in _squash(value)
                ]
                if not kept or len(kept) == len(words):
                    return None
                words = [w for w, _ in kept]
                lines = [ln for _, ln in kept]
                text = " ".join(w.text for w in words)
                row = int(words[0].attrs["row"])
                row_text = " / ".join(w.text for w in words)
                score = _best_similarity(_squash(value), _squash(text))
            if score < self.min_similarity:
                return None
            if len(lines) == 1:
                lines = [
                    _clip_to(lines[0], re.search(re.escape(value.strip()), lines[0].text, re.I))
                ]
            return Evidence(row, row_text, tuple(lines), round(score, 3))
        if field.type == "number" and isinstance(field.value, float):
            pattern: Finder = number_pattern(field.value, loose=True)
        else:
            pattern = date_pattern(field.value)
        if not pattern.search(_tidy(text)):
            return None
        picked = [
            _clip_to(ln, pattern.search(ln.text)) for ln in lines if pattern.search(_tidy(ln.text))
        ]
        return Evidence(row, row_text, tuple(picked or lines), 1.0)

    def _checked(self, field: ExtractedField, evidence: Evidence) -> AlignedField:
        notes: list[str] = []
        if evidence.similarity < 1.0:
            notes.append(
                stored(
                    "原文里只有相似的文字（相似度 {similarity:.2f}）",
                    similarity=evidence.similarity,
                )
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


def _shown(field: ExtractedField) -> str:
    value = field.value
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    return str(value)
