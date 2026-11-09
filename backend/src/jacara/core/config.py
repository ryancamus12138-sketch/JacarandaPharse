"""配置：从环境变量和项目根目录的 .env 读取，全项目只在这里读配置。

字段名和 .env 里的变量名一一对应（不区分大小写）：postgres_password ↔ POSTGRES_PASSWORD。
docker compose 读的也是同一个 .env，所以数据库密码只需要写一次。
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    jacara_mode: Literal["personal", "team"] = "personal"

    postgres_user: str = "jacara"
    postgres_password: SecretStr = SecretStr("")
    postgres_db: str = "jacara"
    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5432

    valkey_host: str = "127.0.0.1"
    valkey_port: int = 6379

    s3_endpoint: str = "http://127.0.0.1:8333"
    s3_access_key: SecretStr = SecretStr("")
    s3_secret_key: SecretStr = SecretStr("")
    s3_bucket: str = "jacara"

    model_server: str = "http://127.0.0.1:8080"

    max_upload_mb: int = 50
    """上传文件的大小上限（MB）。"""

    session_hours: int = 12
    """登录多久后过期（小时）。只在 team 模式用。"""

    @property
    def database_url(self) -> str:
        """SQLAlchemy 用的连接地址。里面有密码，不要打印出来。"""
        password = self.postgres_password.get_secret_value()
        return (
            f"postgresql+psycopg://{self.postgres_user}:{password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def valkey_url(self) -> str:
        return f"redis://{self.valkey_host}:{self.valkey_port}/0"


@lru_cache
def get_settings() -> Settings:
    """读一次配置，之后一直用同一份。测试里改了环境变量，要先调用 get_settings.cache_clear()。"""
    return Settings()
