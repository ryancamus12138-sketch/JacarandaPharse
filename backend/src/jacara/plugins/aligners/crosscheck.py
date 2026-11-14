"""Cross-check: whether the normalized value given by the model (value) agrees with the source text
it copied (text).

Interpretation is left to the model: August 3 , 2014 → 2014-08-03, $1,234.50 → 1234.5, July 1 -
July 31, 2014 → two dates. This module only re-checks with rules:
- Agree: ok;
- Disagree (source 01/07/2014, a Chinese template, yet the model read 7 January): mismatch,
  flagged for review;
- The source is ambiguous (01/07/2014 with unknown country): unsure, flagged for confirmation;
- The rules cannot parse the notation: unknown, also only flagged; the value is kept. New
  notations never cause values to be dropped."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Literal

from jacara.core.daterange import AMBIGUOUS_PROBLEM, parse_date_range
from jacara.core.i18n import stored
from jacara.core.textvalue import parse_date, parse_number

Verdict = Literal["ok", "mismatch", "unsure", "unknown"]

_NUMERIC_DATE = re.compile(r"(?<![\d.])(\d{1,2})[./-](\d{1,2})[./-](\d{4}|\d{2})(?!\d)")


@dataclass(frozen=True)
class Check:
    verdict: Verdict
    note: str = ""


def _shown(value: object) -> str:
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    return str(value)


def date_readings(text: str, day_first: bool | None) -> set[date]:
    """Possible dates of this source date: 01/07/2014 with unknown country could be 1 July or 7
    January."""
    m = _NUMERIC_DATE.search(text)
    if m:
        a, b, raw_year = int(m[1]), int(m[2]), m[3]
        year = int(raw_year) + 2000 if len(raw_year) == 2 else int(raw_year)
        orders = []
        if day_first is not False:
            orders.append((b, a))  # day/month
        if day_first is not True:
            orders.append((a, b))  # month/day
        found: set[date] = set()
        for month, day in orders:
            try:
                found.add(date(year, month, day))
            except ValueError:
                continue
        return found
    day = parse_date(text)
    return {day} if day else set()


def cross_check(type_: str, value: object, text: str, day_first: bool | None = None) -> Check:
    """Whether value agrees with text. Text fields are not checked."""
    shown = stored("原文「{text}」", text=text)
    if type_ == "number":
        number = parse_number(text)
        if number is None or not isinstance(value, (int, float)):
            return Check(
                "unknown",
                stored(
                    "{shown}系统无法读取数字，模型识别为 {value}，请核对",
                    shown=shown,
                    value=_shown(value),
                ),
            )
        if abs(abs(number) - abs(float(value))) < 0.005:
            return Check("ok")
        dotless = f"{abs(float(value)):.2f}".replace(".", "")
        if re.sub(r"\D", "", text) == dotless:
            return Check(
                "mismatch",
                stored(
                    ("{shown}无小数点，模型识别为 {value}，可能是小数点未被识别，请核对原图"),
                    shown=shown,
                    value=_shown(value),
                ),
            )
        return Check(
            "mismatch",
            stored("{shown}与模型输出的 {value} 不符，请核对", shown=shown, value=_shown(value)),
        )
    if type_ == "date":
        try:
            wanted = date.fromisoformat(str(value))
        except ValueError:
            return Check(
                "unknown",
                stored("{shown}，模型输出的 {value} 不是日期，请核对", shown=shown, value=value),
            )
        readings = date_readings(text, day_first)
        if not readings:
            return Check(
                "unknown",
                stored(
                    "{shown}系统无法读取日期，模型识别为 {wanted}，请核对",
                    shown=shown,
                    wanted=wanted,
                ),
            )
        if wanted not in readings:
            return Check(
                "mismatch",
                stored("{shown}与模型输出的 {wanted} 不符，请核对", shown=shown, wanted=wanted),
            )
        if len(readings) > 1:
            return Check(
                "unsure",
                stored(
                    ("{shown}无法区分日与月（模板未选择国家），模型识别为 {wanted}，请确认"),
                    shown=shown,
                    wanted=wanted,
                ),
            )
        return Check("ok")
    if type_ == "date_range":
        model = parse_date_range(str(value)).range
        page = parse_date_range(text, day_first)
        if model is None:
            return Check(
                "unknown",
                stored(
                    "{shown}，无法从模型输出的 {value} 读取开始和结束日期，请核对",
                    shown=shown,
                    value=value,
                ),
            )
        if page.range is None:
            return Check(
                "unknown",
                stored(
                    "{shown}系统无法读取开始和结束日期，模型识别为 {value}，请核对",
                    shown=shown,
                    value=model.text(),
                ),
            )
        if (page.range.start, page.range.end) != (model.start, model.end):
            if page.problem == AMBIGUOUS_PROBLEM:
                return Check(
                    "unsure",
                    stored(
                        "{shown}无法区分日与月，模型识别为 {value}，请确认",
                        shown=shown,
                        value=model.text(),
                    ),
                )
            return Check(
                "mismatch",
                stored("{shown}与模型输出的 {value} 不符，请核对", shown=shown, value=model.text()),
            )
        if page.problem == AMBIGUOUS_PROBLEM:
            return Check(
                "unsure",
                stored(
                    "{shown}无法区分日与月，模型识别为 {value}，请确认",
                    shown=shown,
                    value=model.text(),
                ),
            )
        return Check("ok")
    return Check("ok")
