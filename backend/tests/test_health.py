"""Health checks: unreachable services must report a cause and remedy instead of crashing.

Addresses deliberately point to ports without services, so no real database is needed."""

import socket

import pytest
from pydantic import SecretStr

from jacara.core.config import Settings
from jacara.core.health import check_model, check_postgres, check_s3, check_valkey


def _unused_port() -> int:
    """Find an unused local port."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def nowhere(tmp_path, monkeypatch) -> Settings:
    monkeypatch.chdir(tmp_path)  # Do not read the project's real .env.
    port = _unused_port()
    return Settings(
        postgres_port=port,
        valkey_port=port,
        s3_endpoint=f"http://127.0.0.1:{port}",
        model_server=f"http://127.0.0.1:{port}",
    )


@pytest.mark.parametrize("check", [check_postgres, check_valkey, check_s3])
def test_unreachable_infrastructure_suggests_docker_compose(check, nowhere):
    result = check(nowhere)
    assert not result.ok
    assert result.detail
    assert "docker compose up -d" in result.hint


def test_unreachable_model_suggests_jacara_serve(nowhere):
    result = check_model(nowhere)
    assert not result.ok
    assert "jacara serve" in result.hint


def test_placeholder_secrets_fail_the_check(nowhere):
    from jacara.core.health import check_secrets

    weak = nowhere.model_copy(update={"postgres_password": SecretStr("change-me")})
    result = check_secrets(weak)
    assert not result.ok
    assert "POSTGRES_PASSWORD" in result.detail
    assert "change-me" not in result.hint


def test_web_check_says_how_to_build(nowhere, tmp_path):
    from jacara.core.health import check_web

    result = check_web(nowhere.model_copy(update={"jacara_web_dir": tmp_path / "missing"}))
    assert not result.ok
    assert "npm run build" in result.hint
