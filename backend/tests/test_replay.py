"""回放（ADR 0053）：同一份 OCR 快照、换一种设置再抽，和人审核过的值比。"""

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
    assert group_bootstrap(rows) == group_bootstrap(rows)  # 同一个种子，结果一样
    assert group_bootstrap([_row("g1", True, False)]) is None  # 一组不算
    assert evidence_level(5, 200) == "process"
    assert evidence_level(12, 80) == "thin"
    assert evidence_level(20, 120) == "trend"


@pytest.fixture
def reviewed(client, factory, storage, receipt_page) -> uuid.UUID:
    """处理一份收据（模型把合计读成 8），人改成 9，完成审核。"""
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
        return pipeline(None, SchemaBackend(RECEIPT))  # B：读对了合计；OCR 是空的，只能用快照

    arms = (Arm("A", "当时存下的结果"), Arm("B", "新模型", "http://127.0.0.1:9"))
    result = replay(factory, storage, arms, pipelines)
    s = summarize(result)
    assert (s["documents"], s["fixed"], s["broken"]) == (1, 1, 0)
    total = next(r for r in result.rows if r.field == "total")
    # 8 在原文里找不到，当时核对没通过、值是空的（标黄）：A 算漏了
    assert (total.truth, total.a.value, total.b.value) == ("9.0", None, "9.0")
    assert total.a.flagged and (total.a_outcome, total.b_outcome) == ("missing", "correct")
    assert s["a"]["missing"] == 1 and s["b"]["missing"] == 0
    assert s["evidence"] == "process"
    assert result.manifest.snapshots[str(reviewed)]  # 两边用的同一份快照，记了 sha256
    assert sum(s["models"]["B"].values()) == 1

    md = write_result(result, tmp_path / "out")
    assert "修好了 **1** 个" in md.read_text(encoding="utf-8")
    lines = (tmp_path / "out" / "fields.jsonl").read_text(encoding="utf-8").splitlines()
    assert any(json.loads(x)["field"] == "total" for x in lines)

    # 只读：审核数据一点没动
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
