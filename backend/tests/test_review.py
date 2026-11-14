"""Manual review: edit, confirm, history, review completion.

Stores a processed document from report in conftest (change read as 100; total, cash and change
require confirmation)."""

import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from jacara.core.db import session_scope
from jacara.core.owner import PERSONAL_OWNER
from jacara.models import Document, ExtractedValue, ValueRevision
from jacara.pipeline.store import save_document


@pytest.fixture
def document_id(factory, report, tmp_path) -> uuid.UUID:
    path = tmp_path / "receipt.jpg"
    path.write_bytes(b"x")
    with session_scope(factory) as session:
        return save_document(session, path, [report]).id


@pytest.fixture
def values(client, document_id) -> dict[str, dict]:
    """Every field of the document by name: values["total"]["id"]."""
    detail = client.get(f"/api/v1/documents/{document_id}").json()
    return {v["name"]: v for v in detail["extractions"][0]["values"]}


def revise(client, field: dict, **body):
    return client.post(f"/api/v1/values/{field['id']}/revisions", json=body)


def test_new_values_show_current_and_pending(values):
    total = values["total"]
    assert total["current_value"] == total["value"] == "9.0"
    assert total["pending_review"]  # The change rule fails, so confirmation is required.
    assert not total["reviewed"]
    assert not values["merchant"]["pending_review"]


def test_edit_keeps_original_value(client, factory, values):
    response = revise(
        client, values["change"], action="edit", value="1.00", reason="OCR 把 1.00 读成了 100"
    )

    assert response.status_code == 201
    change = response.json()
    assert change["value"] == "100.0"  # The model value is unchanged.
    assert change["current_value"] == "1.0"  # Normalized to the same format as the original value.
    assert change["reviewed"] and not change["pending_review"]

    with session_scope(factory) as session:
        row = session.get_one(ExtractedValue, uuid.UUID(change["id"]))
        assert row.value == "100.0"  # The original value in the database is unchanged too.
        [revision] = row.revisions
        assert revision.user_id == PERSONAL_OWNER.user_id  # The actor is recorded.


def test_edit_needs_a_reason(client, values):
    response = revise(client, values["change"], action="edit", value="1.00")
    assert response.status_code == 422
    assert "原因" in response.json()["detail"]


@pytest.mark.parametrize(
    ("name", "raw", "stored"),
    [
        ("total", "9.50", "9.5"),
        ("total", "1,234.50", "1234.5"),
        ("merchant", "  BOOK TA .K  ", "BOOK TA .K"),
    ],
)
def test_edit_normalizes_value(client, values, name, raw, stored):
    response = revise(client, values[name], action="edit", value=raw, reason="核对原件")
    assert response.json()["current_value"] == stored


@pytest.mark.parametrize("raw", ["九块", "nan", "inf"])
def test_edit_rejects_bad_number(client, values, raw):
    response = revise(client, values["total"], action="edit", value=raw, reason="x")
    assert response.status_code == 422


def test_edit_to_same_value_suggests_confirm(client, values):
    response = revise(client, values["total"], action="edit", value="9.00", reason="x")
    assert response.status_code == 422
    assert "confirm" in response.json()["detail"]


def test_edit_to_empty_means_not_in_document(client, values):
    response = revise(
        client, values["cash"], action="edit", value=None, reason="收据上没有现金这一项"
    )
    assert response.status_code == 201
    assert response.json()["current_value"] is None


def test_confirm_keeps_value(client, values):
    response = revise(client, values["total"], action="confirm")
    assert response.status_code == 201
    total = response.json()
    assert total["current_value"] == "9.0"
    assert not total["pending_review"]


def test_history_lists_every_revision_in_order(client, values):
    total = values["total"]
    revise(client, total, action="confirm", reason="和收据一致")
    revise(client, total, action="edit", value="9.5", reason="复查：其实是 9.50")

    history = client.get(f"/api/v1/values/{total['id']}/revisions").json()
    assert history["value"]["value"] == "9.0"
    assert history["value"]["current_value"] == "9.5"
    assert [(r["seq"], r["action"], r["new_value"]) for r in history["revisions"]] == [
        (1, "confirm", "9.0"),
        (2, "edit", "9.5"),
    ]


def test_review_count_goes_down(client, document_id, values):
    revise(client, values["total"], action="confirm")
    listed = client.get("/api/v1/documents").json()["items"][0]
    assert listed["review_count"] == 2  # Three originally, one confirmed.


def test_finish_review(client, document_id, values):
    url = f"/api/v1/documents/{document_id}/review"
    response = client.post(url)
    assert response.status_code == 409
    assert "3 个字段" in response.json()["detail"]

    for name in ("total", "cash"):
        revise(client, values[name], action="confirm")
    revise(client, values["change"], action="edit", value="1.00", reason="OCR 读错")

    response = client.post(url)
    assert response.status_code == 200
    assert response.json()["status"] == "reviewed"
    assert response.json()["review_count"] == 0


def test_editing_reviewed_document_needs_review_again(client, document_id, values):
    for name in ("total", "cash", "change"):
        revise(client, values[name], action="confirm")
    client.post(f"/api/v1/documents/{document_id}/review")

    revise(client, values["total"], action="edit", value="9.5", reason="复查")
    assert client.get(f"/api/v1/documents/{document_id}").json()["status"] == "done"


def test_cannot_review_unfinished_document(client, factory, document_id, values):
    with session_scope(factory) as session:
        session.get_one(Document, document_id).status = "processing"
    response = revise(client, values["total"], action="confirm")
    assert response.status_code == 409


def test_unknown_value_is_404(client):
    response = client.post(f"/api/v1/values/{uuid.uuid4()}/revisions", json={"action": "confirm"})
    assert response.status_code == 404


def test_same_seq_twice_is_rejected(factory, document_id, values):
    """Two people reviewing the same field concurrently produce the same seq; the database accepts
    only one."""
    value_id = uuid.UUID(values["total"]["id"])
    with pytest.raises(IntegrityError), session_scope(factory) as session:
        for _ in range(2):
            session.add(
                ValueRevision(
                    value_id=value_id,
                    seq=1,
                    action="confirm",
                    new_value="9.0",
                    user_id=PERSONAL_OWNER.user_id,
                    team_id=PERSONAL_OWNER.team_id,
                )
            )
            session.flush()
