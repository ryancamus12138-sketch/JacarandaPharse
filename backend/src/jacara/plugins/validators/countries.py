# ruff: noqa: E501  多语言的规则名、提示和法规出处，拆行反而难读
"""各国、各地区法定单据的规则（ADR 0049）。

每条规则只管一个国家，依据写在注释里（法规名称和条款，查证日期 2026-11）。都是"警告"：
税号校验位不对多半是 OCR 认错了一位，税率、门槛不对要人看一眼，不替人下结论。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date

from jacara.plugins.validators.base import Rule, Values

MONEY = 0.05
"""税额和"金额 × 税率"比较的容差：各国四舍五入到分，差几分钱算对。"""


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
    """ "10%" "10" "0.1" → 10.0；"免税" 之类返回 None。"""
    m = re.search(r"\d+(?:[.,]\d+)?", text)
    if m is None:
        return None
    rate = float(m[0].replace(",", "."))
    return rate * 100 if rate < 1 and "%" not in text and rate != 0 else rate


def _id_rule(
    field: str, valid: Callable[[str], bool | None], what: str
) -> Callable[[Values], str | None]:
    """号码校验：valid 返回 True 对、False 不对、None 不是这种号码（不提醒）。"""

    def check(values: Values) -> str | None:
        raw = _text(values, field)
        if valid(raw) is False:
            return f"{what}「{raw}」格式或校验位不对，可能有字认错了"
        return None

    return check


def _allowed_rates(rates: tuple[float, ...], where: str) -> Callable[[Values], str | None]:
    """这一行的税率是不是本国现行的税率（per_row）。"""

    def check(values: Values) -> str | None:
        raw = _text(values, "tax_rate")
        rate = _rate(raw)
        if rate is None or any(abs(rate - r) < 0.001 for r in rates):
            return None
        shown = "、".join(f"{r:g}%" for r in rates)
        return f"税率「{raw}」不是{where}现行的税率（{shown}）"

    return check


def _tax_of(rate: float, base: str, tax: str, where: str) -> Callable[[Values], str | None]:
    """税额 = 不含税金额 × 法定税率（差几分钱算对）；税额是 0（免税、零税率）不查。"""

    def check(values: Values) -> str | None:
        amount, paid = _number(values, base), _number(values, tax)
        if amount is None or paid is None or paid == 0:
            return None
        expected = amount * rate / 100
        if abs(expected - paid) <= max(MONEY, amount * 0.0005):
            return None
        return f"税额 {paid:,.2f} 不等于 {amount:,.2f} × {rate:g}%（{expected:,.2f}）——{where}"

    return check


def _needs_over(
    limit: float, total: str, fields: tuple[str, ...], why: str
) -> Callable[[Values], str | None]:
    """金额超过门槛时这些字段要有（各国"简易发票"的门槛）。"""

    def check(values: Values) -> str | None:
        amount = _number(values, total)
        if amount is None or amount <= limit:
            return None
        missing = [f for f in fields if not _text(values, f)]
        if not missing:
            return None
        return f"金额 {amount:,.2f} 超过 {limit:,.0f}，{why}"

    return check


def _title_has(words: tuple[str, ...], why: str) -> Callable[[Values], str | None]:
    def check(values: Values) -> str | None:
        title = _text(values, "document_title")
        if any(w.lower() in title.lower() for w in words):
            return None
        return f"单据标题是「{title}」，{why}"

    return check


# ---------- 日本：適格請求書（消費税法第57条の4、施行令第70条の10）----------


def jp_registration_no(text: str) -> bool | None:
    """登録番号：T + 13 桁（国税庁）。法人は法人番号，校验位只对法人番号成立，这里只查格式。"""
    return re.fullmatch(r"T\d{13}", _compact(text)) is not None


def check_jp_tax_per_rate(values: Values) -> str | None:
    """税率ごとに区分した消費税額：端数処理は 1 枚の請求書につき税率ごとに 1 回（施行令第70条の10）。
    对价可能是税抜，也可能是税込；两种都算一下，差不到 1 日元算对。"""
    problems = []
    for rate, amount_field, tax_field in ((10, "amount_10", "tax_10"), (8, "amount_8", "tax_8")):
        amount, tax = _number(values, amount_field), _number(values, tax_field)
        if amount is None or tax is None:
            continue
        excl, incl = amount * rate / 100, amount * rate / (100 + rate)
        if min(abs(tax - excl), abs(tax - incl)) < 1:
            continue
        problems.append(
            f"{rate}% 対象 {amount:,.0f} 円の消費税額 {tax:,.0f} 円（应约为 {excl:,.0f} 或 {incl:,.0f}）"
        )
    return "；".join(problems) or None


# ---------- 韩国：세금계산서（부가가치세법 제32조、제30조 세율 10%）----------


def kr_business_no(text: str) -> bool | None:
    """사업자등록번호 10 자리：가중치 1,3,7,1,3,7,1,3,5，第 9 位再加 (×5)//10。"""
    digits = re.sub(r"\D", "", text)
    if len(digits) != 10:
        return False if text else None
    d = [int(c) for c in digits]
    total = sum(x * w for x, w in zip(d[:9], (1, 3, 7, 1, 3, 7, 1, 3, 5), strict=True))
    total += d[8] * 5 // 10
    return (10 - total % 10) % 10 == d[9]


# ---------- 中国台湾：統一發票（統一發票使用辦法第9條、加值型及非加值型營業稅法第10條 5%）----------


def tw_ban(text: str) -> bool | None:
    """營利事業統一編號 8 碼；財政部 112 年起檢查碼改為「總和能被 5 整除」（第 7 碼為 7 時另一種算法）。"""
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
    """發票號碼：字軌 2 個英文字母 + 8 位數字。"""
    return re.fullmatch(r"[A-Z]{2}\d{8}", _compact(text)) is not None


# ---------- 新加坡：Tax invoice（GST (General) Regulations reg 11；IRAS）----------


def sg_gst_no(text: str) -> bool | None:
    """GST 登记号：UEN（8 位数字 + 字母；9 位数字 + 字母；T/S/R + 年份 + 实体代码 + 4 位 + 字母）或 M 开头的 GST 号。"""
    t = _compact(text)
    patterns = (
        r"\d{8}[A-Z]",
        r"\d{9}[A-Z]",
        r"[TSR]\d{2}[A-Z]{2}\d{4}[A-Z]",
        r"M[0-9A-Z]\d{7}[0-9A-Z]",
    )
    return any(re.fullmatch(p, t) for p in patterns)


def sg_rate_on(day: date) -> float:
    """GST 税率：2023 年起 8%，2024-01-01 起 9%（IRAS）。"""
    if day >= date(2024, 1, 1):
        return 9
    if day >= date(2023, 1, 1):
        return 8
    return 7


def check_sg_gst(values: Values) -> str | None:
    day = _day(values, "issue_date") or date.today()
    rate = sg_rate_on(day)
    return _tax_of(
        rate, "net_total", "tax_total", f"新加坡 {day:%Y-%m-%d} 适用的 GST 是 {rate:g}%"
    )(values)


# ---------- 印度：Tax invoice（CGST Rules 2017 Rule 46）----------

_B36 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def in_gstin(text: str) -> bool | None:
    """GSTIN 15 位：2 位邦代码 + 10 位 PAN + 实体码 + Z + 校验位（36 进制 Luhn）。"""
    t = _compact(text)
    if not re.fullmatch(r"\d{2}[A-Z]{5}\d{4}[A-Z][0-9A-Z]Z[0-9A-Z]", t):
        return False if text else None
    total = 0
    for i, c in enumerate(t[:14]):
        p = _B36.index(c) * (2 if i % 2 else 1)
        total += p // 36 + p % 36
    return _B36[(36 - total % 36) % 36] == t[14]


def check_in_invoice_no(values: Values) -> str | None:
    """Rule 46(b)：发票号最多 16 个字符，只能有字母、数字、"-"、"/"。"""
    raw = _text(values, "invoice_no")
    if len(raw) <= 16 and re.fullmatch(r"[A-Za-z0-9/\-]+", raw):
        return None
    return (
        f"发票号「{raw}」不符合印度 GST 规则第 46 条 (b)：最多 16 个字符，只能用字母、数字、- 和 /"
    )


def check_in_tax_split(values: Values) -> str | None:
    """邦内交易收 CGST + SGST（两者相等），跨邦收 IGST，不会两种都有。"""
    cgst, sgst, igst = (_number(values, f) or 0 for f in ("cgst", "sgst", "igst"))
    if igst and (cgst or sgst):
        return "同一张发票既有 IGST 又有 CGST/SGST：跨邦只收 IGST，邦内只收 CGST + SGST"
    if (cgst or sgst) and abs(cgst - sgst) > MONEY:
        return f"CGST {cgst:,.2f} 和 SGST {sgst:,.2f} 应该相等（各占一半）"
    return None


# ---------- 新西兰：Taxable supply information（GST Act 1985 s19F–19N；GST 15%）----------


def nz_ird_no(text: str) -> bool | None:
    """IRD / GST 号 8 或 9 位：权重 3,2,7,6,5,4,3,2 模 11；得 10 时用 7,4,3,2,5,2,7,6 再算一次。"""
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
    return f"GST {gst:,.2f} 比含税总额 {gross:,.2f} 的 3/23（{gross * 3 / 23:,.2f}）还多（GST 15%）"


# ---------- 英国：VAT invoice（VAT Regulations 1995 reg 14、16；HMRC VAT Notice 700/21）----------


def gb_vat_no(text: str) -> bool | None:
    """GB VAT 号：GB + 9 位（可带 3 位分支号）；前 7 位乘 8..2 加后两位，模 97 为 0（或加 55 后为 0）。"""
    t = _compact(text).removeprefix("GB")
    if t.startswith(("GD", "HA")):
        return None  # 政府部门、医疗机构号码，另一套规则
    if not re.fullmatch(r"\d{9}(\d{3})?", t):
        return False if text else None
    d = [int(c) for c in t[:9]]
    total = sum(x * w for x, w in zip(d[:7], range(8, 1, -1), strict=True)) + d[7] * 10 + d[8]
    return total % 97 == 0 or (total + 55) % 97 == 0


# ---------- 德国：Rechnung（§14 Abs. 4 UStG；Kleinbetragsrechnung §33 UStDV；Beleg §6 KassenSichV）----------


def de_ust_idnr(text: str) -> bool | None:
    """USt-IdNr.：DE + 9 位，最后一位按 ISO 7064 MOD 11,10。只写 Steuernummer 的不查。"""
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
    """§6 KassenSichV：收银小票要有交易号、收银系统序列号、TSE 序列号、校验值、签名计数器。"""
    names = {
        "transaction_no": "交易号",
        "pos_serial": "收银系统序列号",
        "tse_serial": "安全模块（TSE）序列号",
        "tse_signature": "校验值",
        "signature_counter": "签名计数器",
    }
    missing = [label for field, label in names.items() if not _text(values, field)]
    if not missing:
        return None
    return f"德国收银条例（KassenSichV）第 6 条要求的{'、'.join(missing)}没读到；没有安全模块信息的小票可能不合规，请看一眼原件"


def check_de_kasse_times(values: Values) -> str | None:
    start, end = _text(values, "start_time"), _text(values, "end_time")
    if not start or not end or start <= end:
        return None
    return f"交易开始时间 {start} 晚于结束时间 {end}"


# ---------- 泰国：ใบกำกับภาษี（ประมวลรัษฎากร มาตรา 86/4；ประกาศอธิบดีฯ ฉบับที่ 199；VAT 7% ถึง 30 ก.ย. 2570）----------


def th_tax_id(text: str) -> bool | None:
    """เลขประจำตัวผู้เสียภาษีอากร 13 หลัก：前 12 位乘 13..2，(11 − 和 mod 11) mod 10 是第 13 位。"""
    digits = re.sub(r"\D", "", text)
    if len(digits) != 13:
        return False if text else None
    total = sum(int(c) * w for c, w in zip(digits[:12], range(13, 1, -1), strict=True))
    return (11 - total % 11) % 10 == int(digits[12])


def check_th_vat(values: Values) -> str | None:
    """VAT 7%（พระราชกฤษฎีกา ฉบับที่ 807 พ.ศ. 2569：ถึง 30 ก.ย. 2570）。"""
    day = _day(values, "issue_date")
    if day is not None and day > date(2027, 9, 30):
        return None  # 7% 的期限之后税率可能变，不按 7% 查
    return _tax_of(7, "net_total", "tax_total", "泰国增值税 7%")(values)


def check_th_buyer_branch(values: Values) -> str | None:
    """ประกาศอธิบดีฯ ฉบับที่ 199：买方是 VAT 登记户时要写买方税号和"สำนักงานใหญ่ / สาขาที่"。"""
    if _text(values, "buyer_tax_id") and not _text(values, "buyer_branch"):
        return "写了买方税号，但没写买方是总店还是第几号分店（泰国税务厅公告第 199 号）"
    return None


# ---------- 印度尼西亚：Faktur Pajak（UU PPN Pasal 13 ayat 5；PER-11/PJ/2025 Pasal 33；PMK 131/2024）----------


def id_npwp(text: str) -> bool | None:
    """NPWP：15 位（旧）或 16 位（NIK / 新 NPWP，PMK 112/2022）。"""
    return len(re.sub(r"\D", "", text)) in (15, 16) if text else None


def id_faktur_no(text: str) -> bool | None:
    """Nomor Faktur Pajak：16 位（kode transaksi + status + NSFP）或 Coretax 的 17 位。"""
    return len(re.sub(r"\D", "", text)) in (16, 17) if text else None


def check_id_ppn(values: Values) -> str | None:
    """PMK 131/2024：PPN 12% × DPP；非奢侈品 DPP = 11/12 × harga jual（实际税负 11%）。"""
    dpp, ppn, price = (_number(values, f) for f in ("dpp", "ppn", "net_total"))
    if dpp is None or ppn is None or ppn == 0:
        return None
    problems = []
    if abs(ppn - dpp * 0.12) > max(1, dpp * 0.0005):
        problems.append(f"增值税 {ppn:,.0f} 不等于 12% × 计税基础 {dpp:,.0f}（{dpp * 0.12:,.0f}）")
    if (
        price is not None
        and abs(dpp - price) > 1
        and abs(dpp - price * 11 / 12) > max(1, price * 0.0005)
    ):
        problems.append(
            f"计税基础 {dpp:,.0f} 既不是销售价 {price:,.0f}，也不是它的 11/12（{price * 11 / 12:,.0f}）"
        )
    return "；".join(problems) or None


# ---------- 菲律宾：Invoice（EOPT Act RA 11976；RR 7-2024 Annex B；VAT 12%）----------


def ph_tin(text: str) -> bool | None:
    """TIN：9 位 + 分支码（3 或 5 位，RR 7-2024 示例 123-456-789-00000）。"""
    return re.fullmatch(r"\d{9}(\d{3}|\d{5})?", _compact(text)) is not None if text else None


# ---------- 法国：Facture（CGI art. 242 nonies A；taux de TVA art. 278 s.）----------


def _luhn(digits: str) -> bool:
    total = 0
    for i, c in enumerate(reversed(digits)):
        d = int(c) * (2 if i % 2 else 1)
        total += d - 9 if d > 9 else d
    return total % 10 == 0


def fr_siren(text: str) -> bool | None:
    """SIREN 9 位 / SIRET 14 位：Luhn。"""
    digits = re.sub(r"\D", "", text)
    if len(digits) not in (9, 14):
        return False if text else None
    return _luhn(digits)


def fr_tva(text: str) -> bool | None:
    """N° TVA intracommunautaire：FR + 2 位校验 + SIREN；数字校验 = (12 + 3 × (SIREN mod 97)) mod 97。"""
    t = _compact(text)
    if not t.startswith("FR"):
        return None
    if not re.fullmatch(r"FR[0-9A-Z]{2}\d{9}", t):
        return False
    key, siren = t[2:4], t[4:]
    if not key.isdigit():
        return None  # 字母校验码是另一套算法
    return int(key) == (12 + 3 * (int(siren) % 97)) % 97


# ---------- 意大利：Fattura（DPR 633/1972 art. 21；semplificata art. 21-bis ≤ 400 €）----------


def it_partita_iva(text: str) -> bool | None:
    """Partita IVA 11 位：奇数位相加，偶数位乘 2（大于 9 减 9）相加，(10 − 和 mod 10) mod 10 是第 11 位。"""
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


# ---------- 西班牙：Factura（RD 1619/2012 art. 6；simplificada art. 4、7）----------

_DNI = "TRWAGMYFPDXBNJZSQVHLCKE"


def es_nif(text: str) -> bool | None:
    """NIF：DNI（8 位 + 字母）、NIE（X/Y/Z + 7 位 + 字母）、CIF（字母 + 7 位 + 校验位）。"""
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


# ---------- 荷兰：Factuur（Wet OB 1968 art. 35a；vereenvoudigde factuur ≤ € 100）----------


def nl_btw_id(text: str) -> bool | None:
    """btw-id：NL + 9 位 + B + 2 位；2020 年起的号码按 mod 97，旧号码按 11-proef，两种都认。"""
    t = _compact(text)
    if not re.fullmatch(r"NL\d{9}B\d{2}", t):
        return False if text else None
    as_number = "2321" + t[2:11] + "11" + t[12:]
    if int(as_number) % 97 == 1:
        return True
    d = [int(c) for c in t[2:11]]
    return (sum(x * w for x, w in zip(d[:8], range(9, 1, -1), strict=True)) - d[8]) % 11 == 0


# ---------- 波兰：Faktura（ustawa o VAT art. 106e；KSeF 2026）----------


def pl_nip(text: str) -> bool | None:
    """NIP 10 位：权重 6,5,7,2,3,4,5,6,7，和 mod 11 是第 10 位（得 10 的号码不发）。"""
    digits = re.sub(r"\D", "", _compact(text).removeprefix("PL"))
    if len(digits) != 10:
        return False if text else None
    total = sum(int(c) * w for c, w in zip(digits[:9], (6, 5, 7, 2, 3, 4, 5, 6, 7), strict=True))
    return total % 11 == int(digits[9])


# ---------- 加拿大：GST/HST（Input Tax Credit Information (GST/HST) Regulations s.3）----------


def ca_business_no(text: str) -> bool | None:
    """Business Number 9 位（Luhn）+ RT + 4 位账户号。"""
    t = _compact(text)
    m = re.fullmatch(r"(\d{9})(RT\d{4})?", t)
    if m is None:
        return False if text else None
    return _luhn(m[1])


# ---------- 阿联酋、沙特 ----------


def digits_15(text: str) -> bool | None:
    """阿联酋 TRN、沙特 VAT 号：15 位数字。"""
    return re.fullmatch(r"\d{15}", re.sub(r"\D", "", text)) is not None if text else None


# ---------- 越南：Hóa đơn điện tử（NĐ 123/2020，NĐ 70/2025 sửa đổi；NQ 204/2025/QH15 giảm 8%）----------


def vn_tax_code(text: str) -> bool | None:
    """Mã số thuế：10 位，或 10 位 + 3 位（分支单位）。"""
    return re.fullmatch(r"\d{10}(\d{3})?", _compact(text)) is not None if text else None


def check_vn_reduced_rate(values: Values) -> str | None:
    """8% 是 NQ 204/2025/QH15 的临时减税（到 2026-12-31），之后开的发票不该是 8%。"""
    rate, day = _rate(_text(values, "tax_rate")), _day(values, "issue_date")
    if rate is None or day is None or abs(rate - 8) > 0.001 or day <= date(2026, 12, 31):
        return None
    return f"{day:%Y-%m-%d} 开的发票税率是 8%：越南国会第 204/2025 号决议的减税到 2026-12-31 为止"


# ---------- 美国：临床检验报告（42 CFR 493.1291 CLIA）----------


def us_clia_no(text: str) -> bool | None:
    """CLIA 编号：2 位数字 + D + 7 位数字（如 05D0123456）。"""
    return re.fullmatch(r"\d{2}D\d{7}", _compact(text)) is not None if text else None


def _id(
    rule_id: str, title: str, field: str, valid: Callable[[str], bool | None], what: str
) -> Rule:
    return Rule(rule_id, title, (field,), _id_rule(field, valid, what), "warning")


COUNTRY_RULES: tuple[Rule, ...] = (
    # 日本
    _id(
        "jp_registration_no",
        "发票登记号是 T + 13 位数字",
        "seller_tax_id",
        jp_registration_no,
        "发票登记号",
    ),
    Rule(
        "jp_tax_per_rate",
        "各税率的消费税额对得上（每张发票每个税率只进位一次）",
        ("gross_total",),
        check_jp_tax_per_rate,
        "warning",
        flags=("amount_10", "tax_10", "amount_8", "tax_8"),
        requires=("amount_10", "tax_10", "amount_8", "tax_8"),
    ),
    Rule(
        "jp_rates",
        "税率是 10% 或 8%",
        ("tax_rate",),
        _allowed_rates((10, 8, 0), "日本"),
        "warning",
        per_row="items",
    ),
    # 韩国
    _id(
        "kr_seller_no",
        "供应方事业者登录号码校验位",
        "seller_tax_id",
        kr_business_no,
        "供应方事业者登录号码",
    ),
    _id(
        "kr_buyer_no",
        "接收方事业者登录号码校验位",
        "buyer_tax_id",
        kr_business_no,
        "接收方事业者登录号码",
    ),
    Rule(
        "kr_vat_10",
        "附加价值税 = 供应价额 × 10%",
        ("net_total", "tax_total"),
        _tax_of(10, "net_total", "tax_total", "韩国附加价值税法第 30 条：税率 10%"),
        "warning",
    ),
    # 中国台湾
    _id(
        "tw_seller_ban",
        "卖方统一编号检查码",
        "seller_tax_id",
        tw_ban,
        "卖方统一编号",
    ),
    _id(
        "tw_buyer_ban",
        "买受人统一编号检查码",
        "buyer_tax_id",
        tw_ban,
        "买受人统一编号",
    ),
    _id(
        "tw_invoice_no",
        "发票号码是 2 个英文字母 + 8 位数字",
        "invoice_no",
        tw_invoice_no,
        "发票号码",
    ),
    Rule(
        "tw_tax_5",
        "营业税 = 销售额 × 5%",
        ("net_total", "tax_total"),
        _tax_of(5, "net_total", "tax_total", "台湾营业税法第 10 条：税率 5%"),
        "warning",
    ),
    # 新加坡
    _id(
        "sg_gst_no",
        "GST 登记号格式",
        "seller_tax_id",
        sg_gst_no,
        "GST 登记号",
    ),
    Rule(
        "sg_gst_rate",
        "GST = 不含税金额 × 当时的税率（2024 年起 9%）",
        ("net_total", "tax_total"),
        check_sg_gst,
        "warning",
    ),
    Rule(
        "sg_full_over_1000",
        "超过 S$1,000 要有客户名称和地址",
        ("gross_total",),
        _needs_over(
            1000,
            "gross_total",
            ("buyer_name", "buyer_address"),
            "新加坡税务局规定：超过 S$1,000 不能用简易税务发票，要写客户名称和地址",
        ),
        "warning",
        requires=("buyer_name", "buyer_address"),
    ),
    # 马来西亚
    Rule(
        "my_validated",
        "已经过马来西亚税务局（LHDN）验证（有唯一编号 UIN）",
        ("gross_total",),
        lambda v: (
            None
            if _text(v, "uin")
            else "没读到马来西亚税务局（LHDN）的唯一编号（UIN）：不是已验证的电子发票，或者二维码旁的编号没认出来"
        ),
        "warning",
        requires=("uin",),
    ),
    # 越南
    _id(
        "vn_seller_mst",
        "卖方税号是 10 或 13 位",
        "seller_tax_id",
        vn_tax_code,
        "卖方税号",
    ),
    _id(
        "vn_buyer_mst",
        "买方税号是 10 或 13 位",
        "buyer_tax_id",
        vn_tax_code,
        "买方税号",
    ),
    Rule(
        "vn_rates",
        "增值税税率是 0%、5%、8% 或 10%",
        ("tax_rate",),
        _allowed_rates((0, 5, 8, 10), "越南"),
        "warning",
    ),
    Rule(
        "vn_reduced_8",
        "8% 减税税率只到 2026-12-31",
        ("tax_rate", "issue_date"),
        check_vn_reduced_rate,
        "warning",
    ),
    # 印度
    _id(
        "in_seller_gstin",
        "供应商 GSTIN 校验位",
        "seller_tax_id",
        in_gstin,
        "供应商 GSTIN",
    ),
    _id(
        "in_buyer_gstin",
        "收货方 GSTIN 校验位",
        "buyer_tax_id",
        in_gstin,
        "收货方 GSTIN",
    ),
    Rule(
        "in_invoice_no",
        "发票号不超过 16 个字符",
        ("invoice_no",),
        check_in_invoice_no,
        "warning",
    ),
    Rule(
        "in_tax_split",
        "IGST 和 CGST、SGST 不同时出现，CGST = SGST",
        ("gross_total",),
        check_in_tax_split,
        "warning",
        flags=("cgst", "sgst", "igst"),
        requires=("cgst", "sgst", "igst"),
    ),
    # 新西兰
    _id(
        "nz_gst_no",
        "GST 号校验位",
        "seller_tax_id",
        nz_ird_no,
        "GST 号",
    ),
    Rule(
        "nz_gst_3_23",
        "GST 不超过含税总额的 3/23（15%）",
        ("tax_total", "gross_total"),
        check_nz_gst_3_23,
        "warning",
    ),
    Rule(
        "nz_buyer_over_1000",
        "超过 $1,000 要写买方名称",
        ("gross_total",),
        _needs_over(
            1000,
            "gross_total",
            ("buyer_name",),
            "新西兰商品及服务税法第 19K 条：超过 $1,000 要有买方名称和一项识别信息",
        ),
        "warning",
        requires=("buyer_name",),
    ),
    # 英国
    _id(
        "gb_vat_no",
        "VAT 号校验位",
        "seller_tax_id",
        gb_vat_no,
        "VAT 号",
    ),
    Rule(
        "gb_rates",
        "VAT 税率是 20%、5% 或 0%",
        ("tax_rate",),
        _allowed_rates((20, 5, 0), "英国"),
        "warning",
        per_row="items",
    ),
    Rule(
        "gb_full_over_250",
        "超过 £250 要有客户名称和地址",
        ("gross_total",),
        _needs_over(
            250,
            "gross_total",
            ("buyer_name", "buyer_address"),
            "英国税务海关总署规定（VAT Notice 700/21）：超过 £250 不能用简易发票，要写客户名称和地址",
        ),
        "warning",
        requires=("buyer_name", "buyer_address"),
    ),
    # 德国
    _id(
        "de_ust_idnr",
        "增值税识别号（USt-IdNr.）校验位",
        "seller_tax_id",
        de_ust_idnr,
        "增值税识别号",
    ),
    Rule(
        "de_rates",
        "增值税税率是 19%、7% 或 0%",
        ("tax_rate",),
        _allowed_rates((19, 7, 0), "德国"),
        "warning",
        per_row="items",
    ),
    Rule(
        "de_full_over_250",
        "超过 250 € 要写收票方和发票号",
        ("gross_total",),
        _needs_over(
            250,
            "gross_total",
            ("buyer_name", "invoice_no"),
            "小额发票（德国增值税实施条例第 33 条）只到 250 €，超过要写收票方和发票号",
        ),
        "warning",
        requires=("buyer_name", "invoice_no"),
    ),
    Rule(
        "de_kassenbeleg",
        "收银小票有 TSE 安全模块信息",
        ("gross_total",),
        check_de_kassenbeleg,
        "warning",
        requires=("transaction_no", "tse_serial"),
    ),
    Rule(
        "de_kasse_times",
        "交易开始时间不晚于结束时间",
        ("start_time", "end_time"),
        check_de_kasse_times,
        "warning",
    ),
    # 泰国
    _id(
        "th_seller_tax_id",
        "卖方 13 位税号校验位",
        "seller_tax_id",
        th_tax_id,
        "卖方税号",
    ),
    _id(
        "th_buyer_tax_id",
        "买方 13 位税号校验位",
        "buyer_tax_id",
        th_tax_id,
        "买方税号",
    ),
    Rule(
        "th_vat_7", "增值税 = 不含税金额 × 7%", ("net_total", "tax_total"), check_th_vat, "warning"
    ),
    Rule(
        "th_title",
        "标题写明“税务发票”（ใบกำกับภาษี）",
        ("document_title",),
        _title_has(
            ("ใบกำกับภาษี", "tax invoice"),
            "泰国税法第 86/4 条要求在显眼处写明“税务发票”（ใบกำกับภาษี）",
        ),
        "warning",
    ),
    Rule(
        "th_buyer_branch",
        "写了买方税号就要写买方总店或分店",
        ("buyer_tax_id",),
        check_th_buyer_branch,
        "warning",
        requires=("buyer_branch",),
    ),
    # 印尼
    _id(
        "id_seller_npwp",
        "卖方税号（NPWP）是 15 或 16 位",
        "seller_tax_id",
        id_npwp,
        "卖方税号（NPWP）",
    ),
    _id(
        "id_faktur_no",
        "增值税发票编号是 16 或 17 位",
        "invoice_no",
        id_faktur_no,
        "增值税发票编号",
    ),
    Rule(
        "id_ppn",
        "增值税 = 12% × 计税基础，计税基础 = 销售价或其 11/12",
        ("dpp", "ppn"),
        check_id_ppn,
        "warning",
    ),
    # 菲律宾
    _id(
        "ph_seller_tin",
        "卖方税号（TIN）加分支码格式",
        "seller_tax_id",
        ph_tin,
        "卖方税号（TIN）",
    ),
    Rule(
        "ph_vat_12",
        "增值税 = 应税销售额 × 12%",
        ("net_total", "tax_total"),
        _tax_of(12, "net_total", "tax_total", "菲律宾国内税法第 106、108 条：增值税 12%"),
        "warning",
    ),
    Rule(
        "ph_invoice_title",
        "标题写明 Invoice",
        ("document_title",),
        _title_has(
            ("invoice",),
            "菲律宾税务局 2024 年第 7 号规定：主要凭证是 Invoice，标题要有 Invoice 字样",
        ),
        "warning",
    ),
    # 法国
    _id(
        "fr_seller_siren",
        "卖方 SIREN / SIRET 校验位",
        "seller_siren",
        fr_siren,
        "卖方 SIREN / SIRET",
    ),
    _id(
        "fr_seller_tva",
        "卖方欧盟增值税号校验码",
        "seller_tax_id",
        fr_tva,
        "卖方欧盟增值税号",
    ),
    Rule(
        "fr_rates",
        "增值税税率是 20%、10%、5.5% 或 2.1%",
        ("tax_rate",),
        _allowed_rates((20, 10, 5.5, 2.1, 0), "法国"),
        "warning",
        per_row="items",
    ),
    # 意大利
    _id(
        "it_seller_piva",
        "卖方增值税号（Partita IVA）校验位",
        "seller_tax_id",
        it_partita_iva,
        "卖方增值税号",
    ),
    _id(
        "it_buyer_piva",
        "买方增值税号（Partita IVA）校验位",
        "buyer_tax_id",
        it_partita_iva,
        "买方增值税号",
    ),
    Rule(
        "it_rates",
        "增值税税率是 22%、10%、5% 或 4%",
        ("tax_rate",),
        _allowed_rates((22, 10, 5, 4, 0), "意大利"),
        "warning",
        per_row="items",
    ),
    Rule(
        "it_semplificata_400",
        "简易发票不超过 400 €",
        ("gross_total",),
        lambda v: (
            f"意大利简易发票的金额 {_number(v, 'gross_total') or 0:,.2f} 超过 400 €（增值税法第 21-bis 条）"
            if "semplificata" in _text(v, "document_title").lower()
            and (_number(v, "gross_total") or 0) > 400
            else None
        ),
        "warning",
        requires=("document_title",),
    ),
    # 西班牙
    _id(
        "es_seller_nif",
        "开票方税号（NIF）校验",
        "seller_tax_id",
        es_nif,
        "开票方税号",
    ),
    _id(
        "es_buyer_nif",
        "收票方税号（NIF）校验",
        "buyer_tax_id",
        es_nif,
        "收票方税号",
    ),
    Rule(
        "es_rates",
        "增值税税率是 21%、10% 或 4%",
        ("tax_rate",),
        _allowed_rates((21, 10, 4, 0), "西班牙"),
        "warning",
        per_row="items",
    ),
    Rule(
        "es_simplificada",
        "简易发票不超过 400 €（零售、餐饮等行业 3,000 €）",
        ("gross_total",),
        lambda v: (
            f"西班牙简易发票的金额 {_number(v, 'gross_total') or 0:,.2f} 超过 400 €：只有开票条例第 4.1 条列的行业（零售、餐饮、客运等）能到 3,000 €"
            if "simplificada" in _text(v, "document_title").lower()
            and (_number(v, "gross_total") or 0) > 400
            else None
        ),
        "warning",
        requires=("document_title",),
    ),
    # 荷兰
    _id(
        "nl_btw_id",
        "增值税号（btw-id）校验",
        "seller_tax_id",
        nl_btw_id,
        "增值税号",
    ),
    Rule(
        "nl_rates",
        "增值税税率是 21%、9% 或 0%",
        ("tax_rate",),
        _allowed_rates((21, 9, 0), "荷兰"),
        "warning",
        per_row="items",
    ),
    Rule(
        "nl_full_over_100",
        "超过 € 100 要有发票号、买方和卖方增值税号",
        ("gross_total",),
        _needs_over(
            100,
            "gross_total",
            ("invoice_no", "buyer_name", "seller_tax_id"),
            "荷兰简易发票只到 € 100，超过要写发票号、买方和卖方增值税号",
        ),
        "warning",
        requires=("invoice_no", "buyer_name", "seller_tax_id"),
    ),
    # 波兰
    _id(
        "pl_seller_nip",
        "卖方税号（NIP）校验位",
        "seller_tax_id",
        pl_nip,
        "卖方税号",
    ),
    _id(
        "pl_buyer_nip",
        "买方税号（NIP）校验位",
        "buyer_tax_id",
        pl_nip,
        "买方税号",
    ),
    Rule(
        "pl_rates",
        "增值税税率是 23%、8%、5% 或 0%",
        ("tax_rate",),
        _allowed_rates((23, 8, 5, 0), "波兰"),
        "warning",
        per_row="items",
    ),
    # 加拿大
    _id(
        "ca_business_no",
        "GST/HST 登记号（BN）校验位",
        "seller_tax_id",
        ca_business_no,
        "GST/HST 登记号",
    ),
    Rule(
        "ca_rates",
        "税率是 GST 5% 或 HST 13%、14%、15%",
        ("tax_rate",),
        _allowed_rates((5, 13, 14, 15, 0), "加拿大"),
        "warning",
        per_row="items",
    ),
    Rule(
        "ca_regno_100",
        "$100 以上要写 GST/HST 登记号",
        ("gross_total",),
        _needs_over(
            99.99,
            "gross_total",
            ("seller_tax_id",),
            "加拿大进项税抵扣信息条例第 3 条 (b)：$100 及以上要有供应商的 GST/HST 登记号",
        ),
        "warning",
        requires=("seller_tax_id",),
    ),
    Rule(
        "ca_buyer_500",
        "$500 以上要写买方和付款条件",
        ("gross_total",),
        _needs_over(
            499.99,
            "gross_total",
            ("buyer_name", "payment_terms"),
            "加拿大进项税抵扣信息条例第 3 条 (c)：$500 及以上要有买方名称和付款条件",
        ),
        "warning",
        requires=("buyer_name", "payment_terms"),
    ),
    # 阿联酋
    _id(
        "ae_trn",
        "税号（TRN）是 15 位数字",
        "seller_tax_id",
        digits_15,
        "税号（TRN）",
    ),
    Rule(
        "ae_vat_5",
        "增值税 = 不含税金额 × 5%",
        ("net_total", "tax_total"),
        _tax_of(5, "net_total", "tax_total", "阿联酋增值税 5%"),
        "warning",
    ),
    Rule(
        "ae_title",
        "标题写明 Tax Invoice（税务发票）",
        ("document_title",),
        _title_has(
            ("tax invoice", "فاتورة ضريبية"),
            "阿联酋增值税实施条例第 59 条要求写明 Tax Invoice（税务发票）",
        ),
        "warning",
    ),
    # 沙特
    _id(
        "sa_vat_no",
        "增值税号是 15 位数字",
        "seller_tax_id",
        digits_15,
        "增值税号",
    ),
    Rule(
        "sa_vat_15",
        "增值税 = 不含税金额 × 15%",
        ("net_total", "tax_total"),
        _tax_of(15, "net_total", "tax_total", "沙特增值税 15%"),
        "warning",
    ),
    # 美国
    _id(
        "us_clia_no",
        "CLIA 编号格式",
        "clia_no",
        us_clia_no,
        "CLIA 编号",
    ),
)

COUNTRY_REGIONS: dict[str, str] = {
    rule.id: rule.id.split("_")[0].upper().replace("UK", "GB") for rule in COUNTRY_RULES
}
"""规则编号 → 国家（编号开头就是国家代码）。"""
