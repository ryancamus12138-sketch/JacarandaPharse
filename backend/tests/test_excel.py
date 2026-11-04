"""Excel 导出：写出来再读回去，检查颜色、批注和类型。"""

from datetime import datetime

from openpyxl import load_workbook

from jacara.plugins.aligners import TextMatchAligner
from jacara.plugins.exporters import DocumentReport, create_exporter
from jacara.plugins.extractors import ExtractedField as F
from jacara.plugins.extractors import ExtractionResult
from jacara.plugins.validators import RuleValidator


def test_excel_marks_problems(receipt_page, tmp_path):
    fields = (
        F("date", "date", "2018-12-25", 4),
        F("total", "number", 9.0, 6),
        F("cash", "number", 10.0, 7),
        F("change", "number", 100.0, 8),
        F("fake", "number", 19.99, 6),
    )
    result = ExtractionResult(fields, "", "m", "b", "v", 0)
    report = DocumentReport(
        source="receipt.jpg",
        extraction=result,
        aligned=TextMatchAligner().align(receipt_page, result),
        checks=RuleValidator().validate(fields),
        ocr_engine="test",
        processed_at=datetime(2026, 11, 3, 12, 0),
    )
    path = tmp_path / "out.xlsx"
    create_exporter("excel").export([report], path)

    book = load_workbook(path)
    assert book.sheetnames == ["汇总", "明细", "来源"]
    sheet = book["汇总"]
    headers = [c.value for c in sheet[1]]
    row = {h: sheet.cell(row=2, column=i + 1) for i, h in enumerate(headers)}

    assert row["date"].value == datetime(2018, 12, 25)  # 真正的日期，不是文字
    assert row["fake"].fill.fgColor.rgb.endswith("FFC7CE")  # 找不到出处：红
    assert row["change"].fill.fgColor.rgb.endswith("FFEB9C")  # 规则不通过：黄
    assert row["change"].comment is not None
    assert "100.00" in row["change"].comment.text
    assert row["需要确认"].value == 4  # total、cash、change、fake
