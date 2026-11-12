"""按原工作簿导出（ADR 0031）：把审核之后的数据填回用户建模板时上传的那个 Excel。

- 单据表：一份单据一行；明细表：一项一行（带着单据上的字段）；对象表：同一个编号合并成一行，
  几份单据写得不一样就标黄、批注里写清楚；参照表、选项、说明：原样不动。
- 表头下面原来的数据清掉，新数据用原来第一行数据的格式；
  原来有合计行的，在最后补一行合计（求和公式）。
- 表头一样的几张 sheet（按月分的）：原来的都不动，复制第一张，填进复制出来的那张。
"""

from __future__ import annotations

import io
import re
from copy import copy
from dataclasses import dataclass
from dataclasses import field as dc_field
from datetime import date, datetime
from typing import Any

from openpyxl import load_workbook
from openpyxl.comments import Comment
from openpyxl.styles import PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from jacara.templates import Template
from jacara.templates.extras import WorkbookSheet, normalize

YELLOW = PatternFill("solid", fgColor="FFEB9C")
_TOTAL = re.compile(r"^\s*(合计|总计|小计|共计|total|subtotal)", re.I)


@dataclass
class DocRow:
    """一份单据（一页）审核之后的值。"""

    filename: str
    page: int = 1
    uploaded_at: datetime | None = None
    processed_at: datetime | None = None
    reviewed_at: datetime | None = None
    values: dict[str, Any] = dc_field(default_factory=dict)
    """普通字段 ID → 值（数字、日期已经转好）。"""
    tables: dict[str, list[dict[str, Any]]] = dc_field(default_factory=dict)
    """表格名 → 每一行（列 ID → 值），按显示顺序。"""


def fill_workbook(template: Template, original: bytes, rows: list[DocRow], title: str) -> bytes:
    """title：按月分的 sheet 复制出来的那张叫什么（例如"2026-11-12 导出"）。"""
    layout = template.workbook
    if layout is None:
        raise ValueError("这个模板没有工作簿样式")
    book = load_workbook(io.BytesIO(original))
    grouped = {s.same_as for s in layout.sheets if s.same_as}
    for sheet in layout.sheets:
        if sheet.role not in ("document", "items", "entity") or sheet.same_as:
            continue
        if sheet.name not in book.sheetnames:
            continue
        ws = book[sheet.name]
        if sheet.name in grouped:
            ws = book.copy_worksheet(ws)
            ws.title = _unique_title(book, f"{sheet.name} {title}")
        values = _sheet_rows(template, sheet, rows)
        _write(ws, sheet, values)
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def _unique_title(book: Any, title: str) -> str:
    title = "".join(c for c in title if c not in "[]:*?/\\")[:31]
    candidate, n = title, 2
    while candidate in book.sheetnames:
        suffix = f" ({n})"
        candidate = title[: 31 - len(suffix)] + suffix
        n += 1
    return candidate


Cell = tuple[Any, str | None]
"""（值，批注）。有批注的格子标黄。"""


def _sheet_rows(template: Template, sheet: WorkbookSheet, rows: list[DocRow]) -> list[list[Cell]]:
    if sheet.role == "document":
        return [
            [(_value(template, sheet, c.ref, doc, None, n, 0), None) for c in sheet.columns]
            for n, doc in enumerate(rows, start=1)
        ]
    if sheet.role == "items":
        result: list[list[Cell]] = []
        for doc in rows:
            for i, item in enumerate(doc.tables.get(sheet.table, []), start=1):
                n = len(result) + 1
                result.append(
                    [(_value(template, sheet, c.ref, doc, item, n, i), None) for c in sheet.columns]
                )
        return result
    return _entity_rows(template, sheet, rows)


def _entity_rows(template: Template, sheet: WorkbookSheet, rows: list[DocRow]) -> list[list[Cell]]:
    """同一个编号合并成一行；同一格几份单据写得不一样，用第一份的，批注里列出各份写的。"""
    entity = next(e for e in template.entities if e.name == sheet.entity)
    groups: dict[str, list[DocRow]] = {}
    for n, doc in enumerate(rows):
        key = doc.values.get(entity.key)
        groups.setdefault(normalize(str(key)) if key not in (None, "") else f"#{n}", []).append(doc)
    result: list[list[Cell]] = []
    for n, docs in enumerate(groups.values(), start=1):
        row: list[Cell] = []
        for c in sheet.columns:
            seen = [(_value(template, sheet, c.ref, d, None, n, 0), d.filename) for d in docs]
            present = [(v, f) for v, f in seen if v not in (None, "")]
            value = present[0][0] if present else None
            distinct = {normalize(str(v)) for v, _ in present}
            note = None
            if len(distinct) > 1:
                note = "几份单据写的不一样：\n" + "\n".join(f"{f}：{v}" for v, f in present)
            row.append((value, note))
        result.append(row)
    return result


def _value(
    template: Template,
    sheet: WorkbookSheet,
    ref: str,
    doc: DocRow,
    item: dict[str, Any] | None,
    seq: int,
    row: int,
) -> Any:
    if not ref:
        return None
    if ref.startswith("auto:"):
        return {
            "seq": seq,
            "filename": doc.filename,
            "page": doc.page,
            "row": row or None,
            "uploaded_at": _naive(doc.uploaded_at),
            "processed_at": _naive(doc.processed_at),
            "reviewed_at": _naive(doc.reviewed_at),
        }.get(ref[5:])
    if ref.startswith("ref:"):
        name, _, column = ref[4:].partition(".")
        reference = next((r for r in template.references if r.name == name), None)
        if reference is None or not reference.match_field:
            return None
        table, _, col = reference.match_field.partition(".")
        if col:
            if item is None or sheet.table != table:
                return None
            wanted = item.get(col)
        else:
            wanted = doc.values.get(reference.match_field)
        found = reference.find(wanted)
        return found.get(column) if found else None
    table, _, col = ref.partition(".")
    if col:
        return item.get(col) if item is not None and sheet.table == table else None
    return doc.values.get(ref)


def _naive(moment: datetime | None) -> datetime | None:
    """Excel 不认带时区的时间：换成这台电脑的时间。"""
    return moment.astimezone().replace(tzinfo=None) if moment else None


def _write(ws: Worksheet, sheet: WorkbookSheet, rows: list[list[Cell]]) -> None:
    start = sheet.header_row + 1
    styles = {c.index: copy(ws.cell(row=start, column=c.index)._style) for c in sheet.columns}
    total_label = _total_row(ws, start)
    if ws.max_row >= start:
        ws.delete_rows(start, ws.max_row - start + 1)
    numeric: set[int] = set()
    for r, row in enumerate(rows):
        for column, (value, note) in zip(sheet.columns, row, strict=True):
            cell = ws.cell(row=start + r, column=column.index)
            cell._style = copy(styles[column.index])
            cell.value = value
            if isinstance(value, int | float) and not isinstance(value, bool):
                numeric.add(column.index)
            if isinstance(value, date | datetime) and cell.number_format == "General":
                cell.number_format = "yyyy-mm-dd"
            if note:
                cell.fill = YELLOW
                cell.comment = Comment(note, "JacarandaParse", width=300, height=120)
    if total_label and rows:
        end = start + len(rows) - 1
        label_col, label = total_label
        ws.cell(row=end + 1, column=label_col, value=label)
        for col in numeric:
            if col == label_col:
                continue
            letter = get_column_letter(col)
            ws.cell(row=end + 1, column=col, value=f"=SUM({letter}{start}:{letter}{end})")


def _total_row(ws: Worksheet, start: int) -> tuple[int, str] | None:
    """原来数据下面有没有"合计"行：有的话记下它写在哪一列、写的什么。"""
    for row in ws.iter_rows(min_row=start, max_row=ws.max_row):
        for cell in row:
            if isinstance(cell.value, str) and cell.value.strip():
                if _TOTAL.match(cell.value) and cell.column is not None:
                    return cell.column, cell.value.strip()
                break
    return None
