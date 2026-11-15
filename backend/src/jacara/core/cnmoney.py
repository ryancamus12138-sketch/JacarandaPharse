"""Convert Chinese uppercase amounts (壹佰贰拾叁圆肆角伍分) to numbers.

The uppercase total on invoices is written inconsistently (圆/元, 整/正, an optional leading ⊗, an
optional 壹 before 拾), so amounts are compared as numbers rather than as text."""

from __future__ import annotations

DIGITS = {"零": 0, "壹": 1, "贰": 2, "叁": 3, "肆": 4, "伍": 5, "陆": 6, "柒": 7, "捌": 8, "玖": 9}
DIGITS |= {"〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7}
DIGITS |= {"八": 8, "九": 9}
SMALL = {"拾": 10, "佰": 100, "仟": 1000, "十": 10, "百": 100, "千": 1000}
IGNORED = set("整正人民币⊗ⓧ×（）() \t　")


def parse_chinese_amount(text: str) -> float | None:
    """Return None when the text cannot be parsed (unknown characters or no amount)."""
    text = "".join(ch for ch in text if ch not in IGNORED).replace("元", "圆")
    if not text:
        return None
    integer, _, fraction = text.partition("圆") if "圆" in text else ("", "", text)
    whole = _integer(integer)
    cents = _fraction(fraction)
    if whole is None or cents is None:
        return None
    return round(whole + cents, 2)


def _integer(text: str) -> int | None:
    total = section = digit = 0
    for ch in text:
        if ch in DIGITS:
            digit = DIGITS[ch]
        elif ch in SMALL:
            section += (digit or 1) * SMALL[ch]
            digit = 0
        elif ch == "万":
            total += (section + digit) * 10_000
            section = digit = 0
        elif ch == "亿":
            total = (total + section + digit) * 100_000_000
            section = digit = 0
        else:
            return None
    return total + section + digit


def _fraction(text: str) -> float | None:
    cents = 0
    digit = 0
    for ch in text:
        if ch in DIGITS:
            digit = DIGITS[ch]
        elif ch == "角":
            cents += digit * 10
            digit = 0
        elif ch == "分":
            cents += digit
            digit = 0
        else:
            return None
    return cents / 100
