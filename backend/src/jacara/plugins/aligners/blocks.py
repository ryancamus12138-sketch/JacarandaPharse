"""按文字块核对的证据对齐（ADR 0034，LMDX 的做法）。

模型回答每个值时说了"用了哪几块"（编号）。这里去那几块核对：
1. 那几块里确实有这个值（文字相似度够高；数字、日期必须完全一致）→ 出处就是这几块。
   一块里只有一部分是值（"Date: 31/12/2017"），框只框值那一段。
2. 那几块里没有（或者模型没说用了哪几块）→ 退回按文字在整页找（TextMatchAligner）。
   找到了：值留着，标黄，说明"模型说的那几块里没有，在别处找到了"。
3. 整页都找不到 → 按 LMDX 丢掉：值存成空的，模型给的值记下来（rejected），标红，
   人核对原文后可以一键采用。
"""

from __future__ import annotations

import re
from dataclasses import replace

from jacara.plugins.aligners.base import AlignedField, Evidence
from jacara.plugins.aligners.text_match import (
    TextMatchAligner,
    _best_similarity,
    _clip_to,
    _exact_pattern,
    _sign_mismatch,
    _squash,
    _tidy,
    date_variants,
    number_variants,
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
        """模型说的那几块对不上时，按文字在整页找。"""

    def align(
        self, page: OcrPage, result: ExtractionResult, structure: PageStructure | None = None
    ) -> tuple[AlignedField, ...]:
        structure = structure or analyze_page(page)
        rows = group_rows(page)
        seen = result.vision is not None
        return tuple(self._align_field(f, structure, rows, seen) for f in result.fields)

    def _align_field(
        self, field: ExtractedField, s: PageStructure, rows, seen: bool = False
    ) -> AlignedField:
        if field.value is None:
            return AlignedField(field, "empty", None, False, "模型说原文里没有这个字段")

        if field.blocks:
            evidence = self._in_blocks(field, s)
            if evidence is not None:
                return self._checked(field, evidence)

        # 模型说的那几块里没有（或者没说）：按文字在整页找
        found = self.text._align_field(field, rows)
        if found.status in ("matched", "fuzzy") and found.evidence is not None:
            if not field.blocks and field.row is not None:
                return found  # 照老格式给了行号（extract-v5）：和以前一样按行号找，不另外提示
            where = (
                f"模型说值在 {_cited(field.blocks)}，那几块里没有这个值；在别处找到了"
                if field.blocks
                else "模型没说值在哪几块；按文字在原文里找到了"
            )
            note = "；".join(n for n in (where, found.note) if n)
            return replace(found, needs_review=True, note=note)

        if seen:
            return self._seen(field, s)
        cited = f"它说的文字块 {_cited(field.blocks)} 里没有、" if field.blocks else ""
        return AlignedField(
            field,
            "rejected",
            None,
            True,
            f"模型给了「{_shown(field)}」，但{cited}原文里找不到，按规则没有填（核对原文后可以采用）",
        )

    def _seen(self, field: ExtractedField, s: PageStructure) -> AlignedField:
        """看了原图（ADR 0038），文字里对不上的值：是模型在图上读的。值留着，要人核对。
        模型说了 OCR 认错的是哪几块，就把那几块当出处框出来。"""
        words = [w for n in field.blocks if (w := s.word(n)) is not None]
        lines = [ln for n in field.blocks if (ln := s.ocr_line(n)) is not None]
        if words and len(lines) == len(words):
            ocr = " ".join(w.text for w in words)
            evidence = Evidence(
                int(words[0].attrs["row"]), " / ".join(w.text for w in words), tuple(lines), 0.0
            )
            note = (
                f"看原图读的：OCR 认成「{ocr}」，模型在图上看到的是「{_shown(field)}」，请核对原图"
            )
            return AlignedField(field, "seen", evidence, True, note)
        note = (
            f"看原图读的：OCR 文字里没有「{_shown(field)}」"
            "（可能是手写、印章或没认出来），请核对原图"
        )
        return AlignedField(field, "seen", None, True, note)

    def _in_blocks(self, field: ExtractedField, s: PageStructure) -> Evidence | None:
        """在模型说的那几块里核对值。对得上就返回出处，对不上返回 None。"""
        words = [w for n in field.blocks if (w := s.word(n)) is not None]
        lines = [ln for n in field.blocks if (ln := s.ocr_line(n)) is not None]
        if not words or len(lines) != len(words):
            return None
        text = " ".join(w.text for w in words)
        row = int(words[0].attrs["row"])
        row_text = " / ".join(w.text for w in words)
        if field.type == "string":
            value = str(field.value)
            score = _best_similarity(_squash(value), _squash(text))
            if score < self.min_similarity:
                return None
            if len(lines) == 1:
                lines = [
                    _clip_to(lines[0], re.search(re.escape(value.strip()), lines[0].text, re.I))
                ]
            return Evidence(row, row_text, tuple(lines), round(score, 3))
        if field.type == "number" and isinstance(field.value, float):
            pattern = _exact_pattern(number_variants(field.value, loose=True))
        else:
            pattern = _exact_pattern(date_variants(str(field.value)))
        if not pattern.search(_tidy(text)):
            return None
        picked = [
            _clip_to(ln, pattern.search(ln.text)) for ln in lines if pattern.search(_tidy(ln.text))
        ]
        return Evidence(row, row_text, tuple(picked or lines), 1.0)

    def _checked(self, field: ExtractedField, evidence: Evidence) -> AlignedField:
        notes: list[str] = []
        if evidence.similarity < 1.0:
            notes.append(f"原文里只有相似的文字（相似度 {evidence.similarity:.2f}）")
        if evidence.confidence < self.min_confidence:
            notes.append(f"原文这里 OCR 置信度只有 {evidence.confidence:.2f}")
        if field.type == "number" and isinstance(field.value, float):
            sign = _sign_mismatch(field.value, evidence)
            if sign:
                notes.append(sign)
        status = "matched" if evidence.similarity == 1.0 else "fuzzy"
        return AlignedField(field, status, evidence, bool(notes), "；".join(notes))


def _shown(field: ExtractedField) -> str:
    value = field.value
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    return str(value)
