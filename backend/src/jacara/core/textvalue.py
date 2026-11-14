"""Parse numbers and dates from a span of source text.

When a reviewer fills a value by clicking text on the page image ("RM 1,234.50", "25/12/2018 8:13
PM"), the stored value must have the same format as model output (1234.5, 2018-12-25). Evaluation
scoring uses the same date parsing."""

from __future__ import annotations

import re
from datetime import date

_MONTH_NAMES = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
_MONTHS = {m: i for i, m in enumerate(_MONTH_NAMES, 1)}

_NUMBER = re.compile(r"(\(?)(-?)(\d[\d,]*(?:\.\d+)?|\.\d+)(\)?)(-?)")


def parse_number(text: str) -> float | None:
    """Amount in the text: the last number ("1x 9.99 9.99" → 9.99, "RM 1,234.50" → 1234.5).

        Discount notations "6.00-", "-6.00" and "(6.00)" are all read as -6.0. Returns None if no
    number is found."""
    found = list(_NUMBER.finditer(text))
    if not found:
        return None
    m = found[-1]
    number = float(m[3].replace(",", ""))
    negative = m[2] == "-" or m[5] == "-" or (m[1] == "(" and m[4] == ")")
    return -number if negative else number


def signed_number(text: str, magnitude: float) -> float | None:
    """The number in the text with the same magnitude, carrying the sign from the source text.

        Looking for 6.0 in "SUMMERINA 6.00-" gives -6.0. Used to detect a positive value whose
    source is a discount. Returns None if no number of that magnitude exists (another number on
    the line is never substituted)."""
    for m in _NUMBER.finditer(text):
        number = float(m[3].replace(",", ""))
        if abs(number - abs(magnitude)) < 0.005:
            negative = m[2] == "-" or m[5] == "-" or (m[1] == "(" and m[4] == ")")
            return -number if negative else number
    return None


def parse_date(text: str) -> date | None:
    """Dates come in many formats: 25/12/2018, 15-01-19, 02 MAR 2018, OCT 3, 2016, 20180304,
    2018-12-25.

        Day/month/year is assumed; a month greater than 12 indicates month/day/year and the two
    are swapped. Returns None if unparseable."""
    # Space before the comma in "August 3 , 2014".
    t = re.sub(r"\s+(?=[,.])", "", text.strip().upper())
    try:
        if m := re.search(r"(\d{4})[/.-](\d{1,2})[/.-](\d{1,2})", t):
            return date(int(m[1]), int(m[2]), int(m[3]))
        if m := re.search(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})", t):
            day, month = int(m[1]), int(m[2])
            if month > 12:
                day, month = month, day
            return date(_year(m[3]), month, day)
        if m := re.fullmatch(r"(\d{8})", t):
            s = m[1]
            if s[:2] in ("19", "20"):
                return date(int(s[:4]), int(s[4:6]), int(s[6:]))
            return date(int(s[4:]), int(s[2:4]), int(s[:2]))
        if m := re.search(r"(\d{1,2})\s*[-/]?\s*([A-Z]{3})[A-Z]*\s*[-/,]?\s*(\d{2,4})", t):
            month = _MONTHS.get(m[2])
            return date(_year(m[3]), month, int(m[1])) if month else None
        if m := re.search(r"([A-Z]{3})[A-Z]*\.?\s+(\d{1,2}),?\s+(\d{2,4})", t):
            month = _MONTHS.get(m[1])
            return date(_year(m[3]), month, int(m[2])) if month else None
        if m := re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", t):
            return date(int(m[1]), int(m[2]), int(m[3]))
    except ValueError:
        return None
    return None


def _year(text: str) -> int:
    return int(text) + 2000 if len(text) == 2 else int(text)
