"""RapidOCR 插件：不加载真的模型，用假的引擎看传了什么参数。"""

from types import SimpleNamespace

from PIL import Image

from jacara.plugins.ocr.rapid import RapidOcrEngine


class StickyEngine:
    """和 RapidOCR 一样"记住上一次的开关"：没传的参数沿用上一次的值。"""

    def __init__(self) -> None:
        self.flags = {"use_det": True, "use_cls": True, "use_rec": True}
        self.calls: list[dict[str, bool]] = []

    def __call__(self, image, **flags):
        self.flags.update({k: v for k, v in flags.items() if v is not None})
        self.calls.append(dict(self.flags))
        if not self.flags["use_det"]:
            return SimpleNamespace(txts=("9.99",), scores=(0.98,))  # 没有 boxes，和真的一样
        return SimpleNamespace(boxes=None, txts=(), scores=())


def test_full_page_still_detects_after_reading_one_line(tmp_path, monkeypatch):
    """用过一次"整块当一行识别"（关掉检测）之后，整页识别必须重新打开检测，不然会报错。"""
    image = tmp_path / "a.png"
    Image.new("RGB", (40, 20), "white").save(image)
    fake = StickyEngine()
    engine = RapidOcrEngine()
    monkeypatch.setattr(engine, "_load", lambda: fake)

    assert engine.read_line(image) == ("9.99", 0.98)
    page = engine.recognize(image)  # 以前这里报 'TextRecOutput' object has no attribute 'boxes'
    assert page.lines == ()
    assert fake.calls[-1]["use_det"] is True
