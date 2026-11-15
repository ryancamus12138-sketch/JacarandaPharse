"""Rule validation: report mismatches between numbers; skip rules with missing fields."""

from jacara.plugins.extractors import ExtractedField as F
from jacara.plugins.validators import RuleValidator

BASE = [
    F("date", "date", "2018-12-25"),
    F("total", "number", 9.0),
    F("subtotal", "number", 9.0),
    F("rounding", "number", 0.0),
    F("cash", "number", 10.0),
]


def test_all_rules_pass_on_a_correct_receipt():
    result = RuleValidator().validate([*BASE, F("change", "number", 1.0)])
    assert result.issues == ()
    assert set(result.skipped) == {"items_sum", "item_line"}  # No line items.


def test_ocr_mistake_in_change_is_caught():
    result = RuleValidator().validate([*BASE, F("change", "number", 100.0)])
    assert [i.rule for i in result.issues] == ["change"]
    assert result.fields_with_issues() == {"cash", "total", "change"}
    assert "100.00" in result.issues[0].message


def test_rules_without_enough_fields_are_skipped():
    result = RuleValidator().validate([F("date", "date", "2018-12-25"), F("total", "number", 9.0)])
    assert result.issues == ()
    assert set(result.skipped) == {
        "rounding",
        "cash_covers_total",
        "change",
        "items_sum",
        "item_line",
    }


def test_impossible_date():
    result = RuleValidator().validate([F("date", "date", "2018-02-30")])
    assert [i.rule for i in result.issues] == ["date_exists"]


def test_future_date_is_only_a_warning():
    result = RuleValidator().validate([F("date", "date", "2099-01-01")])
    assert [(i.rule, i.severity) for i in result.issues] == [("date_not_future", "warning")]


def test_small_float_errors_are_tolerated():
    # 10.0 - 9.1 is 0.9000000000000004 in floating point and must not cause an error.
    fields = [F("cash", "number", 10.0), F("total", "number", 9.1), F("change", "number", 0.9)]
    assert RuleValidator().validate(fields).issues == ()
