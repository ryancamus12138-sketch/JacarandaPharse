"""Hand-off between the two pipeline stages: the OCR stage stores its pages as JSON and the model
stage reads them back to continue.

Unlike the OCR result stored in the database (store.ocr_json), nothing is dropped here: four
corners, confidence, engine and models, PDF text layer and form fields are restored exactly as
recognized, so later steps produce the same results as a single-process run."""

from __future__ import annotations

import json
from typing import Any, cast

from jacara.core.i18n import stored
from jacara.plugins.ocr.base import FormValue, OcrLine, OcrPage, Point

VERSION = 1


def _line(line: OcrLine) -> dict[str, Any]:
    return {
        "text": line.text,
        "polygon": [[p.x, p.y] for p in line.polygon],
        "confidence": line.confidence,
    }


def _from_line(data: dict[str, Any]) -> OcrLine:
    points = tuple(Point(float(x), float(y)) for x, y in data["polygon"])
    return OcrLine(
        text=str(data["text"]),
        polygon=cast(tuple[Point, Point, Point, Point], points),
        confidence=float(data["confidence"]),
    )


def dump_pages(pages: list[OcrPage]) -> bytes:
    """Pages recognized by the OCR stage → JSON."""
    return json.dumps(
        {
            "version": VERSION,
            "pages": [
                {
                    "width": p.width,
                    "height": p.height,
                    "lines": [_line(ln) for ln in p.lines],
                    "engine": p.engine,
                    "models": p.models,
                    "duration_ms": p.duration_ms,
                    "text_layer": [_line(ln) for ln in p.text_layer],
                    "forms": [
                        {"name": f.name, "value": f.value, "box": list(f.box), "hidden": f.hidden}
                        for f in p.forms
                    ],
                }
                for p in pages
            ],
        },
        ensure_ascii=False,
    ).encode("utf-8")


def load_pages(data: bytes) -> list[OcrPage]:
    """Read back the pages recognized by the OCR stage in the model stage."""
    content = json.loads(data.decode("utf-8"))
    if content.get("version") != VERSION:
        raise ValueError(
            stored(
                "交接文件版本为 {found}，仅支持 {expected}",
                found=content.get("version"),
                expected=VERSION,
            )
        )
    return [
        OcrPage(
            width=int(p["width"]),
            height=int(p["height"]),
            lines=tuple(_from_line(ln) for ln in p["lines"]),
            engine=str(p["engine"]),
            models={str(k): str(v) for k, v in p["models"].items()},
            duration_ms=int(p["duration_ms"]),
            text_layer=tuple(_from_line(ln) for ln in p["text_layer"]),
            forms=tuple(
                FormValue(
                    name=str(f["name"]),
                    value=str(f["value"]),
                    box=cast(tuple[float, float, float, float], tuple(float(v) for v in f["box"])),
                    hidden=bool(f["hidden"]),
                )
                for f in p["forms"]
            ),
        )
        for p in content["pages"]
    ]
