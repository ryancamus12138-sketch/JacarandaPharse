"""Creating templates by selecting regions on a sample."""

from __future__ import annotations

import uuid
from dataclasses import replace

import pytest

from jacara.core.db import session_scope
from jacara.modules.templates.boxes import (
    field_id,
    guess_type,
    hint,
    lines_in,
    pair_for,
    position,
)
from jacara.pipeline.store import save_document
from jacara.structure.analyze import analyze_page


def line(text: str, x0: float, y0: float, x1: float, y1: float) -> dict:
    return {"text": text, "box": [x0, y0, x1, y1], "confidence": 0.99}


LINES = [
    line("Invoice No", 100, 100, 240, 130),
    line(":", 250, 100, 258, 130),
    line("INV-0042", 280, 100, 400, 130),
    line("Total", 100, 400, 180, 430),
    line("1,234.50", 600, 400, 720, 430),
    line("Bill To", 100, 200, 200, 230),
    line("Example Pty Ltd", 100, 240, 330, 270),
]


def test_value_and_pair():
    box = [275, 95, 410, 135]
    assert [ln["text"] for ln in lines_in(LINES, box)] == ["INV-0042"]

    class Pair:
        def __init__(self, key, value_box):
            self.key, self.value_box = key, value_box

    pairs = [Pair("Invoice No", [280, 100, 400, 130]), Pair("Total", [600, 400, 720, 430])]
    assert getattr(pair_for(pairs, box), "key", None) == "Invoice No"
    assert getattr(pair_for(pairs, [590, 395, 730, 435]), "key", None) == "Total"
    assert pair_for(pairs, [90, 230, 340, 280]) is None  # Unpaired: no guessing.


def test_structure_pairs_labels_correctly():
    """Layout pairing: a value does not swallow the value of the next line's label; for table rows
    not recognized as a table, the row name pairs with the rightmost amount."""
    from tests.conftest import make_line, make_page

    page = make_page(
        make_line("Date :", 40, 100, 110, 125),
        make_line("25/12/2018 8:13:39 PM", 160, 100, 360, 125),
        make_line("Cashier :", 40, 130, 120, 155),
        make_line("MANIS", 160, 128, 230, 153),
        make_line("Room Charges", 40, 300, 180, 325),
        make_line("Rs 1939 x 1 Night", 400, 300, 560, 325),
        make_line("Rs 1939", 700, 300, 780, 325),
    )
    s = analyze_page(page)
    pairs = {s.blocks[k].text: s.blocks[s.blocks[k].attrs["value"]].text for k in s.keys}
    assert pairs["Date"] == "25/12/2018 8:13:39 PM"
    assert pairs["Cashier"] == "MANIS"
    assert pairs["Room Charges"] == "Rs 1939"


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("1,234.50", "number"),
        ("$4.11", "number"),
        ("2018-12-25", "date"),
        ("August 3 , 2014", "date"),
        ("July 1 - July 31, 2014", "date_range"),
        ("INV-0042", "string"),
        ("00030340067212", "string"),
        ("296664039561", "string"),
        ("1", "number"),
        ("Example Pty Ltd", "string"),
    ],
)
def test_guess_type(text, kind):
    assert guess_type(text) == kind


def test_position_id_and_hint():
    assert position([900, 50, 1000, 90], 1000, 1400) == "右上角"
    assert position([400, 650, 600, 700], 1000, 1400) == "中间"
    used: set[str] = set()
    assert field_id("Invoice No", used, 1) == "invoice_no"
    assert field_id("Invoice No", used, 2) == "invoice_no_2"
    assert field_id("发票号码", used, 3) == "field_3"
    assert hint("右上角", "Invoice No", "右边") == "在单据右上角，「Invoice No」右边"


@pytest.fixture
def document_id(factory, report, receipt_page, tmp_path) -> uuid.UUID:
    path = tmp_path / "receipt.jpg"
    path.write_bytes(b"x")
    with session_scope(factory) as session:
        stored = replace(report, ocr_page=receipt_page, structure=analyze_page(receipt_page))
        return save_document(session, path, [stored]).id


def test_describe_region_uses_stored_ocr(client, document_id):
    detail = client.get(f"/api/v1/documents/{document_id}").json()
    ocr = client.get(f"/api/v1/documents/{document_id}/pages/1/ocr").json()
    target = next(ln for ln in ocr["lines"] if ln["text"] == "10.00")
    x0, y0, x1, y1 = target["box"]
    response = client.post(
        f"/api/v1/documents/{document_id}/pages/1/describe-region",
        json={"box": [x0 - 2, y0 - 2, x1 + 2, y1 + 2]},
    )
    assert response.status_code == 200, response.text
    found = response.json()
    assert found["text"] == "10.00" and found["type"] == "number"
    # "Cash → 10.00" paired by layout analysis.
    assert found["label"] == "Cash" and found["side"] == "右边"
    assert found["description"].startswith("在单据")
    assert detail["id"] == str(document_id)


def test_create_template_from_boxes(client, document_id):
    response = client.post(
        "/api/v1/templates/from-boxes",
        json={
            "document_id": str(document_id),
            "title": "框出来的收据",
            "fields": [
                {
                    "name": "total",
                    "label": "总额",
                    "type": "number",
                    "required": True,
                    "description": "在单据右下角，「Total」右边",
                    "aliases": ["Total"],
                    "page": 1,
                    "box": [1, 2, 3, 4],
                }
            ],
        },
    )
    assert response.status_code == 201, response.text
    template = response.json()
    detail = client.get(f"/api/v1/templates/{template['id']}").json()
    [field] = detail["fields"]
    assert field["aliases"] == ["Total"] and "box" not in field
