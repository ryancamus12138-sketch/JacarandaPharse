"""手写最小的 PDF，给测试用：能控制文字层（字体对照表、看不见的字）、表单字段、页面旋转。

用标准字体 Helvetica（每个 PDF 阅读器都有，不用嵌入），只写 ASCII 字符。
页面坐标按 PDF 的习惯：单位是点，原点在左下角。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Run:
    """一段文字：x、y 是基线左端，size 是字号。"""

    x: float
    y: float
    text: str
    size: float = 14
    invisible: bool = False
    """文字渲染模式 3：有文字层、画面上看不见（扫描件上旧 OCR 留下的字就是这样）。"""
    font: str = "F1"
    """F1：普通 Helvetica；F2：带"错误对照表"的 Helvetica（见 Page.wrong_map）。"""


@dataclass
class Field:
    """一个文本类表单字段。rect 是 (x0, y0, x1, y1)。"""

    name: str
    value: str
    rect: tuple[float, float, float, float]
    appearance: bool = True
    """有没有外观（/AP）：没有外观的值，有的阅读器不显示。"""
    hidden: bool = False
    """隐藏标志（/F 第 2 位）。"""


@dataclass
class Page:
    runs: list[Run] = field(default_factory=list)
    fields: list[Field] = field(default_factory=list)
    width: float = 300
    height: float = 200
    rotate: int = 0
    wrong_map: dict[str, str] = field(default_factory=dict)
    """F2 字体的对照表：画的是 key 这个字形，复制出来是 value（例如 {"0": "8"}）。"""


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _content(runs: list[Run]) -> bytes:
    parts = []
    for r in runs:
        mode = 3 if r.invisible else 0
        parts.append(f"BT /{r.font} {r.size} Tf {mode} Tr {r.x} {r.y} Td ({_escape(r.text)}) Tj ET")
    return "\n".join(parts).encode()


def _cmap(mapping: dict[str, str]) -> bytes:
    """ToUnicode：每个 ASCII 字形对到它自己，mapping 里的改成别的字。"""
    lines = []
    for code in range(32, 127):
        ch = mapping.get(chr(code), chr(code))
        hexes = "".join(f"{b:02X}" for b in ch.encode("utf-16-be"))
        lines.append(f"<{code:02X}> <{hexes}>")
    body = "\n".join(lines)
    return (
        "/CIDInit /ProcSet findresource begin 12 dict begin begincmap\n"
        "/CMapName /Wrong def /CMapType 2 def\n"
        "1 begincodespacerange <00> <FF> endcodespacerange\n"
        f"{len(lines)} beginbfchar\n{body}\nendbfchar\n"
        "endcmap CMapName currentdict /CMap defineresource pop end end"
    ).encode()


def make_pdf(*pages: Page) -> bytes:
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    def stream(data: bytes, extra: str = "") -> bytes:
        return f"<< /Length {len(data)} {extra}>>\nstream\n".encode() + data + b"\nendstream"

    catalog = add(b"")  # 先占位，最后填
    tree = add(b"")
    helv = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    kids: list[int] = []
    all_fields: list[int] = []
    for page in pages:
        fonts = f"/F1 {helv} 0 R"
        if page.wrong_map:
            cmap = add(stream(_cmap(page.wrong_map)))
            wrong = add(
                f"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
                f"/Encoding /WinAnsiEncoding /ToUnicode {cmap} 0 R >>".encode()
            )
            fonts += f" /F2 {wrong} 0 R"
        content = add(stream(_content(page.runs)))
        page_no = len(objects) + 1
        widgets: list[int] = []
        for f in page.fields:
            x0, y0, x1, y1 = f.rect
            ap = ""
            if f.appearance:
                draw = f"/Tx BMC BT /F1 10 Tf 2 4 Td ({_escape(f.value)}) Tj ET EMC".encode()
                form = add(
                    stream(
                        draw,
                        f"/Type /XObject /Subtype /Form /BBox [0 0 {x1 - x0} {y1 - y0}] "
                        f"/Resources << /Font << /F1 {helv} 0 R >> >> ",
                    )
                )
                ap = f"/AP << /N {form} 0 R >>"
            flags = 4 | (2 if f.hidden else 0)  # 4 = 打印
            widget = add(
                f"<< /Type /Annot /Subtype /Widget /FT /Tx /T ({_escape(f.name)}) "
                f"/V ({_escape(f.value)}) /Rect [{x0} {y0} {x1} {y1}] /F {flags} "
                f"/P {page_no + len(widgets) + 1 if False else 0} 0 R "
                f"/DA (/F1 10 Tf 0 g) {ap} >>".encode()
            )
            widgets.append(widget)
        annots = f"/Annots [{' '.join(f'{w} 0 R' for w in widgets)}]" if widgets else ""
        page_obj = add(
            f"<< /Type /Page /Parent {tree} 0 R /MediaBox [0 0 {page.width} {page.height}] "
            f"/Rotate {page.rotate} /Resources << /Font << {fonts} >> >> "
            f"/Contents {content} 0 R {annots} >>".encode()
        )
        for w in widgets:  # 控件记一下自己在哪一页
            objects[w - 1] = objects[w - 1].replace(b"/P 0 0 R", f"/P {page_obj} 0 R".encode())
        kids.append(page_obj)
        all_fields += widgets
    objects[tree - 1] = (
        f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>"
    ).encode()
    acro = (
        f"/AcroForm << /Fields [{' '.join(f'{w} 0 R' for w in all_fields)}] "
        f"/DR << /Font << /F1 {helv} 0 R >> >> /DA (/F1 10 Tf 0 g) >>"
        if all_fields
        else ""
    )
    objects[catalog - 1] = f"<< /Type /Catalog /Pages {tree} 0 R {acro} >>".encode()

    out = bytearray(b"%PDF-1.7\n")
    offsets = []
    for n, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root {catalog} 0 R >>\n".encode()
    out += f"startxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)
