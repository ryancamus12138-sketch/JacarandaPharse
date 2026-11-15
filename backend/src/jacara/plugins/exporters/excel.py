"""Excel exporter: workbooks ready for accounting.

Sheets:
- Summary: one row per document, one column per field (tables excluded). Unconfirmed cells are
  yellow or red and manually edited cells blue; hovering shows the reason, the evidence and
  "original → edited".
- Details: one row per field with current value, original value, status, evidence, OCR
  confidence, reason and manual review, for filtering and auditing.
- Line items: one row per item, preceded by file name and row number; cell colors and comments as
  in Summary. Omitted when there are no tables.
- Key-value pairs: for documents processed without a template (the first step of creating a
  template from a sample), one row per pair. Omitted when there are none.
- Source: OCR engine, model, prompt version and processing time of each document (every
  extraction records its provenance).

Column headers use the template display names (report.labels) in template order; fields set not
to export in the template are omitted.

With manual review results (report.reviews), cells contain post-review values; original values
are visible in comments and in Details."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.cell.cell import Cell, MergedCell
from openpyxl.comments import Comment
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from jacara.core.daterange import parse_date_range
from jacara.core.i18n import N_, render, tr
from jacara.plugins.aligners.base import AlignedField
from jacara.plugins.exporters.base import DocumentReport, RangeSetting, Review
from jacara.plugins.extractors.base import split_cell_name

RED = PatternFill("solid", fgColor="FFC7CE")
YELLOW = PatternFill("solid", fgColor="FFEB9C")
BLUE = PatternFill("solid", fgColor="DDEBF7")
"""A manually edited value."""
BOLD = Font(bold=True)

MONEY_FORMAT = "#,##0.00"
DATE_FORMAT = "yyyy-mm-dd"

_STATUS_NAMES = {
    "matched": N_("有出处"),
    "fuzzy": N_("相似"),
    "not_found": N_("未找到出处"),
    "empty": N_("原文缺失"),
    "rejected": N_("与原文不符，未填写"),
    "computed": N_("公式计算"),
    "seen": N_("读取自原图"),
}

_ACTION_NAMES = {
    "edit": N_("人工修改"),
    "confirm": N_("人工确认"),
    "restore": N_("恢复此行"),
    "compute": N_("按公式重算"),
}


def typed(type_: str, value: str | float | None) -> str | float | date | None:
    """Convert values to Excel types: dates as real dates and numbers as numbers, so Excel can sort
    and compute."""
    if value is None:
        return None
    if type_ == "date":
        try:
            return date.fromisoformat(str(value))
        except ValueError:
            # Invalid dates are written as is; rule validation already flags them.
            return str(value)
    if type_ == "number":
        try:
            return float(value)
        except ValueError:
            return str(value)
    return " ".join(str(value).split())


def cell_value(
    field: AlignedField, review: Review | None = None, report: DocumentReport | None = None
) -> str | float | date | None:
    """Cell value: the post-review value if reviewed, otherwise the extracted value. Date ranges are
    written in uniform format or as source text per the template setting (two-column ranges are
    handled separately in the summary sheet)."""
    value = review.value if review is not None else field.field.value
    if field.field.type == "date_range":
        cells = range_cells(field, review, _setting(report, field.field.name))
        return cells[0] if len(cells) == 1 else _joined(cells, _setting(report, field.field.name))
    return typed(field.field.type, value)


def _setting(report: DocumentReport | None, name: str) -> RangeSetting:
    if report is None:
        return RangeSetting()
    cell = split_cell_name(name)
    key = f"{cell[0]}.{cell[2]}" if cell else name
    return report.ranges.get(key, RangeSetting())


def _joined(cells: list[str | date | None], setting: RangeSetting) -> str | None:
    if all(c is None for c in cells):
        return None
    return setting.separator.join("" if c is None else str(c) for c in cells).strip()


def range_cells(
    field: AlignedField, review: Review | None, setting: RangeSetting
) -> list[str | date | None]:
    """Cells for a date range: one cell each for uniform format and source text; two cells (real
    dates) for start and end when split into columns. When it cannot be split, the uniform format
    holds the source text and the two columns stay empty (the comment contains the source text)."""
    raw = review.value if review is not None else field.field.value
    columns = setting.export == "columns"
    if raw is None or str(raw).strip() == "":
        return [None, None] if columns else [None]
    text = " ".join(str(raw).split())
    if setting.export == "original":
        # Source text: as copied by the model; the edited value if a person changed it.
        edited = review is not None and review.action == "edit"
        return [field.field.text if field.field.text and not edited else text]
    found = parse_date_range(text, setting.day_first).range
    if columns:
        return [found.start, found.end] if found else [None, None]
    if found is None or found.start is None or found.end is None:
        return [text]
    return [found.text(setting.separator)]


def _range_note(field: AlignedField, report: DocumentReport) -> str:
    """Comment for a date range: source text plus the split start and end."""
    review = report.reviews.get(field.field.name)
    raw = review.value if review is not None else field.field.value
    if raw is None:
        return ""
    setting = _setting(report, field.field.name)
    parsed = parse_date_range(str(raw), setting.day_first)
    found = parsed.range
    split = (
        tr("开始 {start}，结束 {end}", start=found.start or "？", end=found.end or "？")
        if found
        else tr("无法拆分")
    )
    problem = tr("（{problem}）", problem=tr(parsed.problem)) if parsed.problem else ""
    quote = field.field.text or " ".join(str(raw).split())
    return tr("原文：{quote}\n{split}{problem}", quote=quote, split=split, problem=problem)


def _where(field: AlignedField) -> str:
    """Evidence as one sentence: line number and position on the image."""
    if field.evidence is None:
        return ""
    x0, y0, x1, y1 = (round(v) for v in field.evidence.bbox)
    return tr(
        "第 {row} 行 ({x0},{y0})-({x1},{y1})", row=field.evidence.row, x0=x0, y0=y0, x1=x1, y1=y1
    )


def _reasons(field: AlignedField, report: DocumentReport) -> list[str]:
    """Why this field requires manual confirmation: the alignment explanation plus failed rules."""
    reasons = [render(field.note) or ""] if field.needs_review else []
    return reasons + [
        render(i.message) or "" for i in report.checks.issues if field.field.name in i.fields
    ]


def _local(moment: datetime) -> datetime:
    """Convert to the local time zone of this machine and drop the time zone (Excel does not support
    time zones).

        The database stores UTC; writing it directly would show users in Sydney times 11 hours
    off."""
    return moment.astimezone().replace(tzinfo=None)


def _review_note(field: AlignedField, review: Review) -> str:
    """Review history in a comment: original → new value, reason, time."""
    when = _local(review.reviewed_at).strftime("%Y-%m-%d %H:%M")
    if review.action == "edit":
        old = tr("（空）") if field.field.value is None else field.field.value
        new = tr("（空）") if review.value is None else review.value
        text = tr("人工修改：原值 {old} → {new}", old=old, new=new)
    elif review.action == "compute":
        new = tr("（空）") if review.value is None else review.value
        text = tr("参与计算的值已修改，按公式重新计算 → {new}", new=new)
    elif review.action == "restore":
        text = tr("恢复了已删除的行")
    else:
        text = tr("人工确认：值无误")
    if review.reason:
        text += tr("\n原因：{reason}", reason=render(review.reason))
    return f"{text}\n{when}"


def _setup(sheet: Worksheet, headers: list[str], widths: list[int]) -> None:
    """Write the header row: bold, frozen first row, filter buttons, column widths."""
    sheet.append(headers)
    for cell in sheet[1]:
        cell.font = BOLD
    sheet.freeze_panes = "A2"
    for i, width in enumerate(widths):
        sheet.column_dimensions[get_column_letter(i + 1)].width = width


def _mark(cell: Cell | MergedCell, field: AlignedField, report: DocumentReport) -> None:
    """Format, color and comment of one cell: unconfirmed yellow (red without evidence), manually
    edited blue."""
    if field.field.type == "number":
        cell.number_format = MONEY_FORMAT
    elif field.field.type == "date" or isinstance(cell.value, date):
        cell.number_format = DATE_FORMAT

    review = report.reviews.get(field.field.name)
    reasons = _reasons(field, report)
    notes: list[str] = []
    if review is not None:
        # Reviewed: no yellow or red; edited cells are blue with the original value and reason in a
        # comment.
        if review.action == "edit":
            cell.fill = BLUE
        notes.append(_review_note(field, review))
    elif field.status in ("not_found", "rejected"):
        cell.fill = RED
    elif reasons:
        cell.fill = YELLOW
    if review is not None or reasons:
        notes += [*reasons, _where(field)]
    if field.field.type == "date_range":
        notes.insert(0, _range_note(field, report))
    if notes:
        text = "\n".join(n for n in notes if n)
        cell.comment = Comment(text, "JacarandaParse", width=320, height=140)


def _pending(report: DocumentReport) -> int:
    return sum(
        bool(_reasons(a, report)) and a.field.name not in report.reviews for a in report.aligned
    )


DISCOVER = "discover"


def _visible(report: DocumentReport, name: str) -> bool:
    """Whether this field is exported: fields, tables and table columns set not to export in the
    template are omitted."""
    if name in report.hidden:
        return False
    cell = split_cell_name(name)
    return cell is None or (
        cell[0] not in report.hidden and f"{cell[0]}.{cell[2]}" not in report.hidden
    )


def _fields(report: DocumentReport) -> list[AlignedField]:
    return [a for a in report.aligned if _visible(report, a.field.name)]


def _label(reports: Sequence[DocumentReport], key: str, default: str | None = None) -> str:
    """Column header: the display name from the first document that has one; otherwise the field
    ID."""
    return next((r.labels[key] for r in reports if key in r.labels), default or key)


def _field_label(report: DocumentReport, name: str) -> str:
    """ "Field" in the details sheet: the display name; table cells are written as "Line items row 2
    Amount"."""
    cell = split_cell_name(name)
    if cell is None:
        return report.labels.get(name, name)
    table = report.labels.get(cell[0], cell[0])
    column = report.labels.get(f"{cell[0]}.{cell[2]}", cell[2])
    return (
        tr("{table} 第 {row} 行 {column}", table=table, row=cell[1], column=column)
        if cell[0] in report.labels
        else name
    )


def _sheet_title(title: str, used: set[str]) -> str:
    """Sheet names are at most 31 characters, unique, and must not contain brackets, colons,
    asterisks, question marks or slashes."""
    title = "".join(c for c in title if c not in "[]:*?/\\")[:28] or tr("表格")
    candidate, n = title, 1
    while candidate in used:
        n += 1
        candidate = f"{title} {n}"
    used.add(candidate)
    return candidate


Rows = dict[int, dict[str, AlignedField]]
"""Table row number → column name → cell."""


def _table_rows(report: DocumentReport, table: str) -> Rows:
    rows: Rows = {}
    for a in _fields(report):
        cell = split_cell_name(a.field.name)
        if cell and cell[0] == table:
            rows.setdefault(cell[1], {})[cell[2]] = a
    return rows


class ExcelExporter:
    name = "excel"
    suffix = ".xlsx"

    def export(self, reports: Sequence[DocumentReport], path: Path) -> None:
        book = Workbook()
        book.remove(book.worksheets[0])  # Remove the empty default sheet.
        templated = [r for r in reports if r.template != DISCOVER]
        discovered = [r for r in reports if r.template == DISCOVER]
        self._summary(book.create_sheet(tr("汇总")), templated)
        self._details(book.create_sheet(tr("明细")), reports)
        tables = list(
            dict.fromkeys(
                c[0] for r in templated for a in _fields(r) if (c := split_cell_name(a.field.name))
            )
        )
        used = {tr("汇总"), tr("明细"), tr("键值对"), tr("来源")}
        for table in tables:
            default = tr("商品明细") if table == "items" else tr("表格 {table}", table=table)
            title = next((r.labels[table] for r in templated if table in r.labels), default)
            self._table(book.create_sheet(_sheet_title(title, used)), templated, table)
        if discovered:
            self._pairs(book.create_sheet(tr("键值对")), discovered)
        self._sources(book.create_sheet(tr("来源")), reports)
        path.parent.mkdir(parents=True, exist_ok=True)
        book.save(path)

    def _summary(self, sheet: Worksheet, reports: Sequence[DocumentReport]) -> None:
        names = list(
            dict.fromkeys(
                a.field.name
                for r in reports
                for a in _fields(r)
                if split_cell_name(a.field.name) is None  # Table cells go to a separate sheet.
            )
        )
        # Date ranges set to "two columns": one column each for start and end.
        split = {
            n
            for n in names
            if any(r.ranges.get(n, RangeSetting()).export == "columns" for r in reports)
        }
        slots = [(n, part) for n in names for part in ((0, 1) if n in split else (None,))]
        headers = [
            _label(reports, n)
            if part is None
            else tr("{label} 开始", label=_label(reports, n))
            if part == 0
            else tr("{label} 结束", label=_label(reports, n))
            for n, part in slots
        ]
        _setup(sheet, [tr("文件"), *headers, tr("需要确认")], [18] + [16] * len(slots) + [10])

        for report in reports:
            by_name = {a.field.name: a for a in report.aligned}
            row: list[object] = [report.source]
            for n, part in slots:
                field = by_name.get(n)
                if field is None:
                    row.append(None)
                elif part is not None and field.field.type == "date_range":
                    setting = _setting(report, n)
                    if setting.export != "columns":
                        setting = RangeSetting("columns", setting.separator, setting.day_first)
                    row.append(range_cells(field, report.reviews.get(n), setting)[part])
                else:
                    row.append(cell_value(field, report.reviews.get(n), report))
            row.append(_pending(report))
            sheet.append(row)
            for column, (name, _) in enumerate(slots, start=2):
                if (field := by_name.get(name)) is not None:
                    _mark(sheet.cell(row=sheet.max_row, column=column), field, report)

        sheet.auto_filter.ref = sheet.dimensions

    def _table(self, sheet: Worksheet, reports: Sequence[DocumentReport], table: str) -> None:
        """Tables: one row per item. Columns may differ between documents (different templates);
        they are merged in order of appearance."""
        per_report = [(r, _table_rows(r, table)) for r in reports]
        columns = list(
            dict.fromkeys(c for _, rows in per_report for cells in rows.values() for c in cells)
        )
        headers = [_label(reports, f"{table}.{c}", c) for c in columns]
        _setup(sheet, [tr("文件"), tr("行号"), *headers], [18, 8] + [16] * len(columns))
        # The first column is usually the item name, so it is wider.
        sheet.column_dimensions["C"].width = 36
        for report, rows in per_report:
            for index in sorted(rows):
                cells = rows[index]
                sheet.append(
                    [
                        report.source,
                        index,
                        *(
                            cell_value(cells[c], report.reviews.get(cells[c].field.name), report)
                            if c in cells
                            else None
                            for c in columns
                        ),
                    ]
                )
                for column, name in enumerate(columns, start=3):
                    if (field := cells.get(name)) is not None:
                        cell = sheet.cell(row=sheet.max_row, column=column)
                        _mark(cell, field, report)
                        if name == "quantity":
                            # Quantities are not money; no two-decimal format.
                            cell.number_format = "0.###"
        sheet.auto_filter.ref = sheet.dimensions

    def _details(self, sheet: Worksheet, reports: Sequence[DocumentReport]) -> None:
        headers = [
            tr("文件"),
            tr("字段"),
            tr("值"),
            tr("原值"),
            tr("状态"),
            tr("出处"),
            tr("OCR 置信度"),
            tr("需要确认"),
            tr("原因"),
            tr("人工审核"),
            tr("审核原因"),
        ]
        _setup(sheet, headers, [18, 16, 40, 40, 12, 26, 11, 9, 60, 10, 40])
        for report in reports:
            for field in _fields(report):
                review = report.reviews.get(field.field.name)
                reasons = _reasons(field, report)
                pending = bool(reasons) and review is None
                confidence = field.evidence.confidence if field.evidence else None
                sheet.append(
                    [
                        report.source,
                        _field_label(report, field.field.name),
                        cell_value(field, review, report),
                        cell_value(field, None, report),
                        tr(_STATUS_NAMES[field.status]),
                        _where(field),
                        confidence,
                        tr("是") if pending else "",
                        tr("；").join(reasons),
                        tr(_ACTION_NAMES.get(review.action, review.action)) if review else "",
                        render(review.reason) if review else "",
                    ]
                )
                if field.field.type == "date":
                    for column in (3, 4):
                        sheet.cell(row=sheet.max_row, column=column).number_format = DATE_FORMAT
                sheet.cell(row=sheet.max_row, column=7).number_format = "0.00"
                if review is not None and review.action == "edit":
                    fill = BLUE
                elif pending:
                    fill = RED if field.status in ("not_found", "rejected") else YELLOW
                else:
                    continue
                for column in range(1, len(headers) + 1):
                    sheet.cell(row=sheet.max_row, column=column).fill = fill
        sheet.auto_filter.ref = sheet.dimensions

    def _pairs(self, sheet: Worksheet, reports: Sequence[DocumentReport]) -> None:
        """Documents without a template: one row per key-value pair (file, label on the document,
        value)."""
        _setup(sheet, [tr("文件"), tr("名称"), tr("值")], [18, 28, 40])
        for report in reports:
            for field in _fields(report):
                review = report.reviews.get(field.field.name)
                sheet.append(
                    [
                        report.source,
                        report.labels.get(field.field.name, field.field.name),
                        cell_value(field, review, report),
                    ]
                )
                _mark(sheet.cell(row=sheet.max_row, column=3), field, report)
        sheet.auto_filter.ref = sheet.dimensions

    def _sources(self, sheet: Worksheet, reports: Sequence[DocumentReport]) -> None:
        headers = [
            tr("文件"),
            tr("处理时间"),
            "OCR",
            tr("模型"),
            tr("模型后端"),
            tr("提示词版本"),
            tr("抽取耗时（毫秒）"),
        ]
        _setup(sheet, headers, [18, 20, 20, 24, 14, 12, 16])
        for report in reports:
            e = report.extraction
            sheet.append(
                [
                    report.source,
                    _local(report.processed_at).replace(microsecond=0),
                    report.ocr_engine,
                    e.model,
                    e.backend,
                    e.prompt_version,
                    e.duration_ms,
                ]
            )
            sheet.cell(row=sheet.max_row, column=2).number_format = "yyyy-mm-dd hh:mm:ss"
