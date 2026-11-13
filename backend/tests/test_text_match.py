"""证据对齐：值能不能在原文里找到，找不到要标出来。"""

import pytest

from jacara.core.dates import date_pattern
from jacara.core.numbers import number_pattern
from jacara.plugins.aligners import TextMatchAligner
from jacara.plugins.extractors import ExtractedField, ExtractionResult


def align(page, *fields):
    result = ExtractionResult(tuple(fields), "", "test", "test", "test", 0)
    return {a.field.name: a for a in TextMatchAligner().align(page, result)}


def test_common_date_spellings_are_found():
    for text in ("03/08/2014", "August 3, 2014", "3 Aug 2014", "2014年8月3日"):
        assert date_pattern("2014-08-03").search(text), text


@pytest.mark.parametrize(
    ("iso", "text"),
    [
        ("2018-03-05", "DATE: 5/3/2018"),
        ("2018-01-17", "17/1/2018 13:02"),
        ("2018-03-28", "28 MAR 18"),
        ("2017-01-02", "Date:02/JAN/2017"),
        ("2018-03-25", "25032018"),
        ("2018-03-04", "20180304"),
    ],
)
def test_more_date_spellings_found_by_eval(iso, text):
    """评测里抽对了却找不到出处的日期写法（docs/eval/README.md 全量基线）。"""
    assert date_pattern(iso).search(text)


def test_two_digit_year_is_found_but_not_inside_a_longer_date():
    """评测发现：收据上常写两位年份（18/03/18）。但 12-01-19 不能匹配到 12-01-1995 的前半截。"""
    pattern = date_pattern("2018-03-18")
    assert pattern.search("DATE: 18/03/18 10:21")
    assert not date_pattern("2019-01-12").search("12-01-1995")


@pytest.mark.parametrize(
    ("text", "found"),
    [
        ("Total :  9.00", True),
        ("19.00", False),  # 前面紧挨着数字
        ("9.000", False),  # 后面紧挨着数字
        ("9.00,", True),  # 句尾的逗号不算
    ],
)
def test_number_pattern_boundaries(text, found):
    pattern = number_pattern(9.0, loose=False)
    assert bool(pattern.search(text)) is found


def test_values_that_are_in_the_text_have_evidence(receipt_page):
    result = align(
        receipt_page,
        ExtractedField("merchant", "string", "BOOK TA _K (TAMAN DAYA) SDN BHD", 1),
        ExtractedField("date", "date", "2018-12-25", 4),
        ExtractedField("total", "number", 9.0, 6),
    )
    assert all(a.status == "matched" and not a.needs_review for a in result.values())
    assert result["date"].evidence is not None
    assert result["date"].evidence.row == 4
    assert result["total"].evidence is not None
    assert result["total"].evidence.row == 6  # 原文有好几个 9.00，用模型说的那一行


def test_made_up_value_is_not_found(receipt_page):
    result = align(receipt_page, ExtractedField("total", "number", 19.99, 6))
    assert result["total"].status == "not_found"
    assert result["total"].needs_review


def test_change_one_does_not_match_quantity_one(receipt_page):
    # 真实的找零是 1.00，但 OCR 读成了 100；不能把"1 PC"里的 1 当成证据
    result = align(receipt_page, ExtractedField("change", "number", 1.0, 8))
    assert result["change"].status == "not_found"


def test_rewritten_text_is_fuzzy(receipt_page):
    result = align(receipt_page, ExtractedField("merchant", "string", "BOOK TA.K (TAMAN DAYA)", 1))
    assert result["merchant"].status == "fuzzy"
    assert result["merchant"].needs_review


def test_wrong_row_is_found_but_needs_review(receipt_page):
    result = align(receipt_page, ExtractedField("cash", "number", 10.0, 1))
    assert result["cash"].status == "matched"
    assert result["cash"].needs_review
    assert "实际在第" in result["cash"].note


def test_address_across_rows(receipt_page):
    address = "NO.55 55,57 & 59, JALAN SAGU 18, TAMAN DAYA,"
    result = align(receipt_page, ExtractedField("address", "string", address, 2))
    assert result["address"].status == "matched"


def test_low_ocr_confidence_needs_review(receipt_page):
    result = align(receipt_page, ExtractedField("note", "string", "引物出门", 9))
    assert result["note"].status == "matched"
    assert result["note"].needs_review


def test_empty_value(receipt_page):
    result = align(receipt_page, ExtractedField("member", "string", None, None))
    assert result["member"].status == "empty"
    assert not result["member"].needs_review
