"""Excel 导出器：给财务直接用的表格。

三个工作表：
- 汇总：一张单据一行、一个字段一列。需要确认的格子标黄或标红，鼠标移上去能看到原因和出处。
- 明细：一个字段一行，列出状态、出处位置、OCR 置信度、原因和规则校验结果，方便筛选和审计。
- 来源：每张单据用的 OCR 引擎、模型、提示词版本、处理时间（项目规则：每次抽取都记录来源）。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from pathlib import Path

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from jacara.plugins.aligners.base import AlignedField
from jacara.plugins.exporters.base import DocumentReport

RED = PatternFill("solid", fgColor="FFC7CE")
YELLOW = PatternFill("solid", fgColor="FFEB9C")
BOLD = Font(bold=True)

MONEY_FORMAT = "#,##0.00"
DATE_FORMAT = "yyyy-mm-dd"

_STATUS_NAMES = {
    "matched": "有出处",
    "fuzzy": "相似",
    "not_found": "找不到出处",
    "empty": "原文没有",
}


def cell_value(field: AlignedField) -> str | float | date | None:
    """把字段值变成 Excel 认识的类型：日期写成真正的日期，数字写成数字，Excel 里才能排序和计算。"""
    value = field.field.value
    if value is None:
        return None
    if field.field.type == "date":
        try:
            return date.fromisoformat(str(value))
        except ValueError:
            return str(value)  # 不存在的日期原样写出，规则校验已经标出来了
    if field.field.type == "string":
        return " ".join(str(value).split())
    return value


def _where(field: AlignedField) -> str:
    """出处写成一句话：第几行、图上位置。"""
    if field.evidence is None:
        return ""
    x0, y0, x1, y1 = (round(v) for v in field.evidence.bbox)
    return f"第 {field.evidence.row} 行 ({x0},{y0})-({x1},{y1})"


def _setup(sheet: Worksheet, headers: list[str], widths: list[int]) -> None:
    """写表头，加粗、冻结首行、设置列宽。"""
    sheet.append(headers)
    for cell in sheet[1]:
        cell.font = BOLD
    sheet.freeze_panes = "A2"
    for i, width in enumerate(widths):
        sheet.column_dimensions[get_column_letter(i + 1)].width = width


class ExcelExporter:
    name = "excel"
    suffix = ".xlsx"

    def export(self, reports: Sequence[DocumentReport], path: Path) -> None:
        book = Workbook()
        book.remove(book.worksheets[0])  # 去掉新建时自带的空白表
        self._summary(book.create_sheet("汇总"), reports)
        self._details(book.create_sheet("明细"), reports)
        self._sources(book.create_sheet("来源"), reports)
        path.parent.mkdir(parents=True, exist_ok=True)
        book.save(path)

    def _summary(self, sheet: Worksheet, reports: Sequence[DocumentReport]) -> None:
        names = list(dict.fromkeys(a.field.name for r in reports for a in r.aligned))
        _setup(sheet, ["文件", *names, "需要确认"], [18] + [16] * len(names) + [10])

        for report in reports:
            flagged = report.checks.fields_with_issues()
            by_name = {a.field.name: a for a in report.aligned}
            row: list[object] = [report.source]
            row += [cell_value(by_name[n]) if n in by_name else None for n in names]
            reviews = sum(a.needs_review or a.field.name in flagged for a in report.aligned)
            row.append(reviews)
            sheet.append(row)

            for column, name in enumerate(names, start=2):
                field = by_name.get(name)
                if field is None:
                    continue
                cell = sheet.cell(row=sheet.max_row, column=column)
                if field.field.type == "number":
                    cell.number_format = MONEY_FORMAT
                elif field.field.type == "date":
                    cell.number_format = DATE_FORMAT

                reasons = [field.note] if field.needs_review else []
                reasons += [i.message for i in report.checks.issues if name in i.fields]
                if field.status == "not_found":
                    cell.fill = RED
                elif reasons:
                    cell.fill = YELLOW
                if reasons:
                    text = "\n".join([*reasons, _where(field)]).strip()
                    cell.comment = Comment(text, "JacarandaParse", width=320, height=120)

        sheet.auto_filter.ref = sheet.dimensions

    def _details(self, sheet: Worksheet, reports: Sequence[DocumentReport]) -> None:
        headers = ["文件", "字段", "值", "状态", "出处", "OCR 置信度", "需要确认", "原因"]
        _setup(sheet, headers, [18, 16, 40, 12, 26, 11, 9, 60])
        for report in reports:
            for field in report.aligned:
                issues = [i.message for i in report.checks.issues if field.field.name in i.fields]
                reasons = ([field.note] if field.needs_review else []) + issues
                confidence = field.evidence.confidence if field.evidence else None
                sheet.append(
                    [
                        report.source,
                        field.field.name,
                        cell_value(field),
                        _STATUS_NAMES[field.status],
                        _where(field),
                        confidence,
                        "是" if reasons else "",
                        "；".join(reasons),
                    ]
                )
                cell = sheet.cell(row=sheet.max_row, column=3)
                if field.field.type == "date":
                    cell.number_format = DATE_FORMAT
                sheet.cell(row=sheet.max_row, column=6).number_format = "0.00"
                if reasons:
                    fill = RED if field.status == "not_found" else YELLOW
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
                    report.processed_at.replace(microsecond=0),
                    report.ocr_engine,
                    e.model,
                    e.backend,
                    e.prompt_version,
                    e.duration_ms,
                ]
            )
            sheet.cell(row=sheet.max_row, column=2).number_format = "yyyy-mm-dd hh:mm:ss"
