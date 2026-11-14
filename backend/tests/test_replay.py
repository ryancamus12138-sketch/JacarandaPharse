"""Replay: extract again from the same OCR snapshot with different settings and compare with
reviewed values."""

from __future__ import annotations

import json
import uuid

import pytest

from jacara.core.db import session_scope
from jacara.eval.replay import (
    Arm,
    Options,
    Pred,
    Row,
    evidence_level,
    group_bootstrap,
    mcnemar_exact,
    outcome,
    replay,
    same_value,
    summarize,
    write_result,
)
from jacara.models import Document, ExtractedValue

from .test_template_library import RECEIPT, SchemaBackend, pipeline, process, upload


def test_values_compare_by_meaning():
    assert same_value("number", "9", "9.0")
    assert same_value("number", "1,000.00", "1000")
    assert not same_value("number", "9.0", "8.0")
    assert same_value("string", "BOOK  TA K", "BOOK TA K")
    assert outcome("number", "9.0", None) == "missing"
    assert outcome("number", None, "9.0") == "extra"
    assert outcome("number", None, " ") == "empty"
    assert outcome("number", "9.0", "8.0") == "wrong"


def test_exact_mcnemar():
    assert mcnemar_exact(0, 0) == 1.0
    assert mcnemar_exact(5, 5) == 1.0
    assert mcnemar_exact(6, 0) == pytest.approx(2 / 64)
    assert mcnemar_exact(0, 6) == mcnemar_exact(6, 0)


def _row(group: str, a_ok: bool, b_ok: bool) -> Row:
    return Row(
        "d", 1, group, None, "receipt", "total", "number", "9.0",
        Pred("9.0" if a_ok else "8.0", False), Pred("9.0" if b_ok else "8.0", False),
    )  # fmt: skip


def test_bootstrap_by_group_is_seeded():
    rows = [_row(f"g{i}", False, True) for i in range(5)] + [_row("g9", True, True)]
    low, high = group_bootstrap(rows) or (0, 0)
    assert 0 < low <= high <= 1
    assert group_bootstrap(rows) == group_bootstrap(rows)  # Same seed, same result.
    assert group_bootstrap([_row("g1", True, False)]) is None  # A single group does not count.
    assert evidence_level(5, 200) == "process"
    assert evidence_level(12, 80) == "thin"
    assert evidence_level(20, 120) == "trend"


@pytest.fixture
def reviewed(client, factory, storage, receipt_page) -> uuid.UUID:
    """Process a receipt (the model reads the total as 8), correct it to 9 and complete review."""
    document_id = upload(client)
    wrong = {**RECEIPT, "total": (8.0, 6)}
    process(factory, storage, document_id, receipt_page, SchemaBackend(wrong))
    values = {
        v["name"]: v
        for v in client.get(f"/api/v1/documents/{document_id}").json()["extractions"][0]["values"]
    }
    edit = client.post(
        f"/api/v1/values/{values['total']['id']}/revisions",
        json={"action": "edit", "value": "9.0", "reason": "认错了"},
    )
    assert edit.status_code == 201, edit.text
    for name, v in values.items():
        if v["pending_review"] and name != "total":
            response = client.post(
                f"/api/v1/values/{v['id']}/revisions", json={"action": "confirm", "reason": ""}
            )
            assert response.status_code == 201, response.text
    assert client.post(f"/api/v1/documents/{document_id}/review").status_code == 200
    return uuid.UUID(document_id)


def test_replay_finds_what_the_new_setting_fixed(
    factory, storage, reviewed, receipt_page, tmp_path
):
    with session_scope(factory) as session:
        before = session.query(ExtractedValue).count()
        reviewed_at = session.get_one(Document, reviewed).reviewed_at

    def pipelines(arm: Arm):
        # B reads the total correctly; OCR is empty, so only the snapshot can be used.
        return pipeline(None, SchemaBackend(RECEIPT))

    arms = (Arm("A", "当时存下的结果"), Arm("B", "新模型", "http://127.0.0.1:9"))
    result = replay(factory, storage, arms, pipelines)
    s = summarize(result)
    assert (s["documents"], s["fixed"], s["broken"]) == (1, 1, 0)
    total = next(r for r in result.rows if r.field == "total")
    # 8 is not in the source, so verification failed at the time and the value is empty (flagged): A
    # counts as missed.
    assert (total.truth, total.a.value, total.b.value) == ("9.0", None, "9.0")
    assert total.a.flagged and (total.a_outcome, total.b_outcome) == ("missing", "correct")
    assert s["a"]["missing"] == 1 and s["b"]["missing"] == 0
    assert s["evidence"] == "process"
    # Both arms used the same snapshot, recorded by sha256.
    assert result.manifest.snapshots[str(reviewed)]
    assert sum(s["models"]["B"].values()) == 1

    md = write_result(result, tmp_path / "out")
    assert "修好了 **1** 个" in md.read_text(encoding="utf-8")
    lines = (tmp_path / "out" / "fields.jsonl").read_text(encoding="utf-8").splitlines()
    assert any(json.loads(x)["field"] == "total" for x in lines)

    # Read-only: review data untouched.
    with session_scope(factory) as session:
        assert session.query(ExtractedValue).count() == before
        document = session.get_one(Document, reviewed)
        assert (document.status, document.reviewed_at) == ("reviewed", reviewed_at)


def test_documents_without_snapshot_are_skipped(factory, storage, reviewed):
    from jacara.models import Extraction

    with session_scope(factory) as session:
        for e in session.query(Extraction).all():
            e.ocr_snapshot = None
    arms = (Arm("A", "当时"), Arm("B", "新", "http://127.0.0.1:9"))
    result = replay(factory, storage, arms, lambda arm: pipeline(None, SchemaBackend(RECEIPT)))
    assert result.rows == []
    assert "OCR 快照" in result.skipped[0].reason
    only = replay(factory, storage, arms, lambda a: None, Options(split="test_locked"))  # type: ignore[arg-type,return-value]
    assert only.rows == []


# ---- Line items ----


def _truth(*rows: tuple[str, str]) -> list[dict[str, tuple[str, str | None]]]:
    return [{"name": ("string", n), "amount": ("number", a)} for n, a in rows]


def _pred(*rows: tuple[str | None, str | None]) -> list[dict[str, Pred]]:
    return [{"name": Pred(n, False), "amount": Pred(a, False)} for n, a in rows]


def test_rows_are_matched_by_most_correct_cells_not_by_position():
    from jacara.eval.tables import compare_table, match_rows, micro

    truth = _truth(("PC", "9.00"), ("BAG", "0.50"))
    pred = _pred(("BAG", "0.5"), ("PC", "9"))  # Reversed order, different number notation.
    assert match_rows(truth, pred) == {0: 1, 1: 0}
    side = compare_table("items", truth, pred)
    assert [c.outcome for c in side.cells] == ["correct"] * 4
    assert [c.matched_row for c in side.cells] == [2, 2, 1, 1]
    m = micro([side])
    assert (m.precision, m.recall, m.f1) == (1.0, 1.0, 1.0)


def test_unmatched_rows_count_as_missing_and_extra():
    from jacara.eval.tables import compare_table, micro

    truth = _truth(("PC", "9.00"), ("PEN", "2.00"))
    pred = _pred(("PC", "9.00"), ("SUBTOTAL", "11.00"))  # No correct cell in row 2: not matched.
    side = compare_table("items", truth, pred)
    assert [c.outcome for c in side.cells] == ["correct", "correct", "missing", "missing"]
    assert (side.extra_cells, side.extra_rows) == (2, 1)
    m = micro([side])
    assert (m.correct, m.wrong, m.missing, m.extra) == (2, 0, 2, 2)
    assert m.precision == 0.5 and m.recall == 0.5


def test_a_wrong_cell_in_a_matched_row_is_wrong():
    from jacara.eval.tables import compare_table

    side = compare_table("items", _truth(("PC", "9.00")), _pred(("PC", "8.00")))
    assert [c.outcome for c in side.cells] == ["correct", "wrong"]
    assert side.extra_rows == 0


def test_empty_tables_on_either_side():
    from jacara.eval.tables import compare_table

    assert compare_table("items", [], []).cells == []
    nothing = compare_table("items", _truth(("PC", "9.00")), [])
    assert [c.outcome for c in nothing.cells] == ["missing", "missing"]
    only_pred = compare_table("items", [], _pred(("PC", "9.00")))
    assert (only_pred.extra_cells, only_pred.extra_rows) == (2, 1)


@pytest.fixture
def items_document(client, factory, receipt_page, tmp_path) -> uuid.UUID:
    """Two line items: PC (correct) and BAG (spurious); the reviewer deleted the BAG row."""
    from datetime import datetime

    from jacara.pipeline.store import save_document
    from jacara.plugins.aligners import TextMatchAligner
    from jacara.plugins.exporters import DocumentReport
    from jacara.plugins.extractors import ExtractedField as F
    from jacara.plugins.extractors import ExtractionResult
    from jacara.plugins.validators import RuleValidator
    from jacara.templates import load_template

    fields = (
        F("total", "number", 9.0, 6),
        F("items.1.name", "string", "PC", 5),
        F("items.1.quantity", "number", 1.0, 5),
        F("items.1.unit_price", "number", 9.0, 5),
        F("items.1.amount", "number", 9.0, 5),
        F("items.2.name", "string", "BAG", 5),
        F("items.2.quantity", "number", None, None),
        F("items.2.unit_price", "number", None, None),
        F("items.2.amount", "number", 0.5, None),
    )
    result = ExtractionResult(fields, "{}", "test-model", "fake", "extract-v4", 5)
    report = DocumentReport(
        source="receipt.jpg",
        extraction=result,
        aligned=TextMatchAligner().align(receipt_page, result),
        checks=RuleValidator(load_template("receipt").rules).validate(fields),
        ocr_engine="rapidocr test",
        processed_at=datetime(2026, 11, 11),
        ocr_page=receipt_page,
    )
    path = tmp_path / "receipt.jpg"
    path.write_bytes(b"x")
    with session_scope(factory) as session:
        document_id = save_document(session, path, [report]).id
    extraction_id = client.get(f"/api/v1/documents/{document_id}").json()["extractions"][0]["id"]
    response = client.post(
        f"/api/v1/extractions/{extraction_id}/tables/items/rows/2/delete",
        json={"reason": "不是商品"},
    )
    assert response.status_code == 200, response.text
    return document_id


def test_truth_is_the_reviewed_table_and_a_is_what_the_model_said(factory, items_document):
    from jacara.eval.replay import _compare_tables, _stored_tables
    from jacara.models import Extraction

    with session_scope(factory) as session:
        extraction = session.query(Extraction).filter_by(document_id=items_document).one()
        truth, stored = _stored_tables(extraction)
    # Deleted rows are not in the ground truth.
    assert [row["name"][1] for row in truth["items"]] == ["PC"]
    # The model gave two rows at the time.
    assert [row["name"].value for row in stored["items"]] == ["PC", "BAG"]
    side = _compare_tables(truth, stored)["items"]
    assert {c.outcome for c in side.cells} == {"correct"}
    assert side.extra_rows == 1  # BAG is spurious.


def test_table_summary_and_markdown(tmp_path):
    from jacara.eval.replay import (
        CellPair,
        Manifest,
        ReplayResult,
        TableExtra,
        summary_markdown,
    )
    from jacara.eval.tables import compare_table

    truth = _truth(("PC", "9.00"), ("PEN", "2.00"))
    a = compare_table("items", truth, _pred(("PC", "8.00"), ("PEN", "2.00")))
    b = compare_table("items", truth, _pred(("PC", "9.00"), ("PEN", "2.00"), ("TAX", "0.6")))
    cells = [
        CellPair("d1", 1, "g1", "val", "receipt", x, y)
        for x, y in zip(a.cells, b.cells, strict=True)
    ]
    manifest = Manifest(
        "2026-11-14T00:00:00+00:00",
        "test",
        [{"name": "A", "label": "当时"}, {"name": "B", "label": "新"}],
    )
    result = ReplayResult(
        [],
        [],
        manifest,
        cells=cells,
        extra={
            "A": TableExtra(a.extra_cells, a.extra_rows),
            "B": TableExtra(b.extra_cells, b.extra_rows),
        },
    )
    t = summarize(result)["tables"]
    assert (t["fixed"], t["broken"], t["cells"], t["rows"]) == (1, 0, 4, 2)
    assert (t["a"]["recall"], t["b"]["recall"]) == (0.75, 1.0)
    assert t["b"]["extra_rows"] == 1 and t["b"]["precision"] == 0.6667
    assert t["by_column"]["receipt:items.amount"]["fixed"] == 1
    md = summary_markdown(summarize(result))
    assert "## 表格明细" in md and "修好了 **1** 格" in md
