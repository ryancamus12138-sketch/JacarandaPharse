"""Application settings, read from environment variables and the .env file in the project root. This
is the only place in the code base that reads configuration.

Field names map one-to-one to variable names in .env (case-insensitive): postgres_password ↔
POSTGRES_PASSWORD. docker compose reads the same .env, so the database password is written only
once."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import SecretStr
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Precedence: init arguments → environment variables → .env → settings page → defaults."""
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            PageSettingsSource(settings_cls),
            file_secret_settings,
        )

    jacara_mode: Literal["personal", "team"] = "personal"

    jacara_database: Literal["postgres", "sqlite"] = "postgres"
    """Database: postgres (the server deployment with docker compose) or sqlite (the desktop
    edition; a single file, jacara.db, in the data directory). Mirrors Gitea's [database]
    DB_TYPE."""
    jacara_storage: Literal["s3", "local"] = "s3"
    """Where uploaded originals and other files are stored: s3 (SeaweedFS or any S3-compatible
    store) or local (the files/ folder in the data directory). Mirrors Gitea's STORAGE_TYPE."""
    jacara_queue: Literal["celery", "huey"] = "celery"
    """Background task queue: celery (with Valkey) or huey (stored in queue.db in the data
    directory; no separate service). Mirrors Gitea's [queue] TYPE."""

    jacara_api_docs: bool = True
    """Whether to serve the API documentation at /docs. Disable (false) when others use this
    instance, to expose fewer API details."""

    jacara_web_dir: Path | None = None
    """Directory of the built frontend. Defaults to web/dist in the project (built with `cd web &&
    npm run build`)."""

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
    """Model file (.gguf) started by `jacara start`. Defaults to the only .gguf file in models/."""

    jacara_mmproj: Path | None = None
    """Vision projector file (mmproj-*.gguf) that lets the model see the page image. Defaults to the
    only mmproj*.gguf in models/; without one the model reads text only."""
    jacara_vision: Literal["auto", "always", "off"] = "auto"
    """When the model is shown the page image: auto only for photos and scans where OCR is
    unreliable; always for every page (slow); off never."""
    jacara_pdf_read: Literal["image", "text"] = "image"
    """How text is read from PDFs: image renders each page and runs OCR, the same path as photos,
    and uses the embedded text only for cross-checking; text reads only the embedded text layer
    (fast; suitable only for known-clean digital PDFs)."""
    jacara_ocr_det: Path | None = None
    """Custom text detection model (.onnx). Defaults to the model selected in the web UI, then to
    the model bundled with RapidOCR."""
    jacara_ocr_rec: Path | None = None
    """Custom text recognition model (.onnx); usually requires the matching dictionary
    JACARA_OCR_DICT."""
    jacara_ocr_dict: Path | None = None
    jacara_learn: bool = True
    """Include past manual corrections for the same template as examples in the extraction prompt.
    """
    jacara_learn_mode: Literal["recent", "curated"] = "recent"
    """Example selection: recent prefers recently and frequently corrected values; curated uses only
    model errors, merges duplicates, drops stale examples and prefers the same issuer. Compare
    with `jacara eval replay` before switching."""
    jacara_ocr_max_side: int = 4000
    """Maximum length in pixels of the longer image side before full-page OCR."""
    jacara_layout: bool = True
    """Run the layout model before extraction to detect page regions (title, body text, tables,
    headers and footers)."""
    jacara_table_model: Path | None = None
    """Table structure model (SLANet-plus .onnx). Defaults to models/table/slanet-plus.onnx; without
    it, table cells are derived from text positions by rules."""

    jacara_pipeline: bool = True
    """Run the background pipeline as two stages: an OCR stage and a model stage working
    concurrently, which speeds up batch imports. false: one process handles each document end to
    end (the previous behavior; saves a few hundred MB of memory)."""
    jacara_watch_dir: Path | None = None
    """Watch folder: images and PDFs placed in it are imported automatically. Started by `jacara
    start`; not watched when unset."""
    jacara_watch_template: str | None = None
    """Template used for watch-folder imports (id of one of my templates). Defaults to the template
    used last."""
    jacara_watch_user: str | None = None
    """Team mode: user name recorded as the uploader of watch-folder imports. Not needed in personal
    mode."""

    max_upload_mb: int = 50
    """Maximum upload size in MB."""

    session_hours: int = 12
    """Login session lifetime in hours. Team mode only."""

    @property
    def database_url(self) -> str:
        """SQLAlchemy connection URL. Contains the password; do not log it."""
        if self.jacara_database == "sqlite":
            return f"sqlite:///{(data_home() / SQLITE_FILE).as_posix()}"
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
        """Directory of the built frontend."""
        return self.jacara_web_dir or PROJECT_ROOT / "web" / "dist"

    def find_model(self) -> Path | None:
        """Model used by `jacara start` without --model: JACARA_MODEL first, then models/.

                Returns None when models/ contains several .gguf files instead of guessing;
        specify one with --model or JACARA_MODEL."""
        if self.jacara_model is not None:
            path = self.jacara_model
            return path if path.is_absolute() else data_home() / path
        chosen = chosen_file("model")
        if chosen:
            return chosen
        found = sorted(p for p in (data_home() / "models").glob("*.gguf") if not is_mmproj(p))
        return found[0] if len(found) == 1 else None

    def find_mmproj(self) -> Path | None:
        """Vision projector file: JACARA_MMPROJ first, then the only mmproj*.gguf in models/."""
        if self.jacara_mmproj is not None:
            path = self.jacara_mmproj
            path = path if path.is_absolute() else data_home() / path
            return path if path.is_file() else None
        if model_choices().get("mmproj") == "":
            return None  # Vision model disabled in the web UI.
        chosen = chosen_file("mmproj")
        if chosen:
            return chosen
        found = sorted(p for p in (data_home() / "models").glob("*.gguf") if is_mmproj(p))
        return found[0] if len(found) == 1 else None

    def table_model(self) -> Path | None:
        """Table structure model file, or None."""
        path = (
            self.jacara_table_model
            or chosen_file("table")
            or data_home() / "models" / "table" / "slanet-plus.onnx"
        )
        return path if path.is_file() else None

    def ocr_models(self) -> dict[str, Path]:
        """Custom OCR model files (detection det, recognition rec, recognition dictionary dict).
                JACARA_OCR_DET / _REC / _DICT first, then the web UI selection; otherwise the
        models bundled with RapidOCR."""
        found: dict[str, Path] = {}
        for key, env in (
            ("det", self.jacara_ocr_det),
            ("rec", self.jacara_ocr_rec),
            ("dict", self.jacara_ocr_dict),
        ):
            path = (
                (env if env.is_absolute() else data_home() / env)
                if env
                else chosen_file(f"ocr_{key}")
            )
            if path is not None and path.is_file():
                found[key] = path
        return found

    def weak_secrets(self) -> list[str]:
        """Names in .env of secrets that are still empty or still the example value change-me.
        Only the services actually in use are checked: SQLite and local storage have no secrets."""
        secrets: dict[str, SecretStr] = {}
        if self.jacara_database == "postgres":
            secrets["POSTGRES_PASSWORD"] = self.postgres_password
        if self.jacara_storage == "s3":
            secrets["S3_ACCESS_KEY"] = self.s3_access_key
            secrets["S3_SECRET_KEY"] = self.s3_secret_key
        return [
            name
            for name, value in secrets.items()
            if value.get_secret_value().strip().lower() in _PLACEHOLDERS
        ]


PROJECT_ROOT = Path(__file__).resolve().parents[4]
"""Project root: this file lives in backend/src/jacara/core/, four levels below the root."""

_PLACEHOLDERS = {"", "change-me"}

SQLITE_FILE = "jacara.db"
"""SQLite database file in the data directory (JACARA_DATABASE=sqlite)."""


def data_home() -> Path:
    """Root directory for data written at run time: models/, data/settings.json, .run/, and in the
    desktop edition the SQLite database, the task queue and stored files.

    Set only through the JACARA_HOME environment variable, because the settings page file itself
    lives here and must be found before any settings are read. Defaults to the project root, so
    development and docker compose deployments keep their current layout. Read on every call so
    that tests can replace PROJECT_ROOT."""
    import os

    value = os.environ.get("JACARA_HOME")
    return Path(value).expanduser() if value else PROJECT_ROOT


def is_mmproj(path: Path) -> bool:
    """Vision projector file (mmproj): its name contains mmproj. It is not a chat model and is
    skipped when looking for one."""
    return "mmproj" in path.name.lower()


@lru_cache
def get_settings() -> Settings:
    """Read the settings once and reuse them. Tests that change environment variables must call
    get_settings.cache_clear() first."""
    return Settings()


MODEL_KEY_FILE = data_home() / ".run" / "model-key"
"""Model server API key generated by each `jacara start` (owner-readable only). Read by the worker
and the CLI."""


def model_api_key() -> str | None:
    """API key for the local model server: the MODEL_API_KEY environment variable, or the file
    written by `jacara start`.

    `jacara start` sets a key when launching llama-server, so that other local programs (web
    pages in the browser, other software) cannot use the model without it. A model started
    separately with `jacara serve` has no key; returning None works in that case."""
    import os

    key = os.environ.get("MODEL_API_KEY")
    if key:
        return key
    try:
        return MODEL_KEY_FILE.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


CHOICES_FILE = "models/selected.json"
"""Model files selected in the web UI, stored in models/ (not tracked in the repository; local to
each machine).
Format: {"model": "models/xxx.gguf", "mmproj": "models/mmproj.gguf" or "" (disabled), "table": …,
"ocr_det": …, "ocr_rec": …, "ocr_dict": …}; paths are relative to the data directory. Values set in
.env take precedence."""


def model_choices() -> dict[str, str]:
    """Model files selected in the web UI. Empty when nothing has been selected or the file is
    corrupt."""
    import json

    path = data_home() / CHOICES_FILE
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, str)} if isinstance(data, dict) else {}


def chosen_file(key: str) -> Path | None:
    """File selected in the web UI for key; returned only if it still exists."""
    value = model_choices().get(key)
    if not value:
        return None
    path = Path(value)
    path = path if path.is_absolute() else data_home() / path
    return path if path.is_file() else None


PAGE_SETTINGS_FILE = "data/settings.json"
"""Settings changed on the settings page: local to this machine and not tracked in the repository.
Values set in .env take precedence."""

PAGE_EDITABLE = (
    "jacara_vision",
    "jacara_pdf_read",
    "jacara_ocr_max_side",
    "jacara_layout",
    "jacara_learn",
    "jacara_learn_mode",
    "jacara_pipeline",
    "jacara_watch_dir",
    "jacara_watch_template",
    "max_upload_mb",
    "jacara_api_docs",
)
"""Settings editable on the settings page. Database and object storage addresses and credentials,
and the edition (personal or team), can only be set in .env."""


def page_settings_path() -> Path:
    return data_home() / PAGE_SETTINGS_FILE


def page_settings() -> dict[str, Any]:
    """Values changed on the settings page (only keys in PAGE_EDITABLE; a corrupt file is treated as
    empty)."""
    import json

    try:
        data = json.loads(page_settings_path().read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if k in PAGE_EDITABLE}


class PageSettingsSource(PydanticBaseSettingsSource):
    """pydantic-settings source for the values changed on the settings page (data/settings.json)."""

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        return page_settings().get(field_name), field_name, False

    def __call__(self) -> dict[str, Any]:
        return page_settings()
