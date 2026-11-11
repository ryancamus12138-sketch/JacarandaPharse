"""API 顺便提供网页：打包好的前端、刷新不 404、接口不被网页挡住、不能借网址读到别的文件。

这里不需要真的 npm run build：在临时文件夹里造一个假的 dist 就行。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jacara.app import create_app
from jacara.core.config import get_settings

INDEX = "<!doctype html><title>JacarandaParse</title><div id=root></div>"


@pytest.fixture
def dist(tmp_path, monkeypatch) -> Path:
    """一个假的 web/dist：index.html、一个带指纹的 js、一个图标。旁边放一个不该被读到的 .env。"""
    folder = tmp_path / "dist"
    (folder / "assets").mkdir(parents=True)
    (folder / "index.html").write_text(INDEX)
    (folder / "assets" / "index-3f2a.js").write_text("console.log(1)")
    (folder / "favicon.svg").write_text("<svg/>")
    (tmp_path / ".env").write_text("POSTGRES_PASSWORD=secret")
    monkeypatch.setenv("JACARA_WEB_DIR", str(folder))
    get_settings.cache_clear()
    return folder


def _client() -> Iterator[TestClient]:
    with TestClient(create_app()) as client:
        yield client


@pytest.fixture
def web(dist) -> Iterator[TestClient]:
    yield from _client()


def test_home_page_is_the_web_app(web):
    response = web.get("/")
    assert response.status_code == 200
    assert "JacarandaParse" in response.text
    assert response.headers["cache-control"] == "no-cache"


def test_refreshing_a_page_still_gets_the_web_app(web):
    """在 /documents/123 按刷新：浏览器直接问后端这个网址，要拿到 index.html，交给前端路由。"""
    for path in ("/documents/123", "/admin/users", "/reset-password"):
        response = web.get(path)
        assert response.status_code == 200
        assert response.text == INDEX


def test_built_files_are_served_and_cached(web):
    js = web.get("/assets/index-3f2a.js")
    assert js.status_code == 200
    assert js.text == "console.log(1)"
    assert "immutable" in js.headers["cache-control"]
    assert web.get("/favicon.svg").text == "<svg/>"


def test_api_still_works_and_unknown_api_is_404_json(web):
    assert web.get("/api/v1/health").json()["status"] == "ok"
    for path in ("/api/v1/nothing", "/api/v2/health", "/api"):
        response = web.get(path)
        assert response.status_code == 404
        assert response.json() == {"detail": "没有这个接口"}


def test_cannot_read_files_outside_dist(web):
    for path in ("/../.env", "/%2e%2e/.env", "/assets/..%2f..%2f.env"):
        response = web.get(path)
        assert "secret" not in response.text


def test_docs_are_on_by_default(web):
    assert web.get("/docs").status_code == 200
    assert web.get("/openapi.json").status_code == 200


def test_docs_can_be_turned_off(dist, monkeypatch):
    monkeypatch.setenv("JACARA_API_DOCS", "false")
    get_settings.cache_clear()
    for client in _client():
        # 关掉后这两个网址不再是接口文档，落到网页上
        assert client.get("/docs").text == INDEX
        assert "openapi" not in client.get("/openapi.json").text
        assert client.get("/api/v1/health").status_code == 200


def test_not_built_yet_says_how_to_build(tmp_path, monkeypatch):
    monkeypatch.setenv("JACARA_WEB_DIR", str(tmp_path / "missing"))
    get_settings.cache_clear()
    for client in _client():
        response = client.get("/documents")
        assert response.status_code == 503
        assert "npm run build" in response.text
        assert client.get("/api/v1/health").status_code == 200
