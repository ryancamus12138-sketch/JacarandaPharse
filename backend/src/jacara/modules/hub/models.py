# ruff: noqa: E501
"""数据集库和 OCR 评测的表（ADR 0059、0060）。

datasets         数据集：一个 zip（存对象存储）+ 名字、版本、种类、来源、每边几条。按团队分。
ocr_eval_runs    一次 OCR 模型评测：哪个数据集、哪一边、两个模型，结果和进度。

模型库不在数据库里：模型文件和模型卡在 models/hub/（每台电脑自己的，和 models/selected.json 一样）。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from jacara.core.db import Base, JsonColumn, OwnedMixin, TimestampMixin, new_id


class Dataset(Base, OwnedMixin, TimestampMixin):
    __tablename__ = "datasets"
    __table_args__ = (UniqueConstraint("team_id", "name", "version"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(64))
    version: Mapped[int]
    """同一个团队里同名再存一次，版本号加 1（MLflow 注册模型的版本规则）。"""
    kind: Mapped[str] = mapped_column(String(16))
    """ocr_rec（识别：单行小图 + 文字）或 ocr_det（检测：整页图 + 文字框）。"""
    source: Mapped[str] = mapped_column(String(16))
    """samples（OCR 样本导出）、det_pages（检测样本）、upload（上传的）。"""
    description: Mapped[str] = mapped_column(Text, default="")
    """数据集卡的正文：从哪来、怎么标的、要注意什么。"""
    storage_key: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int]
    counts: Mapped[dict[str, Any]] = mapped_column(JsonColumn)
    """每边几条：{"train": 120, "val": 30, "test": 0, "all": 0}；all 是不分边的（PPOCRLabel 导出）。"""
    groups: Mapped[dict[str, Any]] = mapped_column(JsonColumn, default=dict)
    """每边几组业务单据（有 document_group 的才有）。"""
    files: Mapped[list[Any]] = mapped_column(JsonColumn, default=list)
    """读了 zip 里哪几个标注文件，每个属于哪一边：[{"file": "rec_gt_val.txt", "split": "val"}]。"""
    problems: Mapped[dict[str, Any]] = mapped_column(JsonColumn, default=dict)
    """导入时跳过的行和原因：{"图片打不开": 2}。"""
    export_id: Mapped[uuid.UUID | None]
    """从 OCR 样本导出来的：对应的导出记录（ocr_sample_exports）。"""


class OcrEvalRun(Base, OwnedMixin, TimestampMixin):
    __tablename__ = "ocr_eval_runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    dataset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("datasets.id"), index=True)
    split: Mapped[str] = mapped_column(String(8))
    """评哪一边：val、test、train、all。"""
    task: Mapped[str] = mapped_column(String(8))
    """rec（识别）或 det（检测），跟着数据集的种类。"""
    arms: Mapped[list[Any]] = mapped_column(JsonColumn)
    """两个模型：[{"name": "A", "label": "在用", "det": 路径或 null, "rec": …, "dict": …, "sha256": {…}}]。
    创建时就把"在用"解析成具体文件，以后换了模型也知道当时比的是哪个。"""
    status: Mapped[str] = mapped_column(String(12), default="queued")
    """queued（排队）、running（在跑）、done（完成）、failed（出错）。"""
    progress: Mapped[str] = mapped_column(String(64), default="")
    summary: Mapped[dict[str, Any] | None] = mapped_column(JsonColumn)
    lines_key: Mapped[str | None] = mapped_column(String(255))
    """每一条的结果（对象存储，jsonl）：里面有单据上的文字，是业务数据。"""
    error: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
