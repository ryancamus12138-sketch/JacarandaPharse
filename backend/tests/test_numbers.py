"""Locating numbers in the source text (core/numbers.py): numbers are parsed and compared
numerically instead of guessing notations."""

import pytest

from jacara.core.numbers import number_pattern, numbers_in, readings


@pytest.mark.parametrize(
    ("value", "text"),
    [
        # One decimal place on lab reports (previously only 78.50 was searched).
        (78.5, "NEUT% 78.5 ↑"),
        (78.5, "78.50"),  # Two decimal places written.
        (0.9, "EO% 0.9"),
        (1.234, "单价 1.234"),  # Three decimal places (previously only 1.23 was searched).
        (0.123456789, "0.123456789"),  # More digits work too.
        (1234.5, "RM 1,234.50"),  # Thousands separators.
        (1234.56, "1.234,56 €"),  # European notation: period for thousands, comma for decimals.
        (12.5, "12,5 %"),  # European decimal comma.
        (1234567.0, "1,234,567.00"),
        (6.0, "SUMMERINA 6.00-"),  # Discounts: sign ignored.
        (-6.0, "(6.00)"),
        (9.0, "Total :  9.00"),
        (9.0, "9.0"),
        (9.0, "9.00,"),  # A trailing comma is not part of the number.
    ],
)
def test_found(value, text):
    assert number_pattern(value, loose=False).search(text)


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (9.0, "19.00"),  # Only complete numbers; no substrings.
        (9.0, "9.000"),  # 9 does not match 9.000 (often a weight or quantity).
        (0.9, "0.95"),
        (78.5, "178.5"),
        (55.0, "NO.55"),  # Immediately preceded by a period: not a standalone number.
        (25.0, "25.12.2018"),  # Dates are not numbers.
        (7.0, "007"),  # Leading zero: an identifier.
        (9.0, "9"),  # Integers match only with loose.
    ],
)
def test_not_found(value, text):
    assert number_pattern(value, loose=False).search(text) is None


def test_whole_numbers_only_when_loose():
    assert number_pattern(9.0, loose=True).search("Qty 9")
    assert number_pattern(1234.0, loose=True).search("1,234")
    assert number_pattern(1234.0, loose=True).search("1.234")  # European thousands separator.
    assert not number_pattern(9.5, loose=True).search("9")


def test_span_points_at_the_number():
    found = number_pattern(78.5, loose=False).search("中性粒细胞 78.5 ↑")
    assert found and (found.start(), found.end(), found.group()) == (6, 10, "78.5")


def test_unclear_separators_keep_both_readings():
    assert {str(r.value) for r in readings("1,234")} == {"1.234", "1234"}
    assert {str(r.value) for r in readings("12,50")} == {"12.50"}
    assert readings("1.234.56,7") == ()
    assert [t.text for t in numbers_in("3.5-9.5 10^9/L")] == ["3.5", "9.5", "10", "9"]


def test_float_noise_does_not_matter():
    assert number_pattern(0.1 + 0.2, loose=False).search("0.30000000000000004")
    assert number_pattern(0.3, loose=False).search("0.30")
