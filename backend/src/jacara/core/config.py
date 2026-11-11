"""配置：从环境变量和项目根目录的 .env 读取，全项目只在这里读配置。

字段名和 .env 里的变量名一一对应（不区分大小写）：postgres_password ↔ POSTGRES_PASSWORD。
docker compose 读的也是同一个 .env，所以数据库密码只需要写一次。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    jacara_mode: Literal["personal", "team"] = "personal"

    jacara_api_docs: bool = True
    """是否打开接口文档页 /docs。给别人用时建议关掉（false）：少暴露一些接口细节。"""

    jacara_web_dir: Path | None = None
    """打包好的网页在哪个文件夹。不写就用项目里的 web/dist（cd web && npm run build 生成）。"""

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

    jacara_model: Path | None = None
    """jacara start 要启动的模型文件（.gguf）。不写就用 models/ 文件夹里唯一的那个 .gguf。"""

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

    @property
    def web_dist(self) -> Path:
        """打包好的网页所在的文件夹。"""
        return self.jacara_web_dir or PROJECT_ROOT / "web" / "dist"

    def find_model(self) -> Path | None:
        """jacara start 不带 --model 时用哪个模型：先看 JACARA_MODEL，再看 models/ 里的。

        models/ 里有好几个 .gguf 时不猜，返回 None，让人用 --model 或 JACARA_MODEL 指定。
        """
        if self.jacara_model is not None:
            path = self.jacara_model
            return path if path.is_absolute() else PROJECT_ROOT / path
        found = sorted((PROJECT_ROOT / "models").glob("*.gguf"))
        return found[0] if len(found) == 1 else None

    def weak_secrets(self) -> list[str]:
        """还是空的、或者还是示例值 change-me 的密钥，返回它们在 .env 里的名字。"""
        secrets = {
            "POSTGRES_PASSWORD": self.postgres_password,
            "S3_ACCESS_KEY": self.s3_access_key,
            "S3_SECRET_KEY": self.s3_secret_key,
        }
        return [
            name
            for name, value in secrets.items()
            if value.get_secret_value().strip().lower() in _PLACEHOLDERS
        ]


PROJECT_ROOT = Path(__file__).resolve().parents[4]
"""项目根目录：这个文件在 backend/src/jacara/core/ 下面，往上四层。"""

_PLACEHOLDERS = {"", "change-me"}


@lru_cache
def get_settings() -> Settings:
    """读一次配置，之后一直用同一份。测试里改了环境变量，要先调用 get_settings.cache_clear()。"""
    return Settings()
