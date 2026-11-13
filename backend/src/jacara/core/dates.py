# ruff: noqa: E501
"""在原文里找一个日期：把原文里像日期的写法一个个读成日期，再和值比（不再把值猜成各种写法去搜）。

和数字一样（core/numbers.py）：以前把 2014-08-03 猜成 03/08/2014、August 3, 2014 这些写法去搜，
猜漏一种就找不到出处。现在原文里每一段像日期的字都读一遍：

- 2014-08-03、2014/8/3、2014.08.03、2014年8月3日；
- 3/8/2014、03-08-14：日/月/年和月/日/年两种读法都留着（说不准时值等于其中一种就算）；
- 25032018、20180304：日月年和年月日；
- August 3, 2014、Aug 3 2014、3 Aug 2014、28 MAR 18、02/JAN/2017、3Aug2014；
- 两位年份 18 读成 2018，也读成 1918（看值是哪一年）。

只认完整的一段：12-01-1995 不会读出 12-01-19；读不成真实日期的（40.0-75.0）不算。
"""

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
    # 2014-08-03、2014/8/3、2014.08.03、2014年8月3日
    ("ymd", re.compile(r"(?<![\d.,])(\d{4})\s*([/.\-]|年)\s*(\d{1,2})\s*(?:\2|月)\s*(\d{1,2})(?:\s*日)?(?![\d])")),
    # 3/8/2014、03-08-14、5.3.2018（同一种分隔符）
    ("dmy", re.compile(r"(?<![\d.,])(\d{1,2})([/.\-])(\d{1,2})\2(\d{4}|\d{2})(?![\d])")),
    # 25032018、20180304
    ("digits", re.compile(r"(?<![\d.,])(\d{8})(?![\d])")),
    # 3 Aug 2014、28 MAR 18、02/JAN/2017、3-Aug-2014、3Aug2014
    ("d_mon_y", re.compile(rf"(?<![\d])(\d{{1,2}})(?:st|nd|rd|th)?[\s/.\-]*({_MONTH})\.?[\s/.\-,]*(\d{{4}}|\d{{2}})(?![\d])", re.IGNORECASE)),
    # August 3, 2014、Aug 3 2014、OCT 3, 2016
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
            found |= {_day(year, b, a), _day(year, a, b)}  # 日/月/年、月/日/年
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
    """原文里每一段像日期的字和它可能的读法，按位置排好。"""
    tokens = [
        DateToken(m.start(), m.end(), m[0], frozenset(found))
        for kind, pattern in _PATTERNS
        for m in pattern.finditer(text)
        if (found := _readings(kind, m))
    ]
    return sorted(tokens, key=lambda t: (t.start, -t.end))


def target_of(value: object) -> date | None:
    """值 → 日期：YYYY-MM-DD；模型照抄了原文的（August 3 , 2014）也读一下。读不出是 None。"""
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
    """找到的位置（和 re.Match 一样有 start()、end()、group()）。"""

    token: DateToken

    def start(self) -> int:
        return self.token.start

    def end(self) -> int:
        return self.token.end

    def group(self, _: int = 0) -> str:
        return self.token.text


@dataclass(frozen=True)
class DatePattern:
    """一个日期的查找规则：用法和正则一样，pattern.search(text)。

    值读不成日期时（模型照抄的写法读不出），按原样（不分大小写、不管空格）找。
    """

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
