"""OCR extension point: defines the shape of OCR plugins and OCR results.

Only the contract is defined here; no recognition happens. Later pipeline steps (extraction,
evidence alignment, table reconstruction) depend only on these types, regardless of whether
RapidOCR, PaddleOCR or a future engine runs underneath.

Two ways to change the model:
1. Same engine, different model files (for example a fine-tuned handwriting recognition model):
   change OcrConfig only; no code changes.
2. Different engine (for example the official PaddleOCR package): implement OcrEngine in a new
   module in this package and register it in __init__.py. No other code changes."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

Device = Literal["cpu", "cuda", "coreml"]


@dataclass(frozen=True)
class OcrConfig:
    """Common OCR plugin configuration. None means the plugin's bundled default model."""

    engine: str = "rapidocr"
    """OCR plugin to use; the name must be registered in __init__.py."""

    det_model: Path | None = None
    """Text detection model: finds where text is in the image."""

    rec_model: Path | None = None
    """Text recognition model: reads the text inside each box. Fine-tuned handwriting models go
    here."""

    rec_dict: Path | None = None
    """Dictionary file of the recognition model. Usually required with a custom fine-tuned
    recognition model."""

    cls_model: Path | None = None
    """Orientation classification model: detects upside-down text."""

    use_angle_cls: bool = True
    """Whether to correct upside-down text. Recommended for scans and phone photos."""

    device: Device = "cpu"
    """Execution provider: cpu; cuda (NVIDIA GPUs); coreml (Apple silicon)."""

    min_confidence: float = 0.5
    """Recognition results with confidence below this are discarded."""

    max_side: int = 4000
    """Images whose longer side exceeds this many pixels are downscaled before full-page OCR.

        RapidOCR defaults to 2000: an A4 page at 216 DPI (2526 pixels tall) would first be shrunk
    to 2000, making small text harder to read. On a Mac, raising it to 4000 costs only 0.1–0.2
    seconds per page."""

    extra: dict[str, object] = field(default_factory=dict)
    """Engine-specific advanced parameters, passed to the engine unchanged. Reserved; does not
    affect other plugins."""


@dataclass(frozen=True)
class Point:
    """A point on the image, in pixels, origin at the top left."""

    x: float
    y: float


@dataclass(frozen=True)
class OcrLine:
    """A recognized text line.

        polygon holds the four corners (top left, top right, bottom right, bottom left). A
    quadrilateral rather than a rectangle, because photographed and scanned invoices are often
    skewed and the review page must draw precise highlights on the original image."""

    text: str
    polygon: tuple[Point, Point, Point, Point]
    confidence: float

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        """Bounding rectangle (x0, y0, x1, y1), for sorting and position comparisons."""
        xs = [p.x for p in self.polygon]
        ys = [p.y for p in self.polygon]
        return (min(xs), min(ys), max(xs), max(ys))


@dataclass(frozen=True)
class FormValue:
    """Value stored in a PDF form field. box is the widget's box (x0, y0, x1, y1) on the rendered
    image.

        The stored value is not necessarily drawn on the page: hidden means the field is set to
    hidden and invisible on the page."""

    name: str
    value: str
    box: tuple[float, float, float, float]
    hidden: bool = False


@dataclass(frozen=True)
class OcrPage:
    """Recognition result of one page image and its provenance.

        engine and models must be recorded so that every result can be traced to the engine and
    model files used."""

    width: int
    height: int
    lines: tuple[OcrLine, ...]
    engine: str
    models: dict[str, str]
    duration_ms: int
    text_layer: tuple[OcrLine, ...] = ()
    """Text embedded in the PDF: used only to cross-check OCR, never as the source text. Same
    coordinates as lines. Absent for images and scans; invisible text (usually an old OCR layer
    left by other software) is excluded."""
    forms: tuple[FormValue, ...] = ()
    """Values stored in PDF form fields."""

    @property
    def text(self) -> str:
        """Full text in reading order, one line per text line."""
        return "\n".join(line.text for line in self.lines)


class OcrEngine(Protocol):
    """OCR plugin interface: any class with a name attribute and a recognize method is an OCR
    plugin."""

    name: str

    def recognize(self, image: Path) -> OcrPage:
        """Recognize one image. Returns empty lines rather than raising when the image contains no
        text."""
        ...
