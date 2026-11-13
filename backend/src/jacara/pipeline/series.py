"""单据系列（ADR 0055，B3）：同一个发单方的单据算一个系列，他们的版式、写法、容易错的地方相近。

照 ABBYY FlexiCapture for Invoices 的做法（同一家供应商的发票算一种类型，至少 3 份才单独训练）：
- 系列 = 发单方：有税号用税号（去掉空格和符号、转大写），
  没有就用发单方名称（去掉空格、标点，小写）；
- 处理一张新单据时，在它的 OCR 文字里找已知系列的税号或名称，找到就算这个系列。不多调一次模型；
- 认错的代价只是挑的例子没那么贴切：值照样要找出处、照样核对。
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping

TAX_NAMES = (
    "seller_tax_id",
    "merchant_tax_id",
    "issuer_tax_id",
    "supplier_tax_id",
    "seller_abn",
    "abn",
    "tax_id",
    "vat_number",
)
"""发单方税号的字段名（不含买方税号）。"""

ISSUER_NAMES = (
    "seller_name",
    "merchant",
    "seller",
    "issuer",
    "store_name",
    "vendor",
    "supplier",
    "company",
    "lab_name",
    "carrier",
    "shipper",
)
"""发单方名称的字段名，前面的优先。"""

MIN_TAX = 6
MIN_NAME = 4


def _tax(text: str) -> str:
    return re.sub(r"[^0-9A-Z]", "", unicodedata.normalize("NFKC", text).upper())


def _name(text: str) -> str:
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", text).casefold())


def series_key(values: Mapping[str, str | None]) -> str | None:
    """一张单据审核后的值 → 系列。税号优先（tax:…），没有就用名称（name:…）；都没有是 None。"""
    for name in TAX_NAMES:
        raw = values.get(name)
        if raw and len(key := _tax(raw)) >= MIN_TAX:
            return f"tax:{key}"
    for name in ISSUER_NAMES:
        raw = values.get(name)
        if raw and len(key := _name(raw)) >= MIN_NAME:
            return f"name:{key}"
    return None


def series_in(text: str, candidates: Iterable[str]) -> str | None:
    """这张单据的 OCR 文字里写着哪个已知系列的税号或名称。税号优先；几个都对上取最长的。"""
    taxes = _tax(text)
    names = _name(text)
    found = [
        key
        for key in candidates
        if (key.startswith("tax:") and key[4:] in taxes)
        or (key.startswith("name:") and key[5:] in names)
    ]
    if not found:
        return None
    return max(found, key=lambda k: (k.startswith("tax:"), len(k)))
