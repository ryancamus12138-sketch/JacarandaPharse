"""在原文里找一个数：把原文里的数一个个读出来，按数值比（不再把值猜成各种写法去搜）。

以前把 78.5 猜成 78.50、1,234.50 这几种写法去原文里搜，猜漏一种（一位小数、三位小数、
欧洲的 1.234,56）就找不到出处。现在倒过来：原文里每个数原样切出来、读成精确的十进制数，
和值相等就算找到。

- 只切完整的数：19.00 里不会切出 9.00，9.000 也是一整个数；
- 逗号、句号是千分位还是小数点说不准时（1,234、1.234、12,5），几种读法都留着，值等于其中一种就算；
  两种都出现时最后一个是小数点（1,234.56、1.234,56）；
- 写成整数的（9、1,234）太容易撞上数量、编号，只在 loose=True（模型指定的那一行）时才认；
- 小数位数比值需要的多、又超过两位的不算（金额 9 不认 9.000：那常常是重量、数量）；
- 不管正负号：折扣写成 6.00-、-6.00、(6.00)，找数字本身就行（正负号另外核对）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

_TOKEN = re.compile(r"(?<![\d.,])\d+(?:[.,]\d+)*(?!\d)")
"""原文里的一个数：几段数字，中间只隔一个逗号或句号。前面紧挨着数字、逗号、句号的不算开头。"""


@dataclass(frozen=True)
class Reading:
    value: Decimal
    places: int
    """这种读法写了几位小数（写成整数、或者逗号句号都是千分位的是 0）。"""


@dataclass(frozen=True)
class NumberToken:
    start: int
    end: int
    text: str
    readings: tuple[Reading, ...]


def readings(text: str) -> tuple[Reading, ...]:
    """一段数字（1,234.56、12,5、78.5）可能的读法。读不成数（25.12.2018）是空的。"""
    groups = re.split(r"[.,]", text)
    seps = re.findall(r"[.,]", text)
    if len(groups[0]) > 1 and groups[0].startswith("0"):
        return ()  # 007、0123：编号，不是数
    if not seps:
        return (Reading(Decimal(text), 0),)
    found: list[Reading] = []
    if len(set(seps)) == 2:
        # 两种都有：最后一个是小数点，前面的都得是另一种（千分位）
        point = seps[-1]
        if point in seps[:-1] or any(len(g) != 3 for g in groups[1:-1]) or len(groups[0]) > 3:
            return ()
        found.append(Reading(Decimal("".join(groups[:-1]) + "." + groups[-1]), len(groups[-1])))
        return tuple(found)
    if len(groups) == 2:
        whole, part = groups
        found.append(Reading(Decimal(f"{whole}.{part}"), len(part)))  # 小数点（12,5 是欧洲写法）
    if all(len(g) == 3 for g in groups[1:]) and 1 <= len(groups[0]) <= 3:
        found.append(Reading(Decimal("".join(groups)), 0))  # 千分位（1,234、1.234.567）
    return tuple(found)


def numbers_in(text: str) -> list[NumberToken]:
    """原文里的每个数，和它在文字里的位置。"""
    tokens: list[NumberToken] = []
    for m in _TOKEN.finditer(text):
        found = readings(m[0])
        if found:
            tokens.append(NumberToken(m.start(), m.end(), m[0], found))
    return tokens


def _decimal(value: float | int | str | Decimal) -> Decimal | None:
    try:
        text = repr(value) if isinstance(value, float) else str(value)
        number = value if isinstance(value, Decimal) else Decimal(text)
    except InvalidOperation:
        return None
    return abs(number) if number.is_finite() else None


def _places(number: Decimal) -> int:
    """这个数至少要几位小数：78.5 → 1，9 → 0，0.123 → 3。"""
    exponent = number.normalize().as_tuple().exponent
    return max(0, -exponent) if isinstance(exponent, int) else 0


def matches(token: NumberToken, value: float | int | str | Decimal, *, loose: bool) -> bool:
    """原文里的这个数是不是 value。"""
    target = _decimal(value)
    if target is None:
        return False
    needed = _places(target)
    for reading in token.readings:
        if reading.value != target:
            continue
        if reading.places == 0:
            if loose and needed == 0:
                return True
        elif reading.places <= max(2, needed):
            return True
    return False


@dataclass(frozen=True)
class NumberMatch:
    """找到的位置，和 re.Match 一样有 start()、end()、group()，出处框按它截。"""

    token: NumberToken

    def start(self) -> int:
        return self.token.start

    def end(self) -> int:
        return self.token.end

    def group(self, _: int = 0) -> str:
        return self.token.text


@dataclass(frozen=True)
class NumberPattern:
    """一个值的查找规则：用法和正则一样，pattern.search(text)。"""

    value: float | int | str | Decimal
    loose: bool = True

    def search(self, text: str) -> NumberMatch | None:
        for token in numbers_in(text):
            if matches(token, self.value, loose=self.loose):
                return NumberMatch(token)
        return None


def number_pattern(value: float | int | str | Decimal, *, loose: bool) -> NumberPattern:
    """在原文里找 value 的规则。loose=True 时写成整数的（9、1,234）也算。"""
    return NumberPattern(value, loose)
