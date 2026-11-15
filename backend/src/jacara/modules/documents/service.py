"""Document business logic: upload, list, details, download, reprocessing, review completion,
export.

Permission checks and state transitions live only here. Services know nothing about HTTP; they
raise business errors (DocumentError), which routers translate to HTTP status codes."""

from __future__ import annotations

import hashlib
import io
import logging
import mimetypes
import tempfile
import uuid
import zipfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path, PurePath
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from jacara.core.advice import pick
from jacara.core.config import get_settings
from jacara.core.daterange import parse_date_range
from jacara.core.i18n import N_, nested, render, render_checks, stored, tr, trc
from jacara.core.owner import Owner
from jacara.core.permissions import require
from jacara.core.queue import QueueError, TaskQueue
from jacara.core.storage import ObjectStorage, StorageError
from jacara.models import Document, Extraction, TemplateVersion
from jacara.modules.audit.service import record
from jacara.modules.comments.repository import CommentRepository
from jacara.modules.documents.repository import DocumentFilter, DocumentRepository
from jacara.modules.documents.schemas import (
    REPROCESS_REASONS,
    Dashboard,
    DashboardCounts,
    DeleteOut,
    DocumentDetail,
    DocumentNeighbors,
    DocumentOut,
    DocumentPage,
    ExtractionOut,
    FolderOut,
    LookIn,
    LookOut,
    OcrLineOut,
    PageOcr,
    PageStructureOut,
    RegionDescription,
    RegionOut,
    ReprocessIn,
    ReprocessManyIn,
    ReprocessManyOut,
    RunOut,
    SameFile,
    SentOut,
    SentText,
    SkipIn,
    StopManyOut,
    StructureCell,
    StructurePair,
    StructureRegion,
    StructureTable,
)
from jacara.modules.exports.service import save_export
from jacara.modules.fields.schemas import AdoptOut, RangeOut, SuggestionOut
from jacara.modules.fields.service import FieldService
from jacara.modules.templates.service import Pick, TemplateFailure, TemplateService
from jacara.pipeline.current import (
    current_checks_json,
    evidence_text,
    field_states,
    label_for,
    labels_of,
    previous_extraction,
    source_value,
    suggestions,
    template_of,
    template_title,
    wants_source,
)
from jacara.pipeline.entities import entity_conflicts
from jacara.pipeline.load import load_reports
from jacara.pipeline.pages import covering, ocr_page_json, page_slice, pages_json
from jacara.pipeline.region import read_region, region_png
from jacara.pipeline.segment import SplitError, normalize_split
from jacara.plugins.exporters import create_exporter
from jacara.plugins.exporters.csv_files import workbook_to_csv
from jacara.plugins.exporters.excel import cell_value
from jacara.plugins.extractors import ExtractionError
from jacara.plugins.extractors.base import split_cell_name
from jacara.plugins.extractors.llm import look_at
from jacara.plugins.model_backends import ModelBackend, ModelBackendError
from jacara.plugins.ocr.base import OcrEngine
from jacara.plugins.ocr.loader import page_png
from jacara.templates import Template
from jacara.workbook.fill import DocRow, fill_workbook

log = logging.getLogger(__name__)

NO_VISION = N_(
    "模型当前无法读图：请将视觉模型 mmproj-F16.gguf 放入 models/，并重新执行 jacara start"
)
NO_VISION_DESKTOP = N_(
    "模型当前无法读图：请在「设置 → 模型」中配置视觉模型，并重新启动 JacarandaParse"
)

REPROCESSABLE = {"done", "reviewed", "failed", "uploaded", "processing"}
"""Statuses from which a document can be reprocessed (a new run). A queued or running document is
restarted: the running worker notices within a second and stops (see claim)."""

STOPPABLE = {"uploaded", "processing"}
"""Statuses in which processing can be stopped."""

RETRYABLE = {"uploaded", "failed"}
"""Statuses from which a document can be (re)queued. processing is in progress; done already has
results."""

EXPORTABLE = {"done", "reviewed"}
"""Only processed documents with results can be exported."""

MAX_EXPORT = 100
"""Maximum number of documents per export."""

ALLOWED_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/tiff": ".tiff",
    "application/pdf": ".pdf",
}


DASHBOARD_LIMIT = 8
"""Maximum number of documents per category under "Needs attention" on the dashboard."""


def start_of_today() -> datetime:
    """Midnight today (local date of this machine, converted to UTC). "Today" on the dashboard
    follows the date of the user's machine."""
    return day_start(datetime.now().astimezone().date())


def day_start(day: date) -> datetime:
    """Local midnight of a given day, converted to UTC (database times are compared in UTC)."""
    return datetime.combine(day, datetime.min.time()).astimezone().astimezone(UTC)


class DocumentError(Exception):
    """Business error. code tells the router which HTTP status to return."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class DocumentService:
    def __init__(
        self,
        session: Session,
        storage: ObjectStorage,
        queue: TaskQueue,
        owner: Owner,
        ocr: OcrEngine | None = None,
        model: ModelBackend | None = None,
    ) -> None:
        self.session = session
        self.repo = DocumentRepository(session)
        self.storage = storage
        self.queue = queue
        self.ocr = ocr
        """Used only by "re-recognize region"."""
        self.model = model
        """Used only by "ask the model about a region"."""
        self.owner = owner

    def upload(
        self,
        filename: str,
        content_type: str,
        data: bytes,
        template: str | None = None,
        *,
        folder: str | None = None,
        split: str = "auto",
        skip_duplicate: bool = False,
    ) -> DocumentOut:
        """template: id of one of my templates, or discover (extract all key-value pairs). Defaults
        to the template used last.

                folder: source folder in a batch import; split: number of documents in the file;
        skip_duplicate: if the same file was uploaded before, return the earlier document instead
        of creating a new one."""
        require(self.owner, "document:create")
        split = check_split(split)
        sha256 = hashlib.sha256(data).hexdigest()
        if skip_duplicate:
            earlier = self.repo.first_with_hash(self.owner.team_id, sha256)
            if earlier is not None:
                out = self._with_counts([earlier])[0]
                return out.model_copy(update={"duplicate_skipped": True})
        try:
            pick = TemplateService(self.owner, self.session).pick_for_upload(template)
        except TemplateFailure as err:
            raise DocumentError(err.code, err.message) from err
        if content_type not in ALLOWED_TYPES:
            # Some clients send no media type (application/octet-stream); guess once from the file
            # extension.
            content_type = mimetypes.guess_type(filename)[0] or content_type
        if content_type not in ALLOWED_TYPES:
            raise DocumentError(
                "unsupported",
                tr("不支持的文件类型：{content_type}。支持图片和 PDF", content_type=content_type),
            )
        limit = get_settings().max_upload_mb
        if len(data) > limit * 1024 * 1024:
            raise DocumentError("too_large", tr("文件超过 {limit} MB", limit=limit))
        if not data:
            raise DocumentError("empty", tr("文件为空"))

        document_id = uuid.uuid4()
        safe_name = PurePath(filename).name or f"upload{ALLOWED_TYPES[content_type]}"
        key = f"documents/{document_id}/{safe_name}"
        try:
            # Store the file first, then write the database.
            self.storage.put(key, data, content_type)
        except StorageError as err:
            raise DocumentError("storage", tr("对象存储暂时不可用，请稍后再试")) from err

        document = self.repo.add(
            Document(
                id=document_id,
                filename=safe_name,
                content_type=content_type,
                sha256=sha256,
                folder=clean_folder(folder),
                split=split,
                size_bytes=len(data),
                page_count=_count_pages(content_type, data),
                storage_key=key,
                status="uploaded",
                template_version_id=pick.version_id,
                discover=pick.discover,
                auto=pick.auto,
                user_id=self.owner.user_id,
                team_id=self.owner.team_id,
            )
        )
        record(
            self.session,
            self.owner,
            "document:upload",
            "document",
            document.id,
            filename=safe_name,
            template=pick.audit,
            **({"folder": document.folder} if document.folder else {}),
        )
        self._enqueue(document)
        return self._with_counts([document])[0]

    def process(self, document_id: uuid.UUID) -> DocumentOut:
        """Requeue: retry after a failure, or after the queue was unreachable at upload."""
        document = self._find(document_id)
        require(self.owner, "document:process", document.team_id)
        if document.storage_key is None:
            raise DocumentError("no_file", tr("此文档未保存原始文件（通过命令行导入），无法处理"))
        if document.status not in RETRYABLE:
            raise DocumentError(
                "conflict", tr("文档状态为 {status}，无法重新处理", status=document.status)
            )
        document.status = "uploaded"
        document.error = None
        record(self.session, self.owner, "document:process", "document", document.id)
        if not self._enqueue(document):
            raise DocumentError("queue", tr("任务队列暂时不可用，请稍后再试"))
        return DocumentOut.model_validate(document)

    def reprocess(self, document_id: uuid.UUID, data: ReprocessIn) -> DocumentOut:
        """Reprocess with the selected template (or the latest version of the current template) as a
        new run.

                Earlier runs are kept for inspection; the new run replaces them only once it
        finishes, so the old results remain usable if it fails. Reviewed documents return to
        awaiting review, and the audit log records the reason."""
        document = self._find(document_id)
        require(self.owner, "document:process", document.team_id)
        if document.storage_key is None:
            raise DocumentError("no_file", tr("此文档未保存原始文件（通过命令行导入），无法处理"))
        if document.status not in REPROCESSABLE:
            raise DocumentError(
                "conflict", tr("文档状态为 {status}，当前无法重新处理", status=document.status)
            )
        note = data.note.strip()
        if data.reason == "other" and not note:
            raise DocumentError("invalid", tr("原因为“其他”时，请填写说明"))
        templates = TemplateService(self.owner, self.session)
        try:
            if data.template is not None:
                pick = templates.pick_for_upload(data.template)
            elif document.auto:
                # Re-run automatic selection so that changed and newly added templates are
                # considered.
                pick = Pick(None, auto=True)
            elif document.template_version_id is not None:
                # Latest version of the same template (the most common case: the template was
                # changed).
                current = self.session.get_one(TemplateVersion, document.template_version_id)
                pick = Pick(templates.latest_version_id(current.template_id))
            else:
                pick = Pick(None, discover=document.discover)
        except TemplateFailure as err:
            raise DocumentError(err.code, err.message) from err

        if data.split is not None:
            document.split = check_split(data.split)
        label = REPROCESS_REASONS[data.reason]
        document.template_version_id = pick.version_id
        document.discover = pick.discover
        document.auto = pick.auto
        if data.vision is not None:
            document.vision = None if data.vision == "default" else data.vision
        if data.pdf_read is not None:
            document.pdf_read = None if data.pdf_read == "default" else data.pdf_read
        document.run_reason = (
            stored("{label}：{note}", label=nested(label), note=note) if note else stored(label)
        )
        document.status = "uploaded"
        document.error = None
        document.reviewed_at = None
        # Releases a running worker: it stops at its next progress report and writes nothing.
        document.claim = None
        # Progress of the run being replaced; the new run reports its own.
        document.stage = None
        document.progress = None
        _clear_skip(document)
        record(
            self.session,
            self.owner,
            "document:reprocess",
            "document",
            document.id,
            reason=data.reason,
            note=note,
            template=pick.audit,
            **({"vision": data.vision} if data.vision is not None else {}),
            **({"pdf_read": data.pdf_read} if data.pdf_read is not None else {}),
        )
        if not self._enqueue(document):
            raise DocumentError("queue", tr("任务队列暂时不可用，请稍后再试"))
        return DocumentOut.model_validate(document)

    def stop(self, document_id: uuid.UUID) -> DocumentOut:
        """Stop processing a queued or running document. The running worker notices at its next
        progress report (about once a second), abandons its request to the model server and writes
        nothing; the document becomes failed with a note and can be reprocessed. Earlier runs stay
        as they were."""
        document = self._find(document_id)
        require(self.owner, "document:process", document.team_id)
        if document.status not in STOPPABLE:
            raise DocumentError(
                "conflict", tr("文档状态为 {status}，没有正在进行的处理", status=document.status)
            )
        document.status = "failed"
        document.error = stored("已手动停止处理")
        document.claim = None
        document.stage = None
        document.progress = None
        document.processed_at = datetime.now(UTC)
        record(self.session, self.owner, "document:stop", "document", document.id)
        return DocumentOut.model_validate(document)

    def stop_many(self, ids: list[uuid.UUID]) -> StopManyOut:
        """Stop the selected documents that are queued or being processed. Others (already
        finished, failed or missing) are listed in skipped, one line each."""
        stopped = 0
        skipped: list[str] = []
        for document_id in dict.fromkeys(ids):
            document = self.repo.get(document_id, self.owner.team_id)
            if document is None:
                skipped.append(tr("{name}：文档不存在", name=str(document_id)))
                continue
            if document.status not in STOPPABLE:
                skipped.append(tr("{name}：没有正在进行的处理", name=document.filename))
                continue
            self.stop(document_id)
            stopped += 1
        return StopManyOut(stopped=stopped, skipped=skipped)

    def stop_all(self) -> StopManyOut:
        """Stop every queued and running document of this team (the "Stop all" button of the
        processing queue). Each becomes failed ("stopped manually") and can be reprocessed;
        earlier results stay as they were."""
        require(self.owner, "document:process", self.owner.team_id)
        ids = list(
            self.session.scalars(
                select(Document.id).where(
                    Document.team_id == self.owner.team_id, Document.status.in_(STOPPABLE)
                )
            )
        )
        return self.stop_many(ids) if ids else StopManyOut(stopped=0)

    def add_review_time(self, document_id: uuid.UUID, seconds: int) -> None:
        """Add seconds to the review time (reported by the review page; only time with the page open
        and in use)."""
        document = self._find(document_id)
        require(self.owner, "document:review", document.team_id)
        if document.status not in ("done", "reviewed"):
            raise DocumentError(
                "conflict", tr("文档状态为 {status}，不处于审核阶段", status=document.status)
            )
        document.review_seconds = (document.review_seconds or 0) + seconds

    def reprocess_many(self, data: ReprocessManyIn) -> ReprocessManyOut:
        """Reprocess the selected documents (a new run each) with the same settings (web UI
        defaults: each document's latest template version, system defaults for vision and PDF
        read mode, and the previous split). Documents being processed or still queued are
        restarted: their running worker gives them up (see reprocess). If the task queue is
        unreachable, stop immediately and report the error: a
        document already set to queued is in the same state as an upload whose queue was
        unreachable and can be retried from the list."""
        if data.reason == "other" and not data.note.strip():
            raise DocumentError("invalid", tr("原因为“其他”时，请填写说明"))
        ids = data.ids
        choice = ReprocessIn(
            template=data.template,
            reason=data.reason,
            note=data.note,
            split=data.split,
            vision=data.vision,
            pdf_read=data.pdf_read,
        )
        queued = 0
        skipped: list[str] = []
        for document_id in dict.fromkeys(ids):
            try:
                self.reprocess(document_id, choice)
            except DocumentError as err:
                if err.code == "queue":
                    raise
                document = self.session.get(Document, document_id)
                name = document.filename if document is not None else str(document_id)
                skipped.append(tr("{name}：{message}", name=name, message=err.message))
                continue
            queued += 1
        return ReprocessManyOut(queued=queued, skipped=skipped)

    def adopt_previous(self, document_id: uuid.UUID) -> AdoptOut:
        """Accept previous confirmations: after reprocessing, apply all reviews from the previous
        run that can be carried over (see suggestions)."""
        document = self._find(document_id)
        require(self.owner, "document:review", document.team_id)
        if document.status not in ("done", "reviewed"):
            raise DocumentError(
                "conflict", tr("文档状态为 {status}，处理完成后方可审核", status=document.status)
            )
        fields = FieldService(self.session, self.owner)
        confirmed = edited = 0
        for extraction in document.extractions:
            by_id = {v.id: v for v in extraction.values}
            found = suggestions(extraction, previous_extraction(document, extraction))
            for value_id, (action, value, run) in found.items():
                reason = stored("沿用第 {run} 轮的审核", run=run)
                fields.apply(by_id[value_id], action, value, reason)
                confirmed += action == "confirm"
                edited += action == "edit"
        if confirmed or edited:
            if document.status == "reviewed":
                document.status = "done"
            record(
                self.session,
                self.owner,
                "document:adopt_previous",
                "document",
                document.id,
                confirmed=confirmed,
                edited=edited,
            )
        return AdoptOut(confirmed=confirmed, edited=edited)

    def _sample_crops(self, document_id: uuid.UUID) -> list[str]:
        from jacara.models import OcrSample

        keys = self.session.scalars(
            select(OcrSample.crop_key).where(
                OcrSample.document_id == document_id, OcrSample.crop_key.is_not(None)
            )
        )
        return [k for k in keys if k]

    def _snapshots(self, document_id: uuid.UUID) -> list[str]:
        keys = self.session.scalars(
            select(Extraction.ocr_snapshot)
            .where(Extraction.document_id == document_id, Extraction.ocr_snapshot.is_not(None))
            .distinct()
        )
        return [k for k in keys if k]

    def delete(self, ids: list[uuid.UUID]) -> DeleteOut:
        """Delete a document: the original file, the results of every run, review records and
        comments are deleted permanently. The audit log records what was deleted.

                Queued and running documents are deleted too: the worker holding one notices at its
        next progress report or when it saves (its claim is gone with the row), writes nothing and
        removes the OCR snapshot it made. Files of earlier exports are not affected."""
        documents = [self._find(i) for i in dict.fromkeys(ids)]
        for document in documents:
            require(self.owner, "document:delete", document.team_id)
        keys: list[str] = []
        skipped: list[str] = []
        deleted = 0
        for document in documents:
            record(
                self.session,
                self.owner,
                "document:delete",
                "document",
                document.id,
                filename=document.filename,
                pages=document.page_count,
                status=document.status,
            )
            if document.storage_key:
                keys.append(document.storage_key)
            keys += self._sample_crops(document.id)  # Also delete the OCR sample crops.
            keys += self._snapshots(document.id)  # Also delete the OCR snapshots of every run.
            self.repo.delete(document)
            deleted += 1
        # Commit before deleting the original file: if the database delete fails, the file is still
        # there.
        self.session.commit()
        for key in keys:
            try:
                self.storage.delete(key)
            except StorageError:
                log.warning("删除文档后，文件 %s 没能从对象存储删掉", key)
        return DeleteOut(deleted=deleted, skipped=skipped)

    def folders(self) -> list[FolderOut]:
        require(self.owner, "document:read")
        return [FolderOut(folder=f, count=c) for f, c in self.repo.folders(self.owner.team_id)]

    def page(self, limit: int, offset: int, filters: DocumentFilter | None = None) -> DocumentPage:
        require(self.owner, "document:read")
        documents, total = self.repo.page(self.owner.team_id, limit, offset, filters)
        return DocumentPage(items=self._with_counts(documents), total=total)

    def _with_counts(self, documents: list[Document]) -> list[DocumentOut]:
        """Documents with their number of fields awaiting confirmation and unresolved comments."""
        ids = [d.id for d in documents]
        counts = self.repo.review_counts(ids)
        comments = CommentRepository(self.session).open_counts(ids)
        same = self.repo.same_files(self.owner.team_id, documents)
        return [
            DocumentOut.model_validate(d).model_copy(
                update={
                    "review_count": counts.get(d.id, 0),
                    "open_comments": comments.get(d.id, 0),
                    "same_file": [
                        SameFile.model_validate(o, from_attributes=True)
                        for o in same.get(d.id, [])[:3]
                    ],
                }
            )
            for d in documents
        ]

    def neighbors(
        self, document_id: uuid.UUID, filters: DocumentFilter | None = None
    ) -> DocumentNeighbors:
        """Previous / next on the review page: the adjacent documents of the list as filtered on
        the list page, and the next document awaiting review (done) for after a review."""
        document = self._find(document_id)
        require(self.owner, "document:read", document.team_id)
        previous, following, position, total = self.repo.adjacent(
            document, filters or DocumentFilter()
        )
        before, after, remaining = self.repo.neighbors(document, ("done",))
        return DocumentNeighbors(
            previous=previous,
            next=following,
            position=position,
            total=total,
            next_to_review=after or before,
            remaining=remaining,
        )

    def dashboard(self) -> Dashboard:
        """Dashboard: counts per status, processed and reviewed today, processing queue, items
        needing attention."""
        require(self.owner, "document:read")
        team = self.owner.team_id
        status = self.repo.status_counts(team)
        today = start_of_today()
        average = self.repo.average_page_ms(team, today - timedelta(days=7))
        counts = DashboardCounts(
            queued=status.get("uploaded", 0),
            processing=status.get("processing", 0),
            to_review=status.get("done", 0),
            failed=status.get("failed", 0),
            reviewed=status.get("reviewed", 0),
            total=sum(status.values()),
            skipped=self.repo.skipped_count(team),
            pending_fields=self.repo.pending_fields(team),
            processed_today=self.repo.processed_since(team, today),
            reviewed_today=self.repo.reviewed_since(team, today),
            average_page_seconds=None if average is None else round(average / 1000, 1),
            stage_seconds=self.repo.stage_seconds(team, today - timedelta(days=7)),
        )
        to_review = [
            DocumentOut.model_validate(d).model_copy(update={"review_count": n})
            for d, n in self.repo.most_pending(team, DASHBOARD_LIMIT)
        ]
        return Dashboard(
            counts=counts,
            queue=[DocumentOut.model_validate(d) for d in self.repo.in_progress(team, 20)],
            failed=[DocumentOut.model_validate(d) for d in self.repo.failed(team, DASHBOARD_LIMIT)],
            to_review=to_review,
        )

    def get(self, document_id: uuid.UUID, run: int | None = None) -> DocumentDetail:
        """Document details. run: which run to show (earlier runs are read-only); defaults to the
        current run."""
        document = self._find(document_id)
        require(self.owner, "document:read", document.team_id)
        viewing = run or document.current_run
        if viewing == document.current_run:
            chosen = list(document.extractions)
        else:
            chosen = [e for e in document.all_extractions if e.run == viewing]
            if not chosen:
                raise DocumentError(
                    "not_found", tr("此文档没有第 {viewing} 轮的结果", viewing=viewing)
                )
        detail = DocumentDetail.model_validate(document).model_copy(
            update={"extractions": [ExtractionOut.model_validate(e) for e in chosen]}
        )
        reviews = sum(v.pending_review for e in document.extractions for v in e.values)
        current = viewing == document.current_run
        extractions = [
            _with_states(
                out,
                e,
                previous_extraction(document, e) if current else None,
                self.session if current else None,
            )
            for out, e in zip(detail.extractions, chosen, strict=True)
        ]
        hints = sum(v.suggestion is not None for e in extractions for v in e.values)
        return detail.model_copy(
            update={
                "review_count": reviews,
                "extractions": extractions,
                "runs": _runs(document),
                "viewing_run": viewing,
                "suggestions": hints,
            }
        )

    def sent_text(self, document_id: uuid.UUID, extraction_id: uuid.UUID) -> SentText:
        """Document text sent to the model for one document in one run."""
        document = self._find(document_id)
        require(self.owner, "document:read", document.team_id)
        extraction = next((e for e in document.all_extractions if e.id == extraction_id), None)
        if extraction is None:
            raise DocumentError("not_found", tr("抽取结果不存在"))
        data = extraction.model_input
        if not data:
            raise DocumentError("not_found", tr("此次抽取未记录发送给模型的文字（在更新前处理）"))
        return SentText(
            blocks=[int(n) for n in data.get("blocks", [])],
            text=str(data.get("text", "")),
            sha256=str(data.get("sha256", "")),
        )

    def page_ocr(self, document_id: uuid.UUID, page: int) -> PageOcr:
        """OCR result of page page: text and position of every text block. Used by "select from
        source text" on the review page."""
        document = self._find(document_id)
        require(self.owner, "document:read", document.team_id)
        extraction = covering(document.extractions, page)
        if extraction is None:
            raise DocumentError("not_found", tr("第 {page} 页无抽取结果", page=page))
        ocr = ocr_page_json(extraction.ocr, page, extraction.page)
        lines = (ocr or {}).get("lines", [])
        return PageOcr(
            available=ocr is not None,
            lines=[OcrLineOut(index=i, **line) for i, line in enumerate(lines)],
        )

    def page_structure(
        self, document_id: uuid.UUID, page: int, run: int | None = None
    ) -> PageStructureOut:
        """Structure of page page: layout regions, table cells, label → value pairs. Used by the
        layout view of the review page."""
        document = self._find(document_id)
        require(self.owner, "document:read", document.team_id)
        wanted = run or document.current_run
        extraction = covering((e for e in document.all_extractions if e.run == wanted), page)
        if extraction is None:
            raise DocumentError("not_found", tr("第 {page} 页无抽取结果", page=page))
        return structure_view(extraction.structure, page, extraction.page)

    def review(self, document_id: uuid.UUID) -> DocumentOut:
        """Complete review: every field requiring confirmation has been handled; the status changes
        from done to reviewed."""
        document = self._find(document_id)
        require(self.owner, "document:review", document.team_id)
        if document.status != "done":
            raise DocumentError(
                "conflict",
                tr("文档状态为 {status}，处理完成（done）后方可完成审核", status=document.status),
            )
        pending = sum(v.pending_review for e in document.extractions for v in e.values)
        if pending:
            raise DocumentError("conflict", tr("尚有 {pending} 个字段待确认", pending=pending))
        document.status = "reviewed"
        document.reviewed_at = datetime.now(UTC)
        _clear_skip(document)
        record(self.session, self.owner, "document:review", "document", document.id)
        return DocumentOut.model_validate(document)

    def skip(self, document_id: uuid.UUID, data: SkipIn) -> DocumentOut:
        """Skip for now: set aside a document that needs more thought. Skipped documents are
        excluded from "Next", can be filtered in the list, and can be reviewed later."""
        document = self._find(document_id)
        require(self.owner, "document:review", document.team_id)
        if document.status != "done":
            raise DocumentError("conflict", tr("仅处理完成且未完成审核的文档可跳过"))
        document.skipped_at = datetime.now(UTC)
        document.skip_reason = data.reason.strip() or None
        document.skipped_by = self.owner.user_id
        record(
            self.session,
            self.owner,
            "document:skip",
            "document",
            document.id,
            reason=document.skip_reason or "",
        )
        return self._with_counts([document])[0]

    def unskip(self, document_id: uuid.UUID) -> DocumentOut:
        """Unskip: return the document to the review queue, where "Next" reaches it again."""
        document = self._find(document_id)
        require(self.owner, "document:review", document.team_id)
        if document.skipped_at is not None:
            _clear_skip(document)
            record(self.session, self.owner, "document:unskip", "document", document.id)
        return self._with_counts([document])[0]

    def export(
        self, document_ids: list[uuid.UUID], language: str | None = None
    ) -> tuple[str, bytes]:
        """Export one or more documents to one Excel workbook (a summary sheet with one row per
        document); return (file name, bytes).

                Cells contain post-review values (current_value); edited cells are blue, with the
        original value and reason in a cell comment."""
        documents = self._exportable(document_ids)
        data, layout = self._excel(documents, language)
        if len(documents) == 1:
            name = f"{PurePath(documents[0].filename).stem}.xlsx"
        elif layout:
            # Export to the original sheet: use its file name.
            name = tr(
                "{stem}-{day:%Y%m%d}-{n}份.xlsx",
                stem=PurePath(layout).stem,
                day=date.today(),
                n=len(documents),
            )
        else:
            name = tr("jacara-{day:%Y%m%d}-{n}份.xlsx", day=date.today(), n=len(documents))
        self._record_export(documents, "xlsx", name, data)
        return name, data

    def export_zip(
        self, document_ids: list[uuid.UUID], language: str | None = None
    ) -> tuple[str, bytes]:
        """One Excel workbook per document, packed into a zip.

                Browsers download one file at a time and cannot download folders, hence the zip;
        it extracts to a folder.

                File names in the zip follow the originals (receipt.jpg → receipt.xlsx);
        duplicates get (2), (3)."""
        documents = self._exportable(document_ids)
        out = io.BytesIO()
        used: set[str] = set()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
            for document in documents:
                name = _unique(f"{PurePath(document.filename).stem}.xlsx", used)
                archive.writestr(name, self._excel([document], language)[0])
        name = tr("jacara-{day:%Y%m%d}-{n}份.zip", day=date.today(), n=len(documents))
        data = out.getvalue()
        self._record_export(documents, "zip", name, data)
        return name, data

    def export_csv(
        self, document_ids: list[uuid.UUID], language: str | None = None
    ) -> tuple[str, bytes, str]:
        """Export CSV with the same content as the standard Excel export (summary with one row per
        document, one table each for line items and key-value pairs).

                A single .csv when there is only the summary table; otherwise one .csv per table
        packed into a zip. Returns (file name, content, format csv or zip)."""
        documents = self._exportable(document_ids)
        files = workbook_to_csv(_workbook(documents, language))
        if len(documents) == 1:
            stem = PurePath(documents[0].filename).stem
        else:
            stem = tr("jacara-{day:%Y%m%d}-{n}份", day=date.today(), n=len(documents))
        if len(files) == 1:
            name, data, kind = f"{stem}.csv", files[0][1], "csv"
        else:
            out = io.BytesIO()
            used: set[str] = set()
            with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
                for title, content in files:
                    archive.writestr(_unique(f"{title}.csv", used), content)
            name, data, kind = f"{stem}-csv.zip", out.getvalue(), "zip"
        self._record_export(documents, "csv" if kind == "csv" else "csv-zip", name, data)
        return name, data, kind

    def export_json(
        self, document_ids: list[uuid.UUID], language: str | None = None
    ) -> tuple[str, bytes]:
        """Export JSON for other programs. Same content as Excel, nested as document → sub-document
        → fields and tables."""
        from jacara.plugins.exporters.json_export import to_json

        documents = self._exportable(document_ids)
        content = to_json(
            [
                (
                    {
                        "id": str(d.id),
                        "filename": d.filename,
                        "status": d.status,
                        "page_count": d.page_count,
                        "reviewed_at": d.reviewed_at.isoformat() if d.reviewed_at else None,
                    },
                    load_reports(d, language),
                )
                for d in documents
            ],
            datetime.now().astimezone(),
        )
        if len(documents) == 1:
            stem = PurePath(documents[0].filename).stem
        else:
            stem = tr("jacara-{day:%Y%m%d}-{n}份", day=date.today(), n=len(documents))
        name = f"{stem}.json"
        self._record_export(documents, "json", name, content)
        return name, content

    def _excel(self, documents: list[Document], language: str | None = None) -> tuple[bytes, str]:
        """Export to Excel: fill the original sheet if the template has one (templates created from
        Excel), otherwise use the standard format.

                Returns (bytes, original sheet file name; None for the standard format). Falls
        back to the standard format if the original sheet cannot be retrieved from object
        storage."""
        template = _layout_template(documents)
        if template is not None and template.workbook is not None:
            try:
                original = self.storage.get(template.workbook.file)
            except StorageError:
                log.warning("取不到模板 %s 的原表，改用标准格式导出", template.name)
            else:
                rows = _doc_rows(documents, template)
                title = tr("{day:%Y-%m-%d} 导出", day=date.today())
                return fill_workbook(template, original, rows, title), template.workbook.filename
        return _workbook(documents, language), ""

    def _exportable(self, document_ids: list[uuid.UUID]) -> list[Document]:
        """Pre-export checks (shared by both export formats): permission, count, existence,
        processing finished. Returns documents in the order selected by the user."""
        require(self.owner, "document:export")
        ids = list(dict.fromkeys(document_ids))  # Remove duplicate ids, preserving order.
        if not ids:
            raise DocumentError("invalid", tr("请至少选择一个文档"))
        if len(ids) > MAX_EXPORT:
            raise DocumentError("invalid", tr("单次最多导出 {limit} 个文档", limit=MAX_EXPORT))

        found = {d.id: d for d in self.repo.get_many(ids, self.owner.team_id)}
        missing = [str(i) for i in ids if i not in found]
        if missing:
            raise DocumentError(
                "not_found", tr("文档不存在：{documents}", documents=", ".join(missing))
            )
        documents = [found[i] for i in ids]
        not_ready = [d.filename for d in documents if d.status not in EXPORTABLE]
        if not_ready:
            raise DocumentError(
                "conflict",
                tr("以下文档尚未处理完成，无法导出：{documents}", documents=", ".join(not_ready)),
            )
        return documents

    def _record_export(
        self, documents: list[Document], file_format: str, filename: str, data: bytes
    ) -> None:
        # An export is data leaving the system and must be recorded: who, when, which documents,
        # which format. The file is also stored so that it can be downloaded again from the export
        # history.
        export = save_export(
            self.session, self.storage, self.owner, filename, file_format, data, documents
        )
        record(
            self.session,
            self.owner,
            "document:export",
            "export",
            export.id,
            documents=[str(d.id) for d in documents],
            format=file_format,
        )

    def download(self, document_id: uuid.UUID) -> tuple[Document, bytes]:
        document = self._find(document_id)
        require(self.owner, "document:download", document.team_id)
        if document.storage_key is None:
            raise DocumentError("no_file", tr("此文档未保存原始文件（通过命令行导入）"))
        try:
            return document, self.storage.get(document.storage_key)
        except StorageError as err:
            raise DocumentError("storage", tr("对象存储暂时不可用，请稍后再试")) from err

    def page_image(self, document_id: uuid.UUID, page: int) -> bytes:
        """PNG image of page page (1-based). Coordinates match extraction; the review page draws
        evidence boxes on it."""
        document, data = self._page_file(document_id, page)
        try:
            return page_png(data, document.content_type == "application/pdf", page)
        except Exception as err:
            raise DocumentError("unreadable", tr("无法打开此文件，文件可能已损坏")) from err

    def _page_file(self, document_id: uuid.UUID, page: int) -> tuple[Document, bytes]:
        """Check permission and page number, and retrieve the original file (not yet rendered)."""
        document = self._find(document_id)
        require(self.owner, "document:read", document.team_id)
        if document.storage_key is None:
            raise DocumentError("no_file", tr("此文档未保存原始文件（通过命令行导入）"))
        if not 1 <= page <= document.page_count:
            raise DocumentError(
                "not_found",
                tr(
                    "此文档仅有 {page_count} 页，不存在第 {page} 页",
                    page_count=document.page_count,
                    page=page,
                ),
            )
        try:
            return document, self.storage.get(document.storage_key)
        except StorageError as err:
            raise DocumentError("storage", tr("对象存储暂时不可用，请稍后再试")) from err

    def describe_region(
        self, document_id: uuid.UUID, page: int, box: list[float]
    ) -> RegionDescription:
        """Template building by region selection: the value inside the box (from the stored OCR,
        otherwise by re-recognizing the region), the nearby label, its position on the page and
        its likely type."""
        from jacara.modules.templates.boxes import (
            field_id,
            guess_type,
            hint,
            lines_in,
            pair_for,
            position,
        )

        document = self._find(document_id)
        require(self.owner, "template:manage", document.team_id)
        extraction = covering(document.extractions, page)
        ocr = ocr_page_json(extraction.ocr, page, extraction.page) if extraction else None
        lines = (ocr or {}).get("lines", [])
        inside = lines_in(lines, box)
        text = " ".join(str(ln["text"]) for ln in inside)
        confidence = min((float(ln["confidence"]) for ln in inside), default=None)
        if not text and self.ocr is not None:
            try:
                text, confidence = read_region(self.page_image(document_id, page), box, self.ocr)
            except ValueError as err:
                raise DocumentError("invalid", str(err)) from err
        # Labels come only from label → value pairs paired by layout analysis (computed during
        # processing); if none matches, leave it empty rather than guess.
        structure = (
            structure_view(extraction.structure, page, extraction.page) if extraction else None
        )
        pair = pair_for(structure.pairs, box) if structure else None
        label = pair.key if pair else None
        side = tr(SIDES[pair.rule]) if pair and pair.rule in SIDES else None
        where = position(
            box, float((ocr or {}).get("width") or 1), float((ocr or {}).get("height") or 1)
        )
        return RegionDescription(
            text=text,
            confidence=confidence,
            label=label,
            side=side,
            type=guess_type(text),  # type: ignore[arg-type]
            position=where,
            name=field_id(label or "", set(), 1) if label else "",
            description=hint(where, label, side),
        )

    def page_crop(self, document_id: uuid.UUID, page: int, box: list[float]) -> bytes:
        """Zoomed PNG of box (page image coordinates) on this page: the review page shows it next to
        candidates. PDFs are re-rendered at the zoomed re-recognition scale; images use the
        original."""
        from jacara.pipeline.region import crop_region
        from jacara.pipeline.zoom import ZOOM_SCALE
        from jacara.plugins.ocr.loader import PDF_SCALE

        # Check permission and page number, and retrieve the original file.
        document, data = self._page_file(document_id, page)
        is_pdf = document.content_type == "application/pdf"
        try:
            image = (
                page_png(data, is_pdf, page, scale=ZOOM_SCALE)
                if is_pdf
                else page_png(data, False, 1)
            )
            factor = ZOOM_SCALE / PDF_SCALE if is_pdf else 1.0
            crop = crop_region(image, [v * factor for v in box])
        except ValueError as err:
            raise DocumentError("invalid", str(err)) from err
        except Exception as err:
            raise DocumentError("unreadable", tr("无法打开此文件，文件可能已损坏")) from err
        out = io.BytesIO()
        crop.save(out, format="PNG")
        return out.getvalue()

    def ocr_region(self, document_id: uuid.UUID, page: int, box: list[float]) -> RegionOut:
        """Crop a region of the page and re-run OCR. Requires permission to view the document and to
        edit fields (the result is useless otherwise)."""
        document = self._find(document_id)
        require(self.owner, "document:review", document.team_id)
        if self.ocr is None:
            raise DocumentError("unavailable", tr("OCR 引擎未就绪"))
        image = self.page_image(document_id, page)
        try:
            text, confidence = read_region(image, box, self.ocr)
        except ValueError as err:
            raise DocumentError("invalid", str(err)) from err
        return RegionOut(text=text, confidence=confidence)

    def look_region(self, document_id: uuid.UUID, page: int, data: LookIn) -> LookOut:
        """Ask the model about a small region of the page: for handwriting, stamps and text covered
        by pen marks that OCR cannot read. Returns the reading only and saves nothing; after
        confirmation, save with field review."""
        document = self._find(document_id)
        require(self.owner, "document:review", document.team_id)
        check = getattr(self.model, "sees_images", None)
        if self.model is None or not callable(check) or not check():
            raise DocumentError(
                "unavailable",
                tr(pick(NO_VISION_DESKTOP, NO_VISION)),
            )
        image = self.page_image(document_id, page)
        try:
            picture = region_png(image, data.box)
        except ValueError as err:
            raise DocumentError("invalid", str(err)) from err
        try:
            text, value, model = look_at(self.model, picture, data.label, data.type)
        except (ModelBackendError, ExtractionError) as err:
            raise DocumentError("unavailable", str(err)) from err
        return LookOut(text=text, value=value, model=model)

    def _enqueue(self, document: Document) -> bool:
        """Commit first, then enqueue.

                The order matters: the worker is another process that looks up the document over
        its own database connection. If enqueued first, the worker may look before the commit and
        not find it. An unreachable queue is not an upload failure: the file and the record
        exist, the status stays uploaded, and the document can be requeued later."""
        self.session.commit()
        try:
            self.queue.enqueue_document(document.id)
        except QueueError:
            log.warning("文档 %s 没能放进队列，状态保持 uploaded", document.id)
            return False
        return True

    def _find(self, document_id: uuid.UUID) -> Document:
        document = self.repo.get(document_id, self.owner.team_id)
        if document is None:
            raise DocumentError("not_found", tr("文档不存在"))
        return document


SIDES = {
    "inline": N_("后方"),
    "colon": N_("右侧"),
    "label_left": N_("右侧"),
    "row_label": N_("同一行右侧"),
    "colon_below": N_("下方"),
    "header_above": N_("下方"),
    "two_column_table": N_("右侧"),
}
"""Layout pairing rule → side of the label on which the value lies (used for position hints in
region-based template building)."""


def _region_kind(block: dict[str, Any]) -> str:
    """Display name of a layout region in the request language."""
    name = str(block["attrs"].get("kind") or "")
    return tr(name) if name else ""


def structure_view(data: dict[str, Any] | None, page: int = 1, first: int = 1) -> PageStructureOut:
    """Stored structure → shapes drawn over the page image: merged cells are drawn once; empty cells
    are not drawn.

        For documents spanning several pages, only the parts on page page are returned, with
    coordinates converted back to that page."""
    if not data:
        return PageStructureOut(available=False)
    part = page_slice(data, page, first)
    if part is not None:
        data = _one_page(data, *part)
    blocks = {b["id"]: b for b in data.get("blocks", [])}
    regions = [
        StructureRegion(kind=_region_kind(blocks[a]), box=blocks[a]["box"])
        for a in data.get("layouts", [])
        if a in blocks
    ]
    tables: list[StructureTable] = []
    for t in data.get("tables", []):
        table = blocks.get(t)
        if table is None:
            continue
        attrs = table["attrs"]
        covered: set[str] = set()
        cells: list[StructureCell] = []
        for m in attrs.get("merged", []):
            merged = blocks[m]
            covered.update(merged["children"])
            a = merged["attrs"]
            cells.append(
                StructureCell(
                    row=a["row"],
                    col=a["col"],
                    row_span=a["row_span"],
                    col_span=a["col_span"],
                    box=merged["box"],
                    text=merged["text"],
                )
            )
        for c in table["children"]:
            cell = blocks[c]
            if c in covered or cell["attrs"].get("empty"):
                continue
            cells.append(
                StructureCell(
                    row=cell["attrs"]["row"],
                    col=cell["attrs"]["col"],
                    box=cell["box"],
                    text=cell["text"],
                )
            )
        tables.append(
            StructureTable(
                box=table["box"],
                rows=attrs["rows"],
                cols=attrs["cols"],
                by=attrs["by"],
                cells=cells,
            )
        )
    pairs = []
    for k in data.get("keys", []):
        key = blocks[k]
        value = blocks[key["attrs"]["value"]]
        pairs.append(
            StructurePair(
                key=key["text"],
                key_box=key["box"],
                value=value["text"],
                value_box=value["box"],
                rule=key["attrs"].get("rule", ""),
            )
        )
    return PageStructureOut(
        available=True,
        sources={
            key: trc("structure source", str(name))
            for key, name in (data.get("sources") or {}).items()
        },
        regions=regions,
        tables=tables,
        pairs=pairs,
    )


def _one_page(data: dict[str, Any], top: float, bottom: float) -> dict[str, Any]:
    """Blocks of a multi-page structure whose center lies within [top, bottom), shifted up by top.
    For tables spanning pages, only cells on this page are kept and the table box is shrunk to
    them."""

    def inside(box: list[float]) -> bool:
        return top <= (box[1] + box[3]) / 2 < bottom

    def moved(box: list[float]) -> list[float]:
        return [box[0], box[1] - top, box[2], box[3] - top]

    blocks: list[dict[str, Any]] = []
    kept: set[str] = set()
    for b in data.get("blocks", []):
        if b["type"] == "TABLE":
            continue
        if inside(b["box"]):
            blocks.append({**b, "box": moved(b["box"])})
            kept.add(b["id"])
    tables: list[str] = []
    for b in data.get("blocks", []):
        if b["type"] != "TABLE":
            continue
        children = [c for c in b["children"] if c in kept]
        if not children:
            continue
        boxes = [next(x["box"] for x in blocks if x["id"] == c) for c in children]
        box = [
            min(x[0] for x in boxes),
            min(x[1] for x in boxes),
            max(x[2] for x in boxes),
            max(x[3] for x in boxes),
        ]
        attrs = {**b["attrs"], "merged": [m for m in b["attrs"].get("merged", []) if m in kept]}
        blocks.append({**b, "box": box, "children": children, "attrs": attrs})
        tables.append(b["id"])
    keys = [
        k
        for k in data.get("keys", [])
        if k in kept and next(b for b in blocks if b["id"] == k)["attrs"].get("value") in kept
    ]
    return {
        **data,
        "blocks": blocks,
        "layouts": [a for a in data.get("layouts", []) if a in kept],
        "tables": tables,
        "keys": keys,
    }


def _clear_skip(document: Document) -> None:
    document.skipped_at = None
    document.skip_reason = None
    document.skipped_by = None


def _runs(document: Document) -> list[RunOut]:
    """Every processed run, newest first."""
    firsts: dict[int, Extraction] = {}
    for e in document.all_extractions:
        firsts.setdefault(e.run, e)
    return [
        RunOut(
            run=run,
            template=e.template,
            template_title=_template_title(e),
            template_version=e.template_version,
            reason=e.run_reason,
            created_at=e.created_at,
            current=run == document.current_run,
        )
        for run, e in sorted(firsts.items(), reverse=True)
    ]


def range_out(value: str | None, day_first: bool | None) -> RangeOut | None:
    """Split date ranges: the review page shows "2014-07-01 ~ 2014-07-31 (31 days)"."""
    if not value:
        return None
    parsed = parse_date_range(value, day_first)
    found = parsed.range
    return RangeOut(
        start=found.start.isoformat() if found and found.start else None,
        end=found.end.isoformat() if found and found.end else None,
        days=found.days if found else None,
        problem=tr(parsed.problem) if parsed.problem else None,
    )


def _template_title(extraction: Extraction) -> str:
    return template_title(extraction)


def _with_states(
    out: ExtractionOut,
    extraction: Extraction,
    previous: Extraction | None = None,
    session: Session | None = None,
) -> ExtractionOut:
    """Re-run rules on the current values, then annotate each field with its validation status and
    color, display name, and any review from the previous run that can be carried over (previous
    is the previous run's result for the same page). With a session, also check whether the
    subject (same patient, same company) matches other documents."""
    checks = current_checks_json(extraction)
    template = template_of(extraction) if session is not None else None
    if session is not None and template is not None and template.entities:
        issues = cast(list[dict[str, Any]], checks["issues"])
        checks = {**checks, "issues": issues + entity_conflicts(session, extraction, template)}
    states = field_states(extraction, checks)
    labels = labels_of(extraction)
    hints = suggestions(extraction, previous)
    by_id = {v.id: v for v in extraction.values}
    # Documents processed before evidence text was stored: locate it with the OCR result of this
    # page (loaded only when needed).
    needs_ocr = any(wants_source(v) and "text" not in (v.evidence or {}) for v in extraction.values)
    lines = (extraction.ocr or {}).get("lines") if needs_ocr else None
    shape = template if template is not None else template_of(extraction)
    day_first = shape.day_first if shape is not None else None
    # Field labels in other languages: the first is the default.
    languages = list(shape.languages) if shape is not None else []
    others = {lang: labels_of(extraction, lang) for lang in languages[1:]}
    values = [
        v.model_copy(
            update={
                "range": range_out(v.current_value, day_first) if v.type == "date_range" else None,
                "check_state": states[v.id][0],
                "level": states[v.id][1],
                "source_value": source_value(by_id[v.id], evidence_text(by_id[v.id], lines)),
                "label": label_for(labels, v.name),
                "names": {
                    lang: found
                    for lang, names in others.items()
                    if (found := label_for(names, v.name)) is not None
                },
                "suggestion": (
                    SuggestionOut(action=hints[v.id][0], value=hints[v.id][1], run=hints[v.id][2])
                    if v.id in hints
                    else None
                ),
            }
        )
        for v in out.values
    ]
    notes = [
        tr("第 {page} 页：{note}", page=page, note=render(data["note"]))
        for page, data in pages_json(extraction.ocr, extraction.page)
        if data.get("note")
    ]
    return out.model_copy(
        update={
            "current_checks": render_checks(checks),
            "values": values,
            "labels": labels,
            "languages": languages if len(languages) > 1 else [],
            "label_sets": others,
            "template_title": _template_title(extraction),
            "source_notes": notes,
            "sent": _sent(extraction),
        }
    )


def _sent(extraction: Extraction) -> SentOut | None:
    data = extraction.model_input
    if not data:
        return None
    return SentOut(
        blocks=len(data.get("blocks", [])),
        total_blocks=int(data.get("total_blocks", 0)),
        chars=int(data.get("chars", 0)),
        images=int(data.get("images", 0)),
        lessons=int(data.get("lessons", 0)),
        has_snapshot=extraction.ocr_snapshot is not None,
    )


def _count_pages(content_type: str, data: bytes) -> int:
    if content_type != "application/pdf":
        return 1
    import pdfplumber

    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            return len(pdf.pages)
    except Exception as err:
        raise DocumentError("unreadable", tr("无法打开 PDF 文件，文件可能已损坏")) from err


def _layout_template(documents: list[Document]) -> Template | None:
    """The template shared by all these documents (same version), if it has an original sheet
    layout; otherwise None."""
    templates = {(e.template, e.template_version): e for d in documents for e in d.extractions[:1]}
    if len(templates) != 1 or any(not d.extractions for d in documents):
        return None
    template = template_of(next(iter(templates.values())))
    return template if template is not None and template.workbook else None


def _doc_rows(documents: list[Document], template: Template) -> list[DocRow]:
    """Post-review values → rows for filling the original sheet. Fields set not to export are left
    empty."""
    hidden = {f.name for f in template.fields if not f.export}
    for t in template.tables:
        hidden |= {f"{t.name}.{c.name}" for c in t.columns if not c.export or not t.export}
    rows: list[DocRow] = []
    for document in documents:
        for report in load_reports(document):
            row = DocRow(
                filename=document.filename,
                page=report.page,
                uploaded_at=document.created_at,
                processed_at=document.processed_at,
                reviewed_at=document.reviewed_at,
            )
            for a in report.aligned:
                value = cell_value(a, report.reviews.get(a.field.name))
                cell = split_cell_name(a.field.name)
                if cell is None:
                    if a.field.name not in hidden:
                        row.values[a.field.name] = value
                    continue
                table, index, column = cell
                items = row.tables.setdefault(table, [])
                while len(items) < index:
                    items.append({})
                if f"{table}.{column}" not in hidden:
                    items[index - 1][column] = value
            rows.append(row)
    return rows


def _workbook(documents: list[Document], language: str | None = None) -> bytes:
    """Write several documents into one Excel workbook and return its bytes. language: language of
    the column headers."""
    reports = [report for d in documents for report in load_reports(d, language)]
    exporter = create_exporter("excel")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"export{exporter.suffix}"
        exporter.export(reports, path)
        return path.read_bytes()


def _unique(name: str, used: set[str]) -> str:
    """A zip cannot contain two files with the same name: if receipt.xlsx exists, use receipt
    (2).xlsx."""
    stem, suffix = PurePath(name).stem, PurePath(name).suffix
    candidate, n = name, 1
    while candidate in used:
        n += 1
        candidate = f"{stem} ({n}){suffix}"
    used.add(candidate)
    return candidate


def clean_folder(folder: str | None) -> str | None:
    """Normalize a batch-import folder: / as separator, leading and trailing / removed, . and ..
    removed, at most 255 characters. None if empty."""
    if not folder:
        return None
    parts = [p.strip() for p in folder.replace("\\", "/").split("/")]
    parts = [p for p in parts if p and p not in (".", "..")]
    return "/".join(parts)[:255] or None


def check_split(split: str | None) -> str:
    """Validate the "documents per file" value and explain any error."""
    try:
        return normalize_split(split)
    except SplitError as err:
        raise DocumentError("invalid", str(err)) from err
