"""折行的值（ADR 0033）：商品名、地址写成两行时，合成一个值、出处框跨两行、商品不拆成两行。"""

import json

from jacara.plugins.aligners import TextMatchAligner
from jacara.plugins.extractors import FieldSpec, LlmExtractor, TableSpec
from jacara.plugins.ocr.layout import continuation_rows, group_rows, numbered_text
from jacara.plugins.ocr.loader import _phrases

from .conftest import make_line, make_page
from .test_llm_extractor import FakeBackend

# 一张酒店账单：店名下面一行是地址（左对齐、挨得近）；明细里商品名折成两行
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
    # 地址接着店名、"Double with Breakfast" 接着商品名：位置上都是"接着写的"
    assert "25, Vembuliamman Koil Street, Chennai" in marked
    assert "Double with Breakfast" in marked
    # 空了一行的、"名称："开头的、数字开头的都不算
    assert not any(t.startswith("Phone") for t in marked)
    assert not any(t.startswith("Total") for t in marked)
    # 注意："Hotel Details" 下面紧挨着的店名也会标上：位置上分不出"标题下面的值"和"折行"，
    # 这只是给模型的线索，是不是同一个值由模型判断

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
            # 模型还是把折行拆成了一行"商品"：只有名字、没有数字，而且位置上接着上一行
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
    assert "items.2.name" not in cells  # 没有第二行商品

    # 出处：名字跨两行，框把两行都框进去；没有"行号对不上"的提示
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
    assert names == ["Coke", "Drinks"]  # 不在下一行、也不是接着写的：不合并


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
    # 店名只框店名那一块，不框同一行右边的日期
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
    # extract-v6：按文字块回答，值跨几块时连起来写（ADR 0034）
    assert "blocks" in system and "跨好几块" in system
    assert "⟨5⟩25, Vembuliamman" in user or "25, Vembuliamman" in user


# ---- PDF 文字层按词之间的空隙分块 ----


def _word(text, x0, x1, top=100.0, bottom=110.0):
    return {"text": text, "x0": x0, "x1": x1, "top": top, "bottom": bottom}


def test_pdf_words_are_split_where_the_gap_is_wide():
    words = [
        _word("OYO", 10, 30),
        _word("4189", 32, 55),
        _word("Resort", 57, 90),
        _word("31/12/2017", 200, 250),  # 隔了很远：另一列
        _word("01/01/2018", 280, 330),
        _word("1", 400, 405),
        _word("Next", 10, 40, top=114, bottom=124),
    ]
    phrases = [p[0] for p in _phrases(words)]
    assert phrases == ["OYO 4189 Resort", "31/12/2017", "01/01/2018", "1", "Next"]


def test_pdf_label_and_value_are_split_after_a_colon():
    words = [_word("Guest", 10, 30), _word("Name:", 32, 55), _word("Sanjay", 57, 90)]
    assert [p[0] for p in _phrases(words)] == ["Guest Name:", "Sanjay"]


# ---- 出处框只框住值 ----


def test_tiny_block_is_not_part_of_a_longer_value():
    """店名 "OYO 4189 Resort" 里有 "1"，但同一行最右边的房间数 "1" 不能算进店名的框。"""
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
    assert evidence.bbox[2] < 400  # 没有拉到日期和房间数那边
    # 值只是这一块的前一部分：框只框前面那段
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
    # "Date: " 6 个字之后才是日期：框从 6/16 处开始
    assert date.bbox[0] == 60 and date.bbox[2] == 160
    assert date.lines[0].text == "Date: 31/12/2017"  # 文字还是整块原文，只是框变小
    assert guest.bbox[0] == 120


# ---- 列标题：上面一行是标题、下面按列写值 ----

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
    # 店名下面接着写的地址也归到 Hotel Details 这一栏，跨两行
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
    # extract-v6：列标题和值的对应写在"标题 → 值"里（ADR 0034）
    assert "Rooms → ⟨" in user and "⟩1" in user
    assert "标题" in backend.received[0].content  # 规则里说明了怎么用


def test_rows_with_numbers_are_not_titles():
    from jacara.plugins.ocr.layout import header_groups

    page = make_page(
        make_line("Date: 25/12/2018", 50, 80, 200, 100),
        make_line("Time: 8:13", 400, 80, 500, 100),
        make_line("Cashier A", 50, 104, 150, 124),
        make_line("Till 2", 400, 104, 460, 124),
    )
    assert header_groups(group_rows(page)) == ()
    # 一行普通文字（只有一块）也不算标题
    single = make_page(
        make_line("Thank you", 50, 80, 200, 100), make_line("Come again", 50, 104, 200, 124)
    )
    assert header_groups(group_rows(single)) == ()
    backend = FakeBackend(json.dumps({"x": {"value": None, "row": None, "row_end": None}}))
    LlmExtractor(backend).extract(page, (FieldSpec("x", "string", "x"),))
    user = backend.received[1].content
    # 不是"上面标题、下面按列写值"；但同一块里的"Date: …"是一对标题和值
    assert "Date → 25/12/2018（同一块）" in user
