"""CSV export: the same content as the standard Excel export, one CSV file per sheet.

CSV holds text only, without colors or comments; the "needs confirmation" column remains. UTF-8
with BOM, so Excel opens non-ASCII text correctly. Dates are written as 2014-08-03, numbers
without thousands separators. Only sheets meant for other systems are exported: summary, each
line-item table, key-value pairs; the details sheet (per-field evidence and review history) and
the source sheet are not."""

from __future__ import annotations

import csv
import io
from datetime import date, datetime

from openpyxl import load_workbook

from jacara.core.i18n import N_, tr

SKIPPED = (N_("明细"), N_("来源"))
"""Sheets not exported (details and sources), by message id; compared in the request language."""


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return (
            value.strftime("%Y-%m-%d")
            if value.time() == datetime.min.time()
            else (value.strftime("%Y-%m-%d %H:%M:%S"))
        )
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float):
        return f"{value:.10g}"
    return str(value)


def workbook_to_csv(xlsx: bytes) -> list[tuple[str, bytes]]:
    """Standard Excel workbook → [(sheet name, CSV content)]. Empty sheets (header only) are
    exported too, so other systems can rely on fixed columns."""
    book = load_workbook(io.BytesIO(xlsx), read_only=True, data_only=True)
    files: list[tuple[str, bytes]] = []
    skipped = {tr(name) for name in SKIPPED}
    for sheet in book.worksheets:
        if sheet.title in skipped:
            continue
        out = io.StringIO()
        writer = csv.writer(out, lineterminator="\r\n")
        for row in sheet.iter_rows(values_only=True):
            writer.writerow([_text(v) for v in row])
        files.append((sheet.title, ("﻿" + out.getvalue()).encode("utf-8")))
    return files
