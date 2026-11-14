"""Check that backend dependencies are reachable (database, task queue, object storage, local model)
and that .env and the frontend are ready.

Each check returns a Check: version information on success, or the cause and remedy on failure.
Checks are read-only and never create or modify anything."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from jacara.core.config import Settings
from jacara.core.i18n import N_, tr

TIMEOUT_S = 3
"""Timeout in seconds for each check. Unreachable services usually fail immediately rather than
waiting for the full timeout."""

_START_INFRA = N_("运行 docker compose up -d 启动，再用 docker compose ps 看状态")


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    hint: str = ""


def check_secrets(settings: Settings) -> Check:
    """Passwords in .env must not be empty or the example value change-me, which anyone can
    guess."""
    weak = settings.weak_secrets()
    if weak:
        return Check(
            tr(".env 密钥"),
            False,
            tr("还是空的或 change-me：{names}", names=tr("、").join(weak)),
            tr("用 openssl rand -hex 16 生成随机值填进 .env"),
        )
    return Check(tr(".env 密钥"), True, tr("都已改成自己的值"))


def check_web(settings: Settings) -> Check:
    """Whether the frontend has been built. The API and `npm run dev` work without it, so this is
    only a warning."""
    index = settings.web_dist / "index.html"
    if index.is_file():
        return Check(tr("网页"), True, tr("已打包（{web_dist}）", web_dist=settings.web_dist))
    return Check(
        tr("网页"), False, tr("还没打包，打开 8000 只会看到提示"), "cd web && npm run build"
    )


def check_postgres(settings: Settings) -> Check:
    import psycopg

    try:
        with psycopg.connect(
            host=settings.postgres_host,
            port=settings.postgres_port,
            user=settings.postgres_user,
            password=settings.postgres_password.get_secret_value(),
            dbname=settings.postgres_db,
            connect_timeout=TIMEOUT_S,
        ) as conn:
            version = conn.execute("SHOW server_version").fetchone()
    except psycopg.OperationalError as err:
        hint = tr(_START_INFRA)
        if "password authentication failed" in str(err):
            hint = tr(".env 里的 POSTGRES_PASSWORD 和数据库创建时用的不一样")
        return Check("PostgreSQL", False, _first_line(err), hint)
    return Check(
        "PostgreSQL", True, tr("版本 {value}", value=version[0] if version else tr("未知"))
    )


def check_valkey(settings: Settings) -> Check:
    import redis
    from redis.backoff import NoBackoff
    from redis.retry import Retry

    client = redis.Redis(
        host=settings.valkey_host,
        port=settings.valkey_port,
        socket_connect_timeout=TIMEOUT_S,
        socket_timeout=TIMEOUT_S,
        retry=Retry(NoBackoff(), 0),  # Single attempt per check: no retries.
    )
    try:
        info = client.info("server")
    except redis.RedisError as err:
        return Check("Valkey", False, _first_line(err), tr(_START_INFRA))
    finally:
        client.close()
    version = info.get("valkey_version") or info.get("redis_version", tr("未知"))
    return Check("Valkey", True, tr("版本 {version}", version=version))


def check_s3(settings: Settings) -> Check:
    import boto3
    from botocore.config import Config
    from botocore.exceptions import BotoCoreError, ClientError

    client = boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint,
        aws_access_key_id=settings.s3_access_key.get_secret_value(),
        aws_secret_access_key=settings.s3_secret_key.get_secret_value(),
        region_name="us-east-1",
        config=Config(
            connect_timeout=TIMEOUT_S, read_timeout=TIMEOUT_S, retries={"max_attempts": 1}
        ),
    )
    try:
        buckets = [b["Name"] for b in client.list_buckets().get("Buckets", [])]
    except ClientError as err:
        code = err.response.get("Error", {}).get("Code", "")
        return Check(
            "SeaweedFS", False, tr("S3 拒绝访问（{code}）", code=code), tr(".env 里的 S3 密钥不对")
        )
    except BotoCoreError as err:
        return Check("SeaweedFS", False, _first_line(err), tr(_START_INFRA))
    if settings.s3_bucket in buckets:
        return Check(
            "SeaweedFS", True, tr("已连接，存储桶 {s3_bucket} 已存在", s3_bucket=settings.s3_bucket)
        )
    return Check(
        "SeaweedFS",
        True,
        tr("已连接，存储桶 {s3_bucket} 还没创建（上传文件时会创建）", s3_bucket=settings.s3_bucket),
    )


def check_model(settings: Settings) -> Check:
    import httpx

    try:
        response = httpx.get(f"{settings.model_server}/health", timeout=TIMEOUT_S)
    except httpx.HTTPError as err:
        return Check(
            "llama-server", False, _first_line(err), tr("运行 uv run jacara serve --model 模型文件")
        )
    if response.status_code != 200:
        return Check(
            "llama-server",
            False,
            tr("服务返回 {status_code}", status_code=response.status_code),
            tr("模型可能还在加载，稍等再试"),
        )
    return Check(
        "llama-server", True, tr("已就绪（{model_server}）", model_server=settings.model_server)
    )


CHECKS: tuple[Callable[[Settings], Check], ...] = (
    check_secrets,
    check_web,
    check_postgres,
    check_valkey,
    check_s3,
    check_model,
)


def run_checks(settings: Settings) -> list[Check]:
    return [check(settings) for check in CHECKS]


def _first_line(err: BaseException) -> str:
    text = str(err).strip() or type(err).__name__
    return text.splitlines()[0][:160]
