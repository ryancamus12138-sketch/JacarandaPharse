"""Date ranges: the model copies the source text verbatim ("July 1 - July 31 , 2014"); this module
splits it into start and end dates.

Splitting is done in code rather than by the model: a value rewritten by the model cannot be
located in the source text for verification, while code is deterministic.
Supported forms:
- Year written once: July 1 - July 31, 2014; July 1-31, 2014; 7月1日至31日
- Across years: Dec 28, 2014 - Jan 3, 2015; Dec 28 - Jan 3, 2015 (the start takes the previous
  year)
- Chinese: 2014年7月1日至2014年7月31日, 2014年7月1日至7月31日
- Numeric: 2014.07.01-2014.07.31, 01/07/2014 ~ 31/07/2014, 7/1 - 7/31/2014
- Separators: - – — ~ ～ 至 到 to through thru until till
Whether 01/07/2014 is 7 January or 1 July follows the template country (month/day in the United
States and some others, day/month elsewhere). When the country is unknown and both numbers are 12
or less, the order is not guessed and the result is reported as ambiguous."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from itertools import pairwise

from jacara.core.i18n import N_

MONTH_FIRST = frozenset({"US", "PH", "FM", "MH", "PW"})
"""Countries and regions that write dates as month/day/year."""

_MONTHS = {
    name: number
    for number, names in enumerate(
        (
            ("jan", "january"),
            ("feb", "february"),
            ("mar", "march"),
            ("apr", "april"),
            ("may",),
            ("jun", "june"),
            ("jul", "july"),
            ("aug", "august"),
            ("sep", "sept", "september"),
            ("oct", "october"),
            ("nov", "november"),
            ("dec", "december"),
        ),
        start=1,
    )
    for name in names
}

_SUFFIX = r"(?:st|nd|rd|th)?"
_PIECE = re.compile(
    "|".join(
        (
            r"(?P<ymd>(?P<y1>\d{4})\s*[年./-]\s*(?P<m1>\d{1,2})\s*[月./-]\s*(?P<d1>\d{1,2})\s*[日号]?)",
            r"(?P<cn>(?:(?P<y2>\d{4})\s*年\s*)?(?P<m2>\d{1,2})\s*月\s*(?P<d2>\d{1,2})\s*[日号])",
            r"(?P<num>(?<![\d.])(?P<a3>\d{1,2})[./-](?P<b3>\d{1,2})[./-](?P<y3>\d{4}|\d{2})(?![\d]))",
            rf"(?P<md>(?P<mon4>[A-Za-z]{{3,9}})\.?\s+(?P<d4>\d{{1,2}}){_SUFFIX}(?![\d:])(?:\s*,?\s*(?P<y4>\d{{4}}))?)",
            rf"(?P<dm>(?<![\d.])(?P<d5>\d{{1,2}}){_SUFFIX}[\s-]+(?P<mon5>[A-Za-z]{{3,9}})\.?(?:[\s,-]+(?P<y5>\d{{4}}))?)",
            r"(?P<short>(?<![\d./-])(?P<a6>\d{1,2})[./](?P<b6>\d{1,2})(?![\d./]))",
            r"(?P<cnday>(?<!\d)(?P<d7>\d{1,2})\s*[日号])",
        )
    ),
    re.IGNORECASE,
)
_SEP = re.compile(r"^\s*(?:-|–|—|~|～|至|到|to|through|thru|until|till)\s*$", re.IGNORECASE)
_BARE_END = re.compile(
    r"^\s*(?:-|–|—|~|～|至|到|to|through|thru|until|till)\s*(?P<d>\d{1,2})(?![\d./:])"
    r"(?:\s*,?\s*(?P<y>\d{4}))?",
    re.IGNORECASE,
)


ORDER_PROBLEM = N_("结束日期早于开始日期")
AMBIGUOUS_PROBLEM = N_("数字日期分不清哪个是日、哪个是月（模板没选国家），先按 日/月 读的")


@dataclass(frozen=True)
class DateRange:
    start: date | None
    end: date | None

    @property
    def days(self) -> int | None:
        """Number of days, inclusive (1 July to 31 July is 31 days)."""
        if self.start is None or self.end is None:
            return None
        return (self.end - self.start).days + 1

    def text(self, separator: str = " ~ ") -> str:
        """Normalized form: 2014-07-01 ~ 2014-07-31."""
        start = self.start.isoformat() if self.start else ""
        end = self.end.isoformat() if self.end else ""
        return f"{start}{separator}{end}".strip()


@dataclass(frozen=True)
class Parsed:
    range: DateRange | None
    problem: str | None = None
    """Reason the text could not be split or is uncertain; None when there is no problem."""


@dataclass
class _Part:
    year: int | None
    month: int | None
    day: int
    unsure: bool = False
    """Both day and month are 12 or less and the country is unknown: the month cannot be determined.
    """
    pair: tuple[int, int] | None = None
    """The two numbers of a numeric date, in written order; None for non-numeric forms."""
    day_first: bool | None = None
    """Whether this numeric date is known to be day/month (True) or month/day (False); None when
    uncertain."""


def day_first_for(countries: Iterable[str]) -> bool | None:
    """Template country → whether numeric dates are day/month/year. None (unknown) for generic
    templates or countries that use both."""
    codes = {c.upper() for c in countries}
    if not codes:
        return None
    month_first = codes & MONTH_FIRST
    if not month_first:
        return True
    return False if month_first == codes else None


def _year(text: str | None) -> int | None:
    if not text:
        return None
    value = int(text)
    return value + 2000 if value < 100 else value


def _month(name: str | None) -> int | None:
    return _MONTHS.get((name or "").lower().rstrip("."))


def _numbers(a: int, b: int, year: int | None, day_first: bool | None) -> _Part:
    """Two numbers, one the day and the other the month."""
    if day_first is True or a > 12:
        return _Part(year, b, a, pair=(a, b), day_first=True)
    if day_first is False or b > 12:
        return _Part(year, a, b, pair=(a, b), day_first=False)
    if a == b:
        return _Part(year, a, b, pair=(a, b), day_first=True)
    return _Part(year, b, a, unsure=True, pair=(a, b))  # Assume day/month, but mark as uncertain.


def _part(m: re.Match[str], day_first: bool | None) -> _Part | None:
    g = m.groupdict()
    if g["ymd"]:
        return _Part(int(g["y1"]), int(g["m1"]), int(g["d1"]))
    if g["cn"]:
        return _Part(_year(g["y2"]), int(g["m2"]), int(g["d2"]))
    if g["num"]:
        return _numbers(int(g["a3"]), int(g["b3"]), _year(g["y3"]), day_first)
    if g["md"]:
        month = _month(g["mon4"])
        return _Part(_year(g["y4"]), month, int(g["d4"])) if month else None
    if g["dm"]:
        month = _month(g["mon5"])
        return _Part(_year(g["y5"]), month, int(g["d5"])) if month else None
    if g["short"]:
        return _numbers(int(g["a6"]), int(g["b6"]), None, day_first)
    return _Part(None, None, int(g["d7"]))


def _pieces(text: str, day_first: bool | None) -> list[tuple[re.Match[str], _Part]]:
    found = []
    for m in _PIECE.finditer(text):
        part = _part(m, day_first)
        if part is not None:
            found.append((m, part))
    return found


def parse_date_range(text: str, day_first: bool | None = None) -> Parsed:
    """Source text → start and end dates. day_first: whether numeric dates are day/month/year (None
    if unknown)."""
    source = " ".join(str(text or "").split())
    if not source:
        return Parsed(None, N_("是空的"))
    pieces = _pieces(source, day_first)
    start = end = None
    for (m1, p1), (m2, p2) in pairwise(pieces):
        if _SEP.match(source[m1.end() : m2.start()]):
            start, end = p1, p2
            break
    if start is None and pieces:
        first, part = pieces[0]
        bare = _BARE_END.match(source[first.end() :])
        if bare:  # July 1-31, 2014: only the day is given for the end.
            start, end = part, _Part(_year(bare["y"]), None, int(bare["d"]))
    if start is None or end is None:
        if len(pieces) == 1 and pieces[0][1].year and pieces[0][1].month:
            only = pieces[0][1]
            try:
                single = date(only.year or 0, only.month or 0, only.day)
            except ValueError:
                return Parsed(None, N_("日期不对"))
            return Parsed(DateRange(None, single), N_("只找到一个日期，当作结束日期"))
        return Parsed(None, N_("认不出开始和结束日期"))
    return _combine(start, end)


def _combine(start: _Part, end: _Part) -> Parsed:
    # If one side determines the order (31/07/2014), read the other side (01/07/2014) the same way:
    # both ends of a range use the same format.
    for unsure, sure in ((start, end), (end, start)):
        if unsure.unsure and unsure.pair and sure.pair and sure.day_first is not None:
            a, b = unsure.pair
            unsure.day, unsure.month = (a, b) if sure.day_first else (b, a)
            unsure.unsure = False
    borrowed_year = start.year is None
    if end.month is None:
        end.month = start.month
    if start.month is None:
        start.month = end.month
    if start.year is None:
        start.year = end.year
    if end.year is None:
        end.year = start.year
    if start.year is None or start.month is None or end.month is None:
        return Parsed(None, N_("原文里没有年份") if start.year is None else N_("认不出月份"))
    try:
        first = date(start.year, start.month, start.day)
        last = date(end.year or start.year, end.month, end.day)
    except ValueError:
        return Parsed(None, N_("日期不对（没有这一天）"))
    if first > last and borrowed_year:
        try:
            # Dec 28 - Jan 3, 2015: the start is in the previous year.
            first = first.replace(year=first.year - 1)
        except ValueError:
            return Parsed(None, N_("日期不对（没有这一天）"))
    parsed = DateRange(first, last)
    if first > last:
        return Parsed(parsed, ORDER_PROBLEM)
    if start.unsure or end.unsure:
        return Parsed(parsed, AMBIGUOUS_PROBLEM)
    return Parsed(parsed)
