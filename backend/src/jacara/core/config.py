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

    jacara_mmproj: Path | None = None
    """视觉模型文件（mmproj-*.gguf，ADR 0038）：有了它模型能看原图。
    不写就用 models/ 里唯一的 mmproj*.gguf；没有就只看文字。"""
    jacara_vision: Literal["auto", "always", "off"] = "auto"
    """什么时候让模型看原图（ADR 0038）：auto 只在照片、扫描件 OCR 不可靠时看；
    always 每张都看（慢）；off 不看。"""
    jacara_pdf_read: Literal["image", "text"] = "image"
    """PDF 怎么读文字（ADR 0042）：image 每页画成图做 OCR，和照片走同一条路，PDF 自带的文字
    只拿来核对；text 只读 PDF 自带的文字（快，只适合确定干净的电子版）。"""
    jacara_ocr_det: Path | None = None
    """自己的文字检测模型（.onnx，ADR 0046）。不写就用网页上选的，再没有就用 RapidOCR 自带的。"""
    jacara_ocr_rec: Path | None = None
    """自己的文字识别模型（.onnx），一般要配套的字典 JACARA_OCR_DICT。"""
    jacara_ocr_dict: Path | None = None
    jacara_learn: bool = True
    """抽取时把同一个模板以前人工改过的例子告诉模型（ADR 0045）。"""
    jacara_ocr_max_side: int = 4000
    """整页 OCR 前，图的长边最多多少像素（ADR 0042）。"""
    jacara_layout: bool = True
    """抽取前先用版面模型找出页面上的区域（标题、正文、表格、页眉页脚），ADR 0034。"""
    jacara_table_model: Path | None = None
    """表格结构模型（SLANet-plus 的 .onnx）。不写就用 models/table/slanet-plus.onnx；
    没有这个文件时，
    表格的格子按文字位置用规则划分。"""

    jacara_watch_dir: Path | None = None
    """监视文件夹（ADR 0036）：放进去的图片和 PDF 自动导入。
    jacara start 会一起启动；不写就不监视。"""
    jacara_watch_template: str | None = None
    """监视文件夹导入时用的模板（我的模板的 id）。不写就用上一次用的。"""
    jacara_watch_user: str | None = None
    """团队模式下，监视文件夹导入的文件算谁传的（用户名）。个人模式不用写。"""

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
        chosen = chosen_file("model")
        if chosen:
            return chosen
        found = sorted(p for p in (PROJECT_ROOT / "models").glob("*.gguf") if not is_mmproj(p))
        return found[0] if len(found) == 1 else None

    def find_mmproj(self) -> Path | None:
        """视觉模型文件（ADR 0038）：先看 JACARA_MMPROJ，再看 models/ 里唯一的 mmproj*.gguf。"""
        if self.jacara_mmproj is not None:
            path = self.jacara_mmproj
            path = path if path.is_absolute() else PROJECT_ROOT / path
            return path if path.is_file() else None
        if model_choices().get("mmproj") == "":
            return None  # 网页上选了"不用视觉模型"
        chosen = chosen_file("mmproj")
        if chosen:
            return chosen
        found = sorted(p for p in (PROJECT_ROOT / "models").glob("*.gguf") if is_mmproj(p))
        return found[0] if len(found) == 1 else None

    def table_model(self) -> Path | None:
        """表格结构模型文件；没有就是 None。"""
        path = (
            self.jacara_table_model
            or chosen_file("table")
            or PROJECT_ROOT / "models" / "table" / "slanet-plus.onnx"
        )
        return path if path.is_file() else None

    def ocr_models(self) -> dict[str, Path]:
        """自己的 OCR 模型文件（检测 det、识别 rec、识别用的字典 dict），ADR 0046。
        先看 JACARA_OCR_DET / _REC / _DICT，再看网页上选的；都没有就用 RapidOCR 自带的。"""
        found: dict[str, Path] = {}
        for key, env in (
            ("det", self.jacara_ocr_det),
            ("rec", self.jacara_ocr_rec),
            ("dict", self.jacara_ocr_dict),
        ):
            path = (
                (env if env.is_absolute() else PROJECT_ROOT / env)
                if env
                else chosen_file(f"ocr_{key}")
            )
            if path is not None and path.is_file():
                found[key] = path
        return found

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


def is_mmproj(path: Path) -> bool:
    """视觉模型文件（mmproj）：名字里有 mmproj。它不是对话模型，找模型时跳过。"""
    return "mmproj" in path.name.lower()


@lru_cache
def get_settings() -> Settings:
    """读一次配置，之后一直用同一份。测试里改了环境变量，要先调用 get_settings.cache_clear()。"""
    return Settings()


MODEL_KEY_FILE = PROJECT_ROOT / ".run" / "model-key"
"""jacara start 每次生成的模型服务密钥（只有自己能读）。worker、命令行从这里读。"""


def model_api_key() -> str | None:
    """调用本机模型服务用的密钥：环境变量 MODEL_API_KEY，或者 jacara start 写的文件。

    jacara start 启动 llama-server 时会设密钥，本机别的程序（浏览器里的网页、别的软件）
    不知道密钥就用不了模型。自己单独 jacara serve 启动的模型没有密钥，这里返回 None 也能用。
    """
    import os

    key = os.environ.get("MODEL_API_KEY")
    if key:
        return key
    try:
        return MODEL_KEY_FILE.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


CHOICES_FILE = "models/selected.json"
"""网页上"换模型"选的文件（ADR 0046），放在 models/ 里（models/ 不进仓库，每台电脑自己的）。
格式：{"model": "models/xxx.gguf", "mmproj": "models/mmproj.gguf" 或 ""（不用）, "table": …,
"ocr_det": …, "ocr_rec": …, "ocr_dict": …}，路径相对项目根目录。.env 里写了的优先。"""


def model_choices() -> dict[str, str]:
    """网页上选的模型文件。没选过、文件坏了是空的。"""
    import json

    path = PROJECT_ROOT / CHOICES_FILE
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, str)} if isinstance(data, dict) else {}


def chosen_file(key: str) -> Path | None:
    """网页上为 key 选的文件，还在才返回。"""
    value = model_choices().get(key)
    if not value:
        return None
    path = Path(value)
    path = path if path.is_absolute() else PROJECT_ROOT / path
    return path if path.is_file() else None
