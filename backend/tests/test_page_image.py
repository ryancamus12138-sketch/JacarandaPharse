"""Page images: the review page displays them and draws evidence boxes from evidence coordinates."""

import io

import pypdfium2 as pdfium
from PIL import Image


def make_png(width: int = 40, height: int = 20) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(out, format="PNG")
    return out.getvalue()


def make_pdf(pages: int) -> bytes:
    """A PDF of blank pages, 100 × 50 points each."""
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


def make_phone_photo() -> bytes:
    """Like a phone photo: pixels stored sideways (40 × 20) with an EXIF tag "rotate 90 degrees
    clockwise"."""
    image = Image.new("RGB", (40, 20), "white")
    exif = image.getexif()
    exif[0x0112] = 6  # Orientation = 6: rotate 90 degrees clockwise.
    out = io.BytesIO()
    image.save(out, format="JPEG", exif=exif)
    return out.getvalue()


def test_image_page_keeps_original_pixels(client):
    document_id = upload(client, "receipt.png", make_png(40, 20), "image/png")
    response = client.get(f"/api/v1/documents/{document_id}/pages/1/image")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert size_of(response.content) == (40, 20)  # Same coordinates as during OCR.


def test_phone_photo_is_turned_upright(client):
    document_id = upload(client, "photo.jpg", make_phone_photo(), "image/jpeg")
    response = client.get(f"/api/v1/documents/{document_id}/pages/1/image")
    assert size_of(response.content) == (20, 40)  # Rotated per EXIF, as RapidOCR sees it.


def test_ocr_size_matches_page_image(tmp_path):
    """Width and height recorded by OCR must equal those of the image on the review page."""
    from jacara.plugins.ocr.loader import page_png
    from jacara.plugins.ocr.rapid import exif_size

    path = tmp_path / "photo.jpg"
    path.write_bytes(make_phone_photo())
    assert exif_size(path) == size_of(page_png(path.read_bytes(), False, 1)) == (20, 40)


def test_pdf_page_uses_same_scale_as_ocr(client):
    document_id = upload(client, "invoice.pdf", make_pdf(2), "application/pdf")
    response = client.get(f"/api/v1/documents/{document_id}/pages/2/image")
    assert response.status_code == 200
    assert size_of(response.content) == (300, 150)  # 100 × 50 points times PDF_SCALE (3).


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
