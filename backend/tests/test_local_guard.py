"""Local access guard: Host check, cross-site write check and the desktop launch token."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jacara.core.local_guard import ENTER_PATH, LocalGuard, host_name


def guarded(token: str | None = None) -> TestClient:
    app = FastAPI()

    @app.get("/api/v1/ping")
    def ping() -> dict[str, str]:
        return {"ok": "yes"}

    @app.post("/api/v1/ping")
    def post_ping() -> dict[str, str]:
        return {"ok": "posted"}

    app.add_middleware(LocalGuard, token=token)
    return TestClient(app, base_url="http://127.0.0.1:8123", follow_redirects=False)


@pytest.mark.parametrize(
    ("host", "name"),
    [("127.0.0.1:8000", "127.0.0.1"), ("localhost", "localhost"), ("[::1]:80", "[::1]")],
)
def test_host_name_drops_the_port(host, name):
    assert host_name(host) == name


def test_only_local_host_names_are_accepted():
    client = guarded()
    assert client.get("/api/v1/ping").status_code == 200
    assert client.get("/api/v1/ping", headers={"host": "localhost:8123"}).status_code == 200
    # DNS rebinding: the attacker's name resolves to 127.0.0.1, but the Host header keeps it.
    assert client.get("/api/v1/ping", headers={"host": "evil.example:8123"}).status_code == 400


def test_cross_site_writes_are_refused():
    client = guarded()
    assert client.post("/api/v1/ping").status_code == 200  # Not from a web page.
    assert client.post("/api/v1/ping", headers={"sec-fetch-site": "same-origin"}).status_code == 200
    for site in ("cross-site", "same-site"):
        assert client.post("/api/v1/ping", headers={"sec-fetch-site": site}).status_code == 403
    assert (
        client.post("/api/v1/ping", headers={"origin": "http://127.0.0.1:8123"}).status_code == 200
    )
    for origin in ("http://evil.example", "http://127.0.0.1:9999", "null"):
        assert client.post("/api/v1/ping", headers={"origin": origin}).status_code == 403
    # Reading is never refused by this check (the browser keeps the answer from other sites).
    assert client.get("/api/v1/ping", headers={"sec-fetch-site": "cross-site"}).status_code == 200


def test_launch_token_is_exchanged_for_a_cookie():
    client = guarded(token="secret-token")
    assert client.get("/api/v1/ping").status_code == 401
    assert client.get(f"{ENTER_PATH}?token=wrong").status_code == 401
    entered = client.get(f"{ENTER_PATH}?token=secret-token")
    assert entered.status_code == 303 and entered.headers["location"] == "/"
    cookie = entered.headers["set-cookie"]
    assert cookie.startswith("jacara_token_8123=secret-token")
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie
    assert client.get("/api/v1/ping").json() == {"ok": "yes"}
    client.cookies.clear()
    client.cookies.set("jacara_token_8123", "guessed")
    assert client.get("/api/v1/ping").status_code == 401
