"""Document series: documents from the same issuer form a series and share layout, wording and
typical errors.

Following ABBYY FlexiCapture for Invoices (invoices from one vendor form a type, trained
separately after at least 3 documents):
- Series = issuer: the tax id if present (whitespace and symbols removed, uppercase), otherwise
  the issuer name (whitespace and punctuation removed, lowercase);
- For a new document, the OCR text is searched for the tax id or name of known series; a match
  assigns the series. No extra model call;
- A wrong match only makes the chosen examples less relevant: values still need evidence and are
  still cross-checked."""

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
"""Field names of the issuer tax id (excluding the buyer tax id)."""

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
"""Field names of the issuer name, earlier ones first."""

MIN_TAX = 6
MIN_NAME = 4


def _tax(text: str) -> str:
    return re.sub(r"[^0-9A-Z]", "", unicodedata.normalize("NFKC", text).upper())


def _name(text: str) -> str:
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", text).casefold())


def series_key(values: Mapping[str, str | None]) -> str | None:
    """Post-review values of a document → series. Tax id first (tax:…), otherwise name (name:…);
    None if neither."""
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
    """Known series whose tax id or name appears in this document's OCR text. Tax ids first; with
    several matches, the longest."""
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
