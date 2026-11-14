"""PDF pages are rendered for OCR; the text layer and form fields serve only as candidates.

Uses hand-written PDFs from pdfmaker: a font mapping off by one glyph, invisible old OCR text,
form fields (with and without appearance, hidden), and a page rotated 90 degrees. OCR uses the
real RapidOCR."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from jacara.core.i18n import render
from jacara.pipeline.fieldcheck import check_fields, overlap, rule_hints, supports
from jacara.pipeline.zoom import Zoom
from jacara.plugins.aligners.base import AlignedField, Candidate, Evidence
from jacara.plugins.extractors.base import ExtractedField, FieldType
from jacara.plugins.ocr import OcrConfig, create_ocr_engine
from jacara.plugins.ocr.loader import load_pages
from jacara.templates import Template, TemplateField
from tests.pdfmaker import Field, Page, Run, make_pdf


@pytest.fixture(scope="module")
def ocr():
    return create_ocr_engine(OcrConfig())


def write(tmp_path: Path, *pages: Page, name: str = "doc.pdf") -> Path:
    path = tmp_path / name
    path.write_bytes(make_pdf(*pages))
    return path


def wrong_invoice() -> Page:
    """The page shows 1234567890; the text layer (font mapping 0 to 8) reads 1234567898."""
    return Page(
        runs=[
            Run(20, 150, "Invoice No:"),
            Run(110, 150, "1234567890", font="F2"),
            Run(20, 100, "Total: 128.00"),
            Run(20, 60, "old scan text", invisible=True),
        ],
        wrong_map={"0": "8"},
    )


def line_with(page, text: str):
    return next(ln for ln in page.lines if text in ln.text)


def aligned(name: str, type_: FieldType, value, line) -> AlignedField:
    field = ExtractedField(name, type_, value)
    return AlignedField(field, "matched", Evidence(0, line.text, (line,), 1.0), False)


def template(*fields: TemplateField) -> Template:
    return Template(name="t", version=1, title="t", description="", fields=fields, rules=())


def spec(name: str, label: str, type_: FieldType = "number", **kw) -> TemplateField:
    return TemplateField(
        name=name, label=label, type=type_, required=kw.pop("required", False), description="", **kw
    )


def test_pdf_pages_are_read_by_ocr_and_keep_the_text_layer_aside(tmp_path, ocr):
    page = load_pages(write(tmp_path, wrong_invoice()), ocr)[0]
    assert page.engine == "rapidocr"
    assert any("1234567890" in ln.text for ln in page.lines)  # OCR read the image correctly.
    layer = [ln.text for ln in page.text_layer]
    assert "1234567898" in layer  # The text layer is wrong but kept aside.
    assert not any("old" in t for t in layer)  # Invisible old OCR text is excluded.
    assert "看不见的字" in page.models["note"]


def test_text_mode_still_reads_only_the_text_layer(tmp_path, ocr):
    page = load_pages(write(tmp_path, wrong_invoice()), ocr, pdf_read="text")[0]
    assert page.engine == "pdf-text"
    assert any("1234567898" in ln.text for ln in page.lines)


def test_wrong_text_layer_never_overwrites_ocr(tmp_path, ocr):
    path = write(tmp_path, wrong_invoice())
    page = load_pages(path, ocr)[0]
    line = line_with(page, "1234567890")
    field = aligned("invoice_no", "string", "1234567890", line)
    [checked] = check_fields(
        [field],
        page,
        template(spec("invoice_no", "Invoice No", "string", unique=True)),
        Zoom(path, ocr),
    )
    assert checked.field.value == "1234567890"  # Value unchanged.
    assert checked.layer == "conflict"
    assert checked.needs_review  # Document numbers are key fields: always await confirmation.
    sources = {c.source: c.text for c in checked.candidates}
    assert "1234567898" in sources["text_layer"]
    assert sources.get("zoom", "1234567890").endswith("1234567890")
    assert "PDF 文字层这里写的是" in checked.note


def test_agreeing_text_layer_is_only_recorded(tmp_path, ocr):
    page = load_pages(write(tmp_path, wrong_invoice()), ocr)[0]
    line = line_with(page, "128.00")
    [checked] = check_fields([aligned("total", "number", 128.0, line)], page, None)
    assert checked.layer == "agree"
    assert not checked.needs_review and not checked.candidates


def lab_page() -> Page:
    """Lab results mostly have one decimal place (78.5, 0.9)."""
    return Page(
        runs=[
            Run(20, 150, "NEUT%"),
            Run(200, 150, "78.5"),
            Run(20, 100, "EO%"),
            Run(200, 100, "0.9"),
        ]
    )


def test_one_decimal_results_agree_with_the_text_layer(tmp_path, ocr):
    """Numbers used to be searched only with two decimals (78.50), so results with one decimal were
    reported as differing in all three sources even when text layer, OCR and zoomed reading
    agreed."""
    path = write(tmp_path, lab_page())
    page = load_pages(path, ocr)[0]
    for text, value in (("78.5", 78.5), ("0.9", 0.9)):
        [checked] = check_fields(
            [aligned("r", "number", value, line_with(page, text))], page, None, Zoom(path, ocr)
        )
        assert checked.layer == "agree", checked.note
        assert not checked.needs_review and not checked.candidates


def test_layer_and_ocr_agree_but_the_value_does_not(tmp_path, ocr):
    """Text layer and OCR agree but neither matches the model value: say the value is the problem,
    not that the text layer differs from OCR."""
    path = write(tmp_path, lab_page())
    page = load_pages(path, ocr)[0]
    [checked] = check_fields(
        [aligned("r", "number", 78.6, line_with(page, "78.5"))], page, None, Zoom(path, ocr)
    )
    assert checked.layer == "conflict" and checked.needs_review
    assert "PDF 文字层和 OCR 这里都是「78.5」，和现在的值「78.6」对不上" in render(checked.note)
    assert "和文字层、OCR 一致" in render(checked.note)
    assert "不一样" not in checked.note


def test_images_have_no_text_layer(tmp_path, ocr):
    page = load_pages(write(tmp_path, wrong_invoice()), ocr)[0]
    bare = replace(page, text_layer=())
    [checked] = check_fields(
        [aligned("total", "number", 128.0, line_with(page, "128.00"))], bare, None
    )
    assert checked.layer == "none"


def test_missing_field_is_looked_for_next_to_its_label(tmp_path, ocr):
    path = write(tmp_path, wrong_invoice())
    page = load_pages(path, ocr)[0]
    missing = AlignedField(
        ExtractedField("total", "number", None), "empty", None, False, "模型说原文里没有这个字段"
    )
    tpl = template(spec("total", "Total", required=True))
    [checked] = check_fields([missing], page, tpl, Zoom(path, ocr))
    assert checked.field.value is None  # Left empty until accepted.
    assert checked.needs_review
    assert any(c.value == "128" for c in checked.candidates)
    assert "模型没有给出「Total」" in render(checked.note)


def test_required_field_without_label_waits_for_review(tmp_path, ocr):
    page = load_pages(write(tmp_path, wrong_invoice()), ocr)[0]
    missing = AlignedField(ExtractedField("tax", "number", None), "empty", None, False)
    [checked] = check_fields([missing], page, template(spec("tax", "Tax amount", required=True)))
    assert checked.needs_review and "留空待审" in checked.note
    [optional] = check_fields([missing], page, template(spec("tax", "Tax amount")))
    assert not optional.needs_review


def test_form_values_and_their_visibility(tmp_path, ocr):
    form = Page(
        runs=[Run(20, 150, "Amount")],
        fields=[
            Field("amount", "128.00", (20, 100, 120, 120)),
            Field("secret", "9.99", (20, 40, 120, 60), hidden=True),
        ],
    )
    page = load_pages(write(tmp_path, form), ocr)[0]
    assert {f.name: f.hidden for f in page.forms} == {"amount": False, "secret": True}
    # Form values are rendered on the image too.
    assert any("128.00" in ln.text for ln in page.lines)
    assert not any("9.99" in ln.text for ln in page.lines)  # Hidden fields are not rendered.
    tpl = template(spec("amount", "Amount"), spec("secret", "Secret"))
    empty = [
        AlignedField(ExtractedField("amount", "number", None), "empty", None, False),
        AlignedField(ExtractedField("secret", "number", None), "empty", None, False),
    ]
    amount, secret = check_fields(empty, page, tpl)
    assert any(c.source == "form" and render(c.note) == "页面上能看到" for c in amount.candidates)
    assert any(c.source == "form" and render(c.note) == "页面不可见" for c in secret.candidates)
    assert any(c.source == "form" and c.visible is True for c in amount.candidates)
    assert any(c.source == "form" and c.visible is False for c in secret.candidates)
    assert secret.needs_review and "页面不可见" in render(secret.note)


def test_rotated_page_text_layer_lines_up_with_ocr(tmp_path, ocr):
    page = load_pages(
        write(tmp_path, Page(runs=[Run(20, 150, "Rotated 42", size=20)], rotate=90)), ocr
    )[0]
    assert page.width < page.height
    ocr_line = line_with(page, "42")
    layer = next(ln for ln in page.text_layer if "42" in ln.text)
    assert overlap(ocr_line.bbox, layer.bbox) > 0.5


def test_zoom_on_a_photo_uses_the_original_image(tmp_path, ocr):
    path = write(tmp_path, wrong_invoice())
    from jacara.plugins.ocr.loader import page_png

    png = tmp_path / "photo.png"
    png.write_bytes(page_png(path.read_bytes(), True, 1))
    photo = load_pages(png, ocr)[0]
    line = line_with(photo, "128.00")
    found = Zoom(png, ocr)(line.bbox)
    assert found is not None and "128.00" in found.text and found.dpi is None


def test_supports_compares_by_type():
    assert supports("number", 128.0, "Total: 128.00")
    assert supports("number", 78.5, "78.5") and supports("number", "0.9", "0.9 ↑")
    assert not supports("number", 78.5, "178.5") and not supports("number", 0.9, "0.95")
    assert not supports("number", 128.0, "Total: 1280.00")
    assert supports("date", "2014-08-03", "August 3 , 2014")
    # Key fields: one differing digit means different.
    assert not supports("string", "1234567890", "1234567898")
    # Radical code points are normalized only for comparison.
    assert supports("string", "示例医院", "⽰例医院")


def test_rule_hints_mark_which_candidate_passes():
    class OnlyPositive:
        name = "only-positive"

        def validate(self, fields):
            from jacara.plugins.validators.base import Issue, ValidationResult

            bad = [f for f in fields if isinstance(f.value, float) and f.value < 0]
            issues = tuple(Issue("pos", "要是正数", "", (f.name,), "error") for f in bad)
            return ValidationResult(issues, (), ())

    field = ExtractedField("total", "number", 5.0)
    a = AlignedField(
        field,
        "fuzzy",
        None,
        True,
        candidates=(Candidate("ocr", "-5", value="-5"), Candidate("zoom", "5", value="5")),
    )
    [hinted] = rule_hints([a], [field], OnlyPositive())
    assert [c.rule_ok for c in hinted.candidates] == [False, True]


def test_rendering_from_many_threads_at_once():
    """pdfium is not thread-safe: the review page requests several crops at once, and handlers
    rendering the same PDF concurrently in the thread pool used to fail with "Failed to load
    page." (500). All pdfium calls now queue on one lock."""
    from concurrent.futures import ThreadPoolExecutor

    from jacara.plugins.ocr.loader import page_png

    data = make_pdf(wrong_invoice(), wrong_invoice())

    def render(k: int) -> bytes:
        return page_png(data, True, 1 + k % 2, scale=2 + k % 3)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(render, range(48)))
    assert all(r.startswith(b"\x89PNG") for r in results)
