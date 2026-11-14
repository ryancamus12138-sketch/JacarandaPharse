"""Correction statistics and review data export."""

from __future__ import annotations

import io
import json
import uuid
import zipfile

import pytest

from jacara.core.db import session_scope
from jacara.models import Document
from jacara.modules.insights.kinds import error_kind
from jacara.pipeline.store import save_document


@pytest.mark.parametrize(
    ("type_", "before", "after", "kind"),
    [
        ("number", None, "9.0", "missing"),
        ("number", "9.0", None, "extra"),
        ("number", "100.0", "1.0", "decimal"),
        ("number", "-5.0", "5.0", "sign"),
        ("number", "9.0", "8.0", "number"),
        ("date", "2018-03-04", "2018-04-03", "day_month"),
        ("date", "2017-12-25", "2018-12-25", "year"),
        ("date", "2018-12-25", "2018-12-26", "date"),
        ("string", "BOOK TA.K", "book ta k", "format"),
        ("string", "BOOK TAK (TAMAN DAYA)", "BOOK TA.K (TAMAN DAYA)", "format"),
        ("string", "Example Supplies", "Example Suppliez", "chars"),
        ("string", "Sanjay", "OYO Hotel", "rewrite"),
    ],
)
def test_error_kinds(type_, before, after, kind):
    assert error_kind(type_, before, after) == kind


@pytest.fixture
def reviewed(client, factory, report, tmp_path) -> uuid.UUID:
    """Store a receipt, edit two fields (change flagged, merchant not flagged), confirm the
    remaining flagged fields and complete review."""
    path = tmp_path / "receipt.jpg"
    path.write_bytes(b"x")
    with session_scope(factory) as session:
        document_id = save_document(session, path, [report]).id
        session.get_one(Document, document_id).status = "done"
    values = {
        v["name"]: v
        for v in client.get(f"/api/v1/documents/{document_id}").json()["extractions"][0]["values"]
    }

    def post(v, **body):
        return client.post(f"/api/v1/values/{v['id']}/revisions", json=body)

    assert post(values["change"], action="edit", value="1.00", reason="小数点").status_code == 201
    merchant = values["merchant"]["value"] + "X"
    edit = post(values["merchant"], action="edit", value=merchant, reason="多认了一个字")
    assert edit.status_code == 201
    for name, v in values.items():
        if v["pending_review"] and name not in ("change", "merchant"):
            assert post(v, action="confirm", reason="").status_code == 201
    response = client.post(f"/api/v1/documents/{document_id}/review")
    assert response.status_code == 200, response.text
    return document_id


def test_correction_stats(client, reviewed):
    stats = client.get("/api/v1/insights/corrections").json()
    assert stats["documents"] == 1
    assert stats["edited"] == 2
    assert stats["silent"] == 1  # Merchant corrected, but not flagged at extraction.
    kinds = {k["kind"]: k["count"] for k in stats["kinds"]}
    assert kinds == {"decimal": 1, "chars": 1}
    change = next(f for f in stats["by_field"] if f["field"] == "change")
    assert change["edited"] == 1 and change["rate"] == 1.0
    assert {c["kind_name"] for c in stats["recent"]} == {"小数点", "个别字符错误"}
    assert stats["templates"][0]["documents"] == 1


def test_dataset_zip(client, reviewed):
    response = client.get("/api/v1/insights/dataset")
    assert response.status_code == 200
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    names = archive.namelist()
    assert {"train.jsonl", "pairs.toml", "README.txt"} <= set(names)
    [line] = archive.read("train.jsonl").decode().splitlines()
    entry = json.loads(line)
    assert entry["truth"]["change"] == "1.0" and entry["model_answer"]["change"] == "100.0"
    assert {c["field"] for c in entry["corrections"]} == {"change", "merchant"}
    assert "ocr_text" in entry


def test_lessons_are_picked_and_written_into_the_prompt(client, reviewed, factory):
    from jacara.pipeline.learn import Lesson, from_database, note_for, pick
    from jacara.plugins.extractors.llm import build_messages
    from tests.conftest import make_line, make_page

    with session_scope(factory) as session:
        document = session.get_one(Document, reviewed)
        team, template = document.team_id, document.extractions[0].template
        lessons = from_database(session, team, template)
        # Its own examples are excluded.
        assert not from_database(session, team, template, exclude=reviewed)
    assert {(lesson.field, lesson.kind) for lesson in lessons} == {
        ("change", "decimal"),
        ("merchant", "chars"),
    }
    many = [Lesson("total", "总额", str(n), "1", "number") for n in range(5)] + lessons
    chosen = pick(many)
    assert sum(lesson.field == "total" for lesson in chosen) == 2  # At most 2 per field.
    note = note_for(chosen)
    assert "人改成「1.0」（小数点）" in note and "不要照抄" in note
    page = make_page(make_line("TOTAL 9.00", 0, 0, 100, 20))
    [_, user] = build_messages(page, [], lessons=note)
    assert note in user.content


def test_pipeline_teaches_the_model_and_records_it(receipt_page):
    from jacara.pipeline.learn import Lesson
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
    lesson = Lesson("change", "找零", "100", "1.00", "decimal")
    pipeline = Pipeline(ocr=None, extractor=extractor, lessons=lambda _t: [lesson])  # type: ignore[arg-type]
    report = pipeline.extract_page(receipt_page, "r.jpg", 1, "receipt")
    assert report.extraction.prompt_version.endswith("+lessons1")
    assert "人改成「1.00」（小数点）" in backend.received[-1].content
    # Cleared after extraction, not carried to the next document.
    assert extractor.lessons == ("", 0)


def test_review_time_is_added_up_and_averaged(client, factory, reviewed):
    """Review time reported by the review page is summed; the statistics page shows the average
    seconds per document."""
    for seconds in (30, 45):
        response = client.post(
            f"/api/v1/documents/{reviewed}/review-time", json={"seconds": seconds}
        )
        assert response.status_code == 204, response.text
    too_long = client.post(f"/api/v1/documents/{reviewed}/review-time", json={"seconds": 9999})
    assert too_long.status_code == 422
    with session_scope(factory) as session:
        assert session.get_one(Document, reviewed).review_seconds == 75
    stats = client.get("/api/v1/insights/corrections").json()
    assert (stats["review_seconds"], stats["timed_documents"]) == (75.0, 1)


def test_stats_show_where_errors_came_from_and_allow_override(client, reviewed):
    """Statistics page: stage at fault for corrected fields; a person can override, and overrides
    survive re-attribution."""
    stats = client.get("/api/v1/insights/corrections").json()
    assert sum(s["count"] for s in stats["stages"]) == stats["edited"] == 2
    change = next(c for c in stats["recent"] if c["kind_name"] == "小数点")
    assert change["stage"] and change["stage_reason"] and change["attribution_id"]
    response = client.patch(
        f"/api/v1/insights/attributions/{change['attribution_id']}",
        json={"stage": "ocr_misread", "note": "原图上小数点很淡"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["overridden"] is True
    again = client.get("/api/v1/insights/corrections").json()
    after = next(c for c in again["recent"] if c["attribution_id"] == change["attribution_id"])
    assert (after["stage"], after["overridden"]) == ("ocr_misread", True)
    assert "原图上小数点很淡" in after["stage_reason"]
    assert after["auto_stage"] == change["auto_stage"]
    bad = client.patch(
        f"/api/v1/insights/attributions/{change['attribution_id']}", json={"stage": "nope"}
    )
    assert bad.status_code == 422
    undo = client.patch(
        f"/api/v1/insights/attributions/{change['attribution_id']}", json={"stage": None}
    )
    assert undo.json()["stage"] == change["auto_stage"]
    actions = [x["action"] for x in client.get("/api/v1/audit-logs").json()["items"]]
    assert actions.count("attribution:override") == 2
    # Attributions are deleted too.
    assert client.delete(f"/api/v1/documents/{reviewed}").status_code == 200
