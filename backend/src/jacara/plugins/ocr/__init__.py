"""OCR plugin registry: create the OCR engine named in the configuration.

To add an engine (for example the official PaddleOCR package):
1. Create paddle.py in this package with a class implementing OcrEngine;
2. Add "paddleocr": PaddleOcrEngine to _ENGINES below.
Callers only use create_ocr_engine(config) and never need the concrete class."""

from collections.abc import Callable

from jacara.core.i18n import join_items, stored
from jacara.plugins.ocr.base import (
    Device,
    FormValue,
    OcrConfig,
    OcrEngine,
    OcrLine,
    OcrPage,
    Point,
)
from jacara.plugins.ocr.rapid import RapidOcrEngine

_ENGINES: dict[str, Callable[[OcrConfig], OcrEngine]] = {
    "rapidocr": RapidOcrEngine,
}


def available_engines() -> list[str]:
    """Names of all registered OCR engines."""
    return sorted(_ENGINES)


def create_ocr_engine(config: OcrConfig | None = None) -> OcrEngine:
    """Create an OCR engine from the configuration. Raises for unregistered names, listing the
    available ones."""
    if config is None:
        from jacara.core.config import get_settings

        settings = get_settings()
        own = settings.ocr_models()  # Custom OCR models.
        config = OcrConfig(
            max_side=settings.jacara_ocr_max_side,
            det_model=own.get("det"),
            rec_model=own.get("rec"),
            rec_dict=own.get("dict"),
        )
    factory = _ENGINES.get(config.engine)
    if factory is None:
        raise ValueError(
            stored(
                "未知的 OCR 引擎：{engine}，可用引擎：{available}",
                engine=config.engine,
                available=join_items(available_engines()),
            )
        )
    return factory(config)


__all__ = [
    "Device",
    "FormValue",
    "OcrConfig",
    "OcrEngine",
    "OcrLine",
    "OcrPage",
    "Point",
    "RapidOcrEngine",
    "available_engines",
    "create_ocr_engine",
]
