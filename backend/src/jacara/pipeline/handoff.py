"""流水线的两个工位之间交接（ADR 0051）：OCR 工位认好的字存成 JSON，模型工位读回来接着做。

和数据库里存的 OCR 结果（store.ocr_json）不同，这里一样不丢：四个角、置信度、引擎和模型、
PDF 文字层、表单字段，读回来和 OCR 刚认出来的一模一样，后面的步骤结果才和一个进程做完时相同。
"""

from __future__ import annotations

import json
from typing import Any, cast

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
    """OCR 工位认好的每一页 → JSON。"""
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
    """模型工位读回 OCR 工位认好的每一页。"""
    content = json.loads(data.decode("utf-8"))
    if content.get("version") != VERSION:
        raise ValueError(f"交接文件的版本是 {content.get('version')}，这里只认 {VERSION}")
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
