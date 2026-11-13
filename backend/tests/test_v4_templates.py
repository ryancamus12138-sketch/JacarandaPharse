"""v4 第 1 批的起步模板和规则（ADR 0043）。

装箱单、送货单、快递运单、海运提单、航空运单、质检报告。
"""

from __future__ import annotations

import pytest

from jacara.plugins.extractors.base import ExtractedField
from jacara.plugins.validators.base import Values
from jacara.plugins.validators.rules import (
    RULES,
    check_awb_no,
    check_container_no,
    check_qc_conclusion,
    check_waybill_fees,
    container_check_digit,
)
from jacara.templates import STARTERS, load_starter

NEW = (
    "quality_report",
    "packing_list",
    "delivery_note",
    "cn_express_waybill",
    "bill_of_lading",
    "air_waybill",
)


@pytest.mark.parametrize("name", NEW)
def test_new_starters_load_with_their_rules(name):
    assert name in STARTERS
    template = load_starter(name)
    assert template.version >= 1
    assert template.rules, "每个新模板都配了规则"
    assert any(f.unique for f in template.fields), "单号、报告编号标成不能重复"
    fields = {f.name for f in template.fields} | {
        f"{t.name}.*.{c.name}" for t in template.tables for c in t.columns
    }
    row_columns = {c.name for t in template.tables for c in t.columns}
    for rule in template.rules:
        for need in rule.needs:
            assert need in fields or need in row_columns, (
                f"{name} 的规则 {rule.id} 要的 {need} 模板里没有"
            )


def test_receipt_document_no_knows_document_no():
    for name in ("receipt_general", "receipt_shopping", "receipt_restaurant"):
        template = load_starter(name)
        assert template.version >= 3
        spec = next(f for f in template.fields if f.name == "document_no")
        assert "Document No" in spec.aliases


def test_container_check_digit():
    assert container_check_digit("CSQU3054383") == 3  # ISO 6346 里的例子
    assert check_container_no({"container_no": "CSQU 305438-3"}) is None
    assert "校验位应是 3" in (check_container_no({"container_no": "CSQU3054384"}) or "")
    assert "格式不对" in (check_container_no({"container_no": "CSQ3054383"}) or "")


def test_awb_check_digit():
    assert check_awb_no({"awb_no": "176-12345675"}) is None  # 1234567 ÷ 7 余 5
    assert check_awb_no({"awb_no": "176 1234 5675"}) is None
    assert "校验位应是 5" in (check_awb_no({"awb_no": "176-12345674"}) or "")


def test_qc_conclusion_must_match_items():
    failed: Values = {
        "items.1.judgment": "合格",
        "items.2.judgment": "不合格",
        "conclusion": "所检项目合格",
    }
    assert "1 项判定不合格" in (check_qc_conclusion(failed) or "")
    passed: Values = {
        "items.1.judgment": "符合",
        "items.2.judgment": "Pass",
        "conclusion": "不合格",
    }
    assert "都合格" in (check_qc_conclusion(passed) or "")
    ok: Values = {"items.1.judgment": "合格", "conclusion": "所检项目符合 GB/T 1234 要求"}
    assert check_qc_conclusion(ok) is None


def test_waybill_fees_add_up():
    values: Values = {"freight": 12.0, "insurance_fee": 1.0, "total": 13.0}
    assert check_waybill_fees(values) is None
    assert "费用合计是 15.00" in (check_waybill_fees({**values, "total": 15.0}) or "")


def validate(template_name: str, values: Values):
    validator = load_starter(template_name).validator()
    fields = [
        ExtractedField(k, "number" if isinstance(v, float) else "string", v)
        for k, v in values.items()
    ]
    return validator.validate(fields)


def test_packing_list_totals_and_weights():
    values: Values = {
        "shipper": "A",
        "packages.1.cartons": 10.0,
        "packages.1.net_weight": 100.0,
        "packages.1.gross_weight": 110.0,
        "packages.2.cartons": 5.0,
        "packages.2.net_weight": 60.0,
        "packages.2.gross_weight": 55.0,
        "total_packages": 15.0,
        "total_net_weight": 160.0,
        "total_gross_weight": 170.0,
    }
    result = validate("packing_list", values)
    rules = {i.rule for i in result.issues}
    assert "net_not_over_gross" in rules  # 第 2 行净重 60 > 毛重 55
    assert "packing_gross" in rules  # 110 + 55 ≠ 170
    assert "packing_packages" in result.passed and "packing_net" in result.passed


def test_bill_of_lading_containers():
    values: Values = {
        "bl_no": "X1",
        "containers.1.container_no": "CSQU3054383",
        "containers.1.packages": 100.0,
        "containers.2.container_no": "MSKU1234566",
        "containers.2.packages": 50.0,
        "total_packages": 150.0,
        "shipped_on_board_date": "2026-03-05",
        "issue_date": "2026-03-04",
    }
    result = validate("bill_of_lading", values)
    rules = [i.rule for i in result.issues]
    assert rules.count("container_no") == 1  # 第 2 个箱号校验位不对
    assert "bl_onboard_before_issue" in rules
    assert "bl_packages" in result.passed


def test_every_new_rule_is_registered():
    for name in NEW:
        for rule in load_starter(name).rules:
            assert RULES[rule.id] is rule
