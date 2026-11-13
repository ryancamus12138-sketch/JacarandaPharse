"""人工修改统计和审核数据导出（ADR 0044）。"""

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
    """存一张收据，改两个字段（找零标黄了、商家没标黄）、确认其余要确认的，完成审核。"""
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
    assert stats["silent"] == 1  # 商家改了，但抽取时没标黄
    kinds = {k["kind"]: k["count"] for k in stats["kinds"]}
    assert kinds == {"decimal": 1, "chars": 1}
    change = next(f for f in stats["by_field"] if f["field"] == "change")
    assert change["edited"] == 1 and change["rate"] == 1.0
    assert {c["kind_name"] for c in stats["recent"]} == {"小数点", "个别字认错"}
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
        assert not from_database(session, team, template, exclude=reviewed)  # 不用自己的
    assert {(lesson.field, lesson.kind) for lesson in lessons} == {
        ("change", "decimal"),
        ("merchant", "chars"),
    }
    many = [Lesson("total", "总额", str(n), "1", "number") for n in range(5)] + lessons
    chosen = pick(many)
    assert sum(lesson.field == "total" for lesson in chosen) == 2  # 每个字段最多 2 条
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
    assert extractor.lessons == ("", 0)  # 抽完清掉，不带到下一张
