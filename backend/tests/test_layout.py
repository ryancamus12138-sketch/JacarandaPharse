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


def tilted_line(text: str, x0: float, y0: float, width: float, slope: float, height: float = 20):
    """一块歪着的文字：左上角在 (x0, y0)，每向右 1 像素往下 slope 像素。"""
    from jacara.plugins.ocr.base import OcrLine, Point

    x1, y1 = x0 + width, y0 + slope * width
    polygon = (Point(x0, y0), Point(x1, y1), Point(x1, y1 + height), Point(x0, y0 + height))
    return OcrLine(text=text, polygon=polygon, confidence=0.99)


def test_tilted_photo_keeps_rows_apart():
    """歪着拍的收据：每块文字的外接矩形都比字高很多，按矩形重叠会把好几行粘成一行。

    按倾斜方向延长过去比较，才能分开。行距 30 像素、字高 20、斜度 0.15（约 8.5 度）。
    """
    slope = 0.15
    lines = []
    for i, (name, price) in enumerate([("SUNGOLD KIWI", "17.99"), ("GOURMET MIX", "9.99")]):
        y = 100 + i * 60
        lines.append(tilted_line(name, 50, y, 200, slope))
        lines.append(tilted_line(price, 400, y + slope * 350, 60, slope))
        lines.append(tilted_line(f"1x {price}", 50, y + 30, 200, slope))
    rows = group_rows(make_page(*lines))
    assert [r.text for r in rows] == [
        "SUNGOLD KIWI  17.99",
        "1x 17.99",
        "GOURMET MIX  9.99",
        "1x 9.99",
    ]


def test_another_piece_of_paper_at_an_angle_is_not_merged():
    """照片里旁边还有一张斜着放的纸：斜度差很多，不和收据的行合并。"""
    receipt = tilted_line("BRIOCHE BUNS", 50, 100, 200, 0.0)
    other = tilted_line("08-Nov-2026 05:50 pm", 300, 60, 200, 0.4)
    rows = group_rows(make_page(receipt, other))
    assert len(rows) == 2


def test_empty_page_has_no_rows():
    assert group_rows(make_page()) == ()
