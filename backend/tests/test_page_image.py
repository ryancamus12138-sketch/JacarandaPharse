"""某一页的图片：审核页面用它显示原图，再按出处坐标画框。"""

import io

import pypdfium2 as pdfium
from PIL import Image


def make_png(width: int = 40, height: int = 20) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(out, format="PNG")
    return out.getvalue()


def make_pdf(pages: int) -> bytes:
    """几页空白的 PDF，每页 100 × 50 点。"""
    pdf = pdfium.PdfDocument.new()
    for _ in range(pages):
        pdf.new_page(100, 50)
    out = io.BytesIO()
    pdf.save(out)
    pdf.close()
    return out.getvalue()


def upload(client, name: str, data: bytes, content_type: str) -> str:
    response = client.post("/api/v1/documents", files={"file": (name, data, content_type)})
    assert response.status_code == 201
    return response.json()["id"]


def size_of(png: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(png)) as image:
        assert image.format == "PNG"
        return image.size


def test_image_page_keeps_original_pixels(client):
    document_id = upload(client, "receipt.png", make_png(40, 20), "image/png")
    response = client.get(f"/api/v1/documents/{document_id}/pages/1/image")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert size_of(response.content) == (40, 20)  # 和 OCR 时的坐标一致


def test_pdf_page_uses_same_scale_as_ocr(client):
    document_id = upload(client, "invoice.pdf", make_pdf(2), "application/pdf")
    response = client.get(f"/api/v1/documents/{document_id}/pages/2/image")
    assert response.status_code == 200
    assert size_of(response.content) == (300, 150)  # 100 × 50 点，乘 PDF_SCALE（3）


def test_page_out_of_range(client):
    document_id = upload(client, "invoice.pdf", make_pdf(2), "application/pdf")
    response = client.get(f"/api/v1/documents/{document_id}/pages/3/image")
    assert response.status_code == 404
    assert "只有 2 页" in response.json()["detail"]
    assert client.get(f"/api/v1/documents/{document_id}/pages/0/image").status_code == 422


def test_broken_file(client):
    document_id = upload(client, "broken.png", b"not really a png", "image/png")
    response = client.get(f"/api/v1/documents/{document_id}/pages/1/image")
    assert response.status_code == 422
    assert "打不开" in response.json()["detail"]
