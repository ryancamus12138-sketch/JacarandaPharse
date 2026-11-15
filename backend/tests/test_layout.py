"""Visual line merging: blocks with sufficient vertical overlap form one line, ordered left to
right."""

from jacara.plugins.ocr.layout import group_rows, numbered_text

from .conftest import make_line, make_page


def test_same_row_blocks_are_merged_left_to_right():
    page = make_page(
        make_line("25/12/2018", 163, 369, 345, 390),  # Right block deliberately first.
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
    # Only 2 pixels of vertical overlap, far less than half the height.
    page = make_page(make_line("A", 0, 0, 50, 20), make_line("B", 60, 18, 100, 38))
    assert len(group_rows(page)) == 2


def test_rows_are_numbered_from_one(receipt_page):
    text = numbered_text(group_rows(receipt_page))
    assert text.startswith("[1] BOOK TA _K")
    assert "Date:  25/12/2018 8:13:39 PM" in text


def tilted_line(text: str, x0: float, y0: float, width: float, slope: float, height: float = 20):
    """A skewed text block: top left at (x0, y0), descending slope pixels per pixel to the right."""
    from jacara.plugins.ocr.base import OcrLine, Point

    x1, y1 = x0 + width, y0 + slope * width
    polygon = (Point(x0, y0), Point(x1, y1), Point(x1, y1 + height), Point(x0, y0 + height))
    return OcrLine(text=text, polygon=polygon, confidence=0.99)


def test_tilted_photo_keeps_rows_apart():
    """A receipt photographed at an angle: bounding rectangles are much taller than the text, and
    rectangle overlap would glue several lines together.

        Comparing along the slope separates them. Line spacing 30 pixels, text height 20, slope
    0.15 (about 8.5 degrees)."""
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
    """Another sheet of paper lies askew in the photo: its slope differs greatly and it is not
    merged with the receipt's lines."""
    receipt = tilted_line("BRIOCHE BUNS", 50, 100, 200, 0.0)
    other = tilted_line("08-Nov-2026 05:50 pm", 300, 60, 200, 0.4)
    rows = group_rows(make_page(receipt, other))
    assert len(rows) == 2


def test_empty_page_has_no_rows():
    assert group_rows(make_page()) == ()
