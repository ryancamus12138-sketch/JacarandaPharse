"""Locate a number in the source text: parse every number in the text and compare numerically,
instead of generating candidate spellings of the value and searching for them.

Generating spellings of 78.5 (78.50, 1,234.50, …) misses any spelling not generated (one decimal
place, three decimal places, European 1.234,56). The approach is inverted: every number in the
source text is cut out as written and parsed to an exact decimal; a match is a number equal to
the value.

- Only complete numbers are cut: 19.00 does not yield 9.00, and 9.000 is a single number;
- When a comma or period may be either a thousands separator or a decimal point (1,234, 1.234,
  12,5), all readings are kept and a match with any of them counts; when both appear, the last
  one is the decimal point (1,234.56, 1.234,56);
- Integers (9, 1,234) collide too easily with quantities and identifiers and are accepted only
  with loose=True (the line indicated by the model);
- Numbers with more decimal places than the value needs, and more than two, do not match (an
  amount of 9 does not match 9.000, which is often a weight or quantity);
- Signs are ignored: a discount written as 6.00-, -6.00 or (6.00) is matched on the digits (the
  sign is checked separately)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

_TOKEN = re.compile(r"(?<![\d.,])\d+(?:[.,]\d+)*(?!\d)")
"""A number in the source text: digit groups separated by single commas or periods. A match
immediately preceded by a digit, comma or period is not the start of a number."""


@dataclass(frozen=True)
class Reading:
    value: Decimal
    places: int
    """Number of decimal places in this reading (0 for integers or when every comma and period is a
    thousands separator)."""


@dataclass(frozen=True)
class NumberToken:
    start: int
    end: int
    text: str
    readings: tuple[Reading, ...]


def readings(text: str) -> tuple[Reading, ...]:
    """Possible readings of a digit sequence (1,234.56, 12,5, 78.5). Empty if it is not a number
    (25.12.2018)."""
    groups = re.split(r"[.,]", text)
    seps = re.findall(r"[.,]", text)
    if len(groups[0]) > 1 and groups[0].startswith("0"):
        return ()  # 007, 0123: identifiers, not numbers.
    if not seps:
        return (Reading(Decimal(text), 0),)
    found: list[Reading] = []
    if len(set(seps)) == 2:
        # Both present: the last one is the decimal point and all earlier ones must be the other
        # (thousands separator).
        point = seps[-1]
        if point in seps[:-1] or any(len(g) != 3 for g in groups[1:-1]) or len(groups[0]) > 3:
            return ()
        found.append(Reading(Decimal("".join(groups[:-1]) + "." + groups[-1]), len(groups[-1])))
        return tuple(found)
    if len(groups) == 2:
        whole, part = groups
        # Decimal point (12,5 is European notation).
        found.append(Reading(Decimal(f"{whole}.{part}"), len(part)))
    if all(len(g) == 3 for g in groups[1:]) and 1 <= len(groups[0]) <= 3:
        # Thousands separator (1,234, 1.234.567).
        found.append(Reading(Decimal("".join(groups)), 0))
    return tuple(found)


def numbers_in(text: str) -> list[NumberToken]:
    """Every number in the source text with its position."""
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
    """Minimum number of decimal places for this value: 78.5 → 1, 9 → 0, 0.123 → 3."""
    exponent = number.normalize().as_tuple().exponent
    return max(0, -exponent) if isinstance(exponent, int) else 0


def matches(token: NumberToken, value: float | int | str | Decimal, *, loose: bool) -> bool:
    """Whether this number in the source text equals value."""
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
    """Match location, like re.Match with start(), end() and group(); the evidence box is cut from
    it."""

    token: NumberToken

    def start(self) -> int:
        return self.token.start

    def end(self) -> int:
        return self.token.end

    def group(self, _: int = 0) -> str:
        return self.token.text


@dataclass(frozen=True)
class NumberPattern:
    """Search rule for one value; used like a regular expression: pattern.search(text)."""

    value: float | int | str | Decimal
    loose: bool = True

    def search(self, text: str) -> NumberMatch | None:
        for token in numbers_in(text):
            if matches(token, self.value, loose=self.loose):
                return NumberMatch(token)
        return None


def number_pattern(value: float | int | str | Decimal, *, loose: bool) -> NumberPattern:
    """Rule for locating value in the source text. With loose=True, integers (9, 1,234) also
    match."""
    return NumberPattern(value, loose)
