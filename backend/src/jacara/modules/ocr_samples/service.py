"""OCR correction samples: collection during review, management on the samples page, and export as
PaddleOCR recognition training data."""

from __future__ import annotations

import io
import json
import logging
import uuid
import zipfile
from collections import Counter, defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from PIL import Image
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from jacara.core.i18n import language, stored, tr
from jacara.core.owner import Owner
from jacara.core.permissions import allowed, require
from jacara.core.storage import ObjectStorage, StorageError
from jacara.modules.audit.service import record
from jacara.modules.documents.models import Document, Extraction
from jacara.modules.fields.models import ExtractedValue
from jacara.modules.ocr_samples import groups
from jacara.modules.ocr_samples.crop import (
    CATEGORIES,
    TAGS,
    _loose,
    box_polygon,
    category_of,
    clean,
    crop_polygon,
    error_tags,
    lines_in_box,
    png_bytes,
    sha256,
)
from jacara.modules.ocr_samples.models import (
    DocGroupMember,
    OcrSample,
    OcrSampleExport,
    PairDecision,
    TeamSetting,
)
from jacara.modules.ocr_samples.schemas import (
    BackfillOut,
    Distribution,
    Evidence,
    Excluded,
    ExportPreview,
    ExportRecordOut,
    GroupList,
    GroupMemberOut,
    GroupOut,
    PairIn,
    PairOut,
    PairResult,
    SampleIn,
    SampleList,
    SampleOut,
    SampleSettings,
    SplitCount,
    TemplateOption,
    UnitOut,
    VerifyOut,
)
from jacara.pipeline.current import label_for, labels_of, template_of, template_title
from jacara.pipeline.pages import ocr_page_json
from jacara.plugins.extractors.base import split_cell_name
from jacara.templates import Template

log = logging.getLogger(__name__)

SETTING = "collect_ocr_samples"
FORMAT_VERSION = 1
"""Export format version, written to the README."""

PADDING = 0.3
"""Padding around region-selection samples (same as "re-recognize region"; see
pipeline/region.py)."""


class SampleError(Exception):
    """Business error. code tells the router which HTTP status to return."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ---- Switch ----


def collecting(session: Session, team_id: uuid.UUID) -> bool:
    """Whether this team has OCR correction sample collection enabled. Off by default."""
    setting = session.get(TeamSetting, (team_id, SETTING))
    return bool(setting.value) if setting is not None else False


# ---- Collection during review ----


def generic_field(name: str) -> str:
    """Remove the row number from table cells: items.2.amount → items.amount."""
    cell = split_cell_name(name)
    return f"{cell[0]}.{cell[2]}" if cell else name


def _sensitive(template: Template | None, field: str) -> bool:
    if template is None:
        return False
    if any(f.name == field and f.sensitive for f in template.fields):
        return True
    return any(
        f"{table.name}.{column.name}" == field and getattr(column, "sensitive", False)
        for table in template.tables
        for column in table.columns
    )


def _template_title(extraction: Extraction, template: Template | None) -> str:
    return template_title(extraction, template)


def _template_key(sample: OcrSample) -> str:
    """Identity of the sample's template; samples whose field value no longer existed when keys
    were introduced fall back to the stored name."""
    return sample.template_key or sample.template


def display_names(
    session: Session, samples: Sequence[OcrSample]
) -> dict[uuid.UUID, tuple[str, str]]:
    """Sample id → (template name, field name) in the reader's language, read from the template its
    extraction used (one sample may be listed in Chinese and exported in English: the identity is
    template_key and field, the names follow the reader). The names stored at collection time
    are used when the field value or template no longer exists."""
    value_ids = {s.value_id for s in samples if s.value_id is not None}
    extraction_of: dict[uuid.UUID, uuid.UUID] = (
        {
            value_id: extraction_id
            for value_id, extraction_id in session.execute(
                select(ExtractedValue.id, ExtractedValue.extraction_id).where(
                    ExtractedValue.id.in_(value_ids)
                )
            ).all()
        }
        if value_ids
        else {}
    )
    names: dict[uuid.UUID, tuple[str, dict[str, str]]] = {}
    if extraction_of:
        for extraction in session.scalars(
            select(Extraction).where(Extraction.id.in_(set(extraction_of.values())))
        ):
            template = template_of(extraction)
            names[extraction.id] = (
                _template_title(extraction, template),
                labels_of(extraction, language()),
            )
    out: dict[uuid.UUID, tuple[str, str]] = {}
    for sample in samples:
        extraction_id = extraction_of.get(sample.value_id) if sample.value_id else None
        found = names.get(extraction_id) if extraction_id else None
        if found is None:
            out[sample.id] = (sample.template, sample.field_label)
        else:
            title, labels = found
            out[sample.id] = (title, label_for(labels, sample.field) or sample.field_label)
    return out


def collect(
    session: Session,
    storage: ObjectStorage | None,
    owner: Owner,
    value: ExtractedValue,
    revision_id: uuid.UUID | None,
    page: int,
    transcription: str,
    *,
    line: dict[str, Any] | None = None,
    box: Sequence[float] | None = None,
) -> OcrSample | None:
    """Collect a sample while saving a review. Returns None when nothing should be collected (switch
    off, sensitive field, text from the PDF text layer, …).

        Cropping errors never affect the review: the sample is recorded as "crop failed" and can
    be retried on the samples page."""
    if not collecting(session, value.team_id):
        return None
    truth = clean(transcription)
    if truth is None:
        return None
    extraction = value.extraction
    document = extraction.document
    if document.storage_key is None or storage is None:
        return None
    ocr = ocr_page_json(extraction.ocr, page, extraction.page) or {}
    if ocr.get("engine", extraction.source) == "pdf-text":
        return None  # The text of this page comes from the PDF text layer, not OCR.
    template = template_of(extraction)
    field = generic_field(value.name)
    if _sensitive(template, field):
        return None
    if line is not None:
        kind, polygon = "line", [[float(x), float(y)] for x, y in line["polygon"]]
        ocr_text: str | None = str(line["text"])
    elif box is not None:
        kind, polygon = "box", box_polygon(box)
        inside = lines_in_box(ocr.get("lines", []), box)
        ocr_text = " ".join(str(ln["text"]) for ln in inside) if inside else None
    else:
        return None
    labels = labels_of(extraction)
    sample = OcrSample(
        id=uuid.uuid4(),
        # Microsecond precision so that samples collected in the same second are still ordered.
        created_at=datetime.now(UTC),
        document_id=document.id,
        value_id=value.id,
        revision_id=revision_id,
        page=page,
        unit_page=extraction.page,
        kind=kind,
        polygon=polygon,
        transcription=truth,
        ocr_text=ocr_text,
        category=category_of(kind, ocr_text, truth),
        error_tags=error_tags(ocr_text, truth),
        template_key=extraction.template[:64],
        template=_template_title(extraction, template)[:200],
        field=field[:200],
        field_label=(labels.get(field) or value.label or value.name)[:200],
        source_format="pdf" if document.content_type == "application/pdf" else "image",
        ocr_engine=(extraction.ocr_engine or "")[:128],
        status="ready",
        error="",
        user_id=owner.user_id,
        team_id=value.team_id,
    )
    _crop(storage, sample, document)
    session.add(sample)
    # Grouping: assign group and split when this business document gets its first sample.
    groups.ensure_member(session, value.team_id, owner.user_id, (document.id, extraction.page))
    return sample


def _crop(storage: ObjectStorage, sample: OcrSample, document: Document) -> None:
    """Crop and store the line image in object storage. Errors are recorded on the sample
    (status=failed) and not raised."""
    from jacara.pipeline.region import crop_region
    from jacara.plugins.ocr.loader import page_png

    try:
        if document.storage_key is None:
            raise ValueError(stored("此文档未保存原始文件"))
        data = storage.get(document.storage_key)
        page = page_png(data, document.content_type == "application/pdf", sample.page)
        if sample.kind == "line":
            image = crop_polygon(page, sample.polygon)
        else:
            (x0, y0), (x1, y1) = sample.polygon[0], sample.polygon[2]
            image = crop_region(page, [x0, y0, x1, y1], PADDING)
        png = png_bytes(image)
        key = f"ocr-samples/{sample.team_id}/{sample.id}.png"
        storage.put(key, png, "image/png")
    # Cropping is incidental: unreadable files, invalid boxes and unreachable storage never affect
    # the review.
    except Exception as err:
        log.warning("OCR 样本裁图失败：%s 第 %s 页", document.filename, sample.page, exc_info=True)
        sample.status = "failed"
        sample.error = _reason(err)
        return
    sample.crop_key = key
    sample.crop_sha256 = sha256(png)
    sample.width, sample.height = image.size
    # Backfilled samples and automatically collected correct lines require verification before use.
    sample.status = "pending" if sample.origin in ("backfill", "confirmed") else "ready"
    sample.error = ""


def _reason(err: Exception) -> str:
    if isinstance(err, StorageError):
        return stored("对象存储暂时不可用")
    return str(err) or type(err).__name__


# ---- Samples page ----


class OcrSampleService:
    def __init__(
        self, session: Session, owner: Owner, storage: ObjectStorage | None = None
    ) -> None:
        self.last_export_id: uuid.UUID | None = None
        """Id of the latest export record (recorded when storing in the dataset library)."""
        self.session = session
        self.owner = owner
        self.storage = storage

    def settings(self) -> SampleSettings:
        return SampleSettings(
            collect=collecting(self.session, self.owner.team_id),
            can_change="system:manage" in allowed(self.owner),
        )

    def set_settings(self, collect: bool) -> SampleSettings:
        require(self.owner, "system:manage")
        setting = self.session.get(TeamSetting, (self.owner.team_id, SETTING))
        if setting is None:
            self.session.add(TeamSetting(team_id=self.owner.team_id, key=SETTING, value=collect))
        else:
            setting.value = collect
        record(self.session, self.owner, "ocr_samples:settings", None, None, collect=collect)
        self.session.flush()
        return self.settings()

    def _query(
        self,
        category: Sequence[str] | None = None,
        status: str | None = None,
        source_format: str | None = None,
        template: str | None = None,
    ) -> Any:
        query = select(OcrSample).where(OcrSample.team_id == self.owner.team_id)
        if category:
            query = query.where(OcrSample.category.in_(list(category)))
        if status:
            query = query.where(OcrSample.status == status)
        if source_format:
            query = query.where(OcrSample.source_format == source_format)
        if template:
            query = query.where(
                or_(
                    OcrSample.template_key == template,
                    and_(OcrSample.template_key == "", OcrSample.template == template),
                )
            )
        return query

    def list(
        self,
        category: Sequence[str] | None = None,
        status: str | None = None,
        source_format: str | None = None,
        template: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> SampleList:
        require(self.owner, "value:read")
        query = self._query(category, status, source_format, template)
        total = self.session.scalar(select(func.count()).select_from(query.subquery())) or 0
        samples = list(
            self.session.scalars(
                query.order_by(OcrSample.created_at.desc(), OcrSample.id)
                .limit(limit)
                .offset(offset)
            )
        )
        team = OcrSample.team_id == self.owner.team_id
        categories = dict(
            self.session.execute(
                select(OcrSample.category, func.count())
                .where(team, OcrSample.status != "withdrawn")
                .group_by(OcrSample.category)
            ).all()
        )
        statuses = dict(
            self.session.execute(
                select(OcrSample.status, func.count()).where(team).group_by(OcrSample.status)
            ).all()
        )
        return SampleList(
            items=self._out(samples),
            total=total,
            categories={k: int(v) for k, v in categories.items()},
            statuses={k: int(v) for k, v in statuses.items()},
        )

    def templates(self) -> list[TemplateOption]:
        """Templates that have samples, for filtering: one entry per template identity, named in
        the reader's language."""
        require(self.owner, "value:read")
        team = self.owner.team_id
        pairs = self.session.execute(
            select(OcrSample.template_key, OcrSample.template)
            .where(OcrSample.team_id == team)
            .distinct()
        ).all()
        # One sample per template identity is enough to read its name.
        samples: list[OcrSample] = []
        for key in {k or t for k, t in pairs}:
            sample = self.session.scalars(self._query(None, None, None, key).limit(1)).first()
            if sample is not None:
                samples.append(sample)
        names = display_names(self.session, samples)
        options: dict[str, str] = {}
        for sample in samples:
            key = _template_key(sample)
            if key:
                options.setdefault(key, names[sample.id][0] or key)
        return sorted(
            (TemplateOption(key=k, title=v) for k, v in options.items()), key=lambda o: o.title
        )

    def _out(self, samples: Sequence[OcrSample]) -> list[SampleOut]:
        from jacara.modules.fields.models import ValueRevision

        backfilled = {s.revision_id for s in samples if s.origin == "backfill" and s.revision_id}
        reviewed: dict[uuid.UUID, str | None] = (
            dict(
                self.session.execute(
                    select(ValueRevision.id, ValueRevision.new_value).where(
                        ValueRevision.id.in_(backfilled)
                    )
                ).all()
            )
            if backfilled
            else {}
        )
        ids = {s.document_id for s in samples}
        names: dict[uuid.UUID, str] = (
            dict(
                self.session.execute(
                    select(Document.id, Document.filename).where(Document.id.in_(ids))
                ).all()
            )
            if ids
            else {}
        )
        shown = display_names(self.session, samples)
        return [
            SampleOut(
                id=s.id,
                created_at=s.created_at,
                document_id=s.document_id,
                filename=names.get(s.document_id, ""),
                page=s.page,
                kind=s.kind,
                transcription=s.transcription,
                ocr_text=s.ocr_text,
                category=s.category,  # type: ignore[arg-type]
                error_tags=list(s.error_tags or []),
                template=shown[s.id][0],
                template_key=_template_key(s),
                field=s.field,
                field_label=shown[s.id][1],
                source_format=s.source_format,  # type: ignore[arg-type]
                ocr_engine=s.ocr_engine,
                width=s.width,
                height=s.height,
                status=s.status,  # type: ignore[arg-type]
                error=s.error,
                origin=s.origin,
                reviewed_value=reviewed.get(s.revision_id) if s.revision_id else None,
            )
            for s in samples
        ]

    def _find(self, sample_id: uuid.UUID) -> OcrSample:
        sample = self.session.get(OcrSample, sample_id)
        if sample is None or sample.team_id != self.owner.team_id:
            raise SampleError("not_found", tr("样本不存在"))
        return sample

    def image(self, sample_id: uuid.UUID) -> bytes:
        require(self.owner, "value:read")
        sample = self._find(sample_id)
        if sample.crop_key is None or self.storage is None:
            raise SampleError("not_found", tr("此样本无裁剪图像（裁剪失败，可重试）"))
        try:
            return self.storage.get(sample.crop_key)
        except StorageError as err:
            raise SampleError("storage", tr("对象存储暂时不可用，请稍后再试")) from err

    def update(self, sample_id: uuid.UUID, change: SampleIn) -> SampleOut:
        require(self.owner, "value:review")
        sample = self._find(sample_id)
        detail: dict[str, Any] = {}
        if change.transcription is not None:
            truth = clean(change.transcription)
            if truth is None:
                raise SampleError("invalid", tr("图中原文不能为空，也不能包含 Tab 或换行"))
            if truth != sample.transcription:
                detail["transcription"] = [sample.transcription, truth]
                sample.transcription = truth
                # New label revision: export manifests record which revision was used.
                sample.revision += 1
                sample.error_tags = error_tags(sample.ocr_text, truth)
                if change.category is None and sample.kind == "line":
                    sample.category = category_of(sample.kind, sample.ocr_text, truth)
        if change.category is not None and change.category != sample.category:
            detail["category"] = [sample.category, change.category]
            sample.category = change.category
        verified = False
        if change.status is not None and change.status != sample.status:
            if sample.status == "failed":
                raise SampleError("invalid", tr("请先重试裁剪失败的样本"))
            verified = sample.status == "pending" and change.status == "ready"
            if verified and change.category is None:
                # Approved: reclassify using the transcription confirmed by the reviewer.
                sample.category = category_of(sample.kind, sample.ocr_text, sample.transcription)
                sample.error_tags = error_tags(sample.ocr_text, sample.transcription)
            detail["status"] = [sample.status, change.status]
            sample.status = change.status
        if detail:
            action = "ocr_samples:verify" if verified else "ocr_samples:update"
            record(self.session, self.owner, action, "ocr_sample", sample.id, **detail)
        self.session.flush()
        return self._out([sample])[0]

    def backfill(self) -> BackfillOut:
        """Backfill samples from earlier corrections: corrections made by clicking source text or
        selecting a region are cropped into pending samples, exportable only after a person
        confirms (or corrects) the transcription against the crop.

                Typed values (no click, no region) are not collected: their location on the image
        is unknown. Each correction is collected once; repeated calls create no duplicates."""
        require(self.owner, "value:review")
        from jacara.modules.fields.models import ValueRevision

        team = self.owner.team_id
        done = set(
            self.session.scalars(
                select(OcrSample.revision_id).where(
                    OcrSample.team_id == team, OcrSample.revision_id.is_not(None)
                )
            )
        )
        rows = self.session.execute(
            select(ValueRevision, ExtractedValue)
            .join(ExtractedValue, ValueRevision.value_id == ExtractedValue.id)
            .where(ExtractedValue.team_id == team, ValueRevision.action == "edit")
            .order_by(ValueRevision.created_at)
        ).all()
        skipped: Counter[str] = Counter()
        created = 0
        for revision, value in rows:
            if revision.id in done:
                skipped[tr("样本已存在")] += 1
                continue
            reason = self._backfill_one(revision, value)
            if reason:
                skipped[reason] += 1
            else:
                created += 1
                done.add(revision.id)
        record(self.session, self.owner, "ocr_samples:backfill", None, None, created=created)
        self.session.flush()
        return BackfillOut(
            created=created,
            skipped=[Excluded(reason=r, count=n) for r, n in skipped.most_common()],
        )

    def collect_confirmed(self) -> BackfillOut:
        """Collect correctly read lines: in reviewed documents, uncorrected values whose evidence is
        exactly one whole source block containing only this value (no label or other text) were
        read correctly by OCR. They are cropped into pending samples and exportable after a
        person has looked at them.

                PaddleOCR recommends at least 5000 lines for fine-tuning; error cases alone are
        not enough, and training only on errors makes the model forget characters it already
        recognizes well."""
        require(self.owner, "value:review")
        from sqlalchemy.orm import selectinload

        team = self.owner.team_id
        done = set(
            self.session.scalars(
                select(OcrSample.value_id).where(
                    OcrSample.team_id == team, OcrSample.origin == "confirmed"
                )
            )
        )
        documents = self.session.scalars(
            select(Document)
            .where(Document.team_id == team, Document.status == "reviewed")
            .options(selectinload(Document.extractions).selectinload(Extraction.values))
        ).all()
        skipped: Counter[str] = Counter()
        created = 0
        for document in documents:
            for extraction in document.extractions:
                for value in extraction.values:
                    if value.id in done:
                        skipped[tr("已收集")] += 1
                        continue
                    reason = self._confirmed_one(document, extraction, value)
                    if reason:
                        skipped[reason] += 1
                    else:
                        created += 1
        record(self.session, self.owner, "ocr_samples:confirmed", None, None, created=created)
        self.session.flush()
        return BackfillOut(
            created=created,
            skipped=[Excluded(reason=r, count=n) for r, n in skipped.most_common()],
        )

    def _confirmed_one(
        self, document: Document, extraction: Extraction, value: ExtractedValue
    ) -> str | None:
        if value.deleted or value.origin != "model" or value.value is None:
            return tr("无值")
        if (value.current_value or "").strip() != value.value.strip():
            return tr("经人工修改（属于错例，请使用补收）")
        if document.storage_key is None or self.storage is None:
            return tr("无原始文件")
        evidence = value.evidence or {}
        if not evidence.get("bbox"):
            return tr("无出处")
        page = int(evidence.get("page") or extraction.page)
        ocr = ocr_page_json(extraction.ocr, page, extraction.page) or {}
        if ocr.get("engine", extraction.source) == "pdf-text":
            return tr("此页文字来自 PDF 文本层，而非 OCR 识别")
        template = template_of(extraction)
        field = generic_field(value.name)
        if _sensitive(template, field):
            return tr("不收集敏感字段")
        box = [float(v) for v in evidence["bbox"]]
        same = [ln for ln in ocr.get("lines", []) if _same_box(ln.get("box"), box)]
        if len(same) != 1 or not same[0].get("polygon"):
            return tr("出处不是恰好一整块原文")
        text = clean(str(same[0]["text"]))
        if text is None or not only_value(value.type, value.value, text):
            return tr("此文字块除值外还包含其他文字")
        labels = labels_of(extraction)
        sample = OcrSample(
            id=uuid.uuid4(),
            created_at=datetime.now(UTC),
            document_id=document.id,
            value_id=value.id,
            revision_id=None,
            page=page,
            unit_page=extraction.page,
            kind="line",
            polygon=[[float(x), float(y)] for x, y in same[0]["polygon"]],
            transcription=text,
            ocr_text=text,
            category="correct",
            error_tags=[],
            template_key=extraction.template[:64],
            template=_template_title(extraction, template)[:200],
            field=field[:200],
            field_label=(labels.get(field) or value.label or value.name)[:200],
            source_format="pdf" if document.content_type == "application/pdf" else "image",
            ocr_engine=(extraction.ocr_engine or "")[:128],
            origin="confirmed",
            status="pending",
            error="",
            user_id=self.owner.user_id,
            team_id=value.team_id,
        )
        _crop(self.storage, sample, document)
        self.session.add(sample)
        groups.ensure_member(
            self.session, value.team_id, self.owner.user_id, (document.id, extraction.page)
        )
        return None

    def verify_many(self, ids: Sequence[uuid.UUID]) -> VerifyOut:
        """Approve several pending samples at once (the reviewer has checked the crops on this
        page). Samples that are not pending are skipped."""
        require(self.owner, "value:review")
        verified = 0
        for sample_id in dict.fromkeys(ids):
            sample = self.session.get(OcrSample, sample_id)
            if sample is None or sample.team_id != self.owner.team_id or sample.status != "pending":
                continue
            sample.category = category_of(sample.kind, sample.ocr_text, sample.transcription)
            sample.error_tags = error_tags(sample.ocr_text, sample.transcription)
            sample.status = "ready"
            verified += 1
        if verified:
            record(self.session, self.owner, "ocr_samples:verify", None, None, count=verified)
        self.session.flush()
        return VerifyOut(verified=verified)

    def _backfill_one(self, revision: Any, value: ExtractedValue) -> str | None:
        """Backfill one sample; returns the reason if it cannot be collected."""
        evidence = revision.evidence
        if not evidence or not evidence.get("bbox"):
            return tr("手动输入的值（未点选原文或框选），无法确定在图像中的位置")
        extraction = value.extraction
        document = extraction.document
        if document.storage_key is None or self.storage is None:
            return tr("无原始文件")
        page = int(evidence.get("page") or extraction.page)
        ocr = ocr_page_json(extraction.ocr, page, extraction.page) or {}
        if ocr.get("engine", extraction.source) == "pdf-text":
            return tr("此页文字来自 PDF 文本层，而非 OCR 识别")
        template = template_of(extraction)
        field = generic_field(value.name)
        if _sensitive(template, field):
            return tr("不收集敏感字段")
        box = [float(v) for v in evidence["bbox"]]
        lines = list(ocr.get("lines", []))
        if evidence.get("drawn"):
            kind, polygon = "box", box_polygon(box)
            inside = lines_in_box(lines, box)
            ocr_text = " ".join(str(ln["text"]) for ln in inside) if inside else None
            guess = clean(str(evidence.get("text") or "")) or clean(revision.new_value or "")
        else:
            same = [ln for ln in lines if _same_box(ln.get("box"), box)]
            if len(same) == 1 and same[0].get("polygon"):
                kind = "line"
                polygon = [[float(x), float(y)] for x, y in same[0]["polygon"]]
                ocr_text = str(same[0]["text"])
            else:  # Several blocks clicked: collect by bounding box.
                kind, polygon = "box", box_polygon(box)
                ocr_text = str(evidence.get("text") or "") or None
            guess = clean(ocr_text or "") or clean(revision.new_value or "")
        if guess is None:
            return tr("没有可作为原文的文字")
        labels = labels_of(extraction)
        sample = OcrSample(
            id=uuid.uuid4(),
            created_at=datetime.now(UTC),
            document_id=document.id,
            value_id=value.id,
            revision_id=revision.id,
            page=page,
            unit_page=extraction.page,
            kind=kind,
            polygon=polygon,
            # Initial guess; the reviewer corrects it to the actual text on the image during
            # verification.
            transcription=guess,
            ocr_text=ocr_text,
            category=category_of(kind, ocr_text, guess),
            error_tags=error_tags(ocr_text, guess),
            template_key=extraction.template[:64],
            template=_template_title(extraction, template)[:200],
            field=field[:200],
            field_label=(labels.get(field) or value.label or value.name)[:200],
            source_format="pdf" if document.content_type == "application/pdf" else "image",
            ocr_engine=(extraction.ocr_engine or "")[:128],
            origin="backfill",
            status="pending",
            error="",
            user_id=self.owner.user_id,
            team_id=value.team_id,
        )
        _crop(self.storage, sample, document)
        self.session.add(sample)
        groups.ensure_member(
            self.session, value.team_id, self.owner.user_id, (document.id, extraction.page)
        )
        return None

    def retry(self, sample_id: uuid.UUID) -> SampleOut:
        """Retry cropping a sample whose crop failed."""
        require(self.owner, "value:review")
        sample = self._find(sample_id)
        if sample.status != "failed":
            raise SampleError("invalid", tr("此样本未失败，无需重试"))
        document = self.session.get(Document, sample.document_id)
        if document is None or self.storage is None:
            raise SampleError("not_found", tr("原文档不存在"))
        _crop(self.storage, sample, document)
        self.session.flush()
        if sample.status == "failed":
            raise SampleError("storage", tr("仍未裁剪成功：{error}", error=sample.error))
        return self._out([sample])[0]

    # ---- Grouping ----

    def _groups_ready(self) -> None:
        groups.ensure_sample_groups(self.session, self.owner.team_id, self.owner.user_id)

    def group_list(self) -> GroupList:
        require(self.owner, "value:read")
        self._groups_ready()
        team = self.owner.team_id
        seed = groups.split_seed(self.session, team)
        splits = groups.splits_of(self.session, team)
        members = self.session.scalars(
            select(DocGroupMember).where(DocGroupMember.team_id == team)
        ).all()
        documents = (
            {
                d.id: d
                for d in self.session.scalars(
                    select(Document).where(Document.id.in_({m.document_id for m in members}))
                )
            }
            if members
            else {}
        )
        counts = Counter(
            (s.document_id, s.unit_page)
            for s in self.session.scalars(
                select(OcrSample).where(OcrSample.team_id == team, OcrSample.status != "withdrawn")
            )
        )
        by_group: dict[uuid.UUID, list[GroupMemberOut]] = defaultdict(list)
        for m in members:
            document = documents.get(m.document_id)
            by_group[m.group_id].append(
                GroupMemberOut(
                    id=m.id,
                    document_id=m.document_id,
                    filename=document.filename if document else "",
                    first_page=m.first_page,
                    source_format="pdf"
                    if document is not None and document.content_type == "application/pdf"
                    else "image",
                    samples=counts.get((m.document_id, m.first_page), 0),
                )
            )
        items: list[GroupOut] = []
        for group_id, found in by_group.items():
            split = splits.get(group_id) or groups.ensure_split(self.session, team, group_id)
            items.append(
                GroupOut(
                    group_id=group_id,
                    code=groups.anonymous(seed, group_id),
                    split=split.split,  # type: ignore[arg-type]
                    reason=split.reason,
                    members=sorted(found, key=lambda m: (m.filename, m.first_page)),
                    samples=sum(m.samples for m in found),
                    exported_as=sorted(groups.exported_as(self.session, team, group_id)),
                )
            )
        items.sort(key=lambda g: (-len(g.members), -g.samples, g.code))
        return GroupList(
            items=items,
            split_version=groups.split_version(self.session, team),
            counts=dict(Counter(g.split for g in items)),
        )

    def change_split(self, group_id: uuid.UUID, split: str) -> GroupOut:
        require(self.owner, "system:manage")
        try:
            found = groups.set_split(self.session, self.owner.team_id, group_id, split)
        except groups.GroupError as err:
            raise SampleError(err.code, err.message) from err
        record(
            self.session, self.owner, "ocr_samples:split", "ocr_group", group_id, split=found.split
        )
        return next(g for g in self.group_list().items if g.group_id == group_id)

    def _unit_out(self, key: groups.UnitKey, members: dict[groups.Unit, uuid.UUID]) -> UnitOut:
        document = self.session.get_one(Document, key.unit[0])
        extraction = next((e for e in document.extractions if e.page == key.unit[1]), None)
        template = template_of(extraction) if extraction is not None else None
        samples = self.session.scalar(
            select(func.count()).where(
                OcrSample.document_id == key.unit[0],
                OcrSample.unit_page == key.unit[1],
                OcrSample.status != "withdrawn",
            )
        )
        labels = labels_of(extraction, language()) if extraction is not None else {}
        return UnitOut(
            document_id=document.id,
            filename=document.filename,
            page=key.unit[1],
            last_page=(extraction.last_page or extraction.page) if extraction else key.unit[1],
            template=_template_title(extraction, template) if extraction is not None else "",
            values={labels.get(k, k): v for k, v in key.shown.items()},
            group_id=members.get(key.unit),
            samples=int(samples or 0),
        )

    def pairs(self) -> list[PairOut]:
        """Pairs that may be the same document (PDF and photo), awaiting confirmation."""
        require(self.owner, "value:read")
        self._groups_ready()
        members = groups.groups_of(self.session, self.owner.team_id)
        return [
            PairOut(a=self._unit_out(a, members), b=self._unit_out(b, members))
            for a, b in groups.nominations(self.session, self.owner.team_id)
        ]

    def units(self, q: str) -> list[UnitOut]:
        """Find business documents by file name (for manual pairing)."""
        require(self.owner, "value:read")
        documents = self.session.scalars(
            select(Document)
            .where(
                Document.team_id == self.owner.team_id,
                Document.status.in_(("done", "reviewed")),
                Document.filename.ilike(f"%{q.strip()}%"),
            )
            .order_by(Document.created_at.desc())
            .limit(20)
        ).all()
        members = groups.groups_of(self.session, self.owner.team_id)
        return [
            self._unit_out(groups.unit_key(e), members) for d in documents for e in d.extractions
        ]

    def decide_pair(self, change: PairIn) -> PairResult:
        """Record a person's decision on whether two business documents are the same. Same: merge
        into one group; different: record it and stop proposing the pair."""
        require(self.owner, "value:review")
        a = (change.a.document_id, change.a.page)
        b = (change.b.document_id, change.b.page)
        if a == b:
            raise SampleError("invalid", tr("两侧为同一单据"))
        for unit in (a, b):
            document = self.session.get(Document, unit[0])
            if document is None or document.team_id != self.owner.team_id:
                raise SampleError("not_found", tr("单据不存在"))
            if not any(e.page == unit[1] for e in document.extractions):
                raise SampleError(
                    "invalid",
                    tr(
                        "{filename} 中没有从第 {page} 页开始的单据",
                        filename=document.filename,
                        page=unit[1],
                    ),
                )
        team, user = self.owner.team_id, self.owner.user_id
        self.session.add(
            PairDecision(
                a_document=a[0],
                a_page=a[1],
                b_document=b[0],
                b_page=b[1],
                decision=change.decision,
                user_id=user,
                team_id=team,
            )
        )
        if change.decision == "different":
            record(self.session, self.owner, "ocr_samples:pair", None, None, decision="different")
            return PairResult(
                group_id=None,
                split=None,
                quarantined=False,
                affected_exports=0,
                message=tr("已记录：不是同一张单据，不再推荐"),
            )
        result = groups.merge(self.session, team, user, a, b)
        record(
            self.session,
            self.owner,
            "ocr_samples:pair",
            "ocr_group",
            result.group_id,
            decision="same",
            split=result.split,
            affected_exports=result.affected_exports,
        )
        message = (
            tr(
                (
                    "已合并。两个分组此前已导出且位于不同划分，整组已隔离，{affec"
                    "ted_exports} 次导出受影响"
                ),
                affected_exports=result.affected_exports,
            )
            if result.quarantined and result.affected_exports
            else tr("已合并为一组（{split}）", split=tr(groups.SPLIT_NAMES[result.split]))
        )
        return PairResult(
            group_id=result.group_id,
            split=result.split,  # type: ignore[arg-type]
            quarantined=result.quarantined,
            affected_exports=result.affected_exports,
            message=message,
        )

    def detach(self, member_id: uuid.UUID) -> PairResult:
        require(self.owner, "value:review")
        try:
            result = groups.detach(self.session, self.owner.team_id, member_id)
        except groups.GroupError as err:
            raise SampleError(err.code, err.message) from err
        member = self.session.get_one(DocGroupMember, member_id)
        record(
            self.session,
            self.owner,
            "ocr_samples:detach",
            "ocr_group",
            result.group_id,
            document_id=str(member.document_id),
            affected_exports=result.affected_exports,
        )
        return PairResult(
            group_id=result.group_id,
            split=result.split,  # type: ignore[arg-type]
            quarantined=result.quarantined,
            affected_exports=result.affected_exports,
            message=tr(
                ("已拆分。原分组已导出，两部分均已隔离，{affected_exports} 次导出受影响"),
                affected_exports=result.affected_exports,
            )
            if result.quarantined
            else tr("已拆分"),
        )

    def export_records(self) -> list[ExportRecordOut]:
        require(self.owner, "document:export")
        rows = self.session.scalars(
            select(OcrSampleExport)
            .where(OcrSampleExport.team_id == self.owner.team_id)
            .order_by(OcrSampleExport.created_at.desc())
            .limit(100)
        ).all()
        return [
            ExportRecordOut(
                id=r.id,
                created_at=r.created_at,
                kind=r.kind,  # type: ignore[arg-type]
                split_version=r.split_version,
                counts=r.counts,
                filters=r.filters,
                affected=list(r.affected or []),
            )
            for r in rows
        ]

    # ---- Export ----

    def _selection(
        self,
        category: Sequence[str] | None,
        source_format: str | None,
        template: str | None,
        kind: str = "train_val",
    ) -> tuple[list[tuple[OcrSample, uuid.UUID, str]], Counter[str]]:
        """Samples to export (sample, group, split) and excluded counts by reason."""
        self._groups_ready()
        team = self.owner.team_id
        members = groups.groups_of(self.session, team)
        splits = groups.splits_of(self.session, team)
        samples = list(
            self.session.scalars(
                self._query(category, None, source_format, template).order_by(
                    OcrSample.created_at, OcrSample.id
                )
            )
        )
        excluded: Counter[str] = Counter()
        usable: list[tuple[OcrSample, uuid.UUID, str]] = []
        for sample in samples:
            group = members[(sample.document_id, sample.unit_page)]
            split = (splits.get(group) or groups.ensure_split(self.session, team, group)).split
            if sample.status == "withdrawn":
                excluded[tr("已撤回")] += 1
            elif sample.status == "pending":
                excluded[tr("补收样本，尚未复核")] += 1
            elif sample.status != "ready" or not sample.crop_key or not sample.crop_sha256:
                excluded[tr("裁剪失败，无图像")] += 1
            elif clean(sample.transcription) != sample.transcription:
                excluded[tr("图中原文为空或包含 Tab、换行")] += 1
            elif split == "quarantined":
                excluded[tr("所在分组已被隔离（合并或拆分时发现已导出）")] += 1
            elif kind == "train_val" and split == "test_locked":
                excluded[tr("锁定测试集（单独导出）")] += 1
            elif kind == "test" and split != "test_locked":
                # When exporting the test set, training and validation samples do not count as
                # excluded.
                continue
            else:
                usable.append((sample, group, split))
        by_image: dict[str, list[tuple[OcrSample, uuid.UUID, str]]] = defaultdict(list)
        for entry in usable:
            by_image[str(entry[0].crop_sha256)].append(entry)
        kept: list[tuple[OcrSample, uuid.UUID, str]] = []
        for same in by_image.values():
            if len({s.transcription for s, _, _ in same}) > 1:
                excluded[tr("同一图像存在两种转写")] += len(same)
                continue
            kept.append(same[0])
            if len(same) > 1:
                excluded[tr("同一图像重复，仅保留一份")] += len(same) - 1
        kept.sort(key=lambda e: (e[0].created_at, str(e[0].id)))
        return kept, excluded

    def _summary(self, kept: list[tuple[OcrSample, uuid.UUID, str]], kind: str) -> dict[str, Any]:
        names = ("test_locked",) if kind == "test" else ("train", "val")
        splits = {
            ("test" if n == "test_locked" else n): SplitCount(
                groups=len({g for _, g, s in kept if s == n}),
                rows=sum(1 for _, _, s in kept if s == n),
            )
            for n in names
        }
        val = splits.get("val")
        evidence = None
        if kind == "train_val":
            evidence = _evidence(val.groups if val else 0, val.rows if val else 0)
        val_groups: dict[str, set[uuid.UUID]] = defaultdict(set)
        for sample, group, split in kept:
            if split == "val":
                val_groups[sample.category].add(group)
        names = display_names(self.session, [s for s, _, _ in kept])
        titles = {_template_key(s): names[s.id][0] for s, _, _ in kept}
        distributions: list[Distribution] = []
        for by in ("template", "category", "source_format"):
            table: dict[str, Counter[str]] = defaultdict(Counter)
            for sample, _, split in kept:
                key = _template_key(sample) if by == "template" else str(getattr(sample, by))
                table[key]["test" if split == "test_locked" else split] += 1
            distributions += [
                Distribution(
                    by=by,  # type: ignore[arg-type]
                    key=key,
                    label=titles.get(key, "") if by == "template" else "",
                    **{k: v for k, v in c.items()},
                )
                for key, c in sorted(table.items())
            ]
        return {
            "splits": splits,
            "evidence": evidence,
            "val_category_groups": {k: len(v) for k, v in val_groups.items()},
            "distributions": distributions,
        }

    def preview(
        self,
        category: Sequence[str] | None = None,
        source_format: str | None = None,
        template: str | None = None,
        kind: str = "train_val",
    ) -> ExportPreview:
        require(self.owner, "document:export")
        kept, excluded = self._selection(category, source_format, template, kind)
        return ExportPreview(
            kind=kind,  # type: ignore[arg-type]
            included=len(kept),
            paddle_minimum=PADDLE_MINIMUM,
            categories=dict(Counter(s.category for s, _, _ in kept)),
            excluded=[Excluded(reason=r, count=n) for r, n in excluded.most_common()],
            split_version=groups.split_version(self.session, self.owner.team_id),
            **self._summary(kept, kind),
        )

    def export(
        self,
        category: Sequence[str] | None = None,
        source_format: str | None = None,
        template: str | None = None,
        kind: str = "train_val",
    ) -> tuple[str, bytes]:
        """Samples → zip with crop_img/, rec_gt_train.txt, rec_gt_val.txt, rec_gt.txt (or
        rec_gt_test.txt for the locked test set), manifest.jsonl and README.md. Each export
        writes an export record."""
        require(self.owner, "document:export")
        if self.storage is None:
            raise SampleError("storage", tr("对象存储暂时不可用，请稍后再试"))
        kept, excluded = self._selection(category, source_format, template, kind)
        if not kept:
            raise SampleError("invalid", tr("没有可导出的样本"))
        team = self.owner.team_id
        seed = groups.split_seed(self.session, team)
        version = groups.split_version(self.session, team)
        lists: dict[str, list[str]] = defaultdict(list)
        manifest: list[str] = []
        items: list[dict[str, Any]] = []
        exported: list[tuple[OcrSample, uuid.UUID, str]] = []
        # Names follow the reader's language; template_id and field identify the same template and
        # field in every export, whatever language it was made in.
        names = display_names(self.session, [s for s, _, _ in kept])
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
            for sample, group, split in kept:
                try:
                    png = self.storage.get(str(sample.crop_key))
                    with Image.open(io.BytesIO(png)) as picture:
                        picture.verify()  # The image decodes.
                except Exception:  # Image missing or corrupt in storage: skip this sample.
                    excluded[tr("图片不存在或无法打开")] += 1
                    continue
                number = len(exported) + 1
                name = f"crop_img/s_{number:04d}.png"
                part = "test" if split == "test_locked" else split
                archive.writestr(name, png)
                lists[part].append(f"{name}\t{sample.transcription}\n")
                manifest.append(
                    json.dumps(
                        {
                            "sample_id": f"s_{number:04d}",
                            "image": name,
                            "transcription": sample.transcription,
                            "ocr_text": sample.ocr_text,
                            "category": sample.category,
                            "error_tags": list(sample.error_tags or []),
                            "template_id": _template_key(sample),
                            "template": names[sample.id][0],
                            "field": sample.field,
                            "field_label": names[sample.id][1],
                            "document_group": groups.anonymous(seed, group),
                            "split": part,
                            "split_version": version,
                            "sample_revision": sample.revision,
                            "source_format": sample.source_format,
                            "ocr_model_version": sample.ocr_engine,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                items.append(
                    {
                        "sample": str(sample.id),
                        "revision": sample.revision,
                        "group": str(group),
                        "split": split,
                    }
                )
                exported.append((sample, group, split))
            if not exported:
                raise SampleError("storage", tr("样本图像均不存在"))
            for part in ("train", "val", "test"):
                if part in lists:
                    archive.writestr(f"rec_gt_{part}.txt", "".join(lists[part]).encode("utf-8"))
            if kind == "train_val":
                everything = lists.get("train", []) + lists.get("val", [])
                archive.writestr("rec_gt.txt", "".join(everything).encode("utf-8"))
            archive.writestr("manifest.jsonl", "".join(manifest).encode("utf-8"))
            texts, hard = corpus([s for s, _, _ in exported])
            archive.writestr(
                "corpus/real_text.txt", "".join(f"{t}\n" for t in texts).encode("utf-8")
            )
            archive.writestr(
                "corpus/hard_chars.txt",
                "".join(f"{c}\t{n}\n" for c, n in hard.most_common()).encode("utf-8"),
            )
            summary = self._summary(exported, kind)
            archive.writestr(
                "README.md",
                _readme([s for s, _, _ in exported], excluded, summary, kind, version).encode(
                    "utf-8"
                ),
            )
        counts = {k: v.model_dump() for k, v in summary["splits"].items()}
        filters = {
            "category": sorted(set(category or [])),
            "source_format": source_format,
            "template": template,
        }
        export_record = OcrSampleExport(
            kind=kind,
            split_version=version,
            filters=filters,
            counts=counts,
            items=items,
            affected=[],
            user_id=self.owner.user_id,
            team_id=team,
        )
        self.session.add(export_record)
        self.session.flush()
        self.last_export_id = export_record.id
        record(
            self.session,
            self.owner,
            "ocr_samples:export",
            None,
            None,
            count=len(exported),
            kind=kind,
            split_version=version,
            **filters,
        )
        stamp = datetime.now().strftime("%Y%m%d-%H%M")
        name = (
            tr("OCR样本测试集-{stamp}.zip", stamp=stamp)
            if kind == "test"
            else tr("OCR样本-{stamp}.zip", stamp=stamp)
        )
        return name, out.getvalue()

    def export_to_dataset(
        self,
        name: str,
        description: str,
        category: Sequence[str] | None = None,
        source_format: str | None = None,
        template: str | None = None,
        kind: str = "train_val",
    ) -> Any:
        """Export (writing an export record as usual) and store the result directly in the dataset
        library, without downloading and uploading."""
        from jacara.core.config import get_settings
        from jacara.modules.hub.registry import HubError
        from jacara.modules.hub.service import HubService

        require(self.owner, "value:review")
        _, data = self.export(category, source_format, template, kind)
        hub = HubService(self.session, self.owner, get_settings(), self.storage)
        try:
            dataset = hub.save_dataset(
                name, "ocr_rec", "samples", description, data, self.last_export_id
            )
        except HubError as err:
            raise SampleError(err.code, err.message) from err
        return hub.dataset_out(dataset)


def _evidence(groups_: int, rows: int) -> Evidence:
    """Validation set sufficiency (starting thresholds from an external review)."""
    if groups_ == 0:
        return Evidence(level="none", message=tr("本次无验证集（分组过少，或全部分配至训练集）"))
    where = tr("验证集 {groups} 组、{rows} 行", groups=groups_, rows=rows)
    if groups_ < 10 or rows < 50:
        return Evidence(
            level="process", message=tr("{where}：仅可验证流程，不能反映模型优劣", where=where)
        )
    if groups_ >= 15 and rows >= 100:
        return Evidence(level="trend", message=tr("{where}：可参考整体趋势", where=where))
    return Evidence(level="thin", message=tr("{where}：数量偏少，趋势仅供参考", where=where))


def _readme(
    samples: Sequence[OcrSample],
    excluded: Counter[str],
    summary: dict[str, Any],
    kind: str,
    version: int,
) -> str:
    categories = Counter(s.category for s in samples)
    tags = Counter(t for s in samples for t in (s.error_tags or []))
    splits: dict[str, SplitCount] = summary["splits"]
    names = {"train": tr("训练集"), "val": tr("验证集"), "test": tr("锁定测试集")}
    lines = [
        tr("# JacarandaParse OCR 样本（锁定测试集）")
        if kind == "test"
        else tr("# JacarandaParse OCR 样本"),
        "",
        tr(
            "格式版本 {format_version}，划分版本 {version}，导出时间 {time:%Y-%m-%d %H:%M}。",
            format_version=FORMAT_VERSION,
            version=version,
            time=datetime.now(),
        ),
        tr("共 {n} 条。", n=len(samples)),
        "",
        tr("以上为业务数据，请勿提交到代码仓库或上传至公开位置。"),
        "",
        tr("## 划分"),
        "",
        *(
            tr(
                ("- {name}：{groups} 组（独立业务单据）、{rows} 行（`rec_gt_{split}.txt`）"),
                name=names[k],
                groups=v.groups,
                rows=v.rows,
                split=k,
            )
            for k, v in splits.items()
        ),
    ]
    evidence: Evidence | None = summary["evidence"]
    if evidence is not None:
        lines += ["", f"**{evidence.message}**"]
    if kind == "train_val":
        lines += [
            "",
            tr(
                "按单据分组：同一单据（包括各轮重新处理、PDF 与照片、重复上传"
                "的同一文件）的样本位于同一划分，多次导出保持不变（`document_"
                "group` 为稳定组号，不含业务信息）。80/20 为按组计算的目标比"
                "例，组数较少时行数比例会有偏差。`rec_gt.txt` 为训练集与验证"
                "集的合集。锁定测试集不包含在内，需单独导出。"
            ),
        ]
    lines += [
        "",
        tr("## 文件"),
        "",
        tr("- `crop_img/`：每条样本一张单行图像，文件名仅为编号。"),
        tr("- `rec_gt_*.txt`：每行一条，格式为图片路径、Tab、与图像一致的文字（UTF-8，无 BOM）。"),
        tr(
            "- `manifest.jsonl`：每行一条样本的详细信息：当时的 OCR 结果"
            "（`ocr_text`）、分类、错误标签、模板（`template_id`、`templa"
            "te`）、字段（`field`、`field_label`）、组号、所属划分（`spli"
            "t`）、划分版本、标签修订号（`sample_revision`）、来源及首轮 "
            "OCR 版本。"
        ),
        "",
        tr("## 分类"),
        "",
        *(
            tr(
                "- {name}（`{category}`）：{n} 条",
                name=tr(CATEGORIES[c]),
                category=c,
                n=categories.get(c, 0),
            )
            for c in CATEGORIES
        ),
        "",
        tr(
            "识别错误和漏识别属于错例；识别正确的行是审核时核对过、OCR 原"
            "本就识别正确的行。微调时应将错例与识别正确的行一同使用，仅使"
            "用错例会导致模型遗忘原本识别正确的字符。原因未确定的样本：图"
            "像与首轮识别时不完全一致，请查看后再决定是否使用。"
        ),
        "",
    ]
    if tags:
        lines += [
            tr(
                "错误标签：{tags}",
                tags=tr("，").join(
                    f"{tr(TAGS[t])} {n}" for t, n in tags.most_common() if t in TAGS
                ),
            ),
            "",
        ]
    distributions: list[Distribution] = summary["distributions"]
    if distributions:
        titles = {"template": tr("模板"), "category": tr("分类"), "source_format": tr("来源")}
        lines += [tr("## 分布（仅供参考，不为平衡比例调整分组）"), ""]
        for by, title in titles.items():
            rows = [d for d in distributions if d.by == by]
            if rows:
                lines.append(
                    tr(
                        "- {title}：{items}",
                        title=title,
                        items=tr("；").join(_dist(d, kind) for d in rows),
                    )
                )
        lines.append("")
    if excluded:
        lines += [
            tr("## 未导出"),
            "",
            *(tr("- {reason}：{n} 条", reason=reason, n=n) for reason, n in excluded.most_common()),
            "",
        ]
    lines += [
        tr("## 在 PaddleOCR 里训练识别模型"),
        "",
        tr("1. 解压至一个目录，例如 `/data/jacara_ocr`。"),
        tr(
            "2. 在识别训练配置中：将 `Train.dataset.data_dir` 和 `Eval.da"
            "taset.data_dir` 设为该目录，`Train.dataset.label_file_list` "
            "设为 `rec_gt_train.txt`，`Eval.dataset.label_file_list` 设为"
            " `rec_gt_val.txt`（`SimpleDataSet` 可直接读取）。"
        ),
        tr(
            "3. 使用与系统当前识别模型同系列的配置、字典和预训练权重进行"
            "微调；字典中不存在的字符在训练时会被跳过，建议事先检查。"
        ),
        tr(
            "4. 训练完成后导出推理模型并转换为 ONNX，在“模型和数据集”页导"
            "入（种类选择“OCR 文字识别”），再在“设置 → 模型”中选择，执行 "
            "jacara stop 和 jacara start 后生效。"
        ),
        "",
        tr(
            "反复依据验证集结果调整训练，会使结果逐渐过拟合验证集；正式验"
            "收请使用锁定测试集或新进单据。"
        ),
        "",
        tr("## 数量和配比（PaddleOCR 微调文档的建议）"),
        "",
        tr(
            (
                "- 不修改字典时，识别数据建议至少 {minimum} 行；本次 {n} 行，"
                "数量不足，请继续积累或按下文合成补充。"
            ),
            minimum=PADDLE_MINIMUM,
            n=len(samples),
        )
        if len(samples) < PADDLE_MINIMUM
        else tr(
            "- 不修改字典时，识别数据建议至少 {minimum} 行；本次 {n} 行。",
            minimum=PADDLE_MINIMUM,
            n=len(samples),
        ),
        tr(
            "- 场景数据与通用真实数据（如 LSVT、RCTW、MTWI）每轮约按 1:1 "
            "混合训练，可通过 `Train.dataset.ratio_list` 调整（如场景 1.0"
            "、通用 0.1），以免模型只适应此类单据。"
        ),
        tr(
            "- 某些字符反复识别错误时，可使用 TextRenderer 等工具合成补充"
            "：语料使用 `corpus/real_text.txt`（与图像一致的文字），重点"
            "合成 `corpus/hard_chars.txt` 中的字符；真实数据与合成数据比"
            "例为 10:1 至 5:1，并调低学习率。"
        ),
        tr("- 训练得到的模型需先通过回放（jacara eval replay）和锁定测试集对比验证，再启用。"),
        "",
    ]
    return "\n".join(lines)


PADDLE_MINIMUM = 5000
"""Recommended minimum number of lines for fine-tuning a PaddleOCR recognition model (without
changing the dictionary)."""


def corpus(samples: Sequence[OcrSample]) -> tuple[list[str], Counter[str]]:
    """Real-world corpus for synthetic data: transcriptions exactly as on the images (deduplicated),
    and the characters misread in misread samples (by frequency). Intended for tools such as
    TextRenderer to synthesize realistic data; the system itself does no synthesis or training."""
    from difflib import SequenceMatcher

    texts = sorted({s.transcription for s in samples if s.transcription})
    hard: Counter[str] = Counter()
    for s in samples:
        if s.category != "rec_error" or not s.ocr_text:
            continue
        matcher = SequenceMatcher(None, s.ocr_text, s.transcription, autojunk=False)
        for op, _, _, j1, j2 in matcher.get_opcodes():
            if op in ("replace", "insert"):
                hard.update(ch for ch in s.transcription[j1:j2] if not ch.isspace())
    return texts, hard


def _dist(d: Distribution, kind: str) -> str:
    key = (
        d.label
        or {"pdf": "PDF", "image": tr("照片")}.get(d.key)
        or tr(CATEGORIES.get(d.key, d.key))
    )
    if kind == "test":
        return f"{key} {d.test}"
    return tr("{key} 训练 {train} / 验证 {val}", key=key, train=d.train, val=d.val)


def only_value(type_: str, value: str, text: str) -> bool:
    """Whether this source block contains only this value: for numbers, at most a currency symbol
    (up to 3 characters) in addition; for dates, only digits and separators; for text, exactly
    the value (ignoring whitespace, case and full-width/half-width forms)."""
    import re

    from jacara.pipeline.fieldcheck import supports

    if type_ == "number":
        rest = re.sub(r"[\d\s.,'\-+()]", "", text)
        return len(rest) <= 3 and supports("number", value, text)
    if type_ == "date":
        return bool(re.fullmatch(r"[\d\s/\-.年月日:]+", text)) and supports("date", value, text)
    return _loose(text) == _loose(value)


def _same_box(a: Any, b: Sequence[float]) -> bool:
    """Whether two boxes are the same (when a source block is clicked, the evidence box is that
    block's box)."""
    if not a or len(a) != 4:
        return False
    return all(abs(float(x) - float(y)) <= 1.0 for x, y in zip(a, b, strict=True))
