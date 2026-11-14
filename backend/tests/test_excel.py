"""Excel export: write, read back, and check colors, comments and types."""

from dataclasses import replace
from datetime import datetime

from openpyxl import load_workbook

from jacara.plugins.aligners import TextMatchAligner
from jacara.plugins.exporters import DocumentReport, Review, create_exporter
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

    assert row["date"].value == datetime(2018, 12, 25)  # A real date, not text.
    assert row["fake"].fill.fgColor.rgb.endswith("FFC7CE")  # No evidence: red.
    assert row["change"].fill.fgColor.rgb.endswith("FFEB9C")  # Rule failed: yellow.
    assert row["change"].comment is not None
    assert "100.00" in row["change"].comment.text
    assert row["需要确认"].value == 4  # total, cash, change, fake


def test_excel_uses_reviewed_values(report, tmp_path):
    """With manual review: cells hold post-review values, edited cells are blue with the original
    value and reason in a comment, and reviewed fields no longer need confirmation."""
    reviewed = replace(
        report,
        reviews={
            "change": Review("edit", "1.0", "OCR 把 1.00 读成了 100", datetime(2026, 11, 7, 9, 0)),
            "total": Review("confirm", "9.0", "", datetime(2026, 11, 7, 9, 1)),
        },
    )
    path = tmp_path / "out.xlsx"
    create_exporter("excel").export([reviewed], path)

    book = load_workbook(path)
    sheet = book["汇总"]
    headers = [c.value for c in sheet[1]]
    row = {h: sheet.cell(row=2, column=i + 1) for i, h in enumerate(headers)}

    assert row["change"].value == 1.0  # Post-review value, as a number.
    assert row["change"].fill.fgColor.rgb.endswith("DDEBF7")  # Manually edited: blue.
    comment = row["change"].comment
    assert comment is not None
    assert "原值 100.0 → 1.0" in comment.text
    assert "OCR 把 1.00 读成了 100" in comment.text
    assert row["total"].fill.fill_type is None  # Manually confirmed: no longer yellow.
    assert row["cash"].fill.fgColor.rgb.endswith("FFEB9C")  # Not yet reviewed: still yellow.
    assert row["需要确认"].value == 1  # Only cash remains.

    details = book["明细"]
    columns = [c.value for c in details[1]]
    change = next(r for r in details.iter_rows(min_row=2, values_only=True) if r[1] == "change")
    line = dict(zip(columns, change, strict=True))
    assert (line["值"], line["原值"], line["人工审核"]) == (1.0, 100.0, "人工修改")
