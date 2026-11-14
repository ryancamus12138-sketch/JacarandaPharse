"""Request and response schemas of the model and dataset library API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from jacara.core.i18n import N_, Rendered

Kind = Literal["model", "mmproj", "ocr_det", "ocr_rec", "table"]
DatasetKind = Literal["ocr_rec", "ocr_det"]
Split = Literal["train", "val", "test", "all"]


class FileOut(BaseModel):
    name: str
    size: int
    sha256: str
    role: str
    """weights or dict (recognition dictionary)."""


class VersionOut(BaseModel):
    version: int
    kind: Kind
    files: list[FileOut]
    path: str
    """Path of the weights file relative to the project root (the value selected in model
    selection)."""
    base_model: str | None = None
    datasets: list[str] = []
    license: str | None = None
    imported_at: str | None = None
    imported_by: str | None = None
    description: str = ""
    checks: dict[str, Any] = {}
    """Results of import validation: format, input and output shapes, dictionary size, class count,
    …"""
    in_use: bool = False
    """This version is in use (models/selected.json or .env points to its weights file)."""


class ModelOut(BaseModel):
    name: str
    kind: Kind
    kind_label: str
    versions: list[VersionOut]


RESTART_HINT = N_("换了以后要 jacara stop、jacara start 才生效")
"""Shown on the models page: model changes take effect after a restart."""


class HubModels(BaseModel):
    models: list[ModelOut]
    kinds: dict[str, str]
    env_locked: list[str]
    """Slots fixed in .env; these cannot be changed in the web UI."""
    restart_hint: str = ""


class UploadOut(BaseModel):
    upload_id: str


class UploadedFile(BaseModel):
    name: str
    size: int
    sha256: str


class FinishIn(BaseModel):
    """After uploading files: "create a new version from these files". archive is a zip exported
    from the library (containing a model card); weights, dict and kind are then read from the
    card, and name defaults to the original name."""

    name: str = Field(min_length=1, max_length=64)
    kind: Kind | None = None
    weights: str | None = None
    dict_file: str | None = None
    """File name of the recognition dictionary (.txt)."""
    archive: str | None = None
    base_model: str | None = Field(default=None, max_length=200)
    datasets: list[str] = Field(default_factory=list, max_length=20)
    license: str | None = Field(default=None, max_length=100)
    description: str = Field(default="", max_length=5000)


class CardIn(BaseModel):
    """Change the description on a model card (files are not changed). Omitted fields are
    unchanged."""

    base_model: str | None = Field(default=None, max_length=200)
    datasets: list[str] | None = Field(default=None, max_length=20)
    license: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=5000)


class DatasetOut(BaseModel):
    id: uuid.UUID
    name: str
    version: int
    kind: DatasetKind
    source: str
    description: str
    sha256: str
    size: int
    counts: dict[str, int]
    groups: dict[str, int]
    files: list[dict[str, str]]
    problems: dict[str, int]
    created_at: datetime


class DatasetIn(BaseModel):
    """Store data exported from the system (OCR samples, detection samples) in the dataset
    library."""

    name: str = Field(min_length=1, max_length=64)
    description: str = Field(default="", max_length=5000)


class ModelRef(BaseModel):
    """One arm of an evaluation: current (in use), builtin (bundled with RapidOCR), or a version
    from the library."""

    source: Literal["current", "builtin", "hub"] = "current"
    name: str | None = None
    version: int | None = None


class EvalIn(BaseModel):
    dataset_id: uuid.UUID
    split: Split = "val"
    a: ModelRef = ModelRef(source="current")
    b: ModelRef


class EvalOut(BaseModel):
    id: uuid.UUID
    dataset_id: uuid.UUID
    dataset: str
    """Name and version, for example "receipt samples v2"."""
    split: str
    task: str
    arms: list[dict[str, Any]]
    status: str
    progress: Rendered
    summary: dict[str, Any] | None
    error: Rendered
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
