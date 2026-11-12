"""按规则清单逐条检查的校验器，以及收据常用的规则。

新增一条规则：写一个检查函数，在 ALL_RULES 里加一行 Rule(...)，
再把它的编号写进要用它的模板（templates/*.toml、templates/starters/*.toml 的 rules）。

规则按字段 ID 找值，所以起步模板里同一个意思的字段用同一个 ID（total、issue_date……）。
"""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Callable, Sequence
from datetime import date

from jacara.core.cnmoney import parse_chinese_amount
from jacara.plugins.extractors.base import ExtractedField, split_cell_name
from jacara.plugins.validators.base import Issue, Rule, ValidationResult, Values

TOLERANCE = 0.005
"""金额比较的容差：差不到半分钱就算相等（小数在电脑里有微小误差）。"""


def _num(values: Values, name: str) -> float:
    """取出数字字段。规则只在字段都有值时才运行，抽取器也保证数字字段是 float。"""
    value = values[name]
    if not isinstance(value, float):
        raise TypeError(f"字段 {name} 应该是数字，实际是 {value!r}")
    return value


def _money(value: float) -> str:
    return f"{value:,.2f}"


def check_change(values: Values) -> str | None:
    cash, total, change = _num(values, "cash"), _num(values, "total"), _num(values, "change")
    expected = cash - total
    if abs(expected - change) < TOLERANCE:
        return None
    return (
        f"现金 {_money(cash)} − 总额 {_money(total)} = {_money(expected)}，"
        f"但找零是 {_money(change)}"
    )


def check_rounding(values: Values) -> str | None:
    subtotal, rounding = _num(values, "subtotal"), _num(values, "rounding")
    total = _num(values, "total")
    expected = subtotal + rounding
    if abs(expected - total) < TOLERANCE:
        return None
    return (
        f"小计 {_money(subtotal)} + 调整 {_money(rounding)} = {_money(expected)}，"
        f"但总额是 {_money(total)}"
    )


def check_cash_covers_total(values: Values) -> str | None:
    cash, total = _num(values, "cash"), _num(values, "total")
    if cash + TOLERANCE >= total:
        return None
    return f"现金 {_money(cash)} 比总额 {_money(total)} 还少"


def check_positive_total(values: Values) -> str | None:
    total = _num(values, "total")
    return None if total > 0 else f"总额是 {_money(total)}，应该大于 0"


def _date_exists(field: str) -> Callable[[Values], str | None]:
    def check(values: Values) -> str | None:
        try:
            date.fromisoformat(str(values[field]))
        except ValueError:
            return f"日期 {values[field]} 不存在"
        return None

    return check


def _date_not_future(field: str) -> Callable[[Values], str | None]:
    def check(values: Values) -> str | None:
        try:
            day = date.fromisoformat(str(values[field]))
        except ValueError:
            return None  # 不存在的日期由上一条规则负责
        if day <= date.today():
            return None
        return f"日期 {day} 在今天之后"

    return check


check_date_exists = _date_exists("date")
check_date_not_future = _date_not_future("date")


ITEM_TOLERANCE = 0.015
"""商品明细的容差：单价 × 数量常有四舍五入，差 1 分钱算对。"""


def _amounts(values: Values) -> list[float]:
    return [
        v
        for k, v in values.items()
        if fnmatch.fnmatch(k, "items.*.amount") and isinstance(v, float)
    ]


def check_items_sum(values: Values) -> str | None:
    """商品金额加起来应该等于小计；模板里没有小计，就和总额比。"""
    amounts = _amounts(values)
    target = "subtotal" if isinstance(values.get("subtotal"), float) else "total"
    expected = _num(values, target)
    added = sum(amounts)
    if abs(added - expected) < ITEM_TOLERANCE:
        return None
    label = "小计" if target == "subtotal" else "总额"
    return (
        f"{len(amounts)} 行商品金额加起来是 {_money(added)}，但{label}是 {_money(expected)}"
        "（可能漏了行、多了行，或者有税、折扣没算进去）"
    )


def check_item_line(values: Values) -> str | None:
    """一行商品：数量 × 单价 = 金额。"""
    quantity, price = _num(values, "quantity"), _num(values, "unit_price")
    amount = _num(values, "amount")
    expected = quantity * price
    # 折扣行的金额是负数（3x 2.00 → -6.00），比较绝对值
    if abs(abs(expected) - abs(amount)) < ITEM_TOLERANCE:
        return None
    return (
        f"数量 {quantity:g} × 单价 {_money(price)} = {_money(expected)}，但金额是 {_money(amount)}"
    )


def _sum(values: Values, pattern: str) -> tuple[int, float]:
    found = [v for k, v in values.items() if fnmatch.fnmatch(k, pattern) and isinstance(v, float)]
    return len(found), sum(found)


def _opt(values: Values, name: str) -> float:
    """可选的金额：没值算 0。"""
    value = values.get(name)
    return value if isinstance(value, float) else 0.0


def _text(values: Values, name: str) -> str:
    value = values.get(name)
    return "" if value is None else str(value).strip()


# —— 中国数电发票（国家税务总局公告 2024 年第 11 号）——


def check_cn_invoice_no(values: Values) -> str | None:
    number = _text(values, "invoice_no").replace(" ", "")
    if number.isdigit() and len(number) == 20:
        return None
    return f"发票号码 {number} 不是 20 位数字（数电发票的号码是 20 位）"


def check_cn_tax_balance(values: Values) -> str | None:
    amount, tax = _num(values, "amount_total"), _num(values, "tax_total")
    grand = _num(values, "grand_total")
    if abs(amount + tax - grand) < ITEM_TOLERANCE:
        return None
    return (
        f"合计金额 {_money(amount)} + 合计税额 {_money(tax)} = {_money(amount + tax)}，"
        f"但价税合计是 {_money(grand)}"
    )


def check_cn_items_amount(values: Values) -> str | None:
    count, added = _sum(values, "items.*.amount")
    expected = _num(values, "amount_total")
    if abs(added - expected) < ITEM_TOLERANCE:
        return None
    return f"{count} 行金额加起来是 {_money(added)}，但合计金额是 {_money(expected)}"


def check_cn_items_tax(values: Values) -> str | None:
    count, added = _sum(values, "items.*.tax")
    expected = _num(values, "tax_total")
    # 每行税额四舍五入，行多了合计会差几分
    if abs(added - expected) < max(ITEM_TOLERANCE, 0.01 * count):
        return None
    return f"{count} 行税额加起来是 {_money(added)}，但合计税额是 {_money(expected)}"


def check_cn_words(values: Values) -> str | None:
    words = _text(values, "grand_total_words")
    grand = _num(values, "grand_total")
    parsed = parse_chinese_amount(words)
    if parsed is None:
        return f"大写金额「{words}」读不懂，可能识别错了字"
    if abs(parsed - grand) < TOLERANCE:
        return None
    return f"大写「{words}」是 {_money(parsed)}，但小写价税合计是 {_money(grand)}"


def check_fare_tax_balance(values: Values) -> str | None:
    """铁路电子客票开给单位时：金额（不含税）+ 税额 = 票价。"""
    amount, tax, total = (
        _num(values, "amount_total"),
        _num(values, "tax_total"),
        _num(values, "total"),
    )
    if abs(amount + tax - total) < ITEM_TOLERANCE:
        return None
    return (
        f"金额 {_money(amount)} + 税额 {_money(tax)} = {_money(amount + tax)}，"
        f"但票价是 {_money(total)}"
    )


# —— 航空运输电子客票行程单（公告 2024 年第 9 号）——


def check_air_total(values: Values) -> str | None:
    fare, fuel = _num(values, "fare"), _num(values, "fuel_surcharge")
    fund, total = _num(values, "caac_fund"), _num(values, "total")
    other = _opt(values, "other_taxes")
    expected = fare + fuel + fund + other
    if abs(expected - total) < ITEM_TOLERANCE:
        return None
    return (
        f"票价 {_money(fare)} + 燃油附加费 {_money(fuel)} + 民航发展基金 {_money(fund)}"
        f"{f' + 其他税费 {_money(other)}' if other else ''} = {_money(expected)}，"
        f"但合计是 {_money(total)}"
    )


def _rate(text: str) -> float | None:
    """税率文字 → 小数：9% → 0.09。读不懂返回 None。"""
    try:
        return float(text.replace("%", "").strip()) / 100
    except ValueError:
        return None


def check_air_vat(values: Values) -> str | None:
    """增值税税额 = (票价 + 燃油附加费) ÷ (1 + 税率) × 税率。

    算法见财政部 税务总局 海关总署公告 2019 年第 39 号第六条。

    民航发展基金是政府性基金，不算增值税。改签费等也可能计税，所以只算警告。
    """
    rate = _rate(_text(values, "vat_rate"))
    if rate is None:
        return None
    fare, fuel, vat = _num(values, "fare"), _num(values, "fuel_surcharge"), _num(values, "vat")
    expected = (fare + fuel) / (1 + rate) * rate
    if abs(expected - vat) < 0.02:
        return None
    return (
        f"(票价 {_money(fare)} + 燃油附加费 {_money(fuel)}) ÷ (1 + {rate:.0%}) × {rate:.0%}"
        f" = {_money(expected)}，但增值税税额是 {_money(vat)}"
    )


# —— 通用发票（EN 16931 / Peppol 的金额关系）——


def check_lines_net(values: Values) -> str | None:
    count, added = _sum(values, "items.*.amount")
    expected = _num(values, "net_total")
    if abs(added - expected) < ITEM_TOLERANCE:
        return None
    return f"{count} 行金额加起来是 {_money(added)}，但不含税合计是 {_money(expected)}"


def check_net_tax_gross(values: Values) -> str | None:
    net, tax, gross = (
        _num(values, "net_total"),
        _num(values, "tax_total"),
        _num(values, "gross_total"),
    )
    if abs(net + tax - gross) < ITEM_TOLERANCE:
        return None
    return (
        f"不含税合计 {_money(net)} + 税额 {_money(tax)} = {_money(net + tax)}，"
        f"但含税合计是 {_money(gross)}"
    )


def check_due_not_above_gross(values: Values) -> str | None:
    due, gross = _num(values, "amount_due"), _num(values, "gross_total")
    if due <= gross + TOLERANCE:
        return None
    return f"应付金额 {_money(due)} 比含税合计 {_money(gross)} 还多"


def abn_valid(text: str) -> bool | None:
    """澳洲 ABN 校验位（ATO 公布的算法）。不是 11 位数字（例如欧洲 VAT 号）返回 None。"""
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
            return f"ABN {text} 的校验位不对（可能识别错了一位数字）"
        return None

    return check


def _tax_invoice(values: Values) -> bool:
    return "tax invoice" in _text(values, "document_title").lower()


def check_au_buyer_1000(values: Values) -> str | None:
    """ATO：含税 1,000 澳元及以上的税务发票要写买方名称或 ABN。"""
    gross = _num(values, "gross_total")
    if not _tax_invoice(values) or gross < 1000:
        return None
    if _text(values, "buyer_name") or _text(values, "buyer_tax_id"):
        return None
    return f"含税合计 {_money(gross)} ≥ 1,000，澳洲税务发票要写买方名称或 ABN，这里没有"


def check_au_tax_invoice_title(values: Values) -> str | None:
    """ATO：税务发票要写明是税务发票。"""
    if _tax_invoice(values):
        return None
    return f"单据标题是「{_text(values, 'document_title')}」，澳洲税务发票要写明 Tax invoice"


def check_au_gst_eleventh(values: Values) -> str | None:
    """澳洲 GST 10%：含税总额里的 GST 最多是 1/11（有免 GST 的项目时会更少）。"""
    gst, gross = _num(values, "tax_total"), _num(values, "gross_total")
    if gst <= gross / 11 + ITEM_TOLERANCE:
        return None
    return f"GST {_money(gst)} 比含 GST 总额 {_money(gross)} 的 1/11（{_money(gross / 11)}）还多"


# —— 收据（ATO：收据五项、82.50 澳元以上要税务发票）——


def check_ato_evidence(values: Values) -> str | None:
    """ATO 认可的收据要有：商家、日期、买了什么、金额（金额由 needs 保证）。"""
    missing = [
        label
        for label, ok in (
            ("商家名称", bool(_text(values, "merchant"))),
            ("日期", bool(_text(values, "date"))),
            (
                "买了什么（明细或描述）",
                bool(_text(values, "description")) or bool(_amounts(values)),
            ),
        )
        if not ok
    ]
    if not missing:
        return None
    return f"做报销凭证还缺：{'、'.join(missing)}"


def check_ato_gst_credit(values: Values) -> str | None:
    """总额超过 82.50 澳元，要抵 GST 得是税务发票：写明 Tax Invoice、商家 ABN、GST 金额。"""
    total = _num(values, "total")
    if total <= 82.50:
        return None
    missing = [
        label
        for label, ok in (
            ("“Tax Invoice”字样", _tax_invoice(values)),
            ("商家 ABN", bool(_text(values, "merchant_abn"))),
            ("GST 金额", isinstance(values.get("gst"), float)),
        )
        if not ok
    ]
    if not missing:
        return None
    return f"总额 {_money(total)} 超过 82.50，要抵 GST 还缺：{'、'.join(missing)}"


def check_gst_eleventh(values: Values) -> str | None:
    """澳洲 GST 10%：含税价里的 GST 最多是总额的 1/11（有免税商品时会更少）。"""
    gst, total = _num(values, "gst"), _num(values, "total")
    if gst <= total / 11 + ITEM_TOLERANCE:
        return None
    return f"GST {_money(gst)} 比总额 {_money(total)} 的 1/11（{_money(total / 11)}）还多"


def check_receipt_balance(values: Values) -> str | None:
    """小计 − 折扣 + 附加费 + 小费 + 调整 = 总额（没写的项按 0 算）。"""
    subtotal, total = _num(values, "subtotal"), _num(values, "total")
    discount = abs(_opt(values, "discount"))
    surcharge, tip = _opt(values, "surcharge"), _opt(values, "tip")
    rounding = _opt(values, "rounding")
    expected = subtotal - discount + surcharge + tip + rounding
    if abs(expected - total) < ITEM_TOLERANCE:
        return None
    parts = [f"小计 {_money(subtotal)}"]
    parts += [f"− 折扣 {_money(discount)}"] if discount else []
    parts += [f"+ 附加费 {_money(surcharge)}"] if surcharge else []
    parts += [f"+ 小费 {_money(tip)}"] if tip else []
    parts += [f"+ 调整 {_money(rounding)}"] if rounding else []
    return f"{' '.join(parts)} = {_money(expected)}，但总额是 {_money(total)}"


# —— 酒店账单 ——


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
    return f"离店 {stay[1]} 不在入住 {stay[0]} 之后"


def check_stay_nights(values: Values) -> str | None:
    stay = _stay(values)
    nights = _num(values, "nights")
    if stay is None or (stay[1] - stay[0]).days == nights:
        return None
    return (
        f"入住 {stay[0]} 到离店 {stay[1]} 是 {(stay[1] - stay[0]).days} 晚，但写的是 {nights:g} 晚"
    )


RECEIPT_RULES: tuple[Rule, ...] = (
    Rule("total_positive", "总额大于 0", ("total",), check_positive_total),
    Rule("date_exists", "日期真实存在", ("date",), check_date_exists),
    Rule("date_not_future", "日期不在未来", ("date",), check_date_not_future, "warning"),
    Rule("rounding", "小计 + 调整 = 总额", ("subtotal", "rounding", "total"), check_rounding),
    Rule("cash_covers_total", "现金不少于总额", ("cash", "total"), check_cash_covers_total),
    Rule("change", "现金 − 总额 = 找零", ("cash", "total", "change"), check_change),
    Rule(
        "items_sum",
        "商品金额合计 = 小计或总额",
        ("items.*.amount", "total"),
        check_items_sum,
        "warning",  # 税、服务费、折扣常常单独列，对不上不一定是错
        flags=("items.*.amount",),  # 只标商品金额，不标总额：总额更可能是对的
    ),
    Rule(
        "item_line",
        "数量 × 单价 = 金额",
        ("quantity", "unit_price", "amount"),
        check_item_line,
        per_row="items",
    ),
)

TEMPLATE_RULES: tuple[Rule, ...] = (
    Rule("issue_date_exists", "开票日期真实存在", ("issue_date",), _date_exists("issue_date")),
    Rule(
        "issue_date_not_future",
        "开票日期不在未来",
        ("issue_date",),
        _date_not_future("issue_date"),
        "warning",
    ),
    Rule("cn_invoice_no", "发票号码是 20 位数字", ("invoice_no",), check_cn_invoice_no),
    Rule(
        "cn_tax_balance",
        "合计金额 + 合计税额 = 价税合计",
        ("amount_total", "tax_total", "grand_total"),
        check_cn_tax_balance,
    ),
    Rule(
        "cn_items_amount",
        "明细金额合计 = 合计金额",
        ("items.*.amount", "amount_total"),
        check_cn_items_amount,
        "warning",
        flags=("items.*.amount",),
    ),
    Rule(
        "cn_items_tax",
        "明细税额合计 = 合计税额",
        ("items.*.tax", "tax_total"),
        check_cn_items_tax,
        "warning",
        flags=("items.*.tax",),
    ),
    Rule(
        "cn_words",
        "大写金额 = 小写价税合计",
        ("grand_total_words", "grand_total"),
        check_cn_words,
    ),
    Rule(
        "fare_tax_balance",
        "金额 + 税额 = 票价",
        ("amount_total", "tax_total", "total"),
        check_fare_tax_balance,
    ),
    Rule(
        "air_total",
        "票价 + 燃油 + 民航发展基金 + 其他税费 = 合计",
        ("fare", "fuel_surcharge", "caac_fund", "total"),
        check_air_total,
        "warning",
    ),
    Rule(
        "air_vat",
        "增值税税额 = (票价 + 燃油) ÷ (1 + 税率) × 税率",
        ("fare", "fuel_surcharge", "vat_rate", "vat"),
        check_air_vat,
        "warning",
    ),
    Rule(
        "lines_net",
        "明细金额合计 = 不含税合计",
        ("items.*.amount", "net_total"),
        check_lines_net,
        "warning",
        flags=("items.*.amount",),
    ),
    Rule(
        "net_tax_gross",
        "不含税合计 + 税额 = 含税合计",
        ("net_total", "tax_total", "gross_total"),
        check_net_tax_gross,
    ),
    Rule(
        "due_not_above_gross",
        "应付金额不超过含税合计",
        ("amount_due", "gross_total"),
        check_due_not_above_gross,
        "warning",
    ),
    Rule(
        "au_buyer_1000",
        "1,000 澳元以上的税务发票写了买方",
        ("gross_total",),
        check_au_buyer_1000,
        "warning",
        flags=("buyer_name",),
    ),
    Rule("seller_abn", "卖方 ABN 校验位正确", ("seller_tax_id",), _abn_check("seller_tax_id")),
    Rule("buyer_abn", "买方 ABN 校验位正确", ("buyer_tax_id",), _abn_check("buyer_tax_id")),
    Rule(
        "au_tax_invoice_title",
        "写明是税务发票（ATO）",
        ("document_title",),
        check_au_tax_invoice_title,
        "warning",
    ),
    Rule(
        "au_gst_eleventh",
        "GST 不超过含 GST 总额的 1/11",
        ("tax_total", "gross_total"),
        check_au_gst_eleventh,
        "warning",
    ),
    Rule("merchant_abn", "商家 ABN 校验位正确", ("merchant_abn",), _abn_check("merchant_abn")),
    Rule(
        "ato_evidence",
        "收据有商家、日期、内容、金额（ATO）",
        ("total",),
        check_ato_evidence,
        "warning",
        flags=("merchant", "date"),
    ),
    Rule(
        "ato_gst_credit",
        "超过 82.50 澳元是税务发票（ATO）",
        ("total",),
        check_ato_gst_credit,
        "warning",
        flags=("document_title", "merchant_abn", "gst"),
    ),
    Rule("gst_eleventh", "GST 不超过总额的 1/11", ("gst", "total"), check_gst_eleventh, "warning"),
    Rule(
        "receipt_balance",
        "小计 − 折扣 + 附加费 + 小费 + 调整 = 总额",
        ("subtotal", "total"),
        check_receipt_balance,
        "warning",
    ),
    Rule("travel_date_exists", "乘车日期真实存在", ("travel_date",), _date_exists("travel_date")),
    Rule("stay_order", "离店在入住之后", ("check_in", "check_out"), check_stay_order),
    Rule(
        "stay_nights",
        "晚数 = 离店 − 入住",
        ("check_in", "check_out", "nights"),
        check_stay_nights,
        "warning",
    ),
)

_LAB_NUMBER = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*$")
_LAB_RANGE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*(?:-|－|–|—|~|～|至|--)\s*(-?\d+(?:\.\d+)?)\s*$")
_LAB_BOUND = re.compile(r"^\s*(<=?|≤|＜|>=?|≥|＞)\s*(-?\d+(?:\.\d+)?)\s*$")


def _lab_reference(text: str) -> tuple[float | None, float | None] | None:
    """参考范围 → (下限, 上限)：3.5-9.5、<5.0、≥1.0。定性的（阴性）、读不出的返回 None。"""
    if m := _LAB_RANGE.match(text):
        return float(m[1]), float(m[2])
    if m := _LAB_BOUND.match(text):
        value = float(m[2])
        return (None, value) if m[1] in ("<", "<=", "≤", "＜") else (value, None)
    return None


def _lab_flag(text: str) -> str | None:
    """异常提示 → "high"、"low"、"other"（阳性、*）；没有提示是 None。"""
    t = text.strip().upper()
    if not t:
        return None
    if "↑" in t or "高" in t or t in ("H", "HH", "HIGH"):
        return "high"
    if "↓" in t or "低" in t or t in ("L", "LL", "LOW"):
        return "low"
    return "other"


def check_lab_flag(values: Values) -> str | None:
    """检验结果超出参考范围却没标高低、或者在范围内却标了高低（只查数字结果和数字参考范围）。"""
    result = _LAB_NUMBER.match(str(values.get("result") or ""))
    reference = _lab_reference(str(values.get("reference") or ""))
    if result is None or reference is None:
        return None  # 定性结果、<0.1 这种、读不出的参考范围不查
    value = float(result[1])
    low, high = reference
    flag = _lab_flag(str(values.get("flag") or ""))
    shown = str(values.get("reference")).strip()
    if high is not None and value > high and flag != "high":
        return f"结果 {value:g} 高于参考范围 {shown}，但没有标 ↑（偏高）"
    if low is not None and value < low and flag != "low":
        return f"结果 {value:g} 低于参考范围 {shown}，但没有标 ↓（偏低）"
    inside = (low is None or value >= low) and (high is None or value <= high)
    if inside and flag in ("high", "low"):
        return f"结果 {value:g} 在参考范围 {shown} 内，却标了{'偏高' if flag == 'high' else '偏低'}"
    return None


LAB_RULES: tuple[Rule, ...] = (
    Rule(
        "lab_flag",
        "超出参考范围的结果标了高低",
        ("result", "reference"),
        check_lab_flag,
        "warning",
        per_row="results",
        flags=("result", "flag"),
    ),
)

ALL_RULES = RECEIPT_RULES + TEMPLATE_RULES + LAB_RULES

RULES: dict[str, Rule] = {rule.id: rule for rule in ALL_RULES}
"""规则编号 → 规则。模板文件里按编号选规则。"""

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
"""只适用于某个国家的规则 → 国家代码（ISO 3166-1，CN 中国、AU 澳大利亚）。
其他规则是通用的。起步模板库里，国家模板只用通用规则和本国的规则，通用模板只用通用规则；
"我的模板"不限制，编辑器默认先列通用的和本模板所属国家的（ADR 0037）。"""

REQUIRED = "required"
"""必填检查的编号：模板里 required = true 的字段没抽到值。"""
OPTIONS = "options"
"""可选值检查：值不在模板写的可选值里。"""
LOOKUP = "lookup"
"""参照表检查：值在参照表里找不到。"""


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
        """（字段 ID 或表名，中文名）。表要至少一行有值。"""
        self.options = tuple(options)
        """（字段 ID 或 items.*.category，中文名，可选值）。值不在可选值里就提醒。"""
        self.lookups = tuple(lookups)
        """（字段 ID，参照表名，参照表里能对上的值）。值在参照表里找不到就提醒。"""

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
                issues.append(
                    Issue(
                        OPTIONS,
                        f"可选值：{label}",
                        f"「{value}」不在可选值里（{'、'.join(options[:10])}"
                        f"{'……' if len(options) > 10 else ''}）",
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
                        f"参照表：{label}",
                        f"「{value}」在参照表《{label}》里找不到，对照不出编号",
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
                Issue(REQUIRED, f"必填：{label}", f"必填字段「{label}」没有值", (name,), "error")
            )
        return issues

    def _whole(self, rule: Rule, values: Values) -> list[Issue] | None:
        """整张单据检查一次。返回 None 表示跳过，空列表表示通过。"""
        if not all(_present(values, name) for name in rule.needs):
            return None
        message = rule.check(values)
        if message is None:
            return []
        flagged = tuple(k for p in (rule.flags or rule.needs) for k in _expand(values, p))
        return [Issue(rule.id, rule.title, message, flagged, rule.severity)]

    def _per_row(self, rule: Rule, values: Values) -> list[Issue] | None:
        """表格的每一行单独检查。一行都没法检查（缺列）就算跳过。"""
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
                    Issue(rule.id, rule.title, f"第 {index} 行：{message}", names, rule.severity)
                )
        return issues if checked else None


def _expand(values: Values, pattern: str) -> list[str]:
    """字段名，或 items.*.amount 这样的通配写法 → 实际有值的字段名。"""
    if "*" not in pattern:
        return [pattern]
    return [k for k, v in values.items() if v is not None and fnmatch.fnmatch(k, pattern)]


def _present(values: Values, pattern: str) -> bool:
    if "*" in pattern:
        return bool(_expand(values, pattern))
    return values.get(pattern) is not None
