"""证据对齐：值能不能在原文里找到，找不到要标出来。"""

import pytest

from jacara.plugins.aligners import TextMatchAligner
from jacara.plugins.aligners.text_match import _exact_pattern, date_variants, number_variants
from jacara.plugins.extractors import ExtractedField, ExtractionResult


def align(page, *fields):
    result = ExtractionResult(tuple(fields), "", "test", "test", "test", 0)
    return {a.field.name: a for a in TextMatchAligner().align(page, result)}


def test_number_variants():
    assert number_variants(1234.5, loose=False) == ["1234.50", "1,234.50"]
    assert "9" in number_variants(9.0, loose=True)
    assert "9" not in number_variants(9.0, loose=False)


def test_date_variants_include_common_spellings():
    variants = date_variants("2014-08-03")
    for expected in ("03/08/2014", "August 3, 2014", "3 Aug 2014", "2014年8月3日"):
        assert expected in variants


def test_two_digit_year_is_found_but_not_inside_a_longer_date():
    """评测发现：收据上常写两位年份（18/03/18）。但 12-01-19 不能匹配到 12-01-1995 的前半截。"""
    pattern = _exact_pattern(date_variants("2018-03-18"))
    assert pattern.search("DATE: 18/03/18 10:21")
    assert not _exact_pattern(date_variants("2019-01-12")).search("12-01-1995")


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
    pattern = _exact_pattern(number_variants(9.0, loose=False))
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
