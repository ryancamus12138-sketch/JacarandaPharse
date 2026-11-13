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


class SlowStickyEngine(StickyEngine):
    """调用中间停一下，让别的线程有机会插进来改开关。"""

    def __call__(self, image, **flags):
        import time

        self.flags.update({k: v for k, v in flags.items() if v is not None})
        time.sleep(0.005)
        seen = dict(self.flags)  # 停完再看开关：没加锁时可能已经被别的线程改了
        self.calls.append(seen)
        if not seen["use_det"]:
            return SimpleNamespace(txts=("9.99",), scores=(0.98,))
        return SimpleNamespace(boxes=None, txts=(), scores=())


def test_two_people_at_once_do_not_mix_up(tmp_path, monkeypatch):
    """网页服务里大家共用一个引擎：一个人"整块当一行识别"、另一个人整页识别，同时进行也不串。"""
    from concurrent.futures import ThreadPoolExecutor

    image = tmp_path / "a.png"
    Image.new("RGB", (40, 20), "white").save(image)
    fake = SlowStickyEngine()
    engine = RapidOcrEngine()
    monkeypatch.setattr(engine, "_load", lambda: fake)

    def work(k: int):
        return engine.read_line(image) if k % 2 else engine.recognize(image)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(work, range(40)))
    assert all(r == ("9.99", 0.98) for r in results[1::2])  # 整块识别都认出了字
    assert all(getattr(r, "lines", None) == () for r in results[0::2])  # 整页识别都没报错
