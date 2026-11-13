"""一次人工修改属于哪种错误（ADR 0044）。

只看"模型给的值"和"人改成的值"两个字符串，按字段类型分：
- 补上漏的：模型没给，人填了；删掉多的：模型给了，人清空了。
- 数字：小数点（数字一样、小数点位置不同，常见于 OCR 漏认小数点）、正负号、其他数字错。
- 日期：日月颠倒（03/04 读成 4 月 3 日）、年份不对、其他日期错。
- 文字：只差空格标点大小写、个别字（相似度 ≥ 0.8）、整个改了。
"""

from __future__ import annotations

import re
from datetime import date
from difflib import SequenceMatcher
from typing import Literal

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
    "missing": "模型漏了",
    "extra": "原文没有却给了",
    "decimal": "小数点",
    "sign": "正负号",
    "number": "数字认错",
    "day_month": "日月颠倒",
    "year": "年份不对",
    "date": "日期认错",
    "format": "空格、标点、大小写",
    "chars": "个别字认错",
    "rewrite": "整个不一样",
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
    """模型给的 before 被人改成 after，算哪种错误。两者一样时不要调用。"""
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
