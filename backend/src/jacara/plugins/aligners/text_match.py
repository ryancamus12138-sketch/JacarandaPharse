"""文字比对对齐器：逐行查找值在原文里的位置。

- 数字：把原文里的数读出来按数值比（core/numbers.py），9.0 写成 9.00、78.5、1,234.50 都认；
- 日期：把原文里像日期的写法读成日期再比（core/dates.py），25/12/2018、25 DEC 18 都认。
- 先查模型说的那一行，找不到再查全文。
- 文字字段允许 OCR 小错（相似度够高算 fuzzy）；数字和日期必须完全一致，差一位都不行。
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Protocol

from jacara.core.dates import date_pattern
from jacara.core.numbers import number_pattern
from jacara.plugins.aligners.base import AlignedField, Evidence
from jacara.plugins.extractors.base import ExtractedField, ExtractionResult, split_cell_name
from jacara.plugins.ocr.base import OcrLine, OcrPage, Point
from jacara.plugins.ocr.layout import OcrRow, group_rows


class Span(Protocol):
    """找到的一段：re.Match 和 core.numbers.NumberMatch 都是。"""

    def start(self) -> int: ...

    def end(self) -> int: ...


class Finder(Protocol):
    """查找规则：正则（日期、时间）和 core.numbers.NumberPattern（数字）都是。"""

    def search(self, text: str, /) -> Span | None: ...


def _squash(text: str) -> str:
    """比较文字时忽略大小写和空格：OCR 经常多读或少读空格。"""
    return re.sub(r"\s+", "", text).lower()


_TIME = re.compile(r"^\s*(\d{1,2}):(\d{2})(?::(\d{2}))?\s*$")
_AMPM = r"\s*[ap]\.?\s*m\b\.?"


def time_pattern(value: str) -> re.Pattern[str] | None:
    """一个时间（14:05、20:13:39）在单据上可能的写法；不是时间返回 None。

    模板常让模型写成 24 小时制，单据上却常写 8:13:39 PM：两种都算对上。
    值没写秒时，原文带不带秒都行。24 小时制的写法后面不能跟 AM/PM（8:13 PM 不是 08:13）。
    """
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

        # 模型说的那几行：值折行时是第一行到最后一行（row 到 row_end）
        last = field.row_end or field.row
        claimed = [r for r in rows if field.row is not None and field.row <= r.index <= (last or 0)]
        taken = {r.index for r in claimed}
        others = [r for r in rows if r.index not in taken]
        if field.row is not None:
            # 先找模型说的那一行附近：同一个价格（9.99）在收据上常出现好几次
            others.sort(key=lambda r: abs(r.index - (field.row or 0)))

        clock = time_pattern(str(field.value)) if field.type == "string" else None
        if clock is not None:
            # 时间：原文写 8:13:39 PM、模型写 20:13:39 也算一样
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
                field, "not_found", None, True, "原文里找不到这个值，可能是模型编的或改写了"
            )

        notes: list[str] = []
        if evidence.similarity < 1.0:
            notes.append(f"原文里只有相似的文字（相似度 {evidence.similarity:.2f}）")
        slack = _row_slack(field)
        if field.row is not None and not (
            field.row - slack <= evidence.row <= (last or field.row) + slack
        ):
            where = f"第 {field.row}～{last} 行" if last != field.row else f"第 {field.row} 行"
            notes.append(f"模型说在{where}，实际在第 {evidence.row} 行")
        if evidence.confidence < self.min_confidence:
            notes.append(f"原文这里 OCR 置信度只有 {evidence.confidence:.2f}")
        if field.type == "number" and isinstance(field.value, float):
            sign = _sign_mismatch(field.value, evidence)
            if sign:
                notes.append(sign)

        status = "matched" if evidence.similarity == 1.0 else "fuzzy"
        return AlignedField(field, status, evidence, bool(notes), "；".join(notes))

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
        """文字字段：逐行找；地址这种长文字可能跨几行，所以也试连续 2～4 行拼起来。

        rows 的顺序就是优先顺序（模型说的那一行在最前，然后由近到远）。相似度一样时，
        行数少的优先、排在前面的优先：短文字不会因为"拼了 4 行里包含它"就跑到远处去。
        """
        needle = _squash(value)
        by_index = {r.index: r for r in rows}
        best: tuple[float, int, int, list[OcrRow]] | None = None
        """(相似度, -行数, -优先顺序, 这几行)：越大越好。"""
        # 模型说值跨了几行，就至少试到这么多行（最多 _MAX_CLAIMED_SPAN 行）
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
                    break  # 再多拼几行也只会更长，没必要
            if best is not None and best[:2] == (1.0, -1):
                break  # 一模一样、只占一行：后面的不可能更好
        if best is None:
            return None
        score, _, _, span = best
        lines = _lines_for(needle, tuple(ln for r in span for ln in r.lines))
        if len(lines) == 1:
            # 一块文字里只有一部分是这个值（"Guest Name: Sanjay" 里的 Sanjay）：框只框住值
            found = re.search(re.escape(value.strip()), lines[0].text, re.IGNORECASE)
            lines = (_clip_to(lines[0], found),)
        row_text = " / ".join(r.text for r in span)
        return Evidence(span[0].index, row_text, lines, round(score, 3))


def _span_text(needle: str, span: list[OcrRow]) -> str:
    """几行拼起来比较用的文字。

    一行：整行原样（和以前一样）。跨几行（折行的商品名、地址）：每行只留和这个值有关的文字块，
    否则商品名第一行后面的数量、金额（"Room Charges Deluxe  1  1939.00"）会夹在两段名字中间，
    拼出来就对不上了。
    """
    if len(span) == 1:
        return _squash(span[0].text)
    return _squash("".join(ln.text for r in span for ln in _lines_for(needle, r.lines)))


def _sign_mismatch(value: float, evidence: Evidence) -> str | None:
    """正负号对不上：原文写 "6.00-"（折扣、退款），值却是正的 6.0；或者反过来。

    找出处时不看正负号（收据上负号的写法太多），所以单独查一次。
    """
    if value == 0:
        return None
    number = re.escape(f"{abs(value):.2f}")
    text = " ".join(line.text for line in evidence.lines)
    negative = re.search(rf"(-\s*{number}|\(\s*{number}\s*\)|{number}\s*-)(?![\d])", text)
    if negative and value > 0:
        return f"原文是 {negative[0].strip()}（负数，多半是折扣），值却是正的"
    if not negative and value < 0 and re.search(rf"(?<![\d.]){number}(?![\d])", text):
        return f"原文是 {abs(value):.2f}（正数），值却是负的"
    return None


def _row_slack(field: ExtractedField) -> int:
    """行号允许差几行。表格的一行商品常占两行（上一行商品名，下一行数量和价格），
    模型只给一个行号，所以格子差一行不算问题；普通字段必须是同一行。"""
    return 1 if split_cell_name(field.name) else 0


_MAX_SPAN = 4
"""文字字段最多跨几行去找。"""

_MAX_CLAIMED_SPAN = 8
"""模型说值跨了好几行（长地址、长商品名）时，最多试到几行。"""


MIN_PIECE = 3
"""一个文字块"是这个值的一部分"至少要有几个字：不然 "1"（房间数）也算 "OYO 4189 Resort" 的一部分，
出处框就从店名一直拉到这一行最右边的房间数。和值完全一样的短块（例如值就是 "1"）不受限制。"""


def _lines_for(needle: str, lines: tuple[OcrLine, ...]) -> tuple[OcrLine, ...]:
    """一行里只挑和这个值有关的文字块，高亮框才准。挑不出来就用整行。"""
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
    """值只是这个文字块的一部分时，按字在块里的位置估出值那一段的框（沿着块的上下边按比例截）。

    "Date: 31/12/2017" 是一块，值 31/12/2017 在后面 10 个字：框只画后面那段。
    按字数比例估，等宽字体最准；比例字体会差一点，但比框住整块（连标签一起）准得多。
    只改框，文字还是整块的原文（"6.00-" 的负号、"原文值"都要看整块）。
    找不到、或者就是整块的，原样返回。
    """
    text = line.text
    if found is None or not text or (found.start() == 0 and found.end() >= len(text)):
        return line
    f0, f1 = found.start() / len(text), found.end() / len(text)
    tl, tr, br, bl = line.polygon

    def at(a: Point, b: Point, f: float) -> Point:
        return Point(a.x + (b.x - a.x) * f, a.y + (b.y - a.y) * f)

    polygon = (at(tl, tr, f0), at(tl, tr, f1), at(bl, br, f1), at(bl, br, f0))
    return OcrLine(text=text, polygon=polygon, confidence=line.confidence)
