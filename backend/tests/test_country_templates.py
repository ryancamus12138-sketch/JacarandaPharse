"""各国、各地区法定单据的起步模板和规则（ADR 0049）。"""

from __future__ import annotations

from importlib.resources import files

import pytest

from jacara.plugins.validators import countries as c
from jacara.plugins.validators.rules import REGIONS, RULES
from jacara.templates import STARTERS, load_starter
from jacara.workbook.standard import usable_rule

COUNTRY_STARTERS = [
    n
    for n in STARTERS
    if load_starter(n).countries
    and n
    not in (
        "cn_einvoice",
        "cn_air_itinerary",
        "cn_rail_ticket",
        "cn_express_waybill",
        "au_tax_invoice",
        "cn_quality_report",
    )
]
ENGLISH = {"SG", "NZ", "US", "GB", "PH"}  # 当地语言就是英文


def test_23_regions_covered():
    regions = {load_starter(n).countries[0] for n in COUNTRY_STARTERS}
    # 第一批 + 第二批 23 个，香港没有法定发票格式（不做）
    expected = {
        "JP",
        "KR",
        "TW",
        "SG",
        "MY",
        "VN",
        "IN",
        "NZ",
        "US",
        "GB",
        "DE",
        "TH",
        "ID",
        "PH",
        "FR",
        "IT",
        "ES",
        "NL",
        "PL",
        "CA",
        "AE",
        "SA",
    }
    assert regions == expected


@pytest.mark.parametrize("name", COUNTRY_STARTERS)
def test_country_template_is_grounded_and_clean(name):
    template = load_starter(name)
    text = files("jacara.templates").joinpath("starters", f"{name}.toml").read_text("utf-8")
    assert "依据" in text and "场景" in text and "来源" in text, "要写法规依据、使用场景、来源"
    shape = {**template.definition(), "countries": list(template.countries)}
    assert template.foreign_rules() == []
    assert all(usable_rule(r, shape) for r in template.rules)
    for rule in template.rules:  # 国家规则只给本国模板
        assert REGIONS.get(rule.id) in (None, template.countries[0]), rule.id
    if template.countries[0] not in ENGLISH:
        assert len(template.languages) == 3, "中文、英文、当地语言"
    places = [
        *template.fields,
        *template.tables,
        *(col for t in template.tables for col in t.columns),
    ]
    assert all("en" in dict(p.names) for p in places)


@pytest.mark.parametrize(
    ("check", "good"),
    [
        (c.in_gstin, "27AAPFU0939F1ZV"),
        (c.gb_vat_no, "GB 980 7806 84"),
        (c.de_ust_idnr, "DE136695976"),
        (c.nl_btw_id, "NL004495445B01"),
        (c.it_partita_iva, "IT07643520567"),
        (c.es_nif, "12345678Z"),
        (c.es_nif, "A58818501"),
        (c.tw_ban, "22099131"),
        (c.kr_business_no, "124-81-00998"),
        (c.nz_ird_no, "49-091-850"),
        (c.nz_ird_no, "136-410-132"),
        (c.th_tax_id, "0105536112014"),
        (c.fr_siren, "732 829 320"),
    ],
)
def test_ids_accept_real_numbers_and_catch_one_wrong_digit(check, good):
    assert check(good) is True
    digits = [i for i, ch in enumerate(good) if ch.isdigit()]
    i = digits[len(digits) // 2]  # 改中间一位数字：模拟 OCR 认错一位
    bad = good[:i] + str((int(good[i]) + 1) % 10) + good[i + 1 :]
    assert check(bad) is not True


def test_tax_rules_follow_current_law():
    assert (
        RULES["jp_tax_per_rate"].check(
            {
                "amount_10": 10000.0,
                "tax_10": 1000.0,
                "amount_8": 1080.0,
                "tax_8": 80.0,
                "gross_total": 1.0,
            }
        )
        is None
    )
    assert RULES["jp_tax_per_rate"].check(
        {"amount_10": 10000.0, "tax_10": 900.0, "gross_total": 1.0}
    )
    sg = RULES["sg_gst_rate"].check
    assert sg({"net_total": 100.0, "tax_total": 9.0, "issue_date": "2025-03-01"}) is None
    assert sg({"net_total": 100.0, "tax_total": 8.0, "issue_date": "2023-06-01"}) is None
    assert sg({"net_total": 100.0, "tax_total": 8.0, "issue_date": "2025-03-01"})
    assert RULES["vn_reduced_8"].check({"tax_rate": "8%", "issue_date": "2026-11-01"}) is None
    assert RULES["vn_reduced_8"].check({"tax_rate": "8%", "issue_date": "2027-01-05"})
    assert RULES["th_vat_7"].check({"net_total": 1000.0, "tax_total": 70.0}) is None
    assert RULES["id_ppn"].check({"net_total": 1200.0, "dpp": 1100.0, "ppn": 132.0}) is None
    assert RULES["id_ppn"].check({"net_total": 1200.0, "dpp": 1100.0, "ppn": 121.0})
    assert RULES["in_tax_split"].check({"gross_total": 1.0, "cgst": 9.0, "sgst": 9.0}) is None
    assert RULES["in_tax_split"].check({"gross_total": 1.0, "cgst": 9.0, "igst": 18.0})
    assert (
        RULES["in_invoice_no"].check({"invoice_no": "INV/2026-27/00123"}) is not None
    )  # 17 个字符
    assert RULES["ca_buyer_500"].check({"gross_total": 600.0, "buyer_name": "X"})
    assert RULES["gb_full_over_250"].check({"gross_total": 200.0}) is None
    assert RULES["de_kassenbeleg"].check({"gross_total": 5.0})
    assert RULES["de_rates"].check({"tax_rate": "19 %"}) is None
    assert RULES["de_rates"].check({"tax_rate": "16 %"})
    assert RULES["nz_gst_3_23"].check({"tax_total": 15.0, "gross_total": 115.0}) is None


def test_jp_registration_number_format():
    """登録番号只查格式（个人事业者的号码不一定有法人番号的校验位）。"""
    assert c.jp_registration_no("T8700110005901") is True
    assert c.jp_registration_no("8700110005901") is False


def test_rules_only_show_up_where_they_belong():
    """没选但"用得上"的规则只能是通用的或本国的（收银小票的 TSE 规则不出现在发票里）。"""
    from jacara.plugins.validators import ALL_RULES

    for name in STARTERS:
        template = load_starter(name)
        shape = {**template.definition(), "countries": list(template.countries)}
        for rule in ALL_RULES:
            if usable_rule(rule, shape):
                assert REGIONS.get(rule.id) in (None, *template.countries), (name, rule.id)
    rechnung = load_starter("de_rechnung")
    shape = {**rechnung.definition(), "countries": ["DE"]}
    assert not usable_rule(RULES["de_kassenbeleg"], shape)
    assert usable_rule(
        RULES["de_kassenbeleg"],
        {**load_starter("de_kassenbeleg").definition(), "countries": ["DE"]},
    )


def test_starters_show_their_basis():
    """预览里的"依据"：模板文件开头的注释，续行接上，不要"只写字段……"这类话。"""
    basis = load_starter("de_kassenbeleg").basis
    assert basis[0].startswith("适用：")
    assert any(b.startswith("依据：") and "Zeitpunkt des Vorgangsbeginns" in b for b in basis)
    assert not any("真实" in b for b in basis)
    assert all(load_starter(n).basis for n in STARTERS)
