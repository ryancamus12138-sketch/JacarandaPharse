"""RapidOCR plugin: runs PaddleOCR's PP-OCR models locally with ONNX Runtime.

Approach: train and fine-tune with PaddleOCR, deploy with RapidOCR. A custom fine-tuned model
exported to .onnx is used by setting its path in OcrConfig; no code changes are needed here."""

from __future__ import annotations

import threading
import time
from importlib.metadata import version
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from jacara.core.i18n import stored
from jacara.plugins.ocr.base import OcrConfig, OcrLine, OcrPage, Point


class RapidOcrEngine:
    name = "rapidocr"

    def __init__(self, config: OcrConfig | None = None) -> None:
        self.config = config or OcrConfig()
        # Loading models takes a second or two, so they are loaded lazily on first use, and only
        # once.
        self._engine: Any = None
        self._lock = threading.Lock()
        """All requests of the web service share this engine (running in a thread pool). RapidOCR
        remembers the previous switches (whether detection is enabled), so two concurrent
        re-recognitions would interfere; only one caller may use it at a time."""

    def _params(self) -> dict[str, object]:
        """Translate the generic OcrConfig into RapidOCR parameter names."""
        c = self.config
        params: dict[str, object] = {
            "Global.log_level": "warning",
            "Global.use_cls": c.use_angle_cls,
            "Global.text_score": c.min_confidence,
            "Global.max_side_len": c.max_side,
            "EngineConfig.onnxruntime.use_cuda": c.device == "cuda",
            "EngineConfig.onnxruntime.use_coreml": c.device == "coreml",
        }
        if c.det_model:
            params["Det.model_path"] = str(c.det_model)
        if c.rec_model:
            params["Rec.model_path"] = str(c.rec_model)
        if c.rec_dict:
            params["Rec.rec_keys_path"] = str(c.rec_dict)
        if c.cls_model:
            params["Cls.model_path"] = str(c.cls_model)
        params.update(c.extra)
        return params

    def _models(self) -> dict[str, str]:
        """Record the models used: file names for custom models, otherwise the default models."""
        c = self.config
        return {
            "engine": f"rapidocr {version('rapidocr')}",
            "det": c.det_model.name if c.det_model else stored("PP-OCRv6_det_small（默认）"),
            "rec": c.rec_model.name if c.rec_model else stored("PP-OCRv6_rec_small（默认）"),
            "cls": (c.cls_model.name if c.cls_model else stored("默认"))
            if c.use_angle_cls
            else stored("关闭"),
            "device": c.device,
        }

    def _load(self) -> Any:
        """The caller must hold self._lock."""
        if self._engine is None:
            from rapidocr import RapidOCR

            self._engine = RapidOCR(params=self._params())
        return self._engine

    def recognize(self, image: Path) -> OcrPage:
        width, height = exif_size(image)

        started = time.perf_counter()
        with self._lock:
            # Set all three switches every time: RapidOCR remembers the last values passed
            # (read_line disables detection), so without this, full-page recognition would stop
            # detecting text after one read_line call.
            result = self._load()(
                str(image), use_det=True, use_cls=self.config.use_angle_cls, use_rec=True
            )
        duration_ms = round((time.perf_counter() - started) * 1000)

        lines: list[OcrLine] = []
        if result.boxes is not None:
            for box, text, score in zip(result.boxes, result.txts, result.scores, strict=True):
                p = [Point(float(x), float(y)) for x, y in box]
                lines.append(
                    OcrLine(text=text, polygon=(p[0], p[1], p[2], p[3]), confidence=float(score))
                )

        # Sort top to bottom, left to right as the reading order.
        lines.sort(key=lambda line: (round(line.bbox[1] / 10), line.bbox[0]))

        return OcrPage(
            width=width,
            height=height,
            lines=tuple(lines),
            engine=self.name,
            models=self._models(),
            duration_ms=duration_ms,
        )

    def read_line(self, image: Path) -> tuple[str, float] | None:
        """Recognize the whole image as a single line without locating text first (detection
        skipped).

                For small selected regions, detection sometimes finds no text at all (text
        touching the edge, or too small), while direct recognition succeeds. Returns None if
        nothing is recognized."""
        with self._lock:
            result = self._load()(str(image), use_det=False, use_cls=False, use_rec=True)
        texts = [t for t in (result.txts or ()) if t and t.strip()]
        if not texts:
            return None
        return " ".join(texts), float(min(result.scores or (0.0,)))

    def detect(self, image: Path) -> list[list[tuple[float, float]]]:
        """Locate text only (detection), without recognizing it: the four corners of each box. Used
        by OCR model evaluation."""
        with self._lock:
            result = self._load()(str(image), use_det=True, use_cls=False, use_rec=False)
        if result.boxes is None:
            return []
        return [[(float(x), float(y)) for x, y in box] for box in result.boxes]


def exif_size(image: Path) -> tuple[int, int]:
    """Width and height of the image after orientation correction.

        RapidOCR rotates images by EXIF orientation when reading them (phone photos are often
    stored sideways with a rotation tag), and recognized coordinates refer to the rotated image,
    so width and height must too."""
    with Image.open(image) as img:
        return ImageOps.exif_transpose(img).size
