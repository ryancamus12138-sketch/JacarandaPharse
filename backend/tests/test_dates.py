"""Locating dates in the source text (core/dates.py): date-like spans are parsed and compared
instead of guessing notations."""

import pytest

from jacara.core.dates import date_pattern, dates_in, target_of


@pytest.mark.parametrize(
    ("value", "text"),
    [
        ("2014-08-03", "2014-08-03"),
        ("2014-08-03", "开票日期：2014年8月3日"),
        ("2014-08-03", "2014 年 08 月 03 日"),
        ("2014-08-03", "2014/8/3"),
        ("2014-08-03", "03/08/2014"),  # day/month/year
        ("2014-08-03", "08/03/2014"),  # month/day/year (both accepted when ambiguous)
        ("2014-08-03", "3.8.14"),  # Two-digit year.
        ("2014-08-03", "August 3 , 2014"),
        ("2014-08-03", "Aug 3rd 2014"),
        ("2014-08-03", "3 Aug 2014"),
        ("2014-08-03", "3-AUG-2014"),
        ("2014-08-03", "3Aug2014"),
        ("2014-09-03", "3 Sept 2014"),
        ("2018-03-28", "28 MAR 18"),
        ("2017-01-02", "Date:02/JAN/2017"),
        ("2018-03-25", "25032018"),
        ("2018-03-04", "20180304"),
        ("2024-05-10", "INV-20240510"),
        ("2018-12-25", "25/12/2018 8:13:39 PM"),
        ("1998-12-25", "25/12/98"),  # A two-digit year may also mean 19xx.
    ],
)
def test_found(value, text):
    assert date_pattern(value).search(text), text


@pytest.mark.parametrize(
    ("value", "text"),
    [
        ("2019-01-12", "12-01-1995"),  # No substring of a longer date.
        ("2014-08-03", "2014-08-30"),
        ("2014-08-03", "40.0-75.0"),  # Reference ranges are not dates.
        ("2014-08-03", "1234567890"),
        ("2018-03-18", "18/03/2019"),
    ],
)
def test_not_found(value, text):
    assert date_pattern(value).search(text) is None


def test_span_points_at_the_date():
    found = date_pattern("2018-12-25").search("Date: 25/12/2018 8:13:39 PM")
    assert found and (found.start(), found.end(), found.group()) == (6, 16, "25/12/2018")


def test_value_written_as_on_the_document():
    """The model copied the source text (answers from before text was separate): parse it as a date
    if possible, otherwise search verbatim."""
    assert target_of("August 3 , 2014").isoformat() == "2014-08-03"  # type: ignore[union-attr]
    assert date_pattern("August 3 , 2014").search("Date: 03/08/2014")
    assert target_of("01/07/2014") is None  # Ambiguous date.
    assert date_pattern("01/07/2014").search("on 01/07/2014.")


def test_invalid_dates_are_ignored():
    assert dates_in("31/02/2019 和 2019-13-01") == []
