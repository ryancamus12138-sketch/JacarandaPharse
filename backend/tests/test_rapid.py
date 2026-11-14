"""RapidOCR plugin: a fake engine instead of real models, to inspect the parameters passed."""

from types import SimpleNamespace

from PIL import Image

from jacara.plugins.ocr.rapid import RapidOcrEngine


class StickyEngine:
    """Remembers the previous switches like RapidOCR: omitted parameters keep their previous
    values."""

    def __init__(self) -> None:
        self.flags = {"use_det": True, "use_cls": True, "use_rec": True}
        self.calls: list[dict[str, bool]] = []

    def __call__(self, image, **flags):
        self.flags.update({k: v for k, v in flags.items() if v is not None})
        self.calls.append(dict(self.flags))
        if not self.flags["use_det"]:
            # No boxes, like the real engine.
            return SimpleNamespace(txts=("9.99",), scores=(0.98,))
        return SimpleNamespace(boxes=None, txts=(), scores=())


def test_full_page_still_detects_after_reading_one_line(tmp_path, monkeypatch):
    """After recognizing a region as one line (detection off), full-page recognition must re-enable
    detection; otherwise it fails."""
    image = tmp_path / "a.png"
    Image.new("RGB", (40, 20), "white").save(image)
    fake = StickyEngine()
    engine = RapidOcrEngine()
    monkeypatch.setattr(engine, "_load", lambda: fake)

    assert engine.read_line(image) == ("9.99", 0.98)
    # Previously failed with 'TextRecOutput' object has no attribute 'boxes'.
    page = engine.recognize(image)
    assert page.lines == ()
    assert fake.calls[-1]["use_det"] is True


class SlowStickyEngine(StickyEngine):
    """Pause mid-call to let another thread change the switches."""

    def __call__(self, image, **flags):
        import time

        self.flags.update({k: v for k, v in flags.items() if v is not None})
        time.sleep(0.005)
        # Check the switches after the pause: without the lock another thread may have changed them.
        seen = dict(self.flags)
        self.calls.append(seen)
        if not seen["use_det"]:
            return SimpleNamespace(txts=("9.99",), scores=(0.98,))
        return SimpleNamespace(boxes=None, txts=(), scores=())


def test_two_people_at_once_do_not_mix_up(tmp_path, monkeypatch):
    """All web requests share one engine: concurrent single-line and full-page recognition must not
    interfere."""
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
    # Every single-line recognition returned text.
    assert all(r == ("9.99", 0.98) for r in results[1::2])
    # No full-page recognition failed.
    assert all(getattr(r, "lines", None) == () for r in results[0::2])
