# ruff: noqa: E501  Multilingual rule names, messages and legal citations are harder to read when wrapped.
"""Rules for statutory documents of individual countries and regions.

Each rule covers one country; its basis is cited in comments (regulation name and section,
verified 2026-11). All are warnings: a wrong tax id check digit usually means OCR misread one
digit, and wrong rates or thresholds need a person to look; no conclusion is drawn on the user's
behalf."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date

from jacara.core.i18n import N_, join_items, join_notes, nested, stored
from jacara.plugins.validators.base import Rule, Values

MONEY = 0.05
"""Tolerance when comparing tax with "amount × rate": countries round to the cent, so a few cents of
difference count as correct."""


def _text(values: Values, name: str) -> str:
    value = values.get(name)
    return "" if value is None else str(value).strip()


def _number(values: Values, name: str) -> float | None:
    value = values.get(name)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _day(values: Values, name: str) -> date | None:
    try:
        return date.fromisoformat(_text(values, name))
    except ValueError:
        return None


def _compact(text: str) -> str:
    return re.sub(r"[\s\-./]", "", text).upper()


def _rate(text: str) -> float | None:
    """ "10%", "10", "0.1" → 10.0; values such as "exempt" return None."""
    m = re.search(r"\d+(?:[.,]\d+)?", text)
    if m is None:
        return None
    rate = float(m[0].replace(",", "."))
    return rate * 100 if rate < 1 and "%" not in text and rate != 0 else rate


def _id_rule(
    field: str, valid: Callable[[str], bool | None], what: str
) -> Callable[[Values], str | None]:
    """Number check: valid returns True for valid, False for invalid, None when it is not this kind
    of number (no warning)."""
    label = nested(what)

    def check(values: Values) -> str | None:
        raw = _text(values, field)
        if valid(raw) is False:
            return stored("{label}「{raw}」格式或校验位不对，可能有字认错了", label=label, raw=raw)
        return None

    return check


def _allowed_rates(rates: tuple[float, ...], where: str) -> Callable[[Values], str | None]:
    """Whether the tax rate of this row is a current rate of the country (per_row)."""
    place = nested(where)

    def check(values: Values) -> str | None:
        raw = _text(values, "tax_rate")
        rate = _rate(raw)
        if rate is None or any(abs(rate - r) < 0.001 for r in rates):
            return None
        shown = join_items(f"{r:g}%" for r in rates)
        return stored(
            "税率「{raw}」不是{place}现行的税率（{shown}）", raw=raw, place=place, shown=shown
        )

    return check


def _tax_of(rate: float, base: str, tax: str, where: str) -> Callable[[Values], str | None]:
    """Tax = net amount × statutory rate (a few cents of difference are accepted); a zero tax
    (exempt, zero-rated) is not checked."""
    basis = nested(where)

    def check(values: Values) -> str | None:
        amount, paid = _number(values, base), _number(values, tax)
        if amount is None or paid is None or paid == 0:
            return None
        expected = amount * rate / 100
        if abs(expected - paid) <= max(MONEY, amount * 0.0005):
            return None
        return stored(
            "税额 {paid:,.2f} 不等于 {amount:,.2f} × {rate:g}%（{expected:,.2f}）——{basis}",
            paid=paid,
            amount=amount,
            rate=rate,
            expected=expected,
            basis=basis,
        )

    return check


def _needs_over(
    limit: float, total: str, fields: tuple[str, ...], why: str
) -> Callable[[Values], str | None]:
    """Fields required when the amount exceeds a threshold (each country's simplified invoice
    threshold)."""
    reason = nested(why)

    def check(values: Values) -> str | None:
        amount = _number(values, total)
        if amount is None or amount <= limit:
            return None
        missing = [f for f in fields if not _text(values, f)]
        if not missing:
            return None
        return stored(
            "金额 {amount:,.2f} 超过 {limit:,.0f}，{reason}",
            amount=amount,
            limit=limit,
            reason=reason,
        )

    return check


def _title_has(words: tuple[str, ...], why: str) -> Callable[[Values], str | None]:
    reason = nested(why)

    def check(values: Values) -> str | None:
        title = _text(values, "document_title")
        if any(w.lower() in title.lower() for w in words):
            return None
        return stored("单据标题是「{title}」，{reason}", title=title, reason=reason)

    return check


# ---------- Japan: qualified invoice (Consumption Tax Act art. 57-4, Enforcement Order art. 70-10)
# ----------


def jp_registration_no(text: str) -> bool | None:
    """Registration number (登録番号): T + 13 digits (National Tax Agency). For corporations it is
    the corporate number; the check digit applies only to corporate numbers, so only the
    format is checked here."""
    return re.fullmatch(r"T\d{13}", _compact(text)) is not None


def check_jp_tax_per_rate(values: Values) -> str | None:
    """Consumption tax per rate (税率ごとに区分した消費税額): rounding happens once per rate per
    invoice (Enforcement Order art. 70-10). The consideration may be tax-exclusive
    or tax-inclusive; both are computed, and less than 1 yen of difference is
    accepted."""
    problems = []
    for rate, amount_field, tax_field in ((10, "amount_10", "tax_10"), (8, "amount_8", "tax_8")):
        amount, tax = _number(values, amount_field), _number(values, tax_field)
        if amount is None or tax is None:
            continue
        excl, incl = amount * rate / 100, amount * rate / (100 + rate)
        if min(abs(tax - excl), abs(tax - incl)) < 1:
            continue
        problems.append(
            stored(
                "{rate}% 対象 {amount:,.0f} 円の消費税額 {tax:,.0f} 円（应约为 {excl:,.0f} 或 {incl:,.0f}）",
                rate=rate,
                amount=amount,
                tax=tax,
                excl=excl,
                incl=incl,
            )
        )
    return join_notes(*problems) or None


# ---------- South Korea: tax invoice (VAT Act art. 32; art. 30 rate 10%) ----------


def kr_business_no(text: str) -> bool | None:
    """Business registration number (사업자등록번호), 10 digits: weights 1,3,7,1,3,7,1,3,5, plus
    (digit 9 × 5) // 10."""
    digits = re.sub(r"\D", "", text)
    if len(digits) != 10:
        return False if text else None
    d = [int(c) for c in digits]
    total = sum(x * w for x, w in zip(d[:9], (1, 3, 7, 1, 3, 7, 1, 3, 5), strict=True))
    total += d[8] * 5 // 10
    return (10 - total % 10) % 10 == d[9]


# ---------- Taiwan: uniform invoice (Uniform Invoice Regulations art. 9; Business Tax Act art. 10,
# 5%) ----------


def tw_ban(text: str) -> bool | None:
    """Unified business number (營利事業統一編號), 8 digits; since ROC year 112 the Ministry of
    Finance check is "sum divisible by 5" (with an alternative when digit 7 is 7)."""
    digits = re.sub(r"\D", "", text)
    if len(digits) != 8:
        return False if text else None
    total = 0
    for c, w in zip(digits, (1, 2, 1, 2, 1, 2, 4, 1), strict=True):
        p = int(c) * w
        total += p // 10 + p % 10
    if total % 5 == 0:
        return True
    return digits[6] == "7" and (total + 1) % 5 == 0


def tw_invoice_no(text: str) -> bool | None:
    """Invoice number (發票號碼): a 2-letter track followed by 8 digits."""
    return re.fullmatch(r"[A-Z]{2}\d{8}", _compact(text)) is not None


# ---------- Singapore: tax invoice (GST (General) Regulations reg 11; IRAS) ----------


def sg_gst_no(text: str) -> bool | None:
    """GST registration number: a UEN (8 digits + letter; 9 digits + letter; T/S/R + year + entity
    code + 4 digits + letter) or a GST number starting with M."""
    t = _compact(text)
    patterns = (
        r"\d{8}[A-Z]",
        r"\d{9}[A-Z]",
        r"[TSR]\d{2}[A-Z]{2}\d{4}[A-Z]",
        r"M[0-9A-Z]\d{7}[0-9A-Z]",
    )
    return any(re.fullmatch(p, t) for p in patterns)


def sg_rate_on(day: date) -> float:
    """GST rate: 8% from 2023, 9% from 2024-01-01 (IRAS)."""
    if day >= date(2024, 1, 1):
        return 9
    if day >= date(2023, 1, 1):
        return 8
    return 7


def check_sg_gst(values: Values) -> str | None:
    day = _day(values, "issue_date") or date.today()
    rate = sg_rate_on(day)
    return _tax_of(
        rate,
        "net_total",
        "tax_total",
        stored("新加坡 {day} 适用的 GST 是 {rate:g}%", day=day.isoformat(), rate=rate),
    )(values)


# ---------- India: tax invoice (CGST Rules 2017 Rule 46) ----------

_B36 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def in_gstin(text: str) -> bool | None:
    """GSTIN, 15 characters: 2-digit state code + 10-character PAN + entity code + Z + check
    character (base-36 Luhn)."""
    t = _compact(text)
    if not re.fullmatch(r"\d{2}[A-Z]{5}\d{4}[A-Z][0-9A-Z]Z[0-9A-Z]", t):
        return False if text else None
    total = 0
    for i, c in enumerate(t[:14]):
        p = _B36.index(c) * (2 if i % 2 else 1)
        total += p // 36 + p % 36
    return _B36[(36 - total % 36) % 36] == t[14]


def check_in_invoice_no(values: Values) -> str | None:
    """Rule 46(b): invoice numbers have at most 16 characters: letters, digits, "-" and "/" only."""
    raw = _text(values, "invoice_no")
    if len(raw) <= 16 and re.fullmatch(r"[A-Za-z0-9/\-]+", raw):
        return None
    return stored(
        "发票号「{raw}」不符合印度 GST 规则第 46 条 (b)：最多 16 个字符，只能用字母、数字、- 和 /",
        raw=raw,
    )


def check_in_tax_split(values: Values) -> str | None:
    """Intra-state supplies carry CGST + SGST (equal amounts), inter-state supplies IGST; never
    both."""
    cgst, sgst, igst = (_number(values, f) or 0 for f in ("cgst", "sgst", "igst"))
    if igst and (cgst or sgst):
        return stored("同一张发票既有 IGST 又有 CGST/SGST：跨邦只收 IGST，邦内只收 CGST + SGST")
    if (cgst or sgst) and abs(cgst - sgst) > MONEY:
        return stored(
            "CGST {cgst:,.2f} 和 SGST {sgst:,.2f} 应该相等（各占一半）", cgst=cgst, sgst=sgst
        )
    return None


# ---------- New Zealand: taxable supply information (GST Act 1985 s19F–19N; GST 15%) ----------


def nz_ird_no(text: str) -> bool | None:
    """IRD / GST number, 8 or 9 digits: weights 3,2,7,6,5,4,3,2 modulo 11; if the result is 10,
    recompute with 7,4,3,2,5,2,7,6."""
    digits = re.sub(r"\D", "", text)
    if len(digits) not in (8, 9):
        return False if text else None
    digits = digits.zfill(9)
    if not 10_000_000 < int(digits) < 150_000_000:
        return False
    base, check = [int(c) for c in digits[:8]], int(digits[8])
    for weights in ((3, 2, 7, 6, 5, 4, 3, 2), (7, 4, 3, 2, 5, 2, 7, 6)):
        r = sum(d * w for d, w in zip(base, weights, strict=True)) % 11
        c = 0 if r == 0 else 11 - r
        if c != 10:
            return c == check
    return False


def check_nz_gst_3_23(values: Values) -> str | None:
    gst, gross = _number(values, "tax_total"), _number(values, "gross_total")
    if gst is None or gross is None or gst <= gross * 3 / 23 + MONEY:
        return None
    return stored(
        "GST {gst:,.2f} 比含税总额 {gross:,.2f} 的 3/23（{limit:,.2f}）还多（GST 15%）",
        gst=gst,
        gross=gross,
        limit=gross * 3 / 23,
    )


# ---------- United Kingdom: VAT invoice (VAT Regulations 1995 reg 14, 16; HMRC VAT Notice 700/21)
# ----------


def gb_vat_no(text: str) -> bool | None:
    """GB VAT number: GB + 9 digits (optionally a 3-digit branch); the first 7 digits weighted 8..2
    plus the last two must be 0 modulo 97 (or 0 after adding 55)."""
    t = _compact(text).removeprefix("GB")
    if t.startswith(("GD", "HA")):
        return None  # Government department and health authority numbers follow different rules.
    if not re.fullmatch(r"\d{9}(\d{3})?", t):
        return False if text else None
    d = [int(c) for c in t[:9]]
    total = sum(x * w for x, w in zip(d[:7], range(8, 1, -1), strict=True)) + d[7] * 10 + d[8]
    return total % 97 == 0 or (total + 55) % 97 == 0


# ---------- Germany: Rechnung (§14 Abs. 4 UStG; Kleinbetragsrechnung §33 UStDV; Beleg §6
# KassenSichV) ----------


def de_ust_idnr(text: str) -> bool | None:
    """USt-IdNr.: DE + 9 digits; the last digit follows ISO 7064 MOD 11,10. A Steuernummer alone is
    not checked."""
    t = _compact(text)
    if not t.startswith("DE"):
        return None
    if not re.fullmatch(r"DE\d{9}", t):
        return False
    product = 10
    for c in t[2:10]:
        s = (int(c) + product) % 10 or 10
        product = 2 * s % 11
    check = 11 - product
    return (0 if check == 10 else check) == int(t[10])


def check_de_kassenbeleg(values: Values) -> str | None:
    """§6 KassenSichV: receipts must show transaction number, cash register serial number, TSE
    serial number, check value and signature counter."""
    names = {
        "transaction_no": N_("交易号"),
        "pos_serial": N_("收银系统序列号"),
        "tse_serial": N_("安全模块（TSE）序列号"),
        "tse_signature": N_("校验值"),
        "signature_counter": N_("签名计数器"),
    }
    missing = [label for field, label in names.items() if not _text(values, field)]
    if not missing:
        return None
    items = join_items(missing)
    return stored(
        "德国收银条例（KassenSichV）第 6 条要求的{items}没读到；没有安全模块信息的小票可能不合规，请看一眼原件",
        items=items,
    )


def check_de_kasse_times(values: Values) -> str | None:
    start, end = _text(values, "start_time"), _text(values, "end_time")
    if not start or not end or start <= end:
        return None
    return stored("交易开始时间 {start} 晚于结束时间 {end}", start=start, end=end)


# ---------- Thailand: tax invoice (Revenue Code s. 86/4; DG Notification No. 199; VAT 7% until 30
# Sep B.E. 2570) ----------


def th_tax_id(text: str) -> bool | None:
    """Taxpayer identification number (เลขประจำตัวผู้เสียภาษีอากร), 13 digits: the first 12 weighted
    13..2; (11 − sum mod 11) mod 10 is digit 13."""
    digits = re.sub(r"\D", "", text)
    if len(digits) != 13:
        return False if text else None
    total = sum(int(c) * w for c, w in zip(digits[:12], range(13, 1, -1), strict=True))
    return (11 - total % 11) % 10 == int(digits[12])


def check_th_vat(values: Values) -> str | None:
    """VAT 7% (Royal Decree No. 807 B.E. 2569: until 30 Sep B.E. 2570)."""
    day = _day(values, "issue_date")
    if day is not None and day > date(2027, 9, 30):
        return None  # The rate may change after the 7% period ends; do not check against 7% then.
    return _tax_of(7, "net_total", "tax_total", stored("泰国增值税 7%"))(values)


def check_th_buyer_branch(values: Values) -> str | None:
    """Director-General Notification No. 199: when the buyer is VAT-registered, the buyer's tax id
    and "head office / branch" (สำนักงานใหญ่ / สาขาที่) must be shown."""
    if _text(values, "buyer_tax_id") and not _text(values, "buyer_branch"):
        return stored("写了买方税号，但没写买方是总店还是第几号分店（泰国税务厅公告第 199 号）")
    return None


# ---------- Indonesia: Faktur Pajak (UU PPN Pasal 13 ayat 5; PER-11/PJ/2025 Pasal 33; PMK 131/2024)
# ----------


def id_npwp(text: str) -> bool | None:
    """NPWP: 15 digits (old) or 16 digits (NIK / new NPWP, PMK 112/2022)."""
    return len(re.sub(r"\D", "", text)) in (15, 16) if text else None


def id_faktur_no(text: str) -> bool | None:
    """Nomor Faktur Pajak: 16 digits (kode transaksi + status + NSFP) or the 17-digit Coretax
    number."""
    return len(re.sub(r"\D", "", text)) in (16, 17) if text else None


def check_id_ppn(values: Values) -> str | None:
    """PMK 131/2024: PPN 12% × DPP; for non-luxury goods DPP = 11/12 × harga jual (an effective
    11%)."""
    dpp, ppn, price = (_number(values, f) for f in ("dpp", "ppn", "net_total"))
    if dpp is None or ppn is None or ppn == 0:
        return None
    problems = []
    if abs(ppn - dpp * 0.12) > max(1, dpp * 0.0005):
        problems.append(
            stored(
                "增值税 {ppn:,.0f} 不等于 12% × 计税基础 {dpp:,.0f}（{expected:,.0f}）",
                ppn=ppn,
                dpp=dpp,
                expected=dpp * 0.12,
            )
        )
    if (
        price is not None
        and abs(dpp - price) > 1
        and abs(dpp - price * 11 / 12) > max(1, price * 0.0005)
    ):
        problems.append(
            stored(
                "计税基础 {dpp:,.0f} 既不是销售价 {price:,.0f}，也不是它的 11/12（{reduced:,.0f}）",
                dpp=dpp,
                price=price,
                reduced=price * 11 / 12,
            )
        )
    return join_notes(*problems) or None


# ---------- Philippines: invoice (EOPT Act RA 11976; RR 7-2024 Annex B; VAT 12%) ----------


def ph_tin(text: str) -> bool | None:
    """TIN: 9 digits + branch code (3 or 5 digits; RR 7-2024 example 123-456-789-00000)."""
    return re.fullmatch(r"\d{9}(\d{3}|\d{5})?", _compact(text)) is not None if text else None


# ---------- France: facture (CGI art. 242 nonies A; VAT rates art. 278 ff.) ----------


def _luhn(digits: str) -> bool:
    total = 0
    for i, c in enumerate(reversed(digits)):
        d = int(c) * (2 if i % 2 else 1)
        total += d - 9 if d > 9 else d
    return total % 10 == 0


def fr_siren(text: str) -> bool | None:
    """SIREN (9 digits) / SIRET (14 digits): Luhn."""
    digits = re.sub(r"\D", "", text)
    if len(digits) not in (9, 14):
        return False if text else None
    return _luhn(digits)


def fr_tva(text: str) -> bool | None:
    """Intra-community VAT number: FR + 2 check digits + SIREN; numeric check = (12 + 3 × (SIREN mod
    97)) mod 97."""
    t = _compact(text)
    if not t.startswith("FR"):
        return None
    if not re.fullmatch(r"FR[0-9A-Z]{2}\d{9}", t):
        return False
    key, siren = t[2:4], t[4:]
    if not key.isdigit():
        return None  # Letter check characters use a different algorithm.
    return int(key) == (12 + 3 * (int(siren) % 97)) % 97


# ---------- Italy: fattura (DPR 633/1972 art. 21; simplified art. 21-bis ≤ €400) ----------


def it_partita_iva(text: str) -> bool | None:
    """Partita IVA, 11 digits: odd positions summed, even positions doubled (minus 9 if above 9) and
    summed; (10 − sum mod 10) mod 10 is digit 11."""
    t = _compact(text).removeprefix("IT")
    if not re.fullmatch(r"\d{11}", t):
        return (
            None
            if re.fullmatch(r"[A-Z]{6}\d{2}[A-Z]\d{2}[A-Z]\d{3}[A-Z]", t)
            else (False if text else None)
        )
    total = 0
    for i, c in enumerate(t[:10]):
        d = int(c)
        if i % 2:
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        total += d
    return (10 - total % 10) % 10 == int(t[10])


# ---------- Spain: factura (RD 1619/2012 art. 6; simplified art. 4, 7) ----------

_DNI = "TRWAGMYFPDXBNJZSQVHLCKE"


def es_nif(text: str) -> bool | None:
    """NIF: DNI (8 digits + letter), NIE (X/Y/Z + 7 digits + letter), CIF (letter + 7 digits + check
    character)."""
    t = _compact(text).removeprefix("ES")
    if re.fullmatch(r"\d{8}[A-Z]", t):
        return _DNI[int(t[:8]) % 23] == t[8]
    if re.fullmatch(r"[XYZ]\d{7}[A-Z]", t):
        return _DNI[int("XYZ".index(t[0]).__str__() + t[1:8]) % 23] == t[8]
    if re.fullmatch(r"[ABCDEFGHJNPQRSUVW]\d{7}[0-9A-J]", t):
        digits = [int(c) for c in t[1:8]]
        even = sum(digits[1::2])
        odd = sum((d * 2) // 10 + (d * 2) % 10 for d in digits[0::2])
        control = (10 - (even + odd) % 10) % 10
        return t[8] in (str(control), "JABCDEFGHI"[control])
    return False if text else None


# ---------- Netherlands: factuur (Wet OB 1968 art. 35a; simplified invoice ≤ €100) ----------


def nl_btw_id(text: str) -> bool | None:
    """btw-id: NL + 9 digits + B + 2 digits; numbers issued since 2020 use mod 97, older numbers the
    11-proef; both are accepted."""
    t = _compact(text)
    if not re.fullmatch(r"NL\d{9}B\d{2}", t):
        return False if text else None
    as_number = "2321" + t[2:11] + "11" + t[12:]
    if int(as_number) % 97 == 1:
        return True
    d = [int(c) for c in t[2:11]]
    return (sum(x * w for x, w in zip(d[:8], range(9, 1, -1), strict=True)) - d[8]) % 11 == 0


# ---------- Poland: faktura (VAT Act art. 106e; KSeF 2026) ----------


def pl_nip(text: str) -> bool | None:
    """NIP, 10 digits: weights 6,5,7,2,3,4,5,6,7; the sum mod 11 is digit 10 (numbers yielding 10
    are never issued)."""
    digits = re.sub(r"\D", "", _compact(text).removeprefix("PL"))
    if len(digits) != 10:
        return False if text else None
    total = sum(int(c) * w for c, w in zip(digits[:9], (6, 5, 7, 2, 3, 4, 5, 6, 7), strict=True))
    return total % 11 == int(digits[9])


# ---------- Canada: GST/HST (Input Tax Credit Information (GST/HST) Regulations s.3) ----------


def ca_business_no(text: str) -> bool | None:
    """Business Number: 9 digits (Luhn) + RT + 4-digit account number."""
    t = _compact(text)
    m = re.fullmatch(r"(\d{9})(RT\d{4})?", t)
    if m is None:
        return False if text else None
    return _luhn(m[1])


# ---------- United Arab Emirates, Saudi Arabia ----------


def digits_15(text: str) -> bool | None:
    """UAE TRN and Saudi VAT numbers: 15 digits."""
    return re.fullmatch(r"\d{15}", re.sub(r"\D", "", text)) is not None if text else None


# ---------- Vietnam: e-invoice (ND 123/2020, amended by ND 70/2025; NQ 204/2025/QH15 8% reduction)
# ----------


def vn_tax_code(text: str) -> bool | None:
    """Mã số thuế: 10 digits, or 10 digits + 3 digits (dependent unit)."""
    return re.fullmatch(r"\d{10}(\d{3})?", _compact(text)) is not None if text else None


def check_vn_reduced_rate(values: Values) -> str | None:
    """8% is the temporary reduction under NQ 204/2025/QH15 (until 2026-12-31); invoices issued
    afterwards should not use 8%."""
    rate, day = _rate(_text(values, "tax_rate")), _day(values, "issue_date")
    if rate is None or day is None or abs(rate - 8) > 0.001 or day <= date(2026, 12, 31):
        return None
    return stored(
        "{day} 开的发票税率是 8%：越南国会第 204/2025 号决议的减税到 2026-12-31 为止",
        day=day.isoformat(),
    )


# ---------- United States: clinical laboratory reports (42 CFR 493.1291 CLIA) ----------


def us_clia_no(text: str) -> bool | None:
    """CLIA number: 2 digits + D + 7 digits (for example 05D0123456)."""
    return re.fullmatch(r"\d{2}D\d{7}", _compact(text)) is not None if text else None


def _id(
    rule_id: str, title: str, field: str, valid: Callable[[str], bool | None], what: str
) -> Rule:
    return Rule(rule_id, title, (field,), _id_rule(field, valid, what), "warning")


COUNTRY_RULES: tuple[Rule, ...] = (
    # Japan
    _id(
        "jp_registration_no",
        N_("发票登记号是 T + 13 位数字"),
        "seller_tax_id",
        jp_registration_no,
        N_("发票登记号"),
    ),
    Rule(
        "jp_tax_per_rate",
        N_("各税率的消费税额对得上（每张发票每个税率只进位一次）"),
        ("gross_total",),
        check_jp_tax_per_rate,
        "warning",
        flags=("amount_10", "tax_10", "amount_8", "tax_8"),
        requires=("amount_10", "tax_10", "amount_8", "tax_8"),
    ),
    Rule(
        "jp_rates",
        N_("税率是 10% 或 8%"),
        ("tax_rate",),
        _allowed_rates((10, 8, 0), N_("日本")),
        "warning",
        per_row="items",
    ),
    # South Korea
    _id(
        "kr_seller_no",
        N_("供应方事业者登录号码校验位"),
        "seller_tax_id",
        kr_business_no,
        N_("供应方事业者登录号码"),
    ),
    _id(
        "kr_buyer_no",
        N_("接收方事业者登录号码校验位"),
        "buyer_tax_id",
        kr_business_no,
        N_("接收方事业者登录号码"),
    ),
    Rule(
        "kr_vat_10",
        N_("附加价值税 = 供应价额 × 10%"),
        ("net_total", "tax_total"),
        _tax_of(10, "net_total", "tax_total", N_("韩国附加价值税法第 30 条：税率 10%")),
        "warning",
    ),
    # Taiwan
    _id(
        "tw_seller_ban",
        N_("卖方统一编号检查码"),
        "seller_tax_id",
        tw_ban,
        N_("卖方统一编号"),
    ),
    _id(
        "tw_buyer_ban",
        N_("买受人统一编号检查码"),
        "buyer_tax_id",
        tw_ban,
        N_("买受人统一编号"),
    ),
    _id(
        "tw_invoice_no",
        N_("发票号码是 2 个英文字母 + 8 位数字"),
        "invoice_no",
        tw_invoice_no,
        N_("发票号码"),
    ),
    Rule(
        "tw_tax_5",
        N_("营业税 = 销售额 × 5%"),
        ("net_total", "tax_total"),
        _tax_of(5, "net_total", "tax_total", N_("台湾营业税法第 10 条：税率 5%")),
        "warning",
    ),
    # Singapore
    _id(
        "sg_gst_no",
        N_("GST 登记号格式"),
        "seller_tax_id",
        sg_gst_no,
        N_("GST 登记号"),
    ),
    Rule(
        "sg_gst_rate",
        N_("GST = 不含税金额 × 当时的税率（2024 年起 9%）"),
        ("net_total", "tax_total"),
        check_sg_gst,
        "warning",
    ),
    Rule(
        "sg_full_over_1000",
        N_("超过 S$1,000 要有客户名称和地址"),
        ("gross_total",),
        _needs_over(
            1000,
            "gross_total",
            ("buyer_name", "buyer_address"),
            N_("新加坡税务局规定：超过 S$1,000 不能用简易税务发票，要写客户名称和地址"),
        ),
        "warning",
        requires=("buyer_name", "buyer_address"),
    ),
    # Malaysia
    Rule(
        "my_validated",
        N_("已经过马来西亚税务局（LHDN）验证（有唯一编号 UIN）"),
        ("gross_total",),
        lambda v: (
            None
            if _text(v, "uin")
            else stored(
                "没读到马来西亚税务局（LHDN）的唯一编号（UIN）：不是已验证的电子发票，或者二维码旁的编号没认出来"
            )
        ),
        "warning",
        requires=("uin",),
    ),
    # Vietnam
    _id(
        "vn_seller_mst",
        N_("卖方税号是 10 或 13 位"),
        "seller_tax_id",
        vn_tax_code,
        N_("卖方税号"),
    ),
    _id(
        "vn_buyer_mst",
        N_("买方税号是 10 或 13 位"),
        "buyer_tax_id",
        vn_tax_code,
        N_("买方税号"),
    ),
    Rule(
        "vn_rates",
        N_("增值税税率是 0%、5%、8% 或 10%"),
        ("tax_rate",),
        _allowed_rates((0, 5, 8, 10), N_("越南")),
        "warning",
    ),
    Rule(
        "vn_reduced_8",
        N_("8% 减税税率只到 2026-12-31"),
        ("tax_rate", "issue_date"),
        check_vn_reduced_rate,
        "warning",
    ),
    # India
    _id(
        "in_seller_gstin",
        N_("供应商 GSTIN 校验位"),
        "seller_tax_id",
        in_gstin,
        N_("供应商 GSTIN"),
    ),
    _id(
        "in_buyer_gstin",
        N_("收货方 GSTIN 校验位"),
        "buyer_tax_id",
        in_gstin,
        N_("收货方 GSTIN"),
    ),
    Rule(
        "in_invoice_no",
        N_("发票号不超过 16 个字符"),
        ("invoice_no",),
        check_in_invoice_no,
        "warning",
    ),
    Rule(
        "in_tax_split",
        N_("IGST 和 CGST、SGST 不同时出现，CGST = SGST"),
        ("gross_total",),
        check_in_tax_split,
        "warning",
        flags=("cgst", "sgst", "igst"),
        requires=("cgst", "sgst", "igst"),
    ),
    # New Zealand
    _id(
        "nz_gst_no",
        N_("GST 号校验位"),
        "seller_tax_id",
        nz_ird_no,
        N_("GST 号"),
    ),
    Rule(
        "nz_gst_3_23",
        N_("GST 不超过含税总额的 3/23（15%）"),
        ("tax_total", "gross_total"),
        check_nz_gst_3_23,
        "warning",
    ),
    Rule(
        "nz_buyer_over_1000",
        N_("超过 $1,000 要写买方名称"),
        ("gross_total",),
        _needs_over(
            1000,
            "gross_total",
            ("buyer_name",),
            N_("新西兰商品及服务税法第 19K 条：超过 $1,000 要有买方名称和一项识别信息"),
        ),
        "warning",
        requires=("buyer_name",),
    ),
    # United Kingdom
    _id(
        "gb_vat_no",
        N_("VAT 号校验位"),
        "seller_tax_id",
        gb_vat_no,
        N_("VAT 号"),
    ),
    Rule(
        "gb_rates",
        N_("VAT 税率是 20%、5% 或 0%"),
        ("tax_rate",),
        _allowed_rates((20, 5, 0), N_("英国")),
        "warning",
        per_row="items",
    ),
    Rule(
        "gb_full_over_250",
        N_("超过 £250 要有客户名称和地址"),
        ("gross_total",),
        _needs_over(
            250,
            "gross_total",
            ("buyer_name", "buyer_address"),
            N_(
                "英国税务海关总署规定（VAT Notice 700/21）：超过 £250 不能用简易发票，要写客户名称和地址"
            ),
        ),
        "warning",
        requires=("buyer_name", "buyer_address"),
    ),
    # Germany
    _id(
        "de_ust_idnr",
        N_("增值税识别号（USt-IdNr.）校验位"),
        "seller_tax_id",
        de_ust_idnr,
        N_("增值税识别号"),
    ),
    Rule(
        "de_rates",
        N_("增值税税率是 19%、7% 或 0%"),
        ("tax_rate",),
        _allowed_rates((19, 7, 0), N_("德国")),
        "warning",
        per_row="items",
    ),
    Rule(
        "de_full_over_250",
        N_("超过 250 € 要写收票方和发票号"),
        ("gross_total",),
        _needs_over(
            250,
            "gross_total",
            ("buyer_name", "invoice_no"),
            N_("小额发票（德国增值税实施条例第 33 条）只到 250 €，超过要写收票方和发票号"),
        ),
        "warning",
        requires=("buyer_name", "invoice_no"),
    ),
    Rule(
        "de_kassenbeleg",
        N_("收银小票有 TSE 安全模块信息"),
        ("gross_total",),
        check_de_kassenbeleg,
        "warning",
        requires=("transaction_no", "tse_serial"),
    ),
    Rule(
        "de_kasse_times",
        N_("交易开始时间不晚于结束时间"),
        ("start_time", "end_time"),
        check_de_kasse_times,
        "warning",
    ),
    # Thailand
    _id(
        "th_seller_tax_id",
        N_("卖方 13 位税号校验位"),
        "seller_tax_id",
        th_tax_id,
        N_("卖方税号"),
    ),
    _id(
        "th_buyer_tax_id",
        N_("买方 13 位税号校验位"),
        "buyer_tax_id",
        th_tax_id,
        N_("买方税号"),
    ),
    Rule(
        "th_vat_7",
        N_("增值税 = 不含税金额 × 7%"),
        ("net_total", "tax_total"),
        check_th_vat,
        "warning",
    ),
    Rule(
        "th_title",
        N_("标题写明“税务发票”（ใบกำกับภาษี）"),
        ("document_title",),
        _title_has(
            ("ใบกำกับภาษี", "tax invoice"),
            N_("泰国税法第 86/4 条要求在显眼处写明“税务发票”（ใบกำกับภาษี）"),
        ),
        "warning",
    ),
    Rule(
        "th_buyer_branch",
        N_("写了买方税号就要写买方总店或分店"),
        ("buyer_tax_id",),
        check_th_buyer_branch,
        "warning",
        requires=("buyer_branch",),
    ),
    # Indonesia
    _id(
        "id_seller_npwp",
        N_("卖方税号（NPWP）是 15 或 16 位"),
        "seller_tax_id",
        id_npwp,
        N_("卖方税号（NPWP）"),
    ),
    _id(
        "id_faktur_no",
        N_("增值税发票编号是 16 或 17 位"),
        "invoice_no",
        id_faktur_no,
        N_("增值税发票编号"),
    ),
    Rule(
        "id_ppn",
        N_("增值税 = 12% × 计税基础，计税基础 = 销售价或其 11/12"),
        ("dpp", "ppn"),
        check_id_ppn,
        "warning",
    ),
    # Philippines
    _id(
        "ph_seller_tin",
        N_("卖方税号（TIN）加分支码格式"),
        "seller_tax_id",
        ph_tin,
        N_("卖方税号（TIN）"),
    ),
    Rule(
        "ph_vat_12",
        N_("增值税 = 应税销售额 × 12%"),
        ("net_total", "tax_total"),
        _tax_of(12, "net_total", "tax_total", N_("菲律宾国内税法第 106、108 条：增值税 12%")),
        "warning",
    ),
    Rule(
        "ph_invoice_title",
        N_("标题写明 Invoice"),
        ("document_title",),
        _title_has(
            ("invoice",),
            N_("菲律宾税务局 2024 年第 7 号规定：主要凭证是 Invoice，标题要有 Invoice 字样"),
        ),
        "warning",
    ),
    # France
    _id(
        "fr_seller_siren",
        N_("卖方 SIREN / SIRET 校验位"),
        "seller_siren",
        fr_siren,
        N_("卖方 SIREN / SIRET"),
    ),
    _id(
        "fr_seller_tva",
        N_("卖方欧盟增值税号校验码"),
        "seller_tax_id",
        fr_tva,
        N_("卖方欧盟增值税号"),
    ),
    Rule(
        "fr_rates",
        N_("增值税税率是 20%、10%、5.5% 或 2.1%"),
        ("tax_rate",),
        _allowed_rates((20, 10, 5.5, 2.1, 0), N_("法国")),
        "warning",
        per_row="items",
    ),
    # Italy
    _id(
        "it_seller_piva",
        N_("卖方增值税号（Partita IVA）校验位"),
        "seller_tax_id",
        it_partita_iva,
        N_("卖方增值税号"),
    ),
    _id(
        "it_buyer_piva",
        N_("买方增值税号（Partita IVA）校验位"),
        "buyer_tax_id",
        it_partita_iva,
        N_("买方增值税号"),
    ),
    Rule(
        "it_rates",
        N_("增值税税率是 22%、10%、5% 或 4%"),
        ("tax_rate",),
        _allowed_rates((22, 10, 5, 4, 0), N_("意大利")),
        "warning",
        per_row="items",
    ),
    Rule(
        "it_semplificata_400",
        N_("简易发票不超过 400 €"),
        ("gross_total",),
        lambda v: (
            stored(
                "意大利简易发票的金额 {amount:,.2f} 超过 400 €（增值税法第 21-bis 条）",
                amount=_number(v, "gross_total") or 0,
            )
            if "semplificata" in _text(v, "document_title").lower()
            and (_number(v, "gross_total") or 0) > 400
            else None
        ),
        "warning",
        requires=("document_title",),
    ),
    # Spain
    _id(
        "es_seller_nif",
        N_("开票方税号（NIF）校验"),
        "seller_tax_id",
        es_nif,
        N_("开票方税号"),
    ),
    _id(
        "es_buyer_nif",
        N_("收票方税号（NIF）校验"),
        "buyer_tax_id",
        es_nif,
        N_("收票方税号"),
    ),
    Rule(
        "es_rates",
        N_("增值税税率是 21%、10% 或 4%"),
        ("tax_rate",),
        _allowed_rates((21, 10, 4, 0), N_("西班牙")),
        "warning",
        per_row="items",
    ),
    Rule(
        "es_simplificada",
        N_("简易发票不超过 400 €（零售、餐饮等行业 3,000 €）"),
        ("gross_total",),
        lambda v: (
            stored(
                "西班牙简易发票的金额 {amount:,.2f} 超过 400 €：只有开票条例第 4.1 条列的行业（零售、餐饮、客运等）能到 3,000 €",
                amount=_number(v, "gross_total") or 0,
            )
            if "simplificada" in _text(v, "document_title").lower()
            and (_number(v, "gross_total") or 0) > 400
            else None
        ),
        "warning",
        requires=("document_title",),
    ),
    # Netherlands
    _id(
        "nl_btw_id",
        N_("增值税号（btw-id）校验"),
        "seller_tax_id",
        nl_btw_id,
        N_("增值税号"),
    ),
    Rule(
        "nl_rates",
        N_("增值税税率是 21%、9% 或 0%"),
        ("tax_rate",),
        _allowed_rates((21, 9, 0), N_("荷兰")),
        "warning",
        per_row="items",
    ),
    Rule(
        "nl_full_over_100",
        N_("超过 € 100 要有发票号、买方和卖方增值税号"),
        ("gross_total",),
        _needs_over(
            100,
            "gross_total",
            ("invoice_no", "buyer_name", "seller_tax_id"),
            N_("荷兰简易发票只到 € 100，超过要写发票号、买方和卖方增值税号"),
        ),
        "warning",
        requires=("invoice_no", "buyer_name", "seller_tax_id"),
    ),
    # Poland
    _id(
        "pl_seller_nip",
        N_("卖方税号（NIP）校验位"),
        "seller_tax_id",
        pl_nip,
        N_("卖方税号"),
    ),
    _id(
        "pl_buyer_nip",
        N_("买方税号（NIP）校验位"),
        "buyer_tax_id",
        pl_nip,
        N_("买方税号"),
    ),
    Rule(
        "pl_rates",
        N_("增值税税率是 23%、8%、5% 或 0%"),
        ("tax_rate",),
        _allowed_rates((23, 8, 5, 0), N_("波兰")),
        "warning",
        per_row="items",
    ),
    # Canada
    _id(
        "ca_business_no",
        N_("GST/HST 登记号（BN）校验位"),
        "seller_tax_id",
        ca_business_no,
        N_("GST/HST 登记号"),
    ),
    Rule(
        "ca_rates",
        N_("税率是 GST 5% 或 HST 13%、14%、15%"),
        ("tax_rate",),
        _allowed_rates((5, 13, 14, 15, 0), N_("加拿大")),
        "warning",
        per_row="items",
    ),
    Rule(
        "ca_regno_100",
        N_("$100 以上要写 GST/HST 登记号"),
        ("gross_total",),
        _needs_over(
            99.99,
            "gross_total",
            ("seller_tax_id",),
            N_("加拿大进项税抵扣信息条例第 3 条 (b)：$100 及以上要有供应商的 GST/HST 登记号"),
        ),
        "warning",
        requires=("seller_tax_id",),
    ),
    Rule(
        "ca_buyer_500",
        N_("$500 以上要写买方和付款条件"),
        ("gross_total",),
        _needs_over(
            499.99,
            "gross_total",
            ("buyer_name", "payment_terms"),
            N_("加拿大进项税抵扣信息条例第 3 条 (c)：$500 及以上要有买方名称和付款条件"),
        ),
        "warning",
        requires=("buyer_name", "payment_terms"),
    ),
    # United Arab Emirates
    _id(
        "ae_trn",
        N_("税号（TRN）是 15 位数字"),
        "seller_tax_id",
        digits_15,
        N_("税号（TRN）"),
    ),
    Rule(
        "ae_vat_5",
        N_("增值税 = 不含税金额 × 5%"),
        ("net_total", "tax_total"),
        _tax_of(5, "net_total", "tax_total", N_("阿联酋增值税 5%")),
        "warning",
    ),
    Rule(
        "ae_title",
        N_("标题写明 Tax Invoice（税务发票）"),
        ("document_title",),
        _title_has(
            ("tax invoice", "فاتورة ضريبية"),
            N_("阿联酋增值税实施条例第 59 条要求写明 Tax Invoice（税务发票）"),
        ),
        "warning",
    ),
    # Saudi Arabia
    _id(
        "sa_vat_no",
        N_("增值税号是 15 位数字"),
        "seller_tax_id",
        digits_15,
        N_("增值税号"),
    ),
    Rule(
        "sa_vat_15",
        N_("增值税 = 不含税金额 × 15%"),
        ("net_total", "tax_total"),
        _tax_of(15, "net_total", "tax_total", N_("沙特增值税 15%")),
        "warning",
    ),
    # United States
    _id(
        "us_clia_no",
        N_("CLIA 编号格式"),
        "clia_no",
        us_clia_no,
        N_("CLIA 编号"),
    ),
)

COUNTRY_REGIONS: dict[str, str] = {
    rule.id: rule.id.split("_")[0].upper().replace("UK", "GB") for rule in COUNTRY_RULES
}
"""Rule id → country (ids start with the country code)."""
