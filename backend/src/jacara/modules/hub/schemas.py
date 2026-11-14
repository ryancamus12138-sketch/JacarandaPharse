"""模型和数据集库接口收发的 JSON（ADR 0059、0060）。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

Kind = Literal["model", "mmproj", "ocr_det", "ocr_rec", "table"]
DatasetKind = Literal["ocr_rec", "ocr_det"]
Split = Literal["train", "val", "test", "all"]


class FileOut(BaseModel):
    name: str
    size: int
    sha256: str
    role: str
    """weights（权重）或 dict（识别字典）。"""


class VersionOut(BaseModel):
    version: int
    kind: Kind
    files: list[FileOut]
    path: str
    """权重文件相对项目根目录的路径（换模型时选的就是它）。"""
    base_model: str | None = None
    datasets: list[str] = []
    license: str | None = None
    imported_at: str | None = None
    imported_by: str | None = None
    description: str = ""
    checks: dict[str, Any] = {}
    """导入时检查出来的：格式、输入输出的形状、字符表几个字、类别数……"""
    in_use: bool = False
    """现在用的就是这个版本（models/selected.json 或 .env 指向它的权重文件）。"""


class ModelOut(BaseModel):
    name: str
    kind: Kind
    kind_label: str
    versions: list[VersionOut]


class HubModels(BaseModel):
    models: list[ModelOut]
    kinds: dict[str, str]
    env_locked: list[str]
    """.env 里写死了的槽位：这些在网页上换不了。"""
    restart_hint: str = "换了以后要 jacara stop、jacara start 才生效"


class UploadOut(BaseModel):
    upload_id: str


class UploadedFile(BaseModel):
    name: str
    size: int
    sha256: str


class FinishIn(BaseModel):
    """传完文件以后："用这些文件建一个新版本"。archive 是模型库导出的 zip（里面有模型卡），
    这时 weights、dict、kind 都从模型卡里读，name 不写就用原来的名字。"""

    name: str = Field(min_length=1, max_length=64)
    kind: Kind | None = None
    weights: str | None = None
    dict_file: str | None = None
    """识别字典的文件名（.txt）。"""
    archive: str | None = None
    base_model: str | None = Field(default=None, max_length=200)
    datasets: list[str] = Field(default_factory=list, max_length=20)
    license: str | None = Field(default=None, max_length=100)
    description: str = Field(default="", max_length=5000)


class CardIn(BaseModel):
    """改模型卡上的说明（不改文件）。不写的不变。"""

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
    """把系统里导出的东西（OCR 样本、检测样本）存进数据集库。"""

    name: str = Field(min_length=1, max_length=64)
    description: str = Field(default="", max_length=5000)


class ModelRef(BaseModel):
    """评测时的一边：current（现在用的）、builtin（RapidOCR 自带的），或模型库里的某个版本。"""

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
    """名字和版本，例如"收据样本 v2"。"""
    split: str
    task: str
    arms: list[dict[str, Any]]
    status: str
    progress: str
    summary: dict[str, Any] | None
    error: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
