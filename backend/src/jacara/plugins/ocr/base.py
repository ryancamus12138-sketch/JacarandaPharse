"""OCR 扩展点：规定"OCR 插件长什么样"和"OCR 结果长什么样"。

这里只定义契约，不做任何识别。流水线后面的步骤（抽取、证据对齐、表格还原）只认这些类型，
不关心底层是 RapidOCR、PaddleOCR 还是以后的别的引擎。

换模型有两种情况：
1. 同一个引擎，换模型文件（例如自己微调的手写体识别模型）：只改 OcrConfig，不改代码。
2. 换一个引擎（例如 PaddleOCR 官方包）：在本目录新建一个文件实现 OcrEngine，
   再在 __init__.py 里登记一行。其他代码都不用改。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

Device = Literal["cpu", "cuda", "coreml"]


@dataclass(frozen=True)
class OcrConfig:
    """OCR 插件的通用配置。字段为 None 表示用插件自带的默认模型。"""

    engine: str = "rapidocr"
    """用哪个 OCR 插件，名字要在 __init__.py 的登记表里。"""

    det_model: Path | None = None
    """文字检测模型：找出图上哪里有字。"""

    rec_model: Path | None = None
    """文字识别模型：认出框里写的是什么。微调后的手写体模型放这里。"""

    rec_dict: Path | None = None
    """识别模型的字典文件。自己微调的识别模型通常要配套提供。"""

    cls_model: Path | None = None
    """方向分类模型：判断文字是不是倒过来的。"""

    use_angle_cls: bool = True
    """是否纠正倒置的文字。扫描件、手机拍照建议打开。"""

    device: Device = "cpu"
    """在哪里运行：cpu；cuda（NVIDIA 显卡）；coreml（苹果芯片）。"""

    min_confidence: float = 0.5
    """置信度低于这个值的识别结果直接丢掉。"""

    extra: dict[str, object] = field(default_factory=dict)
    """各引擎特有的高级参数，原样传给引擎。用来预留，不影响其他插件。"""


@dataclass(frozen=True)
class Point:
    """图片上的一个点，单位是像素，原点在左上角。"""

    x: float
    y: float


@dataclass(frozen=True)
class OcrLine:
    """识别出的一行文字。

    polygon 是四个角的坐标（左上、右上、右下、左下）。用四边形而不是矩形，
    是因为拍照和扫描的发票常常是歪的；审核页面要在原图上画出准确的高亮框。
    """

    text: str
    polygon: tuple[Point, Point, Point, Point]
    confidence: float

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        """外接矩形 (x0, y0, x1, y1)，排序和比较位置时用。"""
        xs = [p.x for p in self.polygon]
        ys = [p.y for p in self.polygon]
        return (min(xs), min(ys), max(xs), max(ys))


@dataclass(frozen=True)
class OcrPage:
    """一页图片的识别结果，以及"它是怎么来的"。

    engine 和 models 必须记录：项目规则要求每次结果都能追溯到用的是哪个引擎、哪个模型文件。
    """

    width: int
    height: int
    lines: tuple[OcrLine, ...]
    engine: str
    models: dict[str, str]
    duration_ms: int

    @property
    def text(self) -> str:
        """按阅读顺序拼成的全文，每行一个换行。"""
        return "\n".join(line.text for line in self.lines)


class OcrEngine(Protocol):
    """OCR 插件接口：任何类只要有 name 属性和 recognize 方法，就是一个 OCR 插件。"""

    name: str

    def recognize(self, image: Path) -> OcrPage:
        """识别一张图片。图片里没有文字时返回空的 lines，而不是报错。"""
        ...
