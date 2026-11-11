"""从原文里的一段文字读出数字或日期。

审核时"点原文填值"：点的是原图上的文字（"RM 1,234.50"、"25/12/2018 8:13 PM"），
存进去要变成和模型抽出的值一样的格式（1234.5、2018-12-25）。评测打分也用这里的日期读法。
"""

from __future__ import annotations

import re
from datetime import date

_MONTH_NAMES = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
_MONTHS = {m: i for i, m in enumerate(_MONTH_NAMES, 1)}

_NUMBER = re.compile(r"(\(?)(-?)(\d[\d,]*(?:\.\d+)?|\.\d+)(\)?)(-?)")


def parse_number(text: str) -> float | None:
    """文字里的金额：取最后一个数字（"1x 9.99 9.99" → 9.99，"RM 1,234.50" → 1234.5）。

    折扣的写法 "6.00-"、"-6.00"、"(6.00)" 都读成 -6.0。读不出数字返回 None。
    """
    found = list(_NUMBER.finditer(text))
    if not found:
        return None
    m = found[-1]
    number = float(m[3].replace(",", ""))
    negative = m[2] == "-" or m[5] == "-" or (m[1] == "(" and m[4] == ")")
    return -number if negative else number


def parse_date(text: str) -> date | None:
    """日期的写法很多：25/12/2018、15-01-19、02 MAR 2018、OCT 3, 2016、20180304、2018-12-25。

    一般是 日/月/年；月份大于 12 时说明是 月/日/年，就对调。读不出返回 None。
    """
    t = text.strip().upper()
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
