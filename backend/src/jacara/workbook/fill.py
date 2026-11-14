"""Export into the original workbook: fill post-review data back into the workbook uploaded when the
template was created.

- Document sheets: one row per document; line-item sheets: one row per item (with document-level
  fields); entity sheets: one row per identifier, with cells where documents disagree highlighted
  and explained in a comment; reference, option and note sheets: unchanged.
- Existing data below the header is cleared, and new data takes the format of the original first
  data row; if a total row existed, one is appended at the end (sum formulas).
- Sheets with identical headers (monthly): the originals are left untouched; the first is copied
  and the copy is filled."""

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

from jacara.core.daterange import DateRange, parse_date_range
from jacara.core.i18n import tr
from jacara.templates import Template
from jacara.templates.extras import WorkbookSheet, normalize

YELLOW = PatternFill("solid", fgColor="FFEB9C")
_TOTAL = re.compile(r"^\s*(合计|总计|小计|共计|total|subtotal)", re.I)


@dataclass
class DocRow:
    """Post-review values of one document (one page)."""

    filename: str
    page: int = 1
    uploaded_at: datetime | None = None
    processed_at: datetime | None = None
    reviewed_at: datetime | None = None
    values: dict[str, Any] = dc_field(default_factory=dict)
    """Regular field ID → value (numbers and dates already converted)."""
    tables: dict[str, list[dict[str, Any]]] = dc_field(default_factory=dict)
    """Table name → rows (column ID → value), in display order."""


def fill_workbook(template: Template, original: bytes, rows: list[DocRow], title: str) -> bytes:
    """title: name of the copied sheet for monthly sheets (for example "2026-11-12 export")."""
    layout = template.workbook
    if layout is None:
        raise ValueError(tr("此模板没有工作簿版式"))
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
"""(value, comment). Cells with comments are highlighted."""


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
    """Merge one identifier into one row; where documents disagree on a cell, use the first and list
    every document's value in a comment."""
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
                note = tr("多份单据内容不一致：\n") + "\n".join(
                    tr("{file}：{value}", file=f, value=v) for v, f in present
                )
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
    ranges = template.ranges()
    if table in ranges and col in ("start", "end", "days"):
        # Start, end and number of days of a date range: business workbooks write them in separate
        # cells.
        found = _range(doc.values.get(table), template)
        if found is None:
            return None
        return found.days if col == "days" else getattr(found, col)
    if col:
        return item.get(col) if item is not None and sheet.table == table else None
    if ref in ranges and ranges[ref].range_export == "normalized":
        found = _range(doc.values.get(ref), template)
        if found is not None and found.start and found.end:
            return found.text(ranges[ref].range_separator)
    return doc.values.get(ref)


def _range(raw: Any, template: Template) -> DateRange | None:
    if raw in (None, ""):
        return None
    return parse_date_range(str(raw), template.day_first).range


def _naive(moment: datetime | None) -> datetime | None:
    """Excel does not support time zones: convert to the local time of this machine."""
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
    """Whether a total row follows the original data: if so, record its column and content."""
    for row in ws.iter_rows(min_row=start, max_row=ws.max_row):
        for cell in row:
            if isinstance(cell.value, str) and cell.value.strip():
                if _TOTAL.match(cell.value) and cell.column is not None:
                    return cell.column, cell.value.strip()
                break
    return None
