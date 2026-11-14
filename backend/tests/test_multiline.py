"""Wrapped values: product names and addresses on two lines are joined into one value, evidence
boxes span both lines, and items are not split into two."""

import json

from jacara.plugins.aligners import TextMatchAligner
from jacara.plugins.extractors import FieldSpec, LlmExtractor, TableSpec
from jacara.plugins.ocr.layout import continuation_rows, group_rows, numbered_text
from jacara.plugins.ocr.loader import _phrases

from .conftest import make_line, make_page
from .test_llm_extractor import FakeBackend

# A hotel bill: the address below the shop name (left-aligned, close); a product name wrapped onto
# two lines in the line items.
HOTEL = make_page(
    make_line("Hotel Details", 50, 80, 200, 100),
    make_line("OYO 4189 Resort Nanganallur,", 50, 110, 330, 130),
    make_line("31/12/2017", 400, 110, 500, 130),
    make_line("25, Vembuliamman Koil Street, Chennai", 50, 134, 420, 154),
    make_line("Phone: 044 2222", 50, 180, 220, 200),
    make_line("Room Charges Deluxe", 50, 300, 260, 320),
    make_line("1", 300, 300, 312, 320),
    make_line("1939.00", 400, 300, 470, 320),
    make_line("Double with Breakfast", 50, 324, 270, 344),
    make_line("Total", 50, 400, 110, 420),
    make_line("1939.00", 400, 400, 470, 420),
)


def test_rows_that_continue_the_line_above():
    rows = group_rows(HOTEL)
    text = {r.index: r.text for r in rows}
    marks = continuation_rows(rows)
    marked = {text[i] for i in marks}
    # The address continues the shop name and "Double with Breakfast" continues the product name:
    # both continue by position.
    assert "25, Vembuliamman Koil Street, Chennai" in marked
    assert "Double with Breakfast" in marked
    # Lines after an empty line, starting with "label:", or starting with a number do not continue.
    assert not any(t.startswith("Phone") for t in marked)
    assert not any(t.startswith("Total") for t in marked)
    # Note: the shop name directly below "Hotel Details" is marked too; position cannot tell a value
    # under a header from a wrapped line. This is only a hint; the model decides whether it is the
    # same value.

    numbered = numbered_text(rows, marks)
    assert "↳ 25, Vembuliamman" in numbered
    assert "↳ Room Charges" not in numbered


def test_label_and_numbers_never_continue():
    page = make_page(
        make_line("Address line one", 50, 100, 250, 120),
        make_line("Tel: 123456", 50, 124, 200, 144),
        make_line("Item A", 50, 200, 120, 220),
        make_line("12.00", 50, 224, 110, 244),
    )
    assert continuation_rows(group_rows(page)) == frozenset()


ITEMS = TableSpec(
    "items",
    "商品明细",
    (
        FieldSpec("name", "string", "商品"),
        FieldSpec("quantity", "number", "数量"),
        FieldSpec("amount", "number", "金额"),
    ),
)


def _rows_of(page):
    return {r.text.split("  ")[0]: r.index for r in group_rows(page)}


def test_model_splits_a_wrapped_item_name_and_we_join_it():
    at = _rows_of(HOTEL)
    answer = {
        "total": {"value": 1939, "row": at["Total"], "row_end": None},
        "items": [
            {
                "row": at["Room Charges Deluxe"],
                "row_end": None,
                "name": "Room Charges Deluxe",
                "quantity": 1,
                "amount": 1939,
            },
            # The model still split the wrapped name into a separate item: name only, no numbers,
            # and positioned as a continuation of the previous line.
            {
                "row": at["Double with Breakfast"],
                "row_end": None,
                "name": "Double with Breakfast",
                "quantity": None,
                "amount": None,
            },
        ],
    }
    fields = (FieldSpec("total", "number", "总额"),)
    result = LlmExtractor(FakeBackend(json.dumps(answer))).extract(HOTEL, fields, (ITEMS,))
    cells = {f.name: f for f in result.fields}
    assert cells["items.1.name"].value == "Room Charges Deluxe Double with Breakfast"
    assert cells["items.1.name"].row_end == at["Double with Breakfast"]
    assert "items.2.name" not in cells  # No second item.

    # Evidence: the name spans two lines and the box covers both; no line-number mismatch note.
    aligned = {a.field.name: a for a in TextMatchAligner().align(HOTEL, result)}
    name = aligned["items.1.name"]
    assert name.status == "matched"
    assert name.evidence is not None
    assert {ln.text for ln in name.evidence.lines} == {
        "Room Charges Deluxe",
        "Double with Breakfast",
    }
    assert name.evidence.bbox[1] < 310 < 330 < name.evidence.bbox[3]
    assert "实际在第" not in name.note


def test_a_section_title_above_is_not_joined():
    page = make_page(
        make_line("Drinks", 50, 100, 120, 120),
        make_line("Coke", 50, 160, 110, 180),
        make_line("3.00", 400, 160, 450, 180),
    )
    at = _rows_of(page)
    answer = {
        "items": [
            {"row": at["Coke"], "row_end": None, "name": "Coke", "quantity": 1, "amount": 3},
            {
                "row": at["Drinks"],
                "row_end": None,
                "name": "Drinks",
                "quantity": None,
                "amount": None,
            },
        ]
    }
    result = LlmExtractor(FakeBackend(json.dumps(answer))).extract(page, (), (ITEMS,))
    names = [f.value for f in result.fields if f.name.endswith(".name")]
    assert names == ["Coke", "Drinks"]  # Not on the next line and not a continuation: not merged.


def test_value_across_two_rows_is_found_with_row_end():
    at = _rows_of(HOTEL)
    address = "OYO 4189 Resort Nanganallur, 25, Vembuliamman Koil Street, Chennai"
    answer = {
        "address": {
            "value": address,
            "row": at["OYO 4189 Resort Nanganallur,"],
            "row_end": at["25, Vembuliamman Koil Street, Chennai"],
        },
        "store": {
            "value": "OYO 4189 Resort Nanganallur,",
            "row": at["OYO 4189 Resort Nanganallur,"],
            "row_end": None,
        },
    }
    fields = (FieldSpec("address", "string", "地址"), FieldSpec("store", "string", "店名"))
    result = LlmExtractor(FakeBackend(json.dumps(answer))).extract(HOTEL, fields)
    by_name = {f.name: f for f in result.fields}
    assert by_name["address"].row_end == at["25, Vembuliamman Koil Street, Chennai"]
    assert by_name["store"].row_end is None
    aligned = {a.field.name: a for a in TextMatchAligner().align(HOTEL, result)}
    address_ev, store_ev = aligned["address"].evidence, aligned["store"].evidence
    assert aligned["address"].status == "matched"
    assert address_ev is not None and store_ev is not None
    assert len({ln.text for ln in address_ev.lines}) == 2
    # The shop name box covers only the name block, not the date to its right.
    assert [ln.text for ln in store_ev.lines] == ["OYO 4189 Resort Nanganallur,"]


def test_bad_row_end_is_ignored():
    answer = {"store": {"value": "X", "row": 3, "row_end": 2}}
    result = LlmExtractor(FakeBackend(json.dumps(answer))).extract(
        HOTEL, (FieldSpec("store", "string", "店名"),)
    )
    assert result.fields[0].row == 3
    assert result.fields[0].row_end is None


def test_prompt_explains_wrapped_values(receipt_page):
    backend = FakeBackend(json.dumps({"store": {"value": None, "row": None, "row_end": None}}))
    LlmExtractor(backend).extract(HOTEL, (FieldSpec("store", "string", "店名"),))
    system, user = backend.received[0].content, backend.received[1].content
    # extract-v6: answers by text block; values spanning blocks are joined.
    assert "blocks" in system and "跨好几块" in system
    assert "⟨5⟩25, Vembuliamman" in user or "25, Vembuliamman" in user


# ---- PDF text layer split into blocks by word gaps ----


def _word(text, x0, x1, top=100.0, bottom=110.0):
    return {"text": text, "x0": x0, "x1": x1, "top": top, "bottom": bottom}


def test_pdf_words_are_split_where_the_gap_is_wide():
    words = [
        _word("OYO", 10, 30),
        _word("4189", 32, 55),
        _word("Resort", 57, 90),
        _word("31/12/2017", 200, 250),  # Far apart: another column.
        _word("01/01/2018", 280, 330),
        _word("1", 400, 405),
        _word("Next", 10, 40, top=114, bottom=124),
    ]
    phrases = [p[0] for p in _phrases(words)]
    assert phrases == ["OYO 4189 Resort", "31/12/2017", "01/01/2018", "1", "Next"]


def test_pdf_label_and_value_are_split_after_a_colon():
    words = [_word("Guest", 10, 30), _word("Name:", 32, 55), _word("Sanjay", 57, 90)]
    assert [p[0] for p in _phrases(words)] == ["Guest Name:", "Sanjay"]


# ---- Evidence boxes cover only the value ----


def test_tiny_block_is_not_part_of_a_longer_value():
    """The shop name "OYO 4189 Resort" contains "1", but the room count "1" at the right end of the
    line must not be included in the name's box."""
    page = make_page(
        make_line("OYO 4189 Resort Nanganallur,", 50, 110, 330, 130),
        make_line("31/12/2017", 400, 110, 500, 130),
        make_line("1", 700, 110, 712, 130),
    )
    answer = {"store": {"value": "OYO 4189 Resort", "row": 1, "row_end": None}}
    result = LlmExtractor(FakeBackend(json.dumps(answer))).extract(
        page, (FieldSpec("store", "string", "店名"),)
    )
    evidence = TextMatchAligner().align(page, result)[0].evidence
    assert evidence is not None
    assert evidence.bbox[2] < 400  # Not stretched to the date and room count.
    # The value is the first part of the block: only that part is boxed.
    assert evidence.bbox[0] == 50 and evidence.bbox[2] < 330


def test_box_is_clipped_to_the_value_inside_a_block():
    page = make_page(
        make_line("Date: 31/12/2017", 0, 100, 160, 120),
        make_line("Guest Name: Sanjay", 0, 200, 180, 220),
    )
    answer = {
        "date": {"value": "2017-12-31", "row": 1, "row_end": None},
        "guest": {"value": "Sanjay", "row": 2, "row_end": None},
    }
    fields = (FieldSpec("date", "date", "日期"), FieldSpec("guest", "string", "住客"))
    result = LlmExtractor(FakeBackend(json.dumps(answer))).extract(page, fields)
    date, guest = (a.evidence for a in TextMatchAligner().align(page, result))
    assert date is not None and guest is not None
    # The date starts after the 6 characters of "Date: ": the box starts at 6/16.
    assert date.bbox[0] == 60 and date.bbox[2] == 160
    # The text is still the whole block; only the box shrinks.
    assert date.lines[0].text == "Date: 31/12/2017"
    assert guest.bbox[0] == 120


# ---- Column headers: headers above, values in columns below ----

OYO = make_page(
    make_line("Hotel Details", 50, 80, 200, 100),
    make_line("Check In", 500, 80, 590, 100),
    make_line("Check Out", 660, 80, 760, 100),
    make_line("Rooms", 820, 80, 880, 100),
    make_line("OYO 4189 Resort Nanganallur,", 50, 104, 330, 124),
    make_line("31/12/2017", 500, 104, 600, 124),
    make_line("01/01/2018", 660, 104, 760, 124),
    make_line("1", 820, 104, 832, 124),
    make_line("25, Vembuliamman Koil Street, Chennai", 50, 128, 420, 148),
    make_line("Booking ID", 500, 170, 600, 190),
    make_line("Payment Mode", 660, 170, 800, 190),
    make_line("IBZY2087", 500, 194, 590, 214),
    make_line("Cash at Hotel", 660, 194, 790, 214),
    make_line("Room Charges", 50, 300, 200, 320),
    make_line("Rs 1939 x 1 Night x 1 Room", 400, 300, 700, 320),
    make_line("Rs 1939", 820, 300, 890, 320),
)


def test_values_are_paired_with_the_title_above():
    from jacara.plugins.ocr.layout import header_groups, layout_hints

    groups = header_groups(group_rows(OYO))
    assert len(groups) == 2
    first = {c.label: c for c in groups[0].columns}
    assert first["Rooms"].value == "1"
    assert first["Check In"].value == "31/12/2017"
    # The address continuing the shop name also belongs to the Hotel Details column, spanning two
    # lines.
    assert first["Hotel Details"].value == (
        "OYO 4189 Resort Nanganallur, 25, Vembuliamman Koil Street, Chennai"
    )
    assert first["Hotel Details"].last_row == first["Hotel Details"].first_row + 1
    second = {c.label: c.value for c in groups[1].columns}
    assert second == {"Booking ID": "IBZY2087", "Payment Mode": "Cash at Hotel"}

    hints = layout_hints(group_rows(OYO))
    assert 'Rooms = "1"' in hints
    assert 'Hotel Details = "OYO 4189 Resort Nanganallur, 25, Vembuliamman' in hints


def test_hints_go_to_the_model():
    backend = FakeBackend(json.dumps({"nights": {"value": None, "row": None, "row_end": None}}))
    LlmExtractor(backend).extract(OYO, (FieldSpec("nights", "number", "晚数"),))
    user = backend.received[1].content
    # extract-v6: the mapping of column headers to values appears among the label → value pairs.
    assert "Rooms → ⟨" in user and "⟩1" in user
    assert "标题" in backend.received[0].content  # Usage explained in the rules.


def test_rows_with_numbers_are_not_titles():
    from jacara.plugins.ocr.layout import header_groups

    page = make_page(
        make_line("Date: 25/12/2018", 50, 80, 200, 100),
        make_line("Time: 8:13", 400, 80, 500, 100),
        make_line("Cashier A", 50, 104, 150, 124),
        make_line("Till 2", 400, 104, 460, 124),
    )
    assert header_groups(group_rows(page)) == ()
    # A plain line of text (a single block) is not a header either.
    single = make_page(
        make_line("Thank you", 50, 80, 200, 100), make_line("Come again", 50, 104, 200, 124)
    )
    assert header_groups(group_rows(single)) == ()
    backend = FakeBackend(json.dumps({"x": {"value": None, "row": None, "row_end": None}}))
    LlmExtractor(backend).extract(page, (FieldSpec("x", "string", "x"),))
    user = backend.received[1].content
    # Not headers above columns of values; but "Date: …" within one block is a label and value.
    assert "Date → 25/12/2018（同一块）" in user
