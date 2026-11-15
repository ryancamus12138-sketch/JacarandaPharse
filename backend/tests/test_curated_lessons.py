"""Curated examples: model errors only, duplicates merged, stale examples dropped, same issuer
preferred."""

from __future__ import annotations

from jacara.pipeline.learn import Lesson, curate, field_sig, note_for
from jacara.pipeline.series import series_in, series_key
from jacara.templates import load_template


def L(
    field, kind="chars", stage="extraction", series=None, doc="d1", sig=None, before="a", after="b"
):
    return Lesson(
        field, field, before, after, kind, series=series, stage=stage, sig=sig, document=doc
    )


def test_series_key_prefers_tax_id_then_name():
    assert series_key({"seller_tax_id": "91 3101-1234 56XY", "seller_name": "某公司"}) == (
        "tax:913101123456XY"
    )
    assert series_key({"merchant": "BOOK TA.K (TAMAN DAYA) SDN BHD"}) == (
        "name:booktaktamandayasdnbhd"
    )
    assert series_key({"merchant": "AB"}) is None  # Too short; ignored.
    # The buyer tax id is not the issuer.
    assert series_key({"buyer_tax_id": "913101123456XY"}) is None


def test_series_found_in_ocr_text():
    known = ["tax:913101123456XY", "name:booktaktamandayasdnbhd"]
    assert series_in("税号：9131 0112 3456 XY\n合计 9.00", known) == "tax:913101123456XY"
    assert series_in("BOOK TA .K (TAMAN DAYA) SDN BHD", known) == "name:booktaktamandayasdnbhd"
    assert series_in("别的商店", known) is None


def test_only_mistakes_of_the_model_are_used():
    lessons = [
        L("total", stage="ocr_misread"),
        L("date", stage="ocr_uncovered"),
        L("tax", stage="matching"),
        L("merchant", stage="extraction"),
        L("note", stage="unknown"),
    ]
    assert [x.field for x in curate(lessons)] == ["merchant", "note"]


def test_same_mistakes_are_merged_and_counted():
    lessons = [L("change", "decimal", before=str(n), after="1.00") for n in range(4)] + [
        L("merchant")
    ]
    chosen = curate(lessons)
    assert chosen[0].field == "change" and chosen[0].count == 4  # Most frequent first.
    assert chosen[0].before == "0"  # Keep the newest example.
    assert "同样的错出现过 4 次" in note_for(chosen)


def test_outdated_lessons_are_dropped():
    template = load_template("receipt")
    now = field_sig(template, "total")
    assert now is not None
    chosen = curate(
        [L("total", sig=now), L("total", kind="number", sig="number|旧的说明")], "", template
    )
    assert [x.kind for x in chosen] == ["chars"]


def test_series_with_three_documents_comes_first():
    shop = "name:booktaktamandayasdnbhd"
    ours = [L("change", "decimal", series=shop, doc=f"d{n}") for n in range(3)]
    others = [L("merchant", series="name:othershop", doc=f"o{n}") for n in range(6)]
    text = "BOOK TA .K (TAMAN DAYA) SDN BHD\nCHANGE 1.00"
    chosen = curate(others + ours, text)
    assert chosen[0].field == "change" and chosen[0].series == shop
    # Fewer than 3 documents: no separate series; ordered by count.
    few = curate(others + ours[:2], text)
    assert few[0].field == "merchant"


def test_pipeline_records_curated_prompt_version(receipt_page):
    from jacara.pipeline.run import Pipeline
    from jacara.plugins.extractors import LlmExtractor
    from tests.test_llm_extractor import FakeBackend, answer

    backend = FakeBackend(
        answer(
            merchant=("SHOP", 1),
            store_name=(None, None),
            document_no=(None, None),
            date=("2018-12-25", 4),
            total=(9, 6),
        )
    )
    extractor = LlmExtractor(backend)
    lessons = [L("change", "decimal", before="100", after="1.00"), L("total", stage="ocr_misread")]
    pipeline = Pipeline(ocr=None, extractor=extractor, lessons=lambda _t: lessons)  # type: ignore[arg-type]
    pipeline.learn_mode = "curated"
    report = pipeline.extract_page(receipt_page, "r.jpg", 1, "receipt")
    # The OCR misread was not given to the model.
    assert report.extraction.prompt_version.endswith("+curated1")
    assert extractor.lessons_mode == "recent"  # Restored after extraction.
