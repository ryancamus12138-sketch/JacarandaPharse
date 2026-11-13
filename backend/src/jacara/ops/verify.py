"""恢复以后的核对（ADR 0054）：不只看行数，逐样打开——原始文件、审核记录、模板、OCR 样本小图、
OCR 快照、导出文件、头像。每一样记下几个能打开、哪几个打不开。"""

from __future__ import annotations

import io
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload, sessionmaker

from jacara.core.storage import ObjectStorage


@dataclass
class Check:
    name: str
    ok: int = 0
    failed: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.failed

    def line(self) -> str:
        if self.passed:
            return f"{self.name}：{self.ok} 个都能打开"
        shown = "；".join(self.failed[:3])
        return f"{self.name}：{self.ok} 个能打开，{len(self.failed)} 个不行（{shown}）"


def _try(check: Check, label: str, action: Callable[[], object]) -> None:
    try:
        action()
    except Exception as err:  # 核对时什么错都记下来，不中断
        check.failed.append(f"{label}：{type(err).__name__}: {str(err)[:80]}")
    else:
        check.ok += 1


def _open_original(data: bytes, content_type: str | None) -> int:
    """打开原始文件，返回页数。PDF 用 pdfium，图片用 Pillow 完整解码一次。"""
    if content_type == "application/pdf" or data[:5] == b"%PDF-":
        from jacara.plugins.ocr.loader import PDFIUM_LOCK, open_pdf

        with PDFIUM_LOCK:
            pdf = open_pdf(data)
            try:
                count = len(pdf)
                if count == 0:
                    raise ValueError("PDF 没有页")
                return count
            finally:
                pdf.close()
    from PIL import Image

    with Image.open(io.BytesIO(data)) as image:
        image.load()
    return 1


def _open_png(data: bytes) -> None:
    from PIL import Image

    with Image.open(io.BytesIO(data)) as image:
        image.load()


def verify(factory: sessionmaker[Session], storage: ObjectStorage) -> list[Check]:
    """逐样打开恢复出来的东西。"""
    from jacara.models import (
        Document,
        Export,
        ExtractedValue,
        Extraction,
        OcrSample,
        Profile,
        TemplateVersion,
    )
    from jacara.pipeline.current import template_from_version
    from jacara.pipeline.snapshots import load_snapshot

    originals = Check("原始文件（原图、PDF）")
    reviews = Check("审核记录（每份文档的字段和修改历史）")
    templates = Check("模板（每一个版本）")
    samples = Check("OCR 样本小图")
    snapshots = Check("OCR 快照")
    exports = Check("导出记录的文件")
    avatars = Check("头像")
    with factory() as session:
        documents = session.scalars(
            select(Document).options(
                selectinload(Document.all_extractions)
                .selectinload(Extraction.values)
                .selectinload(ExtractedValue.revisions)
            )
        ).all()
        for d in documents:
            if d.storage_key:
                key, kind = d.storage_key, d.content_type
                _try(originals, d.filename, lambda k=key, t=kind: _open_original(storage.get(k), t))

            def walk(doc: Document = d) -> int:
                # 每一轮、每个字段、每条修改都读一遍，当前值要能算出来
                return sum(
                    len(v.revisions) + (v.current_value is not None)
                    for e in doc.all_extractions
                    for v in e.values
                )

            _try(reviews, d.filename, walk)
        for version in session.scalars(select(TemplateVersion)).all():
            label = f"{version.template_id} 第 {version.version} 版"
            _try(templates, label, lambda v=version: template_from_version(v))
        for sample in session.scalars(select(OcrSample)).all():
            if sample.crop_key:
                key = sample.crop_key
                _try(samples, key, lambda k=key: _open_png(storage.get(k)))
        keys = session.scalars(
            select(Extraction.ocr_snapshot).where(Extraction.ocr_snapshot.is_not(None)).distinct()
        ).all()
        for key in keys:
            if key:
                _try(snapshots, key, lambda k=key: load_snapshot(storage, k))
        for export in session.scalars(select(Export)).all():
            if export.storage_key:
                key = export.storage_key
                _try(exports, export.filename, lambda k=key: storage.get(k))
        for profile in session.scalars(select(Profile).where(Profile.avatar_at.is_not(None))).all():
            key = f"avatars/{profile.user_id}.png"
            _try(avatars, key, lambda k=key: _open_png(storage.get(k)))
    return [originals, reviews, templates, samples, snapshots, exports, avatars]
