"""Excel 导出器：给财务直接用的表格。

四个工作表：
- 汇总：一张单据一行、一个字段一列（不含表格）。还没确认的格子标黄或标红，人工改过的标蓝；
  鼠标移上去能看到原因、出处，以及"原值 → 人工改为"。
- 明细：一个字段一行，列出当前值、原值、状态、出处、OCR 置信度、原因和人工审核，方便筛选和审计。
- 商品明细：一行商品一行，前面是文件名和第几行，格子的颜色和批注同"汇总"。没有表格就不建这张表。
- 来源：每张单据用的 OCR 引擎、模型、提示词版本、处理时间（项目规则：每次抽取都记录来源）。

有人工审核结果时（report.reviews），格子里写审核之后的值；原值在批注和"明细"里都能看到。
"""

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

from jacara.plugins.aligners.base import AlignedField
from jacara.plugins.exporters.base import DocumentReport, Review
from jacara.plugins.extractors.base import split_cell_name

RED = PatternFill("solid", fgColor="FFC7CE")
YELLOW = PatternFill("solid", fgColor="FFEB9C")
BLUE = PatternFill("solid", fgColor="DDEBF7")
"""人工改过的值。"""
BOLD = Font(bold=True)

MONEY_FORMAT = "#,##0.00"
DATE_FORMAT = "yyyy-mm-dd"

_STATUS_NAMES = {
    "matched": "有出处",
    "fuzzy": "相似",
    "not_found": "找不到出处",
    "empty": "原文没有",
}

_ACTION_NAMES = {"edit": "人工修改", "confirm": "人工确认"}


def typed(type_: str, value: str | float | None) -> str | float | date | None:
    """把值变成 Excel 认识的类型：日期写成真正的日期，数字写成数字，Excel 里才能排序和计算。"""
    if value is None:
        return None
    if type_ == "date":
        try:
            return date.fromisoformat(str(value))
        except ValueError:
            return str(value)  # 不存在的日期原样写出，规则校验已经标出来了
    if type_ == "number":
        try:
            return float(value)
        except ValueError:
            return str(value)
    return " ".join(str(value).split())


def cell_value(field: AlignedField, review: Review | None = None) -> str | float | date | None:
    """格子里写的值：有人工审核就用审核之后的值，否则用模型抽出的值。"""
    value = review.value if review is not None else field.field.value
    return typed(field.field.type, value)


def _where(field: AlignedField) -> str:
    """出处写成一句话：第几行、图上位置。"""
    if field.evidence is None:
        return ""
    x0, y0, x1, y1 = (round(v) for v in field.evidence.bbox)
    return f"第 {field.evidence.row} 行 ({x0},{y0})-({x1},{y1})"


def _reasons(field: AlignedField, report: DocumentReport) -> list[str]:
    """这个字段为什么要人工确认：对齐的说明 + 没通过的规则。"""
    reasons = [field.note] if field.needs_review else []
    return reasons + [i.message for i in report.checks.issues if field.field.name in i.fields]


def _local(moment: datetime) -> datetime:
    """换成电脑所在时区的时间，并去掉时区标记（Excel 不支持带时区的时间）。

    数据库里存的是 UTC（世界标准时间），直接写进去，悉尼的用户会看到差 11 个小时的时间。
    """
    return moment.astimezone().replace(tzinfo=None)


def _review_note(field: AlignedField, review: Review) -> str:
    """批注里的审核记录：原值 → 新值、原因、时间。"""
    when = _local(review.reviewed_at).strftime("%Y-%m-%d %H:%M")
    if review.action == "edit":
        old = "（空）" if field.field.value is None else field.field.value
        new = "（空）" if review.value is None else review.value
        text = f"人工修改：原值 {old} → {new}"
    else:
        text = "人工确认：值无误"
    if review.reason:
        text += f"\n原因：{review.reason}"
    return f"{text}\n{when}"


def _setup(sheet: Worksheet, headers: list[str], widths: list[int]) -> None:
    """写表头，加粗、冻结首行、加筛选按钮、设置列宽。"""
    sheet.append(headers)
    for cell in sheet[1]:
        cell.font = BOLD
    sheet.freeze_panes = "A2"
    for i, width in enumerate(widths):
        sheet.column_dimensions[get_column_letter(i + 1)].width = width


def _mark(cell: Cell | MergedCell, field: AlignedField, report: DocumentReport) -> None:
    """一个格子的格式、颜色和批注：没确认的标黄（找不到出处标红），人工改过的标蓝。"""
    if field.field.type == "number":
        cell.number_format = MONEY_FORMAT
    elif field.field.type == "date":
        cell.number_format = DATE_FORMAT

    review = report.reviews.get(field.field.name)
    reasons = _reasons(field, report)
    notes: list[str] = []
    if review is not None:
        # 有人看过了：不再标黄标红；改过的标蓝，批注里写原值和原因
        if review.action == "edit":
            cell.fill = BLUE
        notes.append(_review_note(field, review))
    elif field.status == "not_found":
        cell.fill = RED
    elif reasons:
        cell.fill = YELLOW
    if review is not None or reasons:
        notes += [*reasons, _where(field)]
        text = "\n".join(n for n in notes if n)
        cell.comment = Comment(text, "JacarandaParse", width=320, height=140)


def _pending(report: DocumentReport) -> int:
    return sum(
        bool(_reasons(a, report)) and a.field.name not in report.reviews for a in report.aligned
    )


Rows = dict[int, dict[str, AlignedField]]
"""表格的行号 → 列名 → 格子。"""


def _table_rows(report: DocumentReport, table: str) -> Rows:
    rows: Rows = {}
    for a in report.aligned:
        cell = split_cell_name(a.field.name)
        if cell and cell[0] == table:
            rows.setdefault(cell[1], {})[cell[2]] = a
    return rows


class ExcelExporter:
    name = "excel"
    suffix = ".xlsx"

    def export(self, reports: Sequence[DocumentReport], path: Path) -> None:
        book = Workbook()
        book.remove(book.worksheets[0])  # 去掉新建时自带的空白表
        self._summary(book.create_sheet("汇总"), reports)
        self._details(book.create_sheet("明细"), reports)
        tables = list(
            dict.fromkeys(
                c[0] for r in reports for a in r.aligned if (c := split_cell_name(a.field.name))
            )
        )
        for table in tables:
            title = "商品明细" if table == "items" else f"表格 {table}"
            self._table(book.create_sheet(title), reports, table)
        self._sources(book.create_sheet("来源"), reports)
        path.parent.mkdir(parents=True, exist_ok=True)
        book.save(path)

    def _summary(self, sheet: Worksheet, reports: Sequence[DocumentReport]) -> None:
        names = list(
            dict.fromkeys(
                a.field.name
                for r in reports
                for a in r.aligned
                if split_cell_name(a.field.name) is None  # 表格的格子放在单独的表里
            )
        )
        _setup(sheet, ["文件", *names, "需要确认"], [18] + [16] * len(names) + [10])

        for report in reports:
            by_name = {a.field.name: a for a in report.aligned}
            row: list[object] = [report.source]
            row += [
                cell_value(by_name[n], report.reviews.get(n)) if n in by_name else None
                for n in names
            ]
            row.append(_pending(report))
            sheet.append(row)
            for column, name in enumerate(names, start=2):
                if (field := by_name.get(name)) is not None:
                    _mark(sheet.cell(row=sheet.max_row, column=column), field, report)

        sheet.auto_filter.ref = sheet.dimensions

    def _table(self, sheet: Worksheet, reports: Sequence[DocumentReport], table: str) -> None:
        """表格：一行商品一行。不同单据的列可能不一样（模板不同），按出现的顺序合在一起。"""
        per_report = [(r, _table_rows(r, table)) for r in reports]
        columns = list(
            dict.fromkeys(c for _, rows in per_report for cells in rows.values() for c in cells)
        )
        _setup(sheet, ["文件", "第几行", *columns], [18, 8] + [16] * len(columns))
        sheet.column_dimensions["C"].width = 36  # 第一列一般是商品名，长一些
        for report, rows in per_report:
            for index in sorted(rows):
                cells = rows[index]
                sheet.append(
                    [
                        report.source,
                        index,
                        *(
                            cell_value(cells[c], report.reviews.get(cells[c].field.name))
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
                            cell.number_format = "0.###"  # 数量不是钱，不显示两位小数
        sheet.auto_filter.ref = sheet.dimensions

    def _details(self, sheet: Worksheet, reports: Sequence[DocumentReport]) -> None:
        headers = [
            "文件",
            "字段",
            "值",
            "原值",
            "状态",
            "出处",
            "OCR 置信度",
            "需要确认",
            "原因",
            "人工审核",
            "审核原因",
        ]
        _setup(sheet, headers, [18, 16, 40, 40, 12, 26, 11, 9, 60, 10, 40])
        for report in reports:
            for field in report.aligned:
                review = report.reviews.get(field.field.name)
                reasons = _reasons(field, report)
                pending = bool(reasons) and review is None
                confidence = field.evidence.confidence if field.evidence else None
                sheet.append(
                    [
                        report.source,
                        field.field.name,
                        cell_value(field, review),
                        cell_value(field),
                        _STATUS_NAMES[field.status],
                        _where(field),
                        confidence,
                        "是" if pending else "",
                        "；".join(reasons),
                        _ACTION_NAMES[review.action] if review else "",
                        review.reason if review else "",
                    ]
                )
                if field.field.type == "date":
                    for column in (3, 4):
                        sheet.cell(row=sheet.max_row, column=column).number_format = DATE_FORMAT
                sheet.cell(row=sheet.max_row, column=7).number_format = "0.00"
                if review is not None and review.action == "edit":
                    fill = BLUE
                elif pending:
                    fill = RED if field.status == "not_found" else YELLOW
                else:
                    continue
                for column in range(1, len(headers) + 1):
                    sheet.cell(row=sheet.max_row, column=column).fill = fill
        sheet.auto_filter.ref = sheet.dimensions

    def _sources(self, sheet: Worksheet, reports: Sequence[DocumentReport]) -> None:
        headers = ["文件", "处理时间", "OCR", "模型", "模型后端", "提示词版本", "抽取用时（毫秒）"]
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
