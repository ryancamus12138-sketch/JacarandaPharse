"""RapidOCR 插件：用 ONNX Runtime 在本机运行 PaddleOCR 的 PP-OCR 模型。

方案：用 PaddleOCR 训练和微调，用 RapidOCR 部署（见 docs/adr/0003）。
自己微调的模型导出成 .onnx 后，在 OcrConfig 里填上路径即可使用，不用改这里的代码。
"""

from __future__ import annotations

import time
from importlib.metadata import version
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from jacara.plugins.ocr.base import OcrConfig, OcrLine, OcrPage, Point


class RapidOcrEngine:
    name = "rapidocr"

    def __init__(self, config: OcrConfig | None = None) -> None:
        self.config = config or OcrConfig()
        # 模型加载要一两秒，所以第一次用到时才加载，并且只加载一次。
        self._engine: Any = None

    def _params(self) -> dict[str, object]:
        """把通用的 OcrConfig 翻译成 RapidOCR 自己的参数名。"""
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
        """记录这次用了哪些模型：自定义模型记文件名，否则记默认模型。"""
        c = self.config
        return {
            "engine": f"rapidocr {version('rapidocr')}",
            "det": c.det_model.name if c.det_model else "PP-OCRv6_det_small（默认）",
            "rec": c.rec_model.name if c.rec_model else "PP-OCRv6_rec_small（默认）",
            "cls": (c.cls_model.name if c.cls_model else "默认") if c.use_angle_cls else "关闭",
            "device": c.device,
        }

    def _load(self) -> Any:
        if self._engine is None:
            from rapidocr import RapidOCR

            self._engine = RapidOCR(params=self._params())
        return self._engine

    def recognize(self, image: Path) -> OcrPage:
        engine = self._load()
        width, height = exif_size(image)

        started = time.perf_counter()
        # 三个开关每次都写明：RapidOCR 会记住上一次传的值（read_line 关掉了检测），
        # 不写的话，用过一次 read_line 之后，整页识别也不找字了
        result = engine(str(image), use_det=True, use_cls=self.config.use_angle_cls, use_rec=True)
        duration_ms = round((time.perf_counter() - started) * 1000)

        lines: list[OcrLine] = []
        if result.boxes is not None:
            for box, text, score in zip(result.boxes, result.txts, result.scores, strict=True):
                p = [Point(float(x), float(y)) for x, y in box]
                lines.append(
                    OcrLine(text=text, polygon=(p[0], p[1], p[2], p[3]), confidence=float(score))
                )

        # 按从上到下、从左到右排序，作为阅读顺序。
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
        """整张图当成一行字直接识别，不先找文字在哪（跳过"检测"）。

        框选的小块图片，"检测"那一步有时一个字都找不到（字贴着边、太小），
        直接识别反而能认出来。认不出返回 None。
        """
        result = self._load()(str(image), use_det=False, use_cls=False, use_rec=True)
        texts = [t for t in (result.txts or ()) if t and t.strip()]
        if not texts:
            return None
        return " ".join(texts), float(min(result.scores or (0.0,)))


def exif_size(image: Path) -> tuple[int, int]:
    """图片转正之后的宽高。

    RapidOCR 读图时会按 EXIF 方向转正（手机照片常常横着存、另记一个旋转标记），
    识别出的坐标是转正之后的，所以宽高也要按转正之后的算。
    """
    with Image.open(image) as img:
        return ImageOps.exif_transpose(img).size
