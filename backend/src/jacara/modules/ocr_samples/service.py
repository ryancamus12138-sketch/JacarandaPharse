# ruff: noqa: E501
"""OCR 纠错样本（ADR 0050）：审核时收集、样本页管理、导出成 PaddleOCR 识别训练数据。"""

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
from sqlalchemy import func, select
from sqlalchemy.orm import Session

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
    UnitOut,
    VerifyOut,
)
from jacara.pipeline.current import labels_of, template_of
from jacara.pipeline.pages import ocr_page_json
from jacara.plugins.extractors.base import DISCOVER, split_cell_name
from jacara.templates import Template

log = logging.getLogger(__name__)

SETTING = "collect_ocr_samples"
FORMAT_VERSION = 1
"""导出格式的版本号，写进 README。"""

PADDING = 0.3
"""框选的样本四周多留多少（和"重新识别这块"一样，见 pipeline/region.py）。"""


class SampleError(Exception):
    """业务错误。code 让 router 知道该返回哪种 HTTP 状态。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ---- 开关 ----


def collecting(session: Session, team_id: uuid.UUID) -> bool:
    """这个团队开没开"自动收集纠错样本"。默认关。"""
    setting = session.get(TeamSetting, (team_id, SETTING))
    return bool(setting.value) if setting is not None else False


# ---- 审核时收集 ----


def generic_field(name: str) -> str:
    """表格的格子去掉行号：items.2.amount → items.amount。"""
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
    if extraction.template == DISCOVER:
        return "抽取所有键值对"
    return template.title if template else extraction.template


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
    """保存审核的同时收集一条样本。不该收集的（开关关着、敏感字段、PDF 自带的文字……）返回 None。

    裁图出错不影响审核：样本记成"裁图失败"，样本页可以重试。
    """
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
        return None  # 这一页的字是 PDF 自带的，不是 OCR 认的
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
        created_at=datetime.now(UTC),  # 精确到微秒：同一秒收集的几条也能排出先后
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
    # 分组（ADR 0052）：这张业务单据第一次有样本时分好组、分好边
    groups.ensure_member(session, value.team_id, owner.user_id, (document.id, extraction.page))
    return sample


def _crop(storage: ObjectStorage, sample: OcrSample, document: Document) -> None:
    """裁出小图存进对象存储。出错记在样本上（status=failed），不往外抛。"""
    from jacara.pipeline.region import crop_region
    from jacara.plugins.ocr.loader import page_png

    try:
        if document.storage_key is None:
            raise ValueError("这个文档没有保存原始文件")
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
    except Exception as err:  # 裁图只是顺带：文件读不了、框不对、存储连不上，都不影响审核
        log.warning("OCR 样本裁图失败：%s 第 %s 页", document.filename, sample.page, exc_info=True)
        sample.status = "failed"
        sample.error = _reason(err)
        return
    sample.crop_key = key
    sample.crop_sha256 = sha256(png)
    sample.width, sample.height = image.size
    # 补收的（ADR 0053）、自动收的认对的行（ADR 0055）要人复核过才能用
    sample.status = "pending" if sample.origin in ("backfill", "confirmed") else "ready"
    sample.error = ""


def _reason(err: Exception) -> str:
    if isinstance(err, StorageError):
        return "对象存储暂时不可用"
    return str(err) or type(err).__name__


# ---- 样本页 ----


class OcrSampleService:
    def __init__(
        self, session: Session, owner: Owner, storage: ObjectStorage | None = None
    ) -> None:
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
            query = query.where(OcrSample.template == template)
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

    def templates(self) -> list[str]:
        """有样本的模板名，筛选用。"""
        require(self.owner, "value:read")
        rows = self.session.scalars(
            select(OcrSample.template)
            .where(OcrSample.team_id == self.owner.team_id)
            .distinct()
            .order_by(OcrSample.template)
        )
        return [r for r in rows if r]

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
                template=s.template,
                field=s.field,
                field_label=s.field_label,
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
            raise SampleError("not_found", "找不到这条样本")
        return sample

    def image(self, sample_id: uuid.UUID) -> bytes:
        require(self.owner, "value:read")
        sample = self._find(sample_id)
        if sample.crop_key is None or self.storage is None:
            raise SampleError("not_found", "这条样本没有裁图（裁图失败了，可以重试）")
        try:
            return self.storage.get(sample.crop_key)
        except StorageError as err:
            raise SampleError("storage", "对象存储暂时不可用，请稍后再试") from err

    def update(self, sample_id: uuid.UUID, change: SampleIn) -> SampleOut:
        require(self.owner, "value:review")
        sample = self._find(sample_id)
        detail: dict[str, Any] = {}
        if change.transcription is not None:
            truth = clean(change.transcription)
            if truth is None:
                raise SampleError("invalid", "图中原文不能是空的，也不能有 Tab、换行")
            if truth != sample.transcription:
                detail["transcription"] = [sample.transcription, truth]
                sample.transcription = truth
                sample.revision += 1  # 标签改了一版：导出清单记着用的是哪一版
                sample.error_tags = error_tags(sample.ocr_text, truth)
                if change.category is None and sample.kind == "line":
                    sample.category = category_of(sample.kind, sample.ocr_text, truth)
        if change.category is not None and change.category != sample.category:
            detail["category"] = [sample.category, change.category]
            sample.category = change.category
        verified = False
        if change.status is not None and change.status != sample.status:
            if sample.status == "failed":
                raise SampleError("invalid", "裁图失败的样本先重试")
            verified = sample.status == "pending" and change.status == "ready"
            if verified and change.category is None:
                # 复核通过：按人确认的原文重新分类
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
        """从以前的修改补收样本（ADR 0053）：审核时点了原文或框了一块、改了值的，裁出小图，
        进"待复核"。人对着小图确认（或改正）图里的原文后才能导出。

        手打的（没点原文、没框）不收：不知道值在图上哪里。同一次修改只收一条（再点一次不会重复）。
        """
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
                skipped["已经有样本了"] += 1
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
        """收"认对的行"（ADR 0055，A3）：审核完成的单据里，人没改过的值，出处正好是一整块原文、
        这一块只有这个值（不带标签等别的字）——OCR 当时读对了。裁成小图进"待复核"，人过目才能导出。

        PaddleOCR 微调建议至少 5000 行，只有错例不够，而且只学错例会把认得好的字"忘掉"。
        """
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
                        skipped["已经收过了"] += 1
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
            return "没有值"
        if (value.current_value or "").strip() != value.value.strip():
            return "人改过（这是错例，用补收）"
        if document.storage_key is None or self.storage is None:
            return "没有原始文件"
        evidence = value.evidence or {}
        if not evidence.get("bbox"):
            return "没有出处"
        page = int(evidence.get("page") or extraction.page)
        ocr = ocr_page_json(extraction.ocr, page, extraction.page) or {}
        if ocr.get("engine", extraction.source) == "pdf-text":
            return "这一页的字是 PDF 自带的，不是 OCR 认的"
        template = template_of(extraction)
        field = generic_field(value.name)
        if _sensitive(template, field):
            return "敏感字段不收"
        box = [float(v) for v in evidence["bbox"]]
        same = [ln for ln in ocr.get("lines", []) if _same_box(ln.get("box"), box)]
        if len(same) != 1 or not same[0].get("polygon"):
            return "出处不是正好一整块原文"
        text = clean(str(same[0]["text"]))
        if text is None or not only_value(value.type, value.value, text):
            return "这一块里除了值还有别的字"
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
        """一次复核通过几条待复核的（人在样本页看过这一页的小图）。不是待复核的跳过。"""
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
        """补收一条；收不了返回原因。"""
        evidence = revision.evidence
        if not evidence or not evidence.get("bbox"):
            return "手打的值（没点原文、没框），不知道在图上哪里"
        extraction = value.extraction
        document = extraction.document
        if document.storage_key is None or self.storage is None:
            return "没有原始文件"
        page = int(evidence.get("page") or extraction.page)
        ocr = ocr_page_json(extraction.ocr, page, extraction.page) or {}
        if ocr.get("engine", extraction.source) == "pdf-text":
            return "这一页的字是 PDF 自带的，不是 OCR 认的"
        template = template_of(extraction)
        field = generic_field(value.name)
        if _sensitive(template, field):
            return "敏感字段不收"
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
            else:  # 点了好几块：按框收
                kind, polygon = "box", box_polygon(box)
                ocr_text = str(evidence.get("text") or "") or None
            guess = clean(ocr_text or "") or clean(revision.new_value or "")
        if guess is None:
            return "没有能当原文的文字"
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
            transcription=guess,  # 先猜一个，复核时人改成图里真正的原文
            ocr_text=ocr_text,
            category=category_of(kind, ocr_text, guess),
            error_tags=error_tags(ocr_text, guess),
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
        """裁图失败的重新裁一次。"""
        require(self.owner, "value:review")
        sample = self._find(sample_id)
        if sample.status != "failed":
            raise SampleError("invalid", "这条样本没有失败，不用重试")
        document = self.session.get(Document, sample.document_id)
        if document is None or self.storage is None:
            raise SampleError("not_found", "找不到原来的文档")
        _crop(self.storage, sample, document)
        self.session.flush()
        if sample.status == "failed":
            raise SampleError("storage", f"还是没裁出来：{sample.error}")
        return self._out([sample])[0]

    # ---- 分组（ADR 0052） ----

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
        labels = labels_of(extraction) if extraction is not None else {}
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
        """可能是同一张单据（PDF 和照片）、等人确认的。"""
        require(self.owner, "value:read")
        self._groups_ready()
        members = groups.groups_of(self.session, self.owner.team_id)
        return [
            PairOut(a=self._unit_out(a, members), b=self._unit_out(b, members))
            for a, b in groups.nominations(self.session, self.owner.team_id)
        ]

    def units(self, q: str) -> list[UnitOut]:
        """按文件名找业务单据（手动配对时用）。"""
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
        """人确认两张业务单据是不是同一张。是：合并成一组；不是：记下来，不再提名。"""
        require(self.owner, "value:review")
        a = (change.a.document_id, change.a.page)
        b = (change.b.document_id, change.b.page)
        if a == b:
            raise SampleError("invalid", "两边是同一张单据")
        for unit in (a, b):
            document = self.session.get(Document, unit[0])
            if document is None or document.team_id != self.owner.team_id:
                raise SampleError("not_found", "找不到这张单据")
            if not any(e.page == unit[1] for e in document.extractions):
                raise SampleError("invalid", f"{document.filename} 没有从第 {unit[1]} 页开始的单据")
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
                message="已记下：不是同一张，不再提名",
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
            f"已合并。两组以前导出过、分在不同边，整组隔离，{result.affected_exports} 次导出受影响"
            if result.quarantined and result.affected_exports
            else f"已合并成一组（{groups.SPLIT_NAMES[result.split]}）"
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
            message="已拆开"
            + (
                f"。原来的组导出过，两边都隔离，{result.affected_exports} 次导出受影响"
                if result.quarantined
                else ""
            ),
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

    # ---- 导出 ----

    def _selection(
        self,
        category: Sequence[str] | None,
        source_format: str | None,
        template: str | None,
        kind: str = "train_val",
    ) -> tuple[list[tuple[OcrSample, uuid.UUID, str]], Counter[str]]:
        """要导出的样本（样本、组、划分），和不导出的原因（原因 → 几条）。"""
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
                excluded["撤回了"] += 1
            elif sample.status == "pending":
                excluded["补收的，还没复核"] += 1
            elif sample.status != "ready" or not sample.crop_key or not sample.crop_sha256:
                excluded["裁图失败，没有图"] += 1
            elif clean(sample.transcription) != sample.transcription:
                excluded["图中原文是空的或有 Tab、换行"] += 1
            elif split == "quarantined":
                excluded["所在的组被隔离了（合并或拆开时发现已经导出过）"] += 1
            elif kind == "train_val" and split == "test_locked":
                excluded["锁定测试集（单独导出）"] += 1
            elif kind == "test" and split != "test_locked":
                continue  # 导出测试集时，训练、验证的不算"排除"
            else:
                usable.append((sample, group, split))
        by_image: dict[str, list[tuple[OcrSample, uuid.UUID, str]]] = defaultdict(list)
        for entry in usable:
            by_image[str(entry[0].crop_sha256)].append(entry)
        kept: list[tuple[OcrSample, uuid.UUID, str]] = []
        for same in by_image.values():
            if len({s.transcription for s, _, _ in same}) > 1:
                excluded["同一张图有两种写法"] += len(same)
                continue
            kept.append(same[0])
            if len(same) > 1:
                excluded["同一张图重复，只留一份"] += len(same) - 1
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
        distributions: list[Distribution] = []
        for by in ("template", "category", "source_format"):
            table: dict[str, Counter[str]] = defaultdict(Counter)
            for sample, _, split in kept:
                key = str(getattr(sample, by))
                table[key]["test" if split == "test_locked" else split] += 1
            distributions += [
                Distribution(by=by, key=key, **{k: v for k, v in c.items()})  # type: ignore[arg-type]
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
        """样本 → zip：crop_img/、rec_gt_train.txt、rec_gt_val.txt、rec_gt.txt（或锁定测试集的
        rec_gt_test.txt）、manifest.jsonl、README.md。每次导出记一条导出记录。"""
        require(self.owner, "document:export")
        if self.storage is None:
            raise SampleError("storage", "对象存储暂时不可用，请稍后再试")
        kept, excluded = self._selection(category, source_format, template, kind)
        if not kept:
            raise SampleError("invalid", "没有能导出的样本")
        team = self.owner.team_id
        seed = groups.split_seed(self.session, team)
        version = groups.split_version(self.session, team)
        lists: dict[str, list[str]] = defaultdict(list)
        manifest: list[str] = []
        items: list[dict[str, Any]] = []
        exported: list[tuple[OcrSample, uuid.UUID, str]] = []
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
            for sample, group, split in kept:
                try:
                    png = self.storage.get(str(sample.crop_key))
                    with Image.open(io.BytesIO(png)) as picture:
                        picture.verify()  # 图片能解码
                except Exception:  # 存储里的图丢了或坏了：这条不导出
                    excluded["图片找不到或打不开"] += 1
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
                            "template": sample.template,
                            "field": sample.field,
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
                raise SampleError("storage", "样本的图片都找不到了")
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
        self.session.add(
            OcrSampleExport(
                kind=kind,
                split_version=version,
                filters=filters,
                counts=counts,
                items=items,
                affected=[],
                user_id=self.owner.user_id,
                team_id=team,
            )
        )
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
        suffix = "测试集" if kind == "test" else ""
        return f"OCR样本{suffix}-{stamp}.zip", out.getvalue()


def _evidence(groups_: int, rows: int) -> Evidence:
    """验证集够不够（外部评审给的起步值，ADR 0052）。"""
    if groups_ == 0:
        return Evidence(level="none", message="这次没有验证集（组太少，或都分进了训练集）")
    where = f"验证集 {groups_} 组、{rows} 行"
    if groups_ < 10 or rows < 50:
        return Evidence(level="process", message=f"{where}：只能检查流程，不能说明模型好坏")
    if groups_ >= 15 and rows >= 100:
        return Evidence(level="trend", message=f"{where}：可以看整体趋势")
    return Evidence(level="thin", message=f"{where}：偏少，趋势仅供参考")


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
    names = {"train": "训练集", "val": "验证集", "test": "锁定测试集"}
    lines = [
        "# JacarandaParse OCR 样本" + ("（锁定测试集）" if kind == "test" else ""),
        "",
        f"格式版本 {FORMAT_VERSION}，划分版本 {version}，导出时间 {datetime.now():%Y-%m-%d %H:%M}。",
        f"一共 {len(samples)} 条。",
        "",
        "这是你自己的业务数据，不要放进代码仓库、不要上传到公开的地方。",
        "",
        "## 划分",
        "",
        *(
            f"- {names[k]}：{v.groups} 组（独立的业务单据）、{v.rows} 行（`rec_gt_{k}.txt`）"
            for k, v in splits.items()
        ),
    ]
    evidence: Evidence | None = summary["evidence"]
    if evidence is not None:
        lines += ["", f"**{evidence.message}**"]
    if kind == "train_val":
        lines += [
            "",
            "按单据分组：同一张单据（重新处理的几轮、PDF 和照片、重复上传的同一个文件）的样本全在同一边，"
            "多次导出始终在同一边（`document_group` 是稳定的组号，不含业务信息）。80/20 是按组的目标比例，"
            "组少时行数比例会偏。`rec_gt.txt` 是训练集和验证集合起来。锁定测试集不在这里，单独导出。",
        ]
    lines += [
        "",
        "## 文件",
        "",
        "- `crop_img/`：每条一张单行小图，文件名只是编号。",
        "- `rec_gt_*.txt`：每行一条，图片路径、一个 Tab、图上原样的文字（UTF-8，无 BOM）。",
        "- `manifest.jsonl`：每行一条的详细信息：OCR 当时读成什么（`ocr_text`）、分类、错误标签、模板、字段、"
        "组号、分在哪边（`split`）、划分版本、标签修订号（`sample_revision`）、来源、首轮 OCR 的版本。",
        "",
        "## 分类",
        "",
        *(f"- {CATEGORIES[c]}（`{c}`）：{categories.get(c, 0)} 条" for c in CATEGORIES),
        "",
        "认错字、漏认是错例；认对的行是审核时核对过、OCR 本来就读对的。微调时错例和认对的行一起用，"
        '只用错例模型会把原来认得好的字"忘掉"。原因未确定的：图和首轮识别时的不完全一样，看过再决定用不用。',
        "",
    ]
    if tags:
        lines += [
            "错误标签：" + "，".join(f"{TAGS[t]} {n}" for t, n in tags.most_common() if t in TAGS),
            "",
        ]
    distributions: list[Distribution] = summary["distributions"]
    if distributions:
        titles = {"template": "模板", "category": "分类", "source_format": "来源"}
        lines += ["## 分布（只报告，不为凑比例挪组）", ""]
        for by, title in titles.items():
            rows = [d for d in distributions if d.by == by]
            if rows:
                lines.append(f"- {title}：" + "；".join(_dist(d, kind) for d in rows))
        lines.append("")
    if excluded:
        lines += [
            "## 没有导出的",
            "",
            *(f"- {reason}：{n} 条" for reason, n in excluded.most_common()),
            "",
        ]
    lines += [
        "## 在 PaddleOCR 里训练识别模型",
        "",
        "1. 解压到一个文件夹，例如 `/data/jacara_ocr`。",
        "2. 识别训练配置里：`Train.dataset.data_dir` 和 `Eval.dataset.data_dir` 写这个文件夹，"
        "`Train.dataset.label_file_list` 写 `rec_gt_train.txt`，`Eval.dataset.label_file_list` 写 "
        "`rec_gt_val.txt`（`SimpleDataSet` 直接能读）。",
        "3. 用和系统现在用的识别模型同一系列的配置、字典和预训练权重做微调；"
        "字典里没有的字训练时会被跳过，最好先检查一遍。",
        "4. 训练好后导出推理模型、转成 ONNX，放进项目的 `models/ocr/`（文件名带 rec），"
        '在系统信息页"换模型"里选上，jacara stop、jacara start 后生效。',
        "",
        '反复看验证集的结果来调训练，验证集会慢慢"被调过"；正式验收用锁定测试集，或者新进的单据。',
        "",
        "## 数量和配比（PaddleOCR 微调文档的建议）",
        "",
        f"- 不改字典时，识别数据建议至少 {PADDLE_MINIMUM} 行；这次 {len(samples)} 行"
        + ("，还不够，先攒或按下面合成补。" if len(samples) < PADDLE_MINIMUM else "。"),
        "- 这些场景数据和通用的真实数据（例如 LSVT、RCTW、MTWI）每轮大约 1:1 混着训练，"
        "用 `Train.dataset.ratio_list` 调（例如场景 1.0、通用 0.1），免得只认得这一种单据。",
        "- 某些字老认错时，用 TextRenderer 这类工具合成补充：语料用 `corpus/real_text.txt`（图上原样的文字），"
        "重点合成 `corpus/hard_chars.txt` 里的字；原有数据和合成数据的比例 10:1 到 5:1，学习率调小。",
        "- 训练出来的模型先用回放（jacara eval replay）和锁定测试集比过，再换上去。",
        "",
    ]
    return "\n".join(lines)


PADDLE_MINIMUM = 5000
"""PaddleOCR 微调识别模型（不改字典）建议的最少行数。"""


def corpus(samples: Sequence[OcrSample]) -> tuple[list[str], Counter[str]]:
    """合成数据用的真实语料（ADR 0055，A3）：图上原样的文字（去重），和认错字的样本里认错的字
    （按次数）。给 TextRenderer 这类工具按真实场景合成，系统里不做合成和训练。"""
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
    key = {"pdf": "PDF", "image": "照片"}.get(d.key, CATEGORIES.get(d.key, d.key))
    if kind == "test":
        return f"{key} {d.test}"
    return f"{key} 训练 {d.train} / 验证 {d.val}"


def only_value(type_: str, value: str, text: str) -> bool:
    """这一块原文是不是只有这个值：数字只多货币符号（最多 3 个字），日期只有数字和分隔符，
    文字和值一模一样（不计空白、大小写、全角半角）。"""
    import re

    from jacara.pipeline.fieldcheck import supports

    if type_ == "number":
        rest = re.sub(r"[\d\s.,'\-+()]", "", text)
        return len(rest) <= 3 and supports("number", value, text)
    if type_ == "date":
        return bool(re.fullmatch(r"[\d\s/\-.年月日:]+", text)) and supports("date", value, text)
    return _loose(text) == _loose(value)


def _same_box(a: Any, b: Sequence[float]) -> bool:
    """两个框是不是同一个（点一块原文时，出处的框就是那一块的框）。"""
    if not a or len(a) != 4:
        return False
    return all(abs(float(x) - float(y)) <= 1.0 for x, y in zip(a, b, strict=True))
