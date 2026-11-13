"""交叉检查（ADR 0040）：模型给的标准值（value）和它抄的原文（text）对不对得上。

理解交给模型：August 3 , 2014 → 2014-08-03、$1,234.50 → 1234.5、July 1 - July 31, 2014 → 两个日期。
这里只用规则复查一遍：
- 对得上：ok；
- 对不上（原文 01/07/2014，模板是中国的，模型却读成 1 月 7 日）：mismatch，标黄让人看；
- 原文分不清（01/07/2014 又不知道国家）：unsure，标黄让人确认；
- 规则读不出这种写法：unknown，也只是标黄，值照样留着。新写法不会让值被丢掉。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Literal

from jacara.core.daterange import parse_date_range
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
    """原文这个日期可能是哪几天：01/07/2014 不知道国家时是 7 月 1 日或 1 月 7 日两种。"""
    m = _NUMERIC_DATE.search(text)
    if m:
        a, b, raw_year = int(m[1]), int(m[2]), m[3]
        year = int(raw_year) + 2000 if len(raw_year) == 2 else int(raw_year)
        orders = []
        if day_first is not False:
            orders.append((b, a))  # 日/月
        if day_first is not True:
            orders.append((a, b))  # 月/日
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
    """value 和 text 对不对得上。文字字段不用查。"""
    shown = f"原文「{text}」"
    if type_ == "number":
        number = parse_number(text)
        if number is None or not isinstance(value, (int, float)):
            return Check("unknown", f"{shown}系统读不出数字，模型读成 {_shown(value)}，请看一眼")
        if abs(abs(number) - abs(float(value))) < 0.005:
            return Check("ok")
        dotless = f"{abs(float(value)):.2f}".replace(".", "")
        if re.sub(r"\D", "", text) == dotless:
            return Check(
                "mismatch",
                f"{shown}没有小数点，模型读成 {_shown(value)}，多半是小数点没认出来，请核对原图",
            )
        return Check("mismatch", f"{shown}和模型给的 {_shown(value)} 对不上，请核对")
    if type_ == "date":
        try:
            wanted = date.fromisoformat(str(value))
        except ValueError:
            return Check("unknown", f"{shown}，模型给的 {value} 不是日期，请核对")
        readings = date_readings(text, day_first)
        if not readings:
            return Check("unknown", f"{shown}系统读不出日期，模型读成 {wanted}，请看一眼")
        if wanted not in readings:
            return Check("mismatch", f"{shown}和模型给的 {wanted} 对不上，请核对")
        if len(readings) > 1:
            return Check(
                "unsure",
                f"{shown}分不清哪个是日、哪个是月（模板没选国家），模型读成 {wanted}，请确认",
            )
        return Check("ok")
    if type_ == "date_range":
        model = parse_date_range(str(value)).range
        page = parse_date_range(text, day_first)
        if model is None:
            return Check("unknown", f"{shown}，模型给的 {value} 读不出开始和结束，请核对")
        if page.range is None:
            return Check(
                "unknown", f"{shown}系统读不出开始和结束，模型读成 {model.text()}，请看一眼"
            )
        if (page.range.start, page.range.end) != (model.start, model.end):
            if page.problem and "分不清" in page.problem:
                return Check("unsure", f"{shown}分不清日月，模型读成 {model.text()}，请确认")
            return Check("mismatch", f"{shown}和模型给的 {model.text()} 对不上，请核对")
        if page.problem and "分不清" in page.problem:
            return Check("unsure", f"{shown}分不清日月，模型读成 {model.text()}，请确认")
        return Check("ok")
    return Check("ok")
