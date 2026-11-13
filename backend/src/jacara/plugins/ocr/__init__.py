"""OCR 插件登记表：按配置里的名字创建对应的 OCR 引擎。

新增一个引擎（例如 PaddleOCR 官方包）：
1. 在本目录新建 paddle.py，写一个实现 OcrEngine 接口的类；
2. 在下面的 _ENGINES 里加一行 "paddleocr": PaddleOcrEngine。
调用方只写 create_ocr_engine(config)，永远不需要知道具体是哪个类。
"""

from collections.abc import Callable

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
    """列出所有登记过的 OCR 引擎名字（以后模型管理页面会用到）。"""
    return sorted(_ENGINES)


def create_ocr_engine(config: OcrConfig | None = None) -> OcrEngine:
    """按配置创建 OCR 引擎。名字没登记过就报错，并列出可用的名字。"""
    if config is None:
        from jacara.core.config import get_settings

        settings = get_settings()
        own = settings.ocr_models()  # 自己的 OCR 模型（ADR 0046）
        config = OcrConfig(
            max_side=settings.jacara_ocr_max_side,
            det_model=own.get("det"),
            rec_model=own.get("rec"),
            rec_dict=own.get("dict"),
        )
    factory = _ENGINES.get(config.engine)
    if factory is None:
        raise ValueError(f"未知的 OCR 引擎：{config.engine}，可用的有：{available_engines()}")
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
