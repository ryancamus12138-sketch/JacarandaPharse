# ruff: noqa: E501
"""Locate a date in the source text: parse every date-like span in the text and compare it with the
value, instead of generating candidate spellings of the value and searching for them.

Same approach as numbers (core/numbers.py). Generating spellings of 2014-08-03 (03/08/2014,
August 3, 2014, …) misses any spelling not generated. Instead every date-like span in the source
text is parsed:

- 2014-08-03, 2014/8/3, 2014.08.03, 2014年8月3日;
- 3/8/2014, 03-08-14: both day/month/year and month/day/year readings are kept (when ambiguous, a
  match with either counts);
- 25032018, 20180304: day-month-year and year-month-day;
- August 3, 2014, Aug 3 2014, 3 Aug 2014, 28 MAR 18, 02/JAN/2017, 3Aug2014;
- Two-digit year 18 is read as both 2018 and 1918 (the value decides).

Only complete spans match: 12-01-1995 does not yield 12-01-19; spans that are not valid dates
(40.0-75.0) are ignored."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3, "apr": 4, "april": 4,
    "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8, "august": 8, "sep": 9,
    "sept": 9, "september": 9, "oct": 10, "october": 10, "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}  # fmt: skip
_MONTH = "|".join(sorted(MONTHS, key=len, reverse=True))

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # 2014-08-03, 2014/8/3, 2014.08.03, 2014年8月3日
    ("ymd", re.compile(r"(?<![\d.,])(\d{4})\s*([/.\-]|年)\s*(\d{1,2})\s*(?:\2|月)\s*(\d{1,2})(?:\s*日)?(?![\d])")),
    # 3/8/2014, 03-08-14, 5.3.2018 (same separator throughout)
    ("dmy", re.compile(r"(?<![\d.,])(\d{1,2})([/.\-])(\d{1,2})\2(\d{4}|\d{2})(?![\d])")),
    # 25032018, 20180304
    ("digits", re.compile(r"(?<![\d.,])(\d{8})(?![\d])")),
    # 3 Aug 2014, 28 MAR 18, 02/JAN/2017, 3-Aug-2014, 3Aug2014
    ("d_mon_y", re.compile(rf"(?<![\d])(\d{{1,2}})(?:st|nd|rd|th)?[\s/.\-]*({_MONTH})\.?[\s/.\-,]*(\d{{4}}|\d{{2}})(?![\d])", re.IGNORECASE)),
    # August 3, 2014, Aug 3 2014, OCT 3, 2016
    ("mon_d_y", re.compile(rf"(?<![a-z])({_MONTH})\.?\s*(\d{{1,2}})(?:st|nd|rd|th)?\s*,?\s*(\d{{4}})(?![\d])", re.IGNORECASE)),
)  # fmt: skip


def _years(text: str) -> tuple[int, ...]:
    year = int(text)
    return (year,) if len(text) == 4 else (2000 + year, 1900 + year)


def _day(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _readings(kind: str, m: re.Match[str]) -> set[date]:
    found: set[date | None] = set()
    if kind == "ymd":
        found.add(_day(int(m[1]), int(m[3]), int(m[4])))
    elif kind == "dmy":
        a, b = int(m[1]), int(m[3])
        for year in _years(m[4]):
            found |= {_day(year, b, a), _day(year, a, b)}  # day/month/year, month/day/year
    elif kind == "digits":
        t = m[1]
        found |= {
            _day(int(t[4:]), int(t[2:4]), int(t[:2])),
            _day(int(t[:4]), int(t[4:6]), int(t[6:])),
        }
    elif kind == "d_mon_y":
        month = MONTHS[m[2].lower()]
        found |= {_day(year, month, int(m[1])) for year in _years(m[3])}
    else:
        found.add(_day(int(m[3]), MONTHS[m[1].lower()], int(m[2])))
    return {d for d in found if d is not None}


@dataclass(frozen=True)
class DateToken:
    start: int
    end: int
    text: str
    readings: frozenset[date]


def dates_in(text: str) -> list[DateToken]:
    """Each date-like span in the source text with its possible readings, ordered by position."""
    tokens = [
        DateToken(m.start(), m.end(), m[0], frozenset(found))
        for kind, pattern in _PATTERNS
        for m in pattern.finditer(text)
        if (found := _readings(kind, m))
    ]
    return sorted(tokens, key=lambda t: (t.start, -t.end))


def target_of(value: object) -> date | None:
    """Value → date as YYYY-MM-DD. Values copied verbatim by the model (August 3 , 2014) are parsed
    too. None if unparseable."""
    if isinstance(value, date):
        return value
    text = str(value).strip()
    try:
        return date.fromisoformat(text)
    except ValueError:
        tokens = dates_in(text)
        readings = tokens[0].readings if tokens else frozenset()
        return next(iter(readings)) if len(readings) == 1 else None


@dataclass(frozen=True)
class DateMatch:
    """Match location (like re.Match: start(), end(), group())."""

    token: DateToken

    def start(self) -> int:
        return self.token.start

    def end(self) -> int:
        return self.token.end

    def group(self, _: int = 0) -> str:
        return self.token.text


@dataclass(frozen=True)
class DatePattern:
    """Search rule for one date; used like a regular expression: pattern.search(text).

        If the value cannot be parsed as a date (a spelling the parser does not understand), it
    is searched for verbatim (case-insensitive, ignoring whitespace)."""

    value: str
    target: date | None

    def search(self, text: str) -> DateMatch | re.Match[str] | None:
        if self.target is None:
            raw = re.escape(" ".join(self.value.split())).replace(r"\ ", r"\s*")
            return re.search(raw, text, re.IGNORECASE) if raw else None
        for token in dates_in(text):
            if self.target in token.readings:
                return DateMatch(token)
        return None


def date_pattern(value: object) -> DatePattern:
    return DatePattern(str(value), target_of(value))
