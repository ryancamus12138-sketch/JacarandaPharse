"""Settings page: values changed on the page, .env precedence, restart reminders; only
administrators see system details."""

from __future__ import annotations

import json
import uuid

import pytest

from jacara.core import config, deps
from jacara.core.config import get_settings
from jacara.core.db import session_scope
from jacara.core.owner import Owner
from jacara.models import AuditLog


@pytest.fixture(autouse=True)
def no_env_file(monkeypatch, tmp_path):
    """Run in a folder without .env so the local .env has no effect; also clear these environment
    variables."""
    monkeypatch.chdir(tmp_path)
    for key in config.PAGE_EDITABLE:
        monkeypatch.delenv(key.upper(), raising=False)
    get_settings.cache_clear()


def items(client) -> dict[str, dict]:
    response = client.get("/api/v1/settings")
    assert response.status_code == 200, response.text
    return {i["key"]: i for i in response.json()["items"]}


def test_defaults_then_page_value_wins(client, factory):
    found = items(client)
    assert (
        found["jacara_vision"]["value"] == "auto" and found["jacara_vision"]["source"] == "default"
    )
    assert {o["value"] for o in found["jacara_vision"]["options"]} == {"auto", "always", "off"}
    assert set(found) == set(config.PAGE_EDITABLE)

    response = client.put(
        "/api/v1/settings",
        json={"values": {"jacara_vision": "always", "jacara_ocr_max_side": 3000}},
    )
    assert response.status_code == 200, response.text
    assert response.json()["restart_needed"] is True
    found = {i["key"]: i for i in response.json()["items"]}
    assert (found["jacara_vision"]["value"], found["jacara_vision"]["source"]) == ("always", "page")
    assert get_settings().jacara_vision == "always"  # The API uses the new value immediately.
    assert get_settings().jacara_ocr_max_side == 3000
    saved = json.loads(config.page_settings_path().read_text("utf-8"))
    assert saved["jacara_vision"] == "always"

    # null restores the default.
    client.put("/api/v1/settings", json={"values": {"jacara_vision": None}})
    assert items(client)["jacara_vision"]["source"] == "default"
    with session_scope(factory) as session:
        assert session.query(AuditLog).filter_by(action="settings:update").count() == 2


def test_env_wins_and_cannot_be_changed(client, monkeypatch):
    monkeypatch.setenv("JACARA_VISION", "off")
    get_settings.cache_clear()
    found = items(client)["jacara_vision"]
    assert (found["value"], found["source"]) == ("off", "env")
    response = client.put("/api/v1/settings", json={"values": {"jacara_vision": "always"}})
    assert response.status_code == 400 and "JACARA_VISION" in response.json()["detail"]


def test_bad_values_are_refused(client, tmp_path):
    for values, words in (
        ({"jacara_vision": "sometimes"}, "值无效"),
        ({"jacara_ocr_max_side": 99999}, "1000～8000"),
        ({"jacara_watch_dir": "relative/path"}, "完整路径"),
        ({"postgres_password": "x"}, "无法修改"),
    ):
        response = client.put("/api/v1/settings", json={"values": values})
        assert response.status_code == 400 and words in response.json()["detail"], values
    folder = tmp_path / "inbox"
    folder.mkdir()
    ok = client.put("/api/v1/settings", json={"values": {"jacara_watch_dir": str(folder)}})
    assert ok.status_code == 200
    assert get_settings().jacara_watch_dir == folder


def test_page_file_cannot_set_secrets():
    config.page_settings_path().write_text(
        json.dumps({"postgres_password": "hacked", "jacara_learn": False}), "utf-8"
    )
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.postgres_password.get_secret_value() != "hacked"
    assert settings.jacara_learn is False


def test_members_see_but_cannot_change_and_get_no_details(client, monkeypatch):
    monkeypatch.setenv("JACARA_MODE", "team")
    get_settings.cache_clear()
    member = Owner(uuid.uuid4(), uuid.uuid4(), role="member")
    client.app.dependency_overrides[deps.get_owner] = lambda: member
    monkeypatch.setattr("jacara.modules.system.service.model_props", lambda _s: {})
    try:
        response = client.get("/api/v1/settings")
        assert response.status_code == 200 and response.json()["can_change"] is False
        assert (
            client.put("/api/v1/settings", json={"values": {"jacara_learn": False}}).status_code
            == 403
        )
        info = client.get("/api/v1/system").json()
        assert info["model"]["server"] == "" and info["watch_dir"] is None
        assert all(s["detail"] in ("正常", "无法连接，请联系管理员") for s in info["services"])
    finally:
        client.app.dependency_overrides.pop(deps.get_owner)
