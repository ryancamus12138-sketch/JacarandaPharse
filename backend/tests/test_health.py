"""健康检查：服务连不上时，要给出失败原因和解决办法，而不是直接崩溃。

这里故意把地址指向没有服务的端口，所以不需要真的启动数据库。
"""

import socket

import pytest

from jacara.core.config import Settings
from jacara.core.health import check_model, check_postgres, check_s3, check_valkey


def _unused_port() -> int:
    """找一个本机没人用的端口。"""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def nowhere(tmp_path, monkeypatch) -> Settings:
    monkeypatch.chdir(tmp_path)  # 不读项目里真正的 .env
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
