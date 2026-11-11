"""文字比对对齐器：把值换成原文里可能的写法，逐行查找。

- 数字 9.0 可能写成 9.00、1,234.50；日期 2018-12-25 可能写成 25/12/2018。
- 先查模型说的那一行，找不到再查全文。
- 文字字段允许 OCR 小错（相似度够高算 fuzzy）；数字和日期必须完全一致，差一位都不行。
"""

from __future__ import annotations

import re
from datetime import date
from difflib import SequenceMatcher

from jacara.plugins.aligners.base import AlignedField, Evidence
from jacara.plugins.extractors.base import ExtractedField, ExtractionResult
from jacara.plugins.ocr.base import OcrLine, OcrPage
from jacara.plugins.ocr.layout import OcrRow, group_rows


def _squash(text: str) -> str:
    """比较文字时忽略大小写和空格：OCR 经常多读或少读空格。"""
    return re.sub(r"\s+", "", text).lower()


def number_variants(value: float, *, loose: bool) -> list[str]:
    """一个数字在单据上可能的写法。

    loose=True 时加上不带小数的写法（9、1,234）。这种写法太容易撞上数量、编号，
    所以只在模型指定的那一行里用。
    """
    variants = [f"{value:.2f}", f"{value:,.2f}"]
    if loose and value == int(value):
        variants += [str(int(value)), f"{int(value):,}"]
    return list(dict.fromkeys(variants))  # 去重，保留顺序


def date_variants(value: str) -> list[str]:
    """一个 YYYY-MM-DD 日期在单据上可能的写法。"""
    try:
        d = date.fromisoformat(value)
    except ValueError:
        return [value]
    dd, mm, yyyy = f"{d.day:02d}", f"{d.month:02d}", f"{d.year}"
    yy = yyyy[2:]  # 两位年份：收据上常写 18/03/18（评测发现，见 docs/eval/README.md）
    variants = [value]
    for sep in ("/", "-", "."):
        variants += [f"{dd}{sep}{mm}{sep}{yyyy}", f"{mm}{sep}{dd}{sep}{yyyy}"]
        variants += [f"{yyyy}{sep}{mm}{sep}{dd}"]
        variants += [f"{dd}{sep}{mm}{sep}{yy}", f"{mm}{sep}{dd}{sep}{yy}"]
    variants += [f"{d.year}年{d.month}月{d.day}日", f"{yyyy}年{mm}月{dd}日"]
    for month in (d.strftime("%B"), d.strftime("%b")):  # August、Aug
        variants += [f"{month} {d.day}, {yyyy}", f"{d.day} {month} {yyyy}"]
        variants += [f"{dd} {month} {yyyy}", f"{d.day}-{month}-{yyyy}", f"{dd}-{month}-{yyyy}"]
    return list(dict.fromkeys(variants))


def _exact_pattern(variants: list[str]) -> re.Pattern[str]:
    """数字和日期的查找规则：前后不能紧挨着别的数字，避免 9.00 匹配到 19.00 或 9.000。"""
    alternatives = "|".join(re.escape(v) for v in variants)
    return re.compile(rf"(?<![\d.,])(?:{alternatives})(?![\d]|[.,]\d)", re.IGNORECASE)


def _tidy(text: str) -> str:
    """去掉逗号、句号前多余的空格：PDF 里常见 "August 3 , 2014" 这种写法。"""
    return re.sub(r"\s+(?=[,.])", "", text)


def _best_similarity(needle: str, haystack: str) -> float:
    """needle 在 haystack 里最像的那一段有多像（0～1）。"""
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
        """文字字段相似度低于这个值，就算找不到。"""
        self.min_confidence = min_confidence
        """出处的 OCR 置信度低于这个值，即使找到了也要人工确认。"""

    def align(self, page: OcrPage, result: ExtractionResult) -> tuple[AlignedField, ...]:
        rows = group_rows(page)
        return tuple(self._align_field(field, rows) for field in result.fields)

    def _align_field(self, field: ExtractedField, rows: tuple[OcrRow, ...]) -> AlignedField:
        if field.value is None:
            return AlignedField(field, "empty", None, False, "模型说原文里没有这个字段")

        claimed = [r for r in rows if r.index == field.row]
        others = [r for r in rows if r.index != field.row]

        if field.type == "string":
            evidence = self._find_text(str(field.value), claimed + others)
        elif field.type == "number" and isinstance(field.value, float):
            loose = _exact_pattern(number_variants(field.value, loose=True))
            strict = _exact_pattern(number_variants(field.value, loose=False))
            evidence = self._find_exact(loose, claimed) or self._find_exact(strict, others)
        else:
            pattern = _exact_pattern(date_variants(str(field.value)))
            evidence = self._find_exact(pattern, claimed + others)

        if evidence is None:
            return AlignedField(
                field, "not_found", None, True, "原文里找不到这个值，可能是模型编的或改写了"
            )

        notes: list[str] = []
        if evidence.similarity < 1.0:
            notes.append(f"原文里只有相似的文字（相似度 {evidence.similarity:.2f}）")
        if field.row is not None and evidence.row != field.row:
            notes.append(f"模型说在第 {field.row} 行，实际在第 {evidence.row} 行")
        if evidence.confidence < self.min_confidence:
            notes.append(f"原文这里 OCR 置信度只有 {evidence.confidence:.2f}")

        status = "matched" if evidence.similarity == 1.0 else "fuzzy"
        return AlignedField(field, status, evidence, bool(notes), "；".join(notes))

    def _find_exact(self, pattern: re.Pattern[str], rows: list[OcrRow]) -> Evidence | None:
        for row in rows:
            if pattern.search(_tidy(row.text)):
                lines = tuple(ln for ln in row.lines if pattern.search(_tidy(ln.text))) or row.lines
                return Evidence(row.index, row.text, lines, similarity=1.0)
        return None

    def _find_text(self, value: str, rows: list[OcrRow]) -> Evidence | None:
        """文字字段：逐行找；地址这种长文字可能跨几行，所以也试连续 2～4 行拼起来。"""
        needle = _squash(value)
        by_index = {r.index: r for r in rows}
        best: tuple[float, list[OcrRow]] | None = None
        for start in rows:
            for size in range(1, _MAX_SPAN + 1):
                span = [
                    by_index[i] for i in range(start.index, start.index + size) if i in by_index
                ]
                if len(span) < size:
                    break
                score = _best_similarity(needle, _squash("".join(r.text for r in span)))
                if score >= self.min_similarity and (best is None or score > best[0]):
                    best = (score, span)
                if score == 1.0:
                    break  # 再多拼几行也只会更长，没必要
            if best is not None and best[0] == 1.0:
                break
        if best is None:
            return None
        score, span = best
        lines = tuple(ln for r in span for ln in r.lines)
        row_text = " / ".join(r.text for r in span)
        return Evidence(span[0].index, row_text, _lines_for(needle, lines), round(score, 3))


_MAX_SPAN = 4
"""文字字段最多跨几行去找。"""


def _lines_for(needle: str, lines: tuple[OcrLine, ...]) -> tuple[OcrLine, ...]:
    """一行里只挑和这个值有关的文字块，高亮框才准。挑不出来就用整行。"""
    picked: list[OcrLine] = []
    for line in lines:
        text = _squash(line.text)
        if text and (text in needle or needle in text or _best_similarity(text, needle) >= 0.8):
            picked.append(line)
    return tuple(picked) or lines
