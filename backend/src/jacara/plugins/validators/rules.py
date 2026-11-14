"""Rule-list validator and the rules common to receipts.

To add a rule: write a check function, add a Rule(...) to ALL_RULES, and add its id to the
templates that use it (rules in templates/*.toml and templates/starters/*.toml).

Rules look up values by field ID, so starter templates use the same ID for the same meaning
(total, issue_date, …)."""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Callable, Sequence
from datetime import date

from jacara.core.cnmoney import parse_chinese_amount
from jacara.core.i18n import N_, join_items, joined, nested, stored
from jacara.plugins.extractors.base import ExtractedField, split_cell_name
from jacara.plugins.validators.base import Issue, Rule, ValidationResult, Values
from jacara.plugins.validators.countries import COUNTRY_REGIONS, COUNTRY_RULES

TOLERANCE = 0.005
"""Tolerance for amount comparisons: less than half a cent of difference counts as equal
(floating-point error)."""


def _num(values: Values, name: str) -> float:
    """Read a numeric field. Rules run only when all their fields have values, and the extractor
    guarantees numeric fields are floats."""
    value = values[name]
    if not isinstance(value, float):
        raise TypeError(
            stored("字段 {name} 应该是数字，实际是 {value}", name=name, value=repr(value))
        )
    return value


def _money(value: float) -> str:
    return f"{value:,.2f}"


def check_change(values: Values) -> str | None:
    cash, total, change = _num(values, "cash"), _num(values, "total"), _num(values, "change")
    expected = cash - total
    if abs(expected - change) < TOLERANCE:
        return None
    return stored(
        "现金 {cash} − 总额 {total} = {expected}，但找零是 {change}",
        cash=_money(cash),
        total=_money(total),
        expected=_money(expected),
        change=_money(change),
    )


def check_rounding(values: Values) -> str | None:
    subtotal, rounding = _num(values, "subtotal"), _num(values, "rounding")
    total = _num(values, "total")
    expected = subtotal + rounding
    if abs(expected - total) < TOLERANCE:
        return None
    return stored(
        "小计 {subtotal} + 调整 {rounding} = {expected}，但总额是 {total}",
        subtotal=_money(subtotal),
        rounding=_money(rounding),
        expected=_money(expected),
        total=_money(total),
    )


def check_cash_covers_total(values: Values) -> str | None:
    cash, total = _num(values, "cash"), _num(values, "total")
    if cash + TOLERANCE >= total:
        return None
    return stored("现金 {cash} 比总额 {total} 还少", cash=_money(cash), total=_money(total))


def check_positive_total(values: Values) -> str | None:
    total = _num(values, "total")
    return None if total > 0 else stored("总额是 {total}，应该大于 0", total=_money(total))


def _date_exists(field: str) -> Callable[[Values], str | None]:
    def check(values: Values) -> str | None:
        try:
            date.fromisoformat(str(values[field]))
        except ValueError:
            return stored("日期 {value} 不存在", value=values[field])
        return None

    return check


def _date_not_future(field: str) -> Callable[[Values], str | None]:
    def check(values: Values) -> str | None:
        try:
            day = date.fromisoformat(str(values[field]))
        except ValueError:
            return None  # Invalid dates are handled by the previous rule.
        if day <= date.today():
            return None
        return stored("日期 {day} 在今天之后", day=day)

    return check


check_date_exists = _date_exists("date")
check_date_not_future = _date_not_future("date")


ITEM_TOLERANCE = 0.015
"""Tolerance for line items: unit price × quantity is often rounded, so one cent of difference is
accepted."""


def _amounts(values: Values) -> list[float]:
    return [
        v
        for k, v in values.items()
        if fnmatch.fnmatch(k, "items.*.amount") and isinstance(v, float)
    ]


def check_items_sum(values: Values) -> str | None:
    """Line amounts should add up to the subtotal; without a subtotal in the template, to the
    total."""
    amounts = _amounts(values)
    target = "subtotal" if isinstance(values.get("subtotal"), float) else "total"
    expected = _num(values, target)
    added = sum(amounts)
    if abs(added - expected) < ITEM_TOLERANCE:
        return None
    label = stored("小计") if target == "subtotal" else stored("总额")
    return stored(
        "{rows} 行商品金额加起来是 {added}，但{label}是 {expected}（可能漏了行、多了行，"
        "或者有税、折扣没算进去）",
        rows=len(amounts),
        added=_money(added),
        label=label,
        expected=_money(expected),
    )


def check_item_line(values: Values) -> str | None:
    """One line item: quantity × unit price = amount."""
    quantity, price = _num(values, "quantity"), _num(values, "unit_price")
    amount = _num(values, "amount")
    expected = quantity * price
    # Discount lines have negative amounts (3x 2.00 → -6.00); compare absolute values.
    if abs(abs(expected) - abs(amount)) < ITEM_TOLERANCE:
        return None
    return stored(
        "数量 {quantity:g} × 单价 {price} = {expected}，但金额是 {amount}",
        quantity=quantity,
        price=_money(price),
        expected=_money(expected),
        amount=_money(amount),
    )


def _sum(values: Values, pattern: str) -> tuple[int, float]:
    found = [v for k, v in values.items() if fnmatch.fnmatch(k, pattern) and isinstance(v, float)]
    return len(found), sum(found)


def _opt(values: Values, name: str) -> float:
    """Optional amount: 0 when absent."""
    value = values.get(name)
    return value if isinstance(value, float) else 0.0


def _text(values: Values, name: str) -> str:
    value = values.get(name)
    return "" if value is None else str(value).strip()


# -- China fully digital e-invoices (STA Announcement No. 11 of 2024) --


def check_cn_invoice_no(values: Values) -> str | None:
    number = _text(values, "invoice_no").replace(" ", "")
    if number.isdigit() and len(number) == 20:
        return None
    return stored("发票号码 {number} 不是 20 位数字（数电发票的号码是 20 位）", number=number)


def check_cn_tax_balance(values: Values) -> str | None:
    amount, tax = _num(values, "amount_total"), _num(values, "tax_total")
    grand = _num(values, "grand_total")
    if abs(amount + tax - grand) < ITEM_TOLERANCE:
        return None
    return stored(
        "合计金额 {amount} + 合计税额 {tax} = {expected}，但价税合计是 {grand}",
        amount=_money(amount),
        tax=_money(tax),
        expected=_money(amount + tax),
        grand=_money(grand),
    )


def check_cn_items_amount(values: Values) -> str | None:
    count, added = _sum(values, "items.*.amount")
    expected = _num(values, "amount_total")
    if abs(added - expected) < ITEM_TOLERANCE:
        return None
    return stored(
        "{count} 行金额加起来是 {added}，但合计金额是 {expected}",
        count=count,
        added=_money(added),
        expected=_money(expected),
    )


def check_cn_items_tax(values: Values) -> str | None:
    count, added = _sum(values, "items.*.tax")
    expected = _num(values, "tax_total")
    # Tax is rounded per line, so with many lines the total may differ by a few cents.
    if abs(added - expected) < max(ITEM_TOLERANCE, 0.01 * count):
        return None
    return stored(
        "{count} 行税额加起来是 {added}，但合计税额是 {expected}",
        count=count,
        added=_money(added),
        expected=_money(expected),
    )


def check_cn_words(values: Values) -> str | None:
    words = _text(values, "grand_total_words")
    grand = _num(values, "grand_total")
    parsed = parse_chinese_amount(words)
    if parsed is None:
        return stored("大写金额「{words}」读不懂，可能识别错了字", words=words)
    if abs(parsed - grand) < TOLERANCE:
        return None
    return stored(
        "大写「{words}」是 {parsed}，但小写价税合计是 {grand}",
        words=words,
        parsed=_money(parsed),
        grand=_money(grand),
    )


def check_fare_tax_balance(values: Values) -> str | None:
    """Railway e-tickets issued to organizations: amount (excluding tax) + tax = fare."""
    amount, tax, total = (
        _num(values, "amount_total"),
        _num(values, "tax_total"),
        _num(values, "total"),
    )
    if abs(amount + tax - total) < ITEM_TOLERANCE:
        return None
    return stored(
        "金额 {amount} + 税额 {tax} = {expected}，但票价是 {total}",
        amount=_money(amount),
        tax=_money(tax),
        expected=_money(amount + tax),
        total=_money(total),
    )


# -- Air transport e-ticket itinerary (Announcement No. 9 of 2024) --


def check_air_total(values: Values) -> str | None:
    fare, fuel = _num(values, "fare"), _num(values, "fuel_surcharge")
    fund, total = _num(values, "caac_fund"), _num(values, "total")
    other = _opt(values, "other_taxes")
    expected = fare + fuel + fund + other
    if abs(expected - total) < ITEM_TOLERANCE:
        return None
    if other:
        return stored(
            "票价 {fare} + 燃油附加费 {fuel} + 民航发展基金 {fund} + 其他税费 {other} = "
            "{expected}，但合计是 {total}",
            fare=_money(fare),
            fuel=_money(fuel),
            fund=_money(fund),
            other=_money(other),
            expected=_money(expected),
            total=_money(total),
        )
    return stored(
        "票价 {fare} + 燃油附加费 {fuel} + 民航发展基金 {fund} = {expected}，但合计是 {total}",
        fare=_money(fare),
        fuel=_money(fuel),
        fund=_money(fund),
        expected=_money(expected),
        total=_money(total),
    )


def _rate(text: str) -> float | None:
    """Tax rate text → fraction: 9% → 0.09. None if unreadable."""
    try:
        return float(text.replace("%", "").strip()) / 100
    except ValueError:
        return None


def check_air_vat(values: Values) -> str | None:
    """VAT = (fare + fuel surcharge) ÷ (1 + rate) × rate.

        Formula per art. 6 of Announcement No. 39 of 2019 of the Ministry of Finance, State
    Taxation Administration and General Administration of Customs.

        The civil aviation development fund is a government fund and not subject to VAT. Change
    fees and the like may also be taxed, so this is only a warning."""
    rate = _rate(_text(values, "vat_rate"))
    if rate is None:
        return None
    fare, fuel, vat = _num(values, "fare"), _num(values, "fuel_surcharge"), _num(values, "vat")
    expected = (fare + fuel) / (1 + rate) * rate
    if abs(expected - vat) < 0.02:
        return None
    return stored(
        "(票价 {fare} + 燃油附加费 {fuel}) ÷ (1 + {rate:.0%}) × {rate:.0%} = {expected}，"
        "但增值税税额是 {vat}",
        fare=_money(fare),
        fuel=_money(fuel),
        rate=rate,
        expected=_money(expected),
        vat=_money(vat),
    )


# -- Generic invoices (amount relations of EN 16931 / Peppol) --


def check_lines_net(values: Values) -> str | None:
    count, added = _sum(values, "items.*.amount")
    expected = _num(values, "net_total")
    if abs(added - expected) < ITEM_TOLERANCE:
        return None
    return stored(
        "{count} 行金额加起来是 {added}，但不含税合计是 {expected}",
        count=count,
        added=_money(added),
        expected=_money(expected),
    )


def check_net_tax_gross(values: Values) -> str | None:
    net, tax, gross = (
        _num(values, "net_total"),
        _num(values, "tax_total"),
        _num(values, "gross_total"),
    )
    if abs(net + tax - gross) < ITEM_TOLERANCE:
        return None
    return stored(
        "不含税合计 {net} + 税额 {tax} = {expected}，但含税合计是 {gross}",
        net=_money(net),
        tax=_money(tax),
        expected=_money(net + tax),
        gross=_money(gross),
    )


def check_due_not_above_gross(values: Values) -> str | None:
    due, gross = _num(values, "amount_due"), _num(values, "gross_total")
    if due <= gross + TOLERANCE:
        return None
    return stored("应付金额 {due} 比含税合计 {gross} 还多", due=_money(due), gross=_money(gross))


def abn_valid(text: str) -> bool | None:
    """Australian ABN check digit (algorithm published by the ATO). None for values that are not 11
    digits (for example European VAT numbers)."""
    digits = [int(c) for c in text if c.isdigit()]
    if len(digits) != 11 or any(c.isalpha() for c in text):
        return None
    digits[0] -= 1
    weights = (10, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19)
    return sum(d * w for d, w in zip(digits, weights, strict=True)) % 89 == 0


def _abn_check(field: str) -> Callable[[Values], str | None]:
    def check(values: Values) -> str | None:
        text = _text(values, field)
        if abn_valid(text) is False:
            return stored("ABN {text} 的校验位不对（可能识别错了一位数字）", text=text)
        return None

    return check


def _tax_invoice(values: Values) -> bool:
    return "tax invoice" in _text(values, "document_title").lower()


def check_au_buyer_1000(values: Values) -> str | None:
    """ATO: tax invoices of AUD 1,000 or more (including GST) must show the buyer's name or ABN."""
    gross = _num(values, "gross_total")
    if not _tax_invoice(values) or gross < 1000:
        return None
    if _text(values, "buyer_name") or _text(values, "buyer_tax_id"):
        return None
    return stored(
        "含税合计 {gross} ≥ 1,000，澳洲税务发票要写买方名称或 ABN，这里没有", gross=_money(gross)
    )


def check_au_tax_invoice_title(values: Values) -> str | None:
    """ATO: tax invoices must state that they are tax invoices."""
    if _tax_invoice(values):
        return None
    return stored(
        "单据标题是「{title}」，澳洲税务发票要写明 Tax invoice",
        title=_text(values, "document_title"),
    )


def check_au_gst_eleventh(values: Values) -> str | None:
    """Australian GST 10%: GST is at most 1/11 of the GST-inclusive total (less when GST-free items
    are included)."""
    gst, gross = _num(values, "tax_total"), _num(values, "gross_total")
    if gst <= gross / 11 + ITEM_TOLERANCE:
        return None
    return stored(
        "GST {gst} 比含 GST 总额 {gross} 的 1/11（{limit}）还多",
        gst=_money(gst),
        gross=_money(gross),
        limit=_money(gross / 11),
    )


# -- Receipts (ATO: five receipt elements; tax invoice required above AUD 82.50) --


def check_ato_evidence(values: Values) -> str | None:
    """Receipts accepted by the ATO must show the supplier, date, items purchased and amount (the
    amount is guaranteed by needs)."""
    missing = [
        label
        for label, ok in (
            (N_("商家名称"), bool(_text(values, "merchant"))),
            (N_("日期"), bool(_text(values, "date"))),
            (
                N_("买了什么（明细或描述）"),
                bool(_text(values, "description")) or bool(_amounts(values)),
            ),
        )
        if not ok
    ]
    if not missing:
        return None
    return stored("做报销凭证还缺：{missing}", missing=join_items(missing))


def check_ato_gst_credit(values: Values) -> str | None:
    """Above AUD 82.50, claiming GST requires a tax invoice: "Tax Invoice", supplier ABN and GST
    amount."""
    total = _num(values, "total")
    if total <= 82.50:
        return None
    missing = [
        label
        for label, ok in (
            (N_("“Tax Invoice”字样"), _tax_invoice(values)),
            (N_("商家 ABN"), bool(_text(values, "merchant_abn"))),
            (N_("GST 金额"), isinstance(values.get("gst"), float)),
        )
        if not ok
    ]
    if not missing:
        return None
    return stored(
        "总额 {total} 超过 82.50，要抵 GST 还缺：{missing}",
        total=_money(total),
        missing=join_items(missing),
    )


def check_gst_eleventh(values: Values) -> str | None:
    """Australian GST 10%: GST is at most 1/11 of the GST-inclusive total (less when tax-free goods
    are included)."""
    gst, total = _num(values, "gst"), _num(values, "total")
    if gst <= total / 11 + ITEM_TOLERANCE:
        return None
    return stored(
        "GST {gst} 比总额 {total} 的 1/11（{limit}）还多",
        gst=_money(gst),
        total=_money(total),
        limit=_money(total / 11),
    )


def check_receipt_balance(values: Values) -> str | None:
    """Subtotal − discount + surcharge + tip + adjustment = total (absent items count as 0)."""
    subtotal, total = _num(values, "subtotal"), _num(values, "total")
    discount = abs(_opt(values, "discount"))
    surcharge, tip = _opt(values, "surcharge"), _opt(values, "tip")
    rounding = _opt(values, "rounding")
    expected = subtotal - discount + surcharge + tip + rounding
    if abs(expected - total) < ITEM_TOLERANCE:
        return None
    parts = [stored("小计 {subtotal}", subtotal=_money(subtotal))]
    parts += [stored("− 折扣 {discount}", discount=_money(discount))] if discount else []
    parts += [stored("+ 附加费 {surcharge}", surcharge=_money(surcharge))] if surcharge else []
    parts += [stored("+ 小费 {tip}", tip=_money(tip))] if tip else []
    parts += [stored("+ 调整 {rounding}", rounding=_money(rounding))] if rounding else []
    return stored(
        "{terms} = {expected}，但总额是 {total}",
        terms=_terms(parts),
        expected=_money(expected),
        total=_money(total),
    )


def _terms(parts: list[str]) -> str:
    """Terms of a sum (stored messages) written one after another, separated by spaces."""
    return joined(" ", parts)


# -- Hotel bills --


def _stay(values: Values) -> tuple[date, date] | None:
    try:
        return (
            date.fromisoformat(str(values["check_in"])),
            date.fromisoformat(str(values["check_out"])),
        )
    except ValueError:
        return None


def check_stay_order(values: Values) -> str | None:
    stay = _stay(values)
    if stay is None or stay[1] > stay[0]:
        return None
    return stored("离店 {check_out} 不在入住 {check_in} 之后", check_out=stay[1], check_in=stay[0])


def check_stay_nights(values: Values) -> str | None:
    stay = _stay(values)
    nights = _num(values, "nights")
    if stay is None or (stay[1] - stay[0]).days == nights:
        return None
    return stored(
        "入住 {check_in} 到离店 {check_out} 是 {days} 晚，但写的是 {nights:g} 晚",
        check_in=stay[0],
        check_out=stay[1],
        days=(stay[1] - stay[0]).days,
        nights=nights,
    )


RECEIPT_RULES: tuple[Rule, ...] = (
    Rule("total_positive", N_("总额大于 0"), ("total",), check_positive_total),
    Rule("date_exists", N_("日期真实存在"), ("date",), check_date_exists),
    Rule("date_not_future", N_("日期不在未来"), ("date",), check_date_not_future, "warning"),
    Rule("rounding", N_("小计 + 调整 = 总额"), ("subtotal", "rounding", "total"), check_rounding),
    Rule("cash_covers_total", N_("现金不少于总额"), ("cash", "total"), check_cash_covers_total),
    Rule("change", N_("现金 − 总额 = 找零"), ("cash", "total", "change"), check_change),
    Rule(
        "items_sum",
        N_("商品金额合计 = 小计或总额"),
        ("items.*.amount", "total"),
        check_items_sum,
        # Tax, service charges and discounts are often listed separately; a mismatch is not
        # necessarily an error.
        "warning",
        # Flag only the line amounts, not the total: the total is more likely correct.
        flags=("items.*.amount",),
    ),
    Rule(
        "item_line",
        N_("数量 × 单价 = 金额"),
        ("quantity", "unit_price", "amount"),
        check_item_line,
        per_row="items",
    ),
)

TEMPLATE_RULES: tuple[Rule, ...] = (
    Rule("issue_date_exists", N_("开票日期真实存在"), ("issue_date",), _date_exists("issue_date")),
    Rule(
        "issue_date_not_future",
        N_("开票日期不在未来"),
        ("issue_date",),
        _date_not_future("issue_date"),
        "warning",
    ),
    Rule("cn_invoice_no", N_("发票号码是 20 位数字"), ("invoice_no",), check_cn_invoice_no),
    Rule(
        "cn_tax_balance",
        N_("合计金额 + 合计税额 = 价税合计"),
        ("amount_total", "tax_total", "grand_total"),
        check_cn_tax_balance,
    ),
    Rule(
        "cn_items_amount",
        N_("明细金额合计 = 合计金额"),
        ("items.*.amount", "amount_total"),
        check_cn_items_amount,
        "warning",
        flags=("items.*.amount",),
    ),
    Rule(
        "cn_items_tax",
        N_("明细税额合计 = 合计税额"),
        ("items.*.tax", "tax_total"),
        check_cn_items_tax,
        "warning",
        flags=("items.*.tax",),
    ),
    Rule(
        "cn_words",
        N_("大写金额 = 小写价税合计"),
        ("grand_total_words", "grand_total"),
        check_cn_words,
    ),
    Rule(
        "fare_tax_balance",
        N_("金额 + 税额 = 票价"),
        ("amount_total", "tax_total", "total"),
        check_fare_tax_balance,
    ),
    Rule(
        "air_total",
        N_("票价 + 燃油 + 民航发展基金 + 其他税费 = 合计"),
        ("fare", "fuel_surcharge", "caac_fund", "total"),
        check_air_total,
        "warning",
    ),
    Rule(
        "air_vat",
        N_("增值税税额 = (票价 + 燃油) ÷ (1 + 税率) × 税率"),
        ("fare", "fuel_surcharge", "vat_rate", "vat"),
        check_air_vat,
        "warning",
    ),
    Rule(
        "lines_net",
        N_("明细金额合计 = 不含税合计"),
        ("items.*.amount", "net_total"),
        check_lines_net,
        "warning",
        flags=("items.*.amount",),
    ),
    Rule(
        "net_tax_gross",
        N_("不含税合计 + 税额 = 含税合计"),
        ("net_total", "tax_total", "gross_total"),
        check_net_tax_gross,
    ),
    Rule(
        "due_not_above_gross",
        N_("应付金额不超过含税合计"),
        ("amount_due", "gross_total"),
        check_due_not_above_gross,
        "warning",
    ),
    Rule(
        "au_buyer_1000",
        N_("1,000 澳元以上的税务发票写了买方"),
        ("gross_total",),
        check_au_buyer_1000,
        "warning",
        flags=("buyer_name",),
    ),
    Rule("seller_abn", N_("卖方 ABN 校验位正确"), ("seller_tax_id",), _abn_check("seller_tax_id")),
    Rule("buyer_abn", N_("买方 ABN 校验位正确"), ("buyer_tax_id",), _abn_check("buyer_tax_id")),
    Rule(
        "au_tax_invoice_title",
        N_("写明是税务发票（ATO）"),
        ("document_title",),
        check_au_tax_invoice_title,
        "warning",
    ),
    Rule(
        "au_gst_eleventh",
        N_("GST 不超过含 GST 总额的 1/11"),
        ("tax_total", "gross_total"),
        check_au_gst_eleventh,
        "warning",
    ),
    Rule("merchant_abn", N_("商家 ABN 校验位正确"), ("merchant_abn",), _abn_check("merchant_abn")),
    Rule(
        "ato_evidence",
        N_("收据有商家、日期、内容、金额（ATO）"),
        ("total",),
        check_ato_evidence,
        "warning",
        flags=("merchant", "date"),
    ),
    Rule(
        "ato_gst_credit",
        N_("超过 82.50 澳元是税务发票（ATO）"),
        ("total",),
        check_ato_gst_credit,
        "warning",
        flags=("document_title", "merchant_abn", "gst"),
    ),
    Rule(
        "gst_eleventh", N_("GST 不超过总额的 1/11"), ("gst", "total"), check_gst_eleventh, "warning"
    ),
    Rule(
        "receipt_balance",
        N_("小计 − 折扣 + 附加费 + 小费 + 调整 = 总额"),
        ("subtotal", "total"),
        check_receipt_balance,
        "warning",
    ),
    Rule(
        "travel_date_exists", N_("乘车日期真实存在"), ("travel_date",), _date_exists("travel_date")
    ),
    Rule("stay_order", N_("离店在入住之后"), ("check_in", "check_out"), check_stay_order),
    Rule(
        "stay_nights",
        N_("晚数 = 离店 − 入住"),
        ("check_in", "check_out", "nights"),
        check_stay_nights,
        "warning",
    ),
)

_LAB_NUMBER = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*$")
_LAB_RANGE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*(?:-|－|–|—|~|～|至|--)\s*(-?\d+(?:\.\d+)?)\s*$")
_LAB_BOUND = re.compile(r"^\s*(<=?|≤|＜|>=?|≥|＞)\s*(-?\d+(?:\.\d+)?)\s*$")


def _lab_reference(text: str) -> tuple[float | None, float | None] | None:
    """Reference range → (lower, upper): 3.5-9.5, <5.0, ≥1.0. None for qualitative (negative) or
    unreadable ranges."""
    if m := _LAB_RANGE.match(text):
        return float(m[1]), float(m[2])
    if m := _LAB_BOUND.match(text):
        value = float(m[2])
        return (None, value) if m[1] in ("<", "<=", "≤", "＜") else (value, None)
    return None


def _lab_flag(text: str) -> str | None:
    """Abnormal flag → "high", "low", "other" (positive, *); None without a flag."""
    t = text.strip().upper()
    if not t:
        return None
    if "↑" in t or "高" in t or t in ("H", "HH", "HIGH"):
        return "high"
    if "↓" in t or "低" in t or t in ("L", "LL", "LOW"):
        return "low"
    return "other"


def check_lab_flag(values: Values) -> str | None:
    """Test results outside the reference range but not flagged high or low, or inside the range but
    flagged (numeric results and numeric ranges only)."""
    result = _LAB_NUMBER.match(str(values.get("result") or ""))
    reference = _lab_reference(str(values.get("reference") or ""))
    if result is None or reference is None:
        # Qualitative results, values such as <0.1, and unreadable reference ranges are not checked.
        return None
    value = float(result[1])
    low, high = reference
    flag = _lab_flag(str(values.get("flag") or ""))
    shown = str(values.get("reference")).strip()
    if high is not None and value > high and flag != "high":
        return stored(
            "结果 {value:g} 高于参考范围 {shown}，但没有标 ↑（偏高）", value=value, shown=shown
        )
    if low is not None and value < low and flag != "low":
        return stored(
            "结果 {value:g} 低于参考范围 {shown}，但没有标 ↓（偏低）", value=value, shown=shown
        )
    inside = (low is None or value >= low) and (high is None or value <= high)
    if inside and flag == "high":
        return stored("结果 {value:g} 在参考范围 {shown} 内，却标了偏高", value=value, shown=shown)
    if inside and flag == "low":
        return stored("结果 {value:g} 在参考范围 {shown} 内，却标了偏低", value=value, shown=shown)
    return None


LAB_RULES: tuple[Rule, ...] = (
    Rule(
        "lab_flag",
        N_("超出参考范围的结果标了高低"),
        ("result", "reference"),
        check_lab_flag,
        "warning",
        per_row="results",
        flags=("result", "flag"),
    ),
)

# ---------- Logistics, delivery, quality inspection ----------


def _column(values: Values, table: str, column: str) -> list[float]:
    return [
        v
        for k, v in values.items()
        if fnmatch.fnmatch(k, f"{table}.*.{column}") and isinstance(v, float)
    ]


def _sum_equals(table: str, column: str, total: str, what: str) -> Callable[[Values], str | None]:
    """A table column sums to a total field (pieces, quantity, weight, volume). Weights and volumes
    are often rounded, so the tolerance is the larger of 0.5% of the total and 0.015."""
    noun = nested(what)

    def check(values: Values) -> str | None:
        rows = _column(values, table, column)
        expected = _num(values, total)
        added = sum(rows)
        if abs(added - expected) <= max(ITEM_TOLERANCE, abs(expected) * 0.005):
            return None
        return stored(
            "{rows} 行的{noun}加起来是 {added:g}，但合计写的是 {expected:g}（可能漏了行或多了行）",
            rows=len(rows),
            noun=noun,
            added=added,
            expected=expected,
        )

    return check


def check_net_not_over_gross(values: Values) -> str | None:
    net, gross = _num(values, "net_weight"), _num(values, "gross_weight")
    if net <= gross + TOLERANCE:
        return None
    return stored("净重 {net:g} 比毛重 {gross:g} 还大（毛重 = 净重 + 包装）", net=net, gross=gross)


def check_total_net_not_over_gross(values: Values) -> str | None:
    net, gross = _num(values, "total_net_weight"), _num(values, "total_gross_weight")
    if net <= gross + TOLERANCE:
        return None
    return stored("总净重 {net:g} 比总毛重 {gross:g} 还大", net=net, gross=gross)


_CONTAINER = re.compile(r"^([A-Z]{3}[UJZR])(\d{6})(\d)$")
_LETTER_VALUES = {
    letter: value
    for letter, value in zip(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
        (n for n in range(10, 39) if n % 11 != 0),
        strict=False,
    )
}


def container_check_digit(number: str) -> int | None:
    """ISO 6346 container number check digit: 4 letters + 6 digits, weighted by position (powers of
    2), summed, modulo 11, with 10 counting as 0. None for invalid formats."""
    m = _CONTAINER.match(re.sub(r"[\s-]", "", number.upper()))
    if m is None:
        return None
    digits = [_LETTER_VALUES[c] for c in m[1]] + [int(c) for c in m[2]]
    total = sum(v * 2**i for i, v in enumerate(digits))
    return total % 11 % 10


def check_container_no(values: Values) -> str | None:
    raw = str(values.get("container_no") or "")
    number = re.sub(r"[\s-]", "", raw.upper())
    m = _CONTAINER.match(number)
    if m is None:
        return stored(
            "集装箱号「{raw}」格式不对（应是 4 个字母 + 7 位数字，如 MSKU1234565）", raw=raw
        )
    expected = container_check_digit(number)
    if expected == int(m[3]):
        return None
    return stored(
        "集装箱号「{raw}」最后一位校验位应是 {expected}，可能有字认错了", raw=raw, expected=expected
    )


_AWB = re.compile(r"^(\d{3})[\s-]?(\d{4})\s?(\d{4})$")


def check_awb_no(values: Values) -> str | None:
    """Air waybill number: 3-digit airline prefix + 8-digit serial; the first 7 serial digits modulo
    7 equal the 8th (IATA)."""
    raw = str(values.get("awb_no") or "")
    m = _AWB.match(raw.strip())
    if m is None:
        return stored(
            "运单号「{raw}」格式不对（应是 3 位航空公司代码 + 8 位数字，如 176-12345675）", raw=raw
        )
    serial = m[2] + m[3]
    if int(serial[:7]) % 7 == int(serial[7]):
        return None
    return stored(
        "运单号「{raw}」的校验位应是 {expected}，可能有数字认错了",
        raw=raw,
        expected=int(serial[:7]) % 7,
    )


def check_rate_line(values: Values) -> str | None:
    """Air waybill charge line: chargeable weight × rate = charge (differs when a minimum charge
    applies, so only a warning)."""
    weight, rate, total = (
        _num(values, "chargeable_weight"),
        _num(values, "rate"),
        _num(values, "total"),
    )
    expected = weight * rate
    if abs(expected - total) <= max(ITEM_TOLERANCE, abs(total) * 0.005):
        return None
    return stored(
        "计费重量 {weight:g} × 费率 {rate:g} = {expected}，但这一行运费是 "
        "{total}（如果是最低运费 M 类可以忽略）",
        weight=weight,
        rate=rate,
        expected=_money(expected),
        total=_money(total),
    )


def check_waybill_fees(values: Values) -> str | None:
    """Waybill charges: freight + insurance + packaging + other charges = total charges (absent
    items count as 0)."""
    parts = {N_("运费"): _num(values, "freight")}
    for name, label in (
        ("insurance_fee", N_("保价费")),
        ("packing_fee", N_("包装费")),
        ("other_fees", N_("其他费用")),
    ):
        if isinstance(values.get(name), float):
            parts[label] = _num(values, name)
    total = _num(values, "total")
    added = sum(parts.values())
    if abs(added - total) < TOLERANCE:
        return None
    shown = _plus(list(parts.items()))
    return stored(
        "{shown} = {added}，但费用合计是 {total}",
        shown=shown,
        added=_money(added),
        total=_money(total),
    )


def _plus(parts: list[tuple[str, float]]) -> str:
    """Charges added up: "freight 10.00 + insurance 2.00"."""
    return joined(
        " + ",
        (
            stored("{label} {amount}", label=stored(label), amount=_money(amount))
            for label, amount in parts
        ),
    )


_FAIL = re.compile(r"不合格|不符合|不通过|^\s*(?:fail(?:ed)?|ng|x|×|✗)\s*$", re.IGNORECASE)
_PASS = re.compile(
    r"合格|符合|通过|^\s*(?:pass(?:ed)?|ok|√|✓|conform(?:s|ing)?)\s*$", re.IGNORECASE
)


def _verdict(text: str) -> str | None:
    if _FAIL.search(text):
        return "fail"
    if _PASS.search(text):
        return "pass"
    return None


def check_qc_conclusion(values: Values) -> str | None:
    """An item judged non-conforming while the conclusion says conforming, or all items conforming
    while the conclusion says non-conforming. Checks only the document's internal consistency."""
    judgments = [
        (k, str(v))
        for k, v in values.items()
        if fnmatch.fnmatch(k, "items.*.judgment") and v not in (None, "")
    ]
    conclusion = _verdict(str(values.get("conclusion") or ""))
    verdicts = [_verdict(v) for _, v in judgments]
    failed = [v for (_, v), r in zip(judgments, verdicts, strict=True) if r == "fail"]
    if conclusion == "pass" and failed:
        return stored("有 {failed} 项判定不合格，结论却写的是合格", failed=len(failed))
    if conclusion == "fail" and verdicts and all(r == "pass" for r in verdicts):
        return stored(
            "{verdicts} 项判定都合格，结论却写的是不合格（请看是否还有表外的项目）",
            verdicts=len(verdicts),
        )
    return None


def _date_order(first: str, second: str, what: str) -> Callable[[Values], str | None]:
    noun = nested(what)

    def check(values: Values) -> str | None:
        try:
            a = date.fromisoformat(str(values[first]))
            b = date.fromisoformat(str(values[second]))
        except ValueError:
            return None  # Invalid dates themselves are handled by other rules.
        return None if a <= b else stored("{noun}：{a} 在 {b} 之后", noun=noun, a=a, b=b)

    return check


_CMA = re.compile(r"^\d{12}$")


def check_cma_no(values: Values) -> str | None:
    """Accreditation certificate number: the 12 digits below the CMA mark (CNCA [2015] No. 49)."""
    raw = str(values.get("cma_no") or "")
    if _CMA.match(re.sub(r"\s", "", raw)):
        return None
    return stored("资质认定证书编号「{raw}」应是 12 位数字，可能有字认错了", raw=raw)


LOGISTICS_RULES: tuple[Rule, ...] = (
    Rule(
        "packing_packages",
        N_("每行件数加起来 = 总件数"),
        ("packages.*.cartons", "total_packages"),
        _sum_equals("packages", "cartons", "total_packages", N_("件数")),
        "warning",
        flags=("packages.*.cartons", "total_packages"),
    ),
    Rule(
        "packing_quantity",
        N_("每行数量加起来 = 总数量"),
        ("packages.*.quantity", "total_quantity"),
        _sum_equals("packages", "quantity", "total_quantity", N_("数量")),
        "warning",
        flags=("packages.*.quantity", "total_quantity"),
    ),
    Rule(
        "packing_net",
        N_("每行净重加起来 = 总净重"),
        ("packages.*.net_weight", "total_net_weight"),
        _sum_equals("packages", "net_weight", "total_net_weight", N_("净重")),
        "warning",
        flags=("packages.*.net_weight", "total_net_weight"),
    ),
    Rule(
        "packing_gross",
        N_("每行毛重加起来 = 总毛重"),
        ("packages.*.gross_weight", "total_gross_weight"),
        _sum_equals("packages", "gross_weight", "total_gross_weight", N_("毛重")),
        "warning",
        flags=("packages.*.gross_weight", "total_gross_weight"),
    ),
    Rule(
        "packing_volume",
        N_("每行体积加起来 = 总体积"),
        ("packages.*.volume", "total_volume"),
        _sum_equals("packages", "volume", "total_volume", N_("体积")),
        "warning",
        flags=("packages.*.volume", "total_volume"),
    ),
    Rule(
        "net_not_over_gross",
        N_("净重不超过毛重（每一行）"),
        ("net_weight", "gross_weight"),
        check_net_not_over_gross,
        "warning",
        per_row="packages",
    ),
    Rule(
        "total_net_not_over_gross",
        N_("总净重不超过总毛重"),
        ("total_net_weight", "total_gross_weight"),
        check_total_net_not_over_gross,
    ),
    Rule(
        "delivery_quantity",
        N_("每行数量加起来 = 合计数量"),
        ("items.*.quantity", "total_quantity"),
        _sum_equals("items", "quantity", "total_quantity", N_("数量")),
        "warning",
        flags=("items.*.quantity", "total_quantity"),
    ),
    Rule(
        "waybill_fees",
        N_("运费 + 保价费 + 包装费 + 其他 = 费用合计"),
        ("freight", "total"),
        check_waybill_fees,
        "warning",
    ),
    Rule(
        "container_no",
        N_("集装箱号校验位正确（ISO 6346）"),
        ("container_no",),
        check_container_no,
        "warning",
        per_row="containers",
    ),
    Rule(
        "bl_packages",
        N_("每个集装箱的件数加起来 = 总件数"),
        ("containers.*.packages", "total_packages"),
        _sum_equals("containers", "packages", "total_packages", N_("件数")),
        "warning",
        flags=("containers.*.packages", "total_packages"),
    ),
    Rule(
        "bl_weight",
        N_("每个集装箱的毛重加起来 = 总毛重"),
        ("containers.*.gross_weight", "total_gross_weight"),
        _sum_equals("containers", "gross_weight", "total_gross_weight", N_("毛重")),
        "warning",
        flags=("containers.*.gross_weight", "total_gross_weight"),
    ),
    Rule(
        "bl_measurement",
        N_("每个集装箱的体积加起来 = 总体积"),
        ("containers.*.measurement", "total_measurement"),
        _sum_equals("containers", "measurement", "total_measurement", N_("体积")),
        "warning",
        flags=("containers.*.measurement", "total_measurement"),
    ),
    Rule(
        "bl_onboard_before_issue",
        N_("装船日期不晚于签发日期"),
        ("shipped_on_board_date", "issue_date"),
        _date_order("shipped_on_board_date", "issue_date", N_("装船日期晚于签发日期")),
        "warning",
    ),
    Rule("awb_no", N_("航空运单号校验位正确（IATA）"), ("awb_no",), check_awb_no, "warning"),
    Rule(
        "awb_pieces",
        N_("计费行件数加起来 = 总件数"),
        ("rates.*.pieces", "total_pieces"),
        _sum_equals("rates", "pieces", "total_pieces", N_("件数")),
        "warning",
        flags=("rates.*.pieces", "total_pieces"),
    ),
    Rule(
        "awb_weight",
        N_("计费行毛重加起来 = 总毛重"),
        ("rates.*.gross_weight", "total_gross_weight"),
        _sum_equals("rates", "gross_weight", "total_gross_weight", N_("毛重")),
        "warning",
        flags=("rates.*.gross_weight", "total_gross_weight"),
    ),
    Rule(
        "awb_rate_line",
        N_("计费重量 × 费率 = 运费（每一行）"),
        ("chargeable_weight", "rate", "total"),
        check_rate_line,
        "warning",
        per_row="rates",
    ),
    Rule(
        "qc_conclusion",
        N_("单项判定和检验结论一致"),
        ("items.*.judgment", "conclusion"),
        check_qc_conclusion,
        "warning",
        flags=("conclusion",),
    ),
    Rule(
        "qc_dates",
        N_("收样日期不晚于签发日期"),
        ("sample_received", "issue_date"),
        _date_order("sample_received", "issue_date", N_("收样日期晚于签发日期")),
        "warning",
    ),
    Rule(
        "cn_cma_no",
        N_("资质认定证书编号是 12 位数字（CMA）"),
        ("cma_no",),
        check_cma_no,
        "warning",
    ),
    Rule(
        "cn_sampling_dates",
        N_("抽样日期不晚于签发日期"),
        ("sampling_date", "issue_date"),
        _date_order("sampling_date", "issue_date", N_("抽样日期晚于签发日期")),
        "warning",
    ),
)

ALL_RULES = RECEIPT_RULES + TEMPLATE_RULES + LAB_RULES + LOGISTICS_RULES + COUNTRY_RULES

RULES: dict[str, Rule] = {rule.id: rule for rule in ALL_RULES}
"""Rule id → rule. Template files select rules by id."""

REGIONS: dict[str, str] = {
    **dict.fromkeys(
        (
            "cn_invoice_no",
            "cn_tax_balance",
            "cn_items_amount",
            "cn_items_tax",
            "cn_words",
            "fare_tax_balance",
            "air_total",
            "air_vat",
            "cn_cma_no",
            "cn_sampling_dates",
        ),
        "CN",
    ),
    **dict.fromkeys(
        (
            "au_buyer_1000",
            "seller_abn",
            "buyer_abn",
            "au_tax_invoice_title",
            "au_gst_eleventh",
            "merchant_abn",
            "ato_evidence",
            "ato_gst_credit",
            "gst_eleventh",
        ),
        "AU",
    ),
}
REGIONS.update(COUNTRY_REGIONS)  # Rules for statutory documents of individual countries.
"""Country-specific rules → country code (ISO 3166-1; CN China, AU Australia). All other rules are
generic. In the starter library, country templates use only generic rules and their own country's
rules, and generic templates only generic rules; my templates are unrestricted, and the editor
lists generic rules and those of the template's country first."""

REQUIRED = "required"
"""Id of the required check: a field with required = true in the template has no extracted value."""
OPTIONS = "options"
"""Id of the allowed-values check: the value is not among the template's allowed values."""
LOOKUP = "lookup"
"""Id of the reference table check: the value is not found in the reference table."""


class RuleValidator:
    name = "rules"

    def __init__(
        self,
        rules: Sequence[Rule] = RECEIPT_RULES,
        required: Sequence[tuple[str, str]] = (),
        options: Sequence[tuple[str, str, Sequence[str]]] = (),
        lookups: Sequence[tuple[str, str, frozenset[str]]] = (),
    ) -> None:
        self.rules = tuple(rules)
        self.required = tuple(required)
        """(field ID or table name, display name). Tables need at least one row with a value."""
        self.options = tuple(options)
        """(field ID or items.*.category, display name, allowed values). Warns when a value is not
        allowed."""
        self.lookups = tuple(lookups)
        """(field ID, reference table name, values present in the reference table). Warns when a
        value is not found."""

    def validate(self, fields: Sequence[ExtractedField]) -> ValidationResult:
        values: Values = {f.name: f.value for f in fields}
        issues: list[Issue] = []
        passed: list[str] = []
        skipped: list[str] = []
        if self.required:
            missing = self._missing(values)
            issues += missing
            if not missing:
                passed.append(REQUIRED)
        for found, rule_id in (
            (self._not_in_options(values), OPTIONS),
            (self._not_found(values), LOOKUP),
        ):
            issues += found
            if not found and (self.options if rule_id == OPTIONS else self.lookups):
                passed.append(rule_id)
        for rule in self.rules:
            found = self._per_row(rule, values) if rule.per_row else self._whole(rule, values)
            if found is None:
                skipped.append(rule.id)
            elif found:
                issues += found
            else:
                passed.append(rule.id)
        return ValidationResult(tuple(issues), tuple(passed), tuple(skipped))

    def _not_in_options(self, values: Values) -> list[Issue]:
        from jacara.templates.extras import normalize

        issues = []
        for pattern, label, options in self.options:
            allowed = {normalize(o) for o in options}
            for name in _expand(values, pattern):
                value = values.get(name)
                if value is None or normalize(str(value)) in allowed:
                    continue
                listed = join_items(options[:10])
                issues.append(
                    Issue(
                        OPTIONS,
                        stored("可选值：{label}", label=label),
                        stored("「{value}」不在可选值里（{listed}……）", value=value, listed=listed)
                        if len(options) > 10
                        else stored(
                            "「{value}」不在可选值里（{listed}）", value=value, listed=listed
                        ),
                        (name,),
                        "warning",
                    )
                )
        return issues

    def _not_found(self, values: Values) -> list[Issue]:
        from jacara.templates.extras import normalize

        issues = []
        for pattern, label, known in self.lookups:
            for name in _expand(values, pattern):
                value = values.get(name)
                if value is None or normalize(str(value)) in known:
                    continue
                issues.append(
                    Issue(
                        LOOKUP,
                        stored("参照表：{label}", label=label),
                        stored(
                            "「{value}」在参照表《{label}》里找不到，对照不出编号",
                            value=value,
                            label=label,
                        ),
                        (name,),
                        "warning",
                    )
                )
        return issues

    def _missing(self, values: Values) -> list[Issue]:
        issues = []
        for name, label in self.required:
            if _present(values, name) or _present(values, f"{name}.*.*"):
                continue
            issues.append(
                Issue(
                    REQUIRED,
                    stored("必填：{label}", label=label),
                    stored("必填字段「{label}」没有值", label=label),
                    (name,),
                    "error",
                )
            )
        return issues

    def _whole(self, rule: Rule, values: Values) -> list[Issue] | None:
        """Check the whole document once. None means skipped; an empty list means passed."""
        if not all(_present(values, name) for name in rule.needs):
            return None
        message = rule.check(values)
        if message is None:
            return []
        flagged = tuple(k for p in (rule.flags or rule.needs) for k in _expand(values, p))
        return [Issue(rule.id, rule.title, message, flagged, rule.severity)]

    def _per_row(self, rule: Rule, values: Values) -> list[Issue] | None:
        """Check each table row separately. Skipped when no row can be checked (missing columns)."""
        table = rule.per_row
        rows: dict[int, Values] = {}
        for name, value in values.items():
            cell = split_cell_name(name)
            if cell and cell[0] == table:
                rows.setdefault(cell[1], {})[cell[2]] = value
        issues: list[Issue] = []
        checked = False
        for index in sorted(rows):
            row = rows[index]
            if any(row.get(name) is None for name in rule.needs):
                continue
            checked = True
            message = rule.check(row)
            if message is not None:
                names = tuple(f"{table}.{index}.{c}" for c in (rule.flags or rule.needs))
                issues.append(
                    Issue(
                        rule.id,
                        rule.title,
                        stored("第 {index} 行：{message}", index=index, message=message),
                        names,
                        rule.severity,
                    )
                )
        return issues if checked else None


def _expand(values: Values, pattern: str) -> list[str]:
    """Field name, or a wildcard such as items.*.amount → names of fields that have values."""
    if "*" not in pattern:
        return [pattern]
    return [k for k, v in values.items() if v is not None and fnmatch.fnmatch(k, pattern)]


def _present(values: Values, pattern: str) -> bool:
    if "*" in pattern:
        return bool(_expand(values, pattern))
    return values.get(pattern) is not None
