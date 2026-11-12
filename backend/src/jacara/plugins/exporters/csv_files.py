"""导出 CSV：和标准格式的 Excel 内容一样，每张表一个 CSV 文件。

CSV 只有文字，没有颜色、批注；"需要确认"那一列还在。用 UTF-8（带 BOM），Excel 直接打开中文不乱码。
日期写成 2014-08-03，数字不带千分位。
只导出给别的系统用的表：汇总、每张明细表、键值对；"明细"（逐个字段的出处和审核记录）和"来源"不导出。
"""

from __future__ import annotations

import csv
import io
from datetime import date, datetime

from openpyxl import load_workbook

SKIPPED = frozenset({"明细", "来源"})


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
    """标准格式的 Excel → [(表名, CSV 内容)]。
    空的表（只有表头）也导出，方便别的系统按固定的列读。"""
    book = load_workbook(io.BytesIO(xlsx), read_only=True, data_only=True)
    files: list[tuple[str, bytes]] = []
    for sheet in book.worksheets:
        if sheet.title in SKIPPED:
            continue
        out = io.StringIO()
        writer = csv.writer(out, lineterminator="\r\n")
        for row in sheet.iter_rows(values_only=True):
            writer.writerow([_text(v) for v in row])
        files.append((sheet.title, ("﻿" + out.getvalue()).encode("utf-8")))
    return files
