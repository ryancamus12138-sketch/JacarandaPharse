"""检查后端依赖的服务是否连得上：数据库、任务队列、对象存储、本地模型。

每项检查都返回 Check：成功时写版本等信息，失败时写原因和怎么解决。
检查只读不写，不会创建或修改任何东西。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from jacara.core.config import Settings

TIMEOUT_S = 3
"""每项检查最多等几秒。连不上的服务通常马上就报错，不会等满。"""

_START_INFRA = "运行 docker compose up -d 启动，再用 docker compose ps 看状态"


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    hint: str = ""


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
        hint = _START_INFRA
        if "password authentication failed" in str(err):
            hint = ".env 里的 POSTGRES_PASSWORD 和数据库创建时用的不一样"
        return Check("PostgreSQL", False, _first_line(err), hint)
    return Check("PostgreSQL", True, f"版本 {version[0] if version else '未知'}")


def check_valkey(settings: Settings) -> Check:
    import redis
    from redis.backoff import NoBackoff
    from redis.retry import Retry

    client = redis.Redis(
        host=settings.valkey_host,
        port=settings.valkey_port,
        socket_connect_timeout=TIMEOUT_S,
        socket_timeout=TIMEOUT_S,
        retry=Retry(NoBackoff(), 0),  # 检查只试一次：连不上就是连不上，不要反复重试
    )
    try:
        info = client.info("server")
    except redis.RedisError as err:
        return Check("Valkey", False, _first_line(err), _START_INFRA)
    finally:
        client.close()
    version = info.get("valkey_version") or info.get("redis_version", "未知")
    return Check("Valkey", True, f"版本 {version}")


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
        return Check("SeaweedFS", False, f"S3 拒绝访问（{code}）", ".env 里的 S3 密钥不对")
    except BotoCoreError as err:
        return Check("SeaweedFS", False, _first_line(err), _START_INFRA)
    if settings.s3_bucket in buckets:
        return Check("SeaweedFS", True, f"已连接，存储桶 {settings.s3_bucket} 已存在")
    return Check(
        "SeaweedFS", True, f"已连接，存储桶 {settings.s3_bucket} 还没创建（上传文件时会创建）"
    )


def check_model(settings: Settings) -> Check:
    import httpx

    try:
        response = httpx.get(f"{settings.model_server}/health", timeout=TIMEOUT_S)
    except httpx.HTTPError as err:
        return Check(
            "llama-server", False, _first_line(err), "运行 uv run jacara serve --model 模型文件"
        )
    if response.status_code != 200:
        return Check(
            "llama-server", False, f"服务返回 {response.status_code}", "模型可能还在加载，稍等再试"
        )
    return Check("llama-server", True, f"已就绪（{settings.model_server}）")


CHECKS: tuple[Callable[[Settings], Check], ...] = (
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
