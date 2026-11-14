"""Classify a manual correction by error kind.

Based only on the model value and the corrected value as strings, by field type:
- Missing value added: the model gave none and the reviewer entered one; spurious value removed:
  the model gave one and the reviewer cleared it.
- Numbers: decimal point (same digits, different decimal position; common when OCR misses the
  point), sign, other.
- Dates: day and month swapped (03/04 read as 3 April), wrong year, other.
- Text: whitespace, punctuation or case only; a few characters (similarity ≥ 0.8); rewritten
  entirely."""

from __future__ import annotations

import re
from datetime import date
from difflib import SequenceMatcher
from typing import Literal

from jacara.core.i18n import N_

Kind = Literal[
    "missing",
    "extra",
    "decimal",
    "sign",
    "number",
    "day_month",
    "year",
    "date",
    "format",
    "chars",
    "rewrite",
]

KIND_NAMES: dict[str, str] = {
    "missing": N_("模型漏了"),
    "extra": N_("原文没有却给了"),
    "decimal": N_("小数点"),
    "sign": N_("正负号"),
    "number": N_("数字认错"),
    "day_month": N_("日月颠倒"),
    "year": N_("年份不对"),
    "date": N_("日期认错"),
    "format": N_("空格、标点、大小写"),
    "chars": N_("个别字认错"),
    "rewrite": N_("整个不一样"),
}


def _number(text: str) -> float | None:
    cleaned = re.sub(r"[^\d.\-]", "", text)
    try:
        return float(cleaned)
    except ValueError:
        return None


def _date(text: str) -> date | None:
    try:
        return date.fromisoformat(text.strip()[:10])
    except ValueError:
        return None


def _squash(text: str) -> str:
    return re.sub(r"[\W_]+", "", text).lower()


def error_kind(type_: str, before: str | None, after: str | None) -> Kind:
    """Error kind of changing the model value before to after. Do not call when they are equal."""
    if before is None or not before.strip():
        return "missing"
    if after is None or not after.strip():
        return "extra"
    if type_ == "number":
        a, b = _number(before), _number(after)
        if a is not None and b is not None:
            if abs(a + b) < 1e-9 and a != 0:
                return "sign"
            digits_a = re.sub(r"\D", "", f"{abs(a):.6f}").strip("0")
            digits_b = re.sub(r"\D", "", f"{abs(b):.6f}").strip("0")
            if digits_a == digits_b:
                return "decimal"
            return "number"
    if type_ == "date":
        a, b = _date(before), _date(after)
        if a is not None and b is not None:
            if a.year == b.year and (a.month, a.day) == (b.day, b.month):
                return "day_month"
            if (a.month, a.day) == (b.month, b.day):
                return "year"
            return "date"
    if _squash(before) == _squash(after):
        return "format"
    if SequenceMatcher(None, _squash(before), _squash(after)).ratio() >= 0.8:
        return "chars"
    return "rewrite"
