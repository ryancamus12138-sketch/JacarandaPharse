"""Error attribution: conclusions only with sufficient evidence."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

from jacara.modules.insights.attribution import attribute


def _value(
    type_: str, model: str | None, truth: str, *, rejected: str | None = None, evidence=None
):
    revision = SimpleNamespace(id=uuid.uuid4(), action="edit", evidence=evidence)
    return SimpleNamespace(
        type=type_,
        value=model,
        rejected_value=rejected,
        status="rejected" if rejected else "matched",
        current_value=truth,
        revisions=[revision],
    )


def _extraction(text: str | None, lines: list[dict[str, Any]] | None = None):
    sent = {"text": text, "blocks": []} if text is not None else None
    return SimpleNamespace(model_input=sent, ocr={"lines": lines or []}, page=1)


SENT = "⟨1⟩BOOK TA.K\n⟨2⟩TOTAL      ⟨3⟩9.00\n⟨4⟩25/12/2018"


def judge(value, extraction, samples=(), template=None):
    return attribute(value, extraction, samples, template)  # type: ignore[arg-type]


def test_value_in_sent_text_is_an_extraction_error():
    v = judge(_value("number", "8.0", "9.0"), _extraction(SENT))
    assert (v.stage, v.reason) == ("extraction", "wrong_in_text")
    assert v.evidence[0]["blocks"] == [2, 3]
    missed = judge(_value("number", None, "9.0"), _extraction(SENT))
    assert missed.reason == "missed_in_text"


def test_model_was_right_but_rejected():
    v = judge(_value("number", None, "9.0", rejected="9"), _extraction(SENT))
    assert (v.stage, v.reason) == ("matching", "rejected_but_right")


def test_ocr_sample_says_ocr_misread():
    sample = SimpleNamespace(
        id=uuid.uuid4(), category="rec_error", ocr_text="8.00", transcription="9.00"
    )
    v = judge(_value("number", "8.0", "9.0"), _extraction("⟨1⟩TOTAL ⟨2⟩8.00"), [sample])
    assert (v.stage, v.reason) == ("ocr_misread", "sample_rec_error")
    # OCR misread here, but the correct value appears elsewhere in the text: evidence on both sides.
    both = judge(_value("number", "8.0", "9.0"), _extraction(SENT), [sample])
    assert both.stage == "mixed"


def test_drawn_box_with_no_ocr_text_is_uncovered():
    box = {"page": 1, "bbox": [0, 0, 50, 20], "drawn": True}
    lines = [{"text": "far away", "box": [300, 300, 400, 320]}]
    v = judge(_value("string", None, "Stamp 123", evidence=box), _extraction("⟨1⟩far away", lines))
    assert (v.stage, v.reason) == ("ocr_uncovered", "drawn_box_no_text")
    inside = [{"text": "Stomp 1Z3", "box": [5, 2, 45, 18]}]
    misread = judge(
        _value("string", None, "Stamp 123", evidence=box), _extraction("⟨1⟩Stomp 1Z3", inside)
    )
    assert (misread.stage, misread.reason) == ("ocr_misread", "drawn_box_text_differs")


def test_picked_block_that_reads_differently():
    picked = {"page": 1, "text": "TA.K"}
    v = judge(_value("string", "BOOK", "TAK", evidence=picked), _extraction("⟨1⟩TA.K"))
    assert (v.stage, v.reason) == ("ocr_misread", "picked_text_differs")


def test_day_month_swapped_by_template_rule():
    template = SimpleNamespace(day_first=False)
    v = judge(
        _value("date", "2018-12-03", "2018-03-12"), _extraction("⟨1⟩03/12/2018"), (), template
    )
    assert (v.stage, v.reason) == ("template", "day_first_rule")
    free = judge(_value("date", "2018-12-03", "2018-03-12"), _extraction("⟨1⟩03/12/2018"))
    assert (free.stage, free.reason) == ("extraction", "day_month_swapped")


def test_not_enough_evidence():
    v = judge(_value("number", "8.0", "17.5"), _extraction(SENT))
    assert (v.stage, v.reason) == ("unknown", "not_in_text")
    short = judge(_value("number", "8.0", "1"), _extraction("⟨1⟩1 item"))
    assert (short.stage, short.reason) == ("unknown", "short_value")


def test_old_runs_fall_back_to_stored_ocr_text():
    lines = [{"text": "TOTAL 9.00", "box": [0, 0, 10, 10]}]
    v = judge(_value("number", "8.0", "9.0"), _extraction(None, lines))
    assert v.stage == "extraction" and v.evidence[0]["source"] == "ocr"
