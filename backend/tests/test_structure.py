"""Page structure: text block indices, layout regions, tables, label → value pairs; block-based
verification (LMDX); values left unfilled."""

import json
import uuid

import numpy as np

from jacara.core.db import session_scope
from jacara.core.i18n import render
from jacara.models import ExtractedValue
from jacara.pipeline.process import process_document
from jacara.pipeline.run import Pipeline
from jacara.plugins.aligners import BlockAligner
from jacara.plugins.extractors import FieldSpec, LlmExtractor, TableSpec
from jacara.structure.analyze import analyze_page
from jacara.structure.engines import CellGuess, Region, StructureEngines
from jacara.structure.graph import structure_from_json
from jacara.structure.serialize import spatial_text, structure_text

from .conftest import make_line, make_page
from .test_llm_extractor import FakeBackend

HOTEL = make_page(
    make_line("SAMPLE HOTEL", 80, 50, 360, 90),
    make_line("Guest Name: Alex Lee", 80, 160, 380, 186),
    make_line("Date: 31/12/2017", 1000, 160, 1230, 186),
    make_line("Hotel Details", 80, 260, 260, 286),
    make_line("Check In", 700, 260, 820, 286),
    make_line("Rooms", 1200, 260, 1290, 286),
    make_line("Sample Resort Garden,", 80, 295, 380, 321),
    make_line("31/12/2017", 700, 295, 850, 321),
    make_line("1", 1200, 295, 1215, 321),
    make_line("12 Example Street", 80, 330, 330, 356),
    make_line("Payment Mode:", 80, 400, 300, 426),
    make_line("Cash at Hotel", 320, 400, 500, 426),
    make_line("Grand Total", 780, 760, 930, 786),
    make_line("Rs 2539", 1140, 760, 1250, 786),
)


def numbers(s, text):
    return next(int(b.id[1:]) for b in s.blocks.values() if b.type == "WORD" and b.text == text)


def pairs(s):
    return {
        s.blocks[k].text: (s.blocks[s.blocks[k].attrs["value"]].text, s.blocks[k].attrs["rule"])
        for k in s.keys
    }


def test_words_are_numbered_in_reading_order():
    s = analyze_page(HOTEL)
    texts = [s.blocks[w].text for w in s.words]
    assert texts[:3] == ["SAMPLE HOTEL", "Guest Name: Alex Lee", "Date: 31/12/2017"]
    first = s.word(1)
    assert first is not None and first.text == "SAMPLE HOTEL"
    assert all(s.blocks[line].type == "LINE" for line in s.lines)


def test_key_values_like_textract_forms():
    found = pairs(analyze_page(HOTEL))
    assert found["Guest Name"] == ("Alex Lee", "inline")  # "label: value" within one block.
    assert found["Date"] == ("31/12/2017", "inline")
    # Colon-terminated label with the value to the right.
    assert found["Payment Mode"][0] == "Cash at Hotel"
    # Headers above, values in columns below; the street continuing the shop name belongs to that
    # column.
    assert found["Hotel Details"] == ("Sample Resort Garden, 12 Example Street", "header_above")
    assert found["Rooms"] == ("1", "header_above")
    assert found["Check In"] == ("31/12/2017", "header_above")
    assert found["Grand Total"] == ("Rs 2539", "label_left")


def test_spatial_text_and_structure_for_the_model():
    s = analyze_page(HOTEL)
    text = spatial_text(s)
    # Every block indexed; Rooms and 1 are vertically aligned in one column.
    rooms, one = numbers(s, "Rooms"), numbers(s, "1")
    lines = text.splitlines()
    above = next(ln for ln in lines if f"⟨{rooms}⟩Rooms" in ln)
    below = next(ln for ln in lines if f"⟨{one}⟩1" in ln)
    assert above.index(f"⟨{rooms}⟩") == below.index(f"⟨{one}⟩")
    assert f"⟨{rooms}⟩Rooms → ⟨{one}⟩1" in structure_text(s)


class FakeLayout:
    name = "fake-layout"

    def __init__(self, regions):
        self.regions = regions

    def detect(self, image):
        return self.regions


class FakeTable:
    name = "fake-table"

    def __init__(self, cells):
        self.cells = cells

    def recognize(self, image):
        return self.cells


TABLE_PAGE = make_page(
    make_line("DESCRIPTION", 100, 500, 260, 520),
    make_line("AMOUNT", 1140, 500, 1250, 520),
    make_line("Room Charges", 100, 570, 280, 590),
    make_line("Rs 1939", 1140, 570, 1250, 590),
    make_line("Breakfast", 100, 640, 230, 660),
    make_line("Rs 600", 1140, 640, 1240, 660),
    make_line("Subtotal and taxes included", 100, 710, 600, 730),
)
TABLE_REGION = Region("表格", (80, 480, 1420, 750), 0.95)
IMAGE = np.zeros((1300, 1500, 3), dtype=np.uint8)


def test_table_from_the_table_model_with_a_merged_cell():
    # Table model coordinates refer to the cropped image, which was padded by 4 pixels.
    def cell(r, r1, c, c1, x0, y0, x1, y1):
        return CellGuess(r, r1, c, c1, (x0 - 76, y0 - 476, x1 - 76, y1 - 476))

    guesses = [
        cell(0, 0, 0, 0, 100, 500, 260, 520),
        cell(0, 0, 1, 1, 1140, 500, 1250, 520),
        cell(1, 1, 0, 0, 100, 570, 280, 590),
        cell(1, 1, 1, 1, 1140, 570, 1250, 590),
        cell(2, 2, 0, 0, 100, 640, 230, 660),
        cell(2, 2, 1, 1, 1140, 640, 1240, 660),
        cell(3, 3, 0, 1, 100, 710, 600, 730),  # The last row spans two columns.
    ]
    engines = StructureEngines(FakeLayout([TABLE_REGION]), FakeTable(guesses))
    s = analyze_page(TABLE_PAGE, IMAGE, engines)
    assert s.sources == {"layout": "fake-layout", "table": "fake-table"}
    table = s.blocks[s.tables[0]]
    assert (table.attrs["rows"], table.attrs["cols"], table.attrs["by"]) == (4, 2, "model")
    assert s.blocks["c1-2-1"].text == "Room Charges" and s.blocks["c1-2-2"].text == "Rs 1939"
    merged = s.blocks[table.attrs["merged"][0]]
    assert merged.type == "MERGED_CELL" and merged.attrs["col_span"] == 2
    assert merged.text == "Subtotal and taxes included"
    text = structure_text(s)
    assert "表格 1（4 行 × 2 列）" in text and "（跨 1 行 2 列）" in text and "〃" in text
    assert "版面区域：表格" in text


def test_table_without_the_model_is_split_by_word_positions():
    engines = StructureEngines(FakeLayout([TABLE_REGION]), None)
    s = analyze_page(TABLE_PAGE, IMAGE, engines)
    assert s.sources["table"] == "规则"
    table = s.blocks[s.tables[0]]
    assert (table.attrs["rows"], table.attrs["cols"], table.attrs["by"]) == (4, 2, "rule")
    assert s.blocks["c1-3-2"].text == "Rs 600"
    # Text inside tables is not treated as label → value (Breakfast → Rs 600 is a table row, not a
    # pair).
    assert "Breakfast" not in pairs(s)


def test_structure_round_trip():
    s = analyze_page(HOTEL)
    again = structure_from_json(json.loads(json.dumps(s.to_json())), HOTEL)
    assert again is not None
    assert again.words == s.words and again.keys == s.keys
    assert again.ocr_line(2) is HOTEL.lines[1]
    assert structure_from_json({"blocks": []}, HOTEL) is None


# ---- Block-based verification (LMDX) ----


def extract(answer, fields, tables=()):
    s = analyze_page(HOTEL)
    result = LlmExtractor(FakeBackend(json.dumps(answer))).extract(HOTEL, fields, tables, s)
    return {a.field.name: a for a in BlockAligner().align(HOTEL, result, s)}, s


def test_values_are_checked_in_the_cited_blocks():
    s = analyze_page(HOTEL)
    one, rooms_value = numbers(s, "1"), numbers(s, "Date: 31/12/2017")
    garden, street = numbers(s, "Sample Resort Garden,"), numbers(s, "12 Example Street")
    aligned, _ = extract(
        {
            "rooms": {"value": 1, "blocks": [one]},
            "date": {"value": "2017-12-31", "blocks": [rooms_value]},
            "address": {
                "value": "Sample Resort Garden, 12 Example Street",
                "blocks": [street, garden],
            },
        },
        (
            FieldSpec("rooms", "number", "房间数"),
            FieldSpec("date", "date", "日期"),
            FieldSpec("address", "string", "地址"),
        ),
    )
    assert aligned["rooms"].status == "matched" and not aligned["rooms"].needs_review
    assert aligned["rooms"].field.blocks == (one,)
    # The date is only the end of the block: the box covers just the date.
    date = aligned["date"].evidence
    assert date is not None and date.bbox[0] > 1000
    address = aligned["address"]
    assert address.status == "matched" and address.field.blocks == (garden, street)
    assert address.field.row_end is not None  # Spans two lines.


def test_wrong_blocks_but_found_elsewhere_is_yellow():
    s = analyze_page(HOTEL)
    aligned, _ = extract(
        {"guest": {"value": "Alex Lee", "blocks": [numbers(s, "SAMPLE HOTEL")]}},
        (FieldSpec("guest", "string", "住客"),),
    )
    guest = aligned["guest"]
    assert guest.status == "matched" and guest.needs_review
    assert "那几块里没有这个值；在别处找到了" in guest.note


def test_value_not_in_the_document_is_rejected():
    aligned, _ = extract(
        {"tax": {"value": 123.45, "blocks": [1]}, "nothing": {"value": None, "blocks": []}},
        (FieldSpec("tax", "number", "税额"), FieldSpec("nothing", "string", "x")),
    )
    tax = aligned["tax"]
    assert tax.status == "rejected" and tax.evidence is None and tax.needs_review
    assert "模型给了「123.45」" in render(tax.note) and "⟨1⟩" in render(tax.note)
    assert aligned["nothing"].status == "empty"


def test_table_cells_cite_blocks():
    s = analyze_page(TABLE_PAGE)
    items = TableSpec(
        "items",
        "明细",
        (FieldSpec("name", "string", "内容"), FieldSpec("amount", "number", "金额")),
    )
    answer = {
        "items": [
            {
                "name": {"value": "Room Charges", "blocks": [numbers(s, "Room Charges")]},
                "amount": {"value": 1939, "blocks": [numbers(s, "Rs 1939")]},
            }
        ]
    }
    result = LlmExtractor(FakeBackend(json.dumps(answer))).extract(TABLE_PAGE, (), (items,), s)
    aligned = {a.field.name: a for a in BlockAligner().align(TABLE_PAGE, result, s)}
    assert aligned["items.1.amount"].status == "matched"
    assert aligned["items.1.amount"].field.blocks == (numbers(s, "Rs 1939"),)


# ---- Storage and API ----


class PageOcr:
    name = "fake"

    def recognize(self, image):
        return HOTEL


def test_rejected_value_is_stored_and_rules_ignore_it(client, factory, storage):
    response = client.post("/api/v1/documents", files={"file": ("h.jpg", b"x", "image/jpeg")})
    document_id = uuid.UUID(response.json()["id"])
    s = analyze_page(HOTEL)
    answer = {
        "merchant": {"value": "SAMPLE HOTEL", "blocks": [1]},
        "store_name": {"value": None, "blocks": []},
        "document_no": {"value": None, "blocks": []},
        "date": {"value": "2017-12-31", "blocks": [numbers(s, "Date: 31/12/2017")]},
        "total": {"value": 9999, "blocks": [numbers(s, "Rs 2539")]},  # Invented.
        "items": [],
    }
    pipeline = Pipeline(ocr=PageOcr(), extractor=LlmExtractor(FakeBackend(json.dumps(answer))))
    assert (
        process_document(document_id, storage=storage, pipeline=pipeline, factory=factory) == "done"
    )

    detail = client.get(f"/api/v1/documents/{document_id}").json()
    values = {v["name"]: v for v in detail["extractions"][0]["values"]}
    total = values["total"]
    assert total["value"] is None and total["current_value"] is None
    assert total["rejected_value"] == "9999.0"
    assert total["status"] == "rejected" and total["evidence_state"] == "missing"
    assert values["merchant"]["evidence"]["blocks"] == [1]
    # Rules treat it as unfilled: rules such as total > 0 did not compare against 9999.
    issues = {i["rule"] for i in detail["extractions"][0]["current_checks"]["issues"]}
    assert "receipt_balance" not in issues

    # Accepted after the reviewer checked it.
    adopted = client.post(
        f"/api/v1/values/{total['id']}/revisions",
        json={"action": "edit", "value": "9999", "reason": "核对原图后采用模型给的值"},
    )
    assert adopted.status_code == 201
    with session_scope(factory) as session:
        row = session.get_one(ExtractedValue, uuid.UUID(total["id"]))
        assert row.rejected_value == "9999.0" and row.current_value == "9999.0"

    structure = client.get(f"/api/v1/documents/{document_id}/pages/1/structure").json()
    assert structure["available"] is True
    found = {p["key"]: p["value"] for p in structure["pairs"]}
    assert found["Rooms"] == "1" and found["Grand Total"] == "Rs 2539"
    assert client.get(f"/api/v1/documents/{document_id}/pages/9/structure").status_code == 404
