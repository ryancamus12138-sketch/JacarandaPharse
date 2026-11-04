"""视觉行合并：上下重叠够多的文字块算同一行，行内从左到右。"""

from jacara.plugins.ocr.layout import group_rows, numbered_text

from .conftest import make_line, make_page


def test_same_row_blocks_are_merged_left_to_right():
    page = make_page(
        make_line("25/12/2018", 163, 369, 345, 390),  # 故意把右边的放前面
        make_line("Date:", 48, 369, 100, 390),
    )
    rows = group_rows(page)
    assert len(rows) == 1
    assert rows[0].text == "Date:  25/12/2018"


def test_blocks_that_do_not_overlap_start_a_new_row():
    page = make_page(
        make_line("Date:", 48, 369, 100, 390),
        make_line("Cashier:", 48, 394, 120, 415),
    )
    assert [r.text for r in group_rows(page)] == ["Date:", "Cashier:"]


def test_small_overlap_is_not_enough():
    # 上下只重叠 2 像素，远小于高度的一半
    page = make_page(make_line("A", 0, 0, 50, 20), make_line("B", 60, 18, 100, 38))
    assert len(group_rows(page)) == 2


def test_rows_are_numbered_from_one(receipt_page):
    text = numbered_text(group_rows(receipt_page))
    assert text.startswith("[1] BOOK TA _K")
    assert "Date:  25/12/2018 8:13:39 PM" in text
