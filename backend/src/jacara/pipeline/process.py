"""Process one uploaded document in the background: fetch the file from object storage → run the
pipeline → store the results and update the status.

Status transitions: uploaded → processing → done / failed

Two modes:
- process_document: one process handles the document end to end;
- Pipeline: the OCR stage (read_document) hands its results to the model stage
  (extract_document); while the model extracts document 1, OCR is already reading document 2.
  Saves time in batch imports.

Each step uses its own short transaction rather than one transaction around the whole run:
processing a receipt takes over ten seconds, during which the API must see the processing status,
so the claim is committed immediately."""

from __future__ import annotations

import logging
import mimetypes
import tempfile
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from jacara.core.db import session_scope
from jacara.core.i18n import stored
from jacara.core.storage import ObjectStorage, StorageError
from jacara.models import Document, Extraction, TemplateRecord, TemplateVersion
from jacara.pipeline.classify import AutoChoice, Candidate
from jacara.pipeline.current import template_from_version
from jacara.pipeline.handoff import dump_pages
from jacara.pipeline.handoff import load_pages as load_handoff
from jacara.pipeline.learn import Lesson, from_database
from jacara.pipeline.run import Pipeline, TemplateChoice
from jacara.pipeline.snapshots import save_snapshot
from jacara.pipeline.store import add_extractions, page_count
from jacara.pipeline.vision import VisionMode
from jacara.plugins.exporters import DocumentReport
from jacara.plugins.extractors import ExtractionError
from jacara.plugins.extractors.base import DISCOVER
from jacara.plugins.model_backends import ModelBackendError
from jacara.plugins.ocr import OcrPage
from jacara.plugins.ocr.loader import PdfRead, UnsupportedFileError
from jacara.templates import DEFAULT_TEMPLATE

log = logging.getLogger(__name__)

Outcome = Literal["done", "failed", "skipped"]


class Superseded(Exception):
    """The user stopped or restarted processing of this document while this run was working on it.
    The run ends without writing anything; the document is already in the state the user chose."""


def _lessons_for(
    pipeline: Pipeline,
    factory: sessionmaker[Session] | None,
    team_id: uuid.UUID,
    document_id: uuid.UUID,
) -> Callable[[str], list[Lesson]] | None:
    """With learning from corrections enabled: load past manual corrections for the template from
    the database (same team, reviewed documents, excluding this one). Returns None when disabled
    or unsupported by the extractor."""
    from jacara.core.config import get_settings

    if not get_settings().jacara_learn or not hasattr(pipeline.extractor, "lessons"):
        return None

    def lessons(template: str) -> list[Lesson]:
        try:
            with session_scope(factory) as session:
                return from_database(session, team_id, template, exclude=document_id)
        except Exception:  # Failing to load examples does not affect processing.
            log.exception("找以前改过的例子出错")
            return []

    return lessons


@dataclass(frozen=True)
class Job:
    """Everything needed to process a document, loaded from the database once at the start (later
    steps open no long transactions)."""

    key: str
    filename: str
    content_type: str | None
    team_id: uuid.UUID
    pdf_read: PdfRead | None


def _job(document_id: uuid.UUID, factory: sessionmaker[Session] | None) -> Job:
    with session_scope(factory) as session:
        document = session.get_one(Document, document_id)
        if document.storage_key is None:
            raise StorageError(stored("无原始文件"))
        return Job(
            document.storage_key,
            document.filename,
            document.content_type,
            document.team_id,
            cast(PdfRead | None, document.pdf_read),
        )


@contextmanager
def _original(job: Job, storage: ObjectStorage) -> Iterator[Path]:
    """Place the original file in a temporary folder that is removed after use."""
    data = storage.get(job.key)
    with tempfile.TemporaryDirectory() as tmp:
        # The pipeline detects the file type from the extension, so the temporary file gets an
        # extension derived from content_type.
        path = Path(tmp) / f"original{suffix_for(job.filename, job.content_type)}"
        path.write_bytes(data)
        yield path


def _extract(
    document_id: uuid.UUID,
    job: Job,
    path: Path,
    pages: list[OcrPage],
    pipeline: Pipeline,
    factory: sessionmaker[Session] | None,
    claim: str | None = None,
) -> tuple[list[DocumentReport], int, dict[str, object]]:
    """Everything after OCR: document splitting, template selection, extraction, alignment,
    cross-checking. Returns (reports, run number, data needed for storing)."""
    with session_scope(factory) as session:
        document = session.get_one(Document, document_id)
        template, version_id = choose_template(session, document)
        split = document.split
        vision = cast(VisionMode | None, document.vision)
        history = answer_lengths(session, document.team_id)
        run = next_run(session, document)
        reason = document.run_reason
    # Examples for this run; restored afterwards (the same pipeline processes other documents).
    from jacara.core.config import get_settings

    before = pipeline.lessons
    pipeline.lessons = _lessons_for(pipeline, factory, job.team_id, document_id)
    pipeline.learn_mode = get_settings().jacara_learn_mode
    try:
        reports = pipeline.run(
            path,
            name=job.filename,
            template=template,
            progress=lambda stage, percent: _report(document_id, stage, percent, factory, claim),
            split=split,
            vision=vision,
            history=history,
            pdf_read=job.pdf_read,
            pages=pages,
        )
    finally:
        pipeline.lessons = before
    return reports, run, {"template_version_id": version_id, "run_reason": reason}


def _save(
    document_id: uuid.UUID,
    reports: list[DocumentReport],
    run: int,
    extra: dict[str, object],
    timings: dict[str, object],
    factory: sessionmaker[Session] | None,
    snapshot: str | None = None,
    claim: str | None = None,
) -> None:
    _report(document_id, stored("保存结果"), 98, factory, claim)
    with session_scope(factory) as session:
        # Locked until commit (PostgreSQL FOR UPDATE; SQLite has a single writer anyway), so a stop
        # request cannot slip in between this check and saving.
        document = session.get_one(Document, document_id, with_for_update=True)
        if claim is not None and document.claim != claim:
            raise Superseded
        add_extractions(
            session,
            document,
            reports,
            run=run,
            template_version_id=cast(uuid.UUID | None, extra["template_version_id"]),
            run_reason=cast(str | None, extra["run_reason"]),
            ocr_snapshot=snapshot,
        )
        document.page_count = page_count(reports)
        # Switch to the new run only on success; on failure the old results remain visible.
        document.current_run = run
        document.run_reason = None
        document.status = "done"
        document.timings = timings
        _finish(document)
    log.info("完成 %s：%d 张单据", document_id, len(reports))


def _fail(
    document_id: uuid.UUID,
    err: Exception,
    factory: sessionmaker[Session] | None,
    claim: str | None = None,
) -> None:
    # The full traceback goes to the worker log.
    log.exception("处理失败 %s", document_id, exc_info=err)
    with session_scope(factory) as session:
        document = session.get(Document, document_id, with_for_update=True)
        if document is None or (claim is not None and document.claim != claim):
            return  # Deleted, stopped or restarted meanwhile: the error belongs to an old run.
        document.status = "failed"
        document.error = describe_error(err)
        _finish(document)


def _ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


def process_document(
    document_id: uuid.UUID,
    *,
    storage: ObjectStorage,
    pipeline: Pipeline,
    factory: sessionmaker[Session] | None = None,
) -> Outcome:
    """Process one document in a single process: OCR → extraction → storage (without stages,
    JACARA_PIPELINE=false).

        Returns a result and never raises: on error the cause is written to the database and the
    status becomes failed."""
    claim = _claim(document_id, factory)
    if claim is None:
        # Already processed, being processed, or the document does not exist. Also reached when a
        # task is delivered twice.
        log.info("跳过 %s：状态不是 uploaded", document_id)
        return "skipped"
    try:
        job = _job(document_id, factory)
        with _original(job, storage) as path:
            _report(document_id, stored("文字识别"), 5, factory, claim)
            started = time.perf_counter()
            pages = pipeline.read(path, job.pdf_read)
            read_ms = _ms(started)
            started = time.perf_counter()
            reports, run, extra = _extract(document_id, job, path, pages, pipeline, factory, claim)
            extract_ms = _ms(started)
        timings = {"read_ms": read_ms, "wait_ms": 0, "extract_ms": extract_ms, "split": False}
        snapshot = save_snapshot(storage, document_id, run, pages)
        _save(document_id, reports, run, extra, timings, factory, snapshot, claim)
        return "done"
    except Superseded:
        log.info("停止 %s：已由用户停止或重新处理", document_id)
        return "skipped"
    except Exception as err:
        _fail(document_id, err, factory, claim)
        return "failed"


# ---- Pipeline: OCR stage and model stage ----


def handoff_key(document_id: uuid.UUID, attempt: str) -> str:
    """Object storage key of the OCR stage output (deleted by the model stage when done; what
    remains is this run's OCR snapshot)."""
    return f"pipeline/{document_id}/{attempt}.json"


def read_document(
    document_id: uuid.UUID,
    *,
    storage: ObjectStorage,
    pipeline: Pipeline,
    send: Callable[[uuid.UUID, str], None],
    factory: sessionmaker[Session] | None = None,
) -> Literal["read", "failed", "skipped"]:
    """OCR stage: claim the document, run OCR, store the result in object storage and hand it to the
    model stage (send).

        After the hand-off, the OCR stage immediately takes the next document; while the model
    stage extracts this one, the next is already being read."""
    claim = _claim(document_id, factory)
    if claim is None:
        log.info("跳过 %s：状态不是 uploaded", document_id)
        return "skipped"
    attempt = uuid.uuid4().hex
    try:
        job = _job(document_id, factory)
        with _original(job, storage) as path:
            _report(document_id, stored("文字识别"), 5, factory, claim)
            started = time.perf_counter()
            pages = pipeline.read(path, job.pdf_read)
            read_ms = _ms(started)
        storage.put(handoff_key(document_id, attempt), dump_pages(pages), "application/json")
        with session_scope(factory) as session:
            document = session.get_one(Document, document_id, with_for_update=True)
            if document.claim != claim:
                raise Superseded
            # attempt: the model stage accepts only this hand-off (tasks from before a reprocess are
            # stale).
            document.timings = {"attempt": attempt, "read_ms": read_ms, "handed_at": time.time()}
            document.stage = stored("文字识别完成，排队等待模型")
            document.progress = 10
        send(document_id, attempt)
        return "read"
    except Superseded:
        _drop(storage, document_id, attempt)
        log.info("停止 %s：已由用户停止或重新处理", document_id)
        return "skipped"
    except Exception as err:
        _drop(storage, document_id, attempt)
        _fail(document_id, err, factory, claim)
        return "failed"


def extract_document(
    document_id: uuid.UUID,
    attempt: str,
    *,
    storage: ObjectStorage,
    pipeline: Pipeline,
    factory: sessionmaker[Session] | None = None,
) -> Outcome:
    """Model stage: read back the OCR stage output, split documents, extract, cross-check and
    store."""
    with session_scope(factory) as session:
        document = session.get(Document, document_id)
        timings = dict(document.timings or {}) if document is not None else {}
        current = document is not None and document.status == "processing"
        # The OCR stage's claim carries over to this stage.
        claim = document.claim if document is not None else None
    if not current or timings.get("attempt") != attempt:
        # Document deleted, reprocessed or marked failed meanwhile: this task is stale.
        log.info("跳过 %s：不是这一次交过来的", document_id)
        _drop(storage, document_id, attempt)
        return "skipped"
    handed_at = timings.get("handed_at")
    wait_ms = round((time.time() - float(handed_at)) * 1000) if isinstance(handed_at, float) else 0
    try:
        job = _job(document_id, factory)
        pages = load_handoff(storage.get(handoff_key(document_id, attempt)))
        with _original(job, storage) as path:
            started = time.perf_counter()
            reports, run, extra = _extract(document_id, job, path, pages, pipeline, factory, claim)
            extract_ms = _ms(started)
        done = {
            "read_ms": timings.get("read_ms"),
            "wait_ms": wait_ms,
            "extract_ms": extract_ms,
            "split": True,
        }
        snapshot = save_snapshot(storage, document_id, run, pages)
        _save(document_id, reports, run, extra, done, factory, snapshot, claim)
        return "done"
    except Superseded:
        log.info("停止 %s：已由用户停止或重新处理", document_id)
        return "skipped"
    except Exception as err:
        _fail(document_id, err, factory, claim)
        return "failed"
    finally:
        _drop(storage, document_id, attempt)


def _drop(storage: ObjectStorage, document_id: uuid.UUID, attempt: str) -> None:
    try:
        storage.delete(handoff_key(document_id, attempt))
    except StorageError:
        log.warning("交接文件 %s 没删掉", handoff_key(document_id, attempt))


def choose_template(
    session: Session, document: Document
) -> tuple[TemplateChoice, uuid.UUID | None]:
    """Template for this run: key-value discovery, a version of my template, or the built-in receipt
    template.

        Returns (template for the pipeline, template version id). The version id is recorded on
    the extraction so it can be restored after later template changes. Automatic selection: all
    usable templates of mine are passed to the pipeline; each document picks its own and records
    its version id."""
    if document.auto:
        return AutoChoice(candidates(session, document.team_id)), None
    if document.discover:
        return DISCOVER, None
    if document.template_version_id is not None:
        version = session.get_one(TemplateVersion, document.template_version_id)
        return template_from_version(version), version.id
    return DEFAULT_TEMPLATE, None


def candidates(session: Session, team_id: uuid.UUID) -> tuple[Candidate, ...]:
    """Templates available for automatic selection: the latest version of each non-archived,
    non-deleted template of this team (in personal mode, one's own)."""
    records = session.scalars(
        select(TemplateRecord)
        .where(
            TemplateRecord.team_id == team_id,
            TemplateRecord.archived.is_(False),
            TemplateRecord.deleted_at.is_(None),
        )
        .order_by(TemplateRecord.updated_at.desc())
    ).all()
    found: list[Candidate] = []
    for record in records:
        version = next((v for v in record.versions if v.version == record.latest_version), None)
        if version is not None:
            found.append(Candidate(template_from_version(version), record.id, version.id))
    return tuple(found)


def answer_lengths(session: Session, team_id: uuid.UUID) -> dict[str, int]:
    """Average length (characters) of past model answers per template, to estimate how long this
    answer will take; progress follows it."""
    rows = session.execute(
        select(Extraction.template, func.avg(func.length(Extraction.raw_output)))
        .where(Extraction.team_id == team_id)
        .group_by(Extraction.template)
    ).all()
    return {name: int(avg) for name, avg in rows if avg}


def next_run(session: Session, document: Document) -> int:
    """Number of the new run: highest existing run + 1. The first run is 1."""
    latest = session.scalar(
        select(func.max(Extraction.run)).where(Extraction.document_id == document.id)
    )
    return (latest or 0) + 1


def _report(
    document_id: uuid.UUID,
    stage: str,
    percent: int,
    factory: sessionmaker[Session] | None,
    claim: str | None = None,
) -> None:
    """Write progress to the database (its own short transaction, committed immediately so the
    dashboard and lists see it).

    With a claim, the write happens only while the document is still held by that claim;
    otherwise Superseded is raised. Progress is reported about once a second while the model
    writes, so a stopped run notices within a second and abandons its request to the model
    server."""
    conditions = [Document.id == document_id]
    if claim is not None:
        conditions.append(Document.claim == claim)
    with session_scope(factory) as session:
        written = session.scalar(
            update(Document)
            .where(*conditions)
            .values(stage=stage, progress=max(0, min(100, percent)))
            .returning(Document.id)
        )
    if claim is not None and written is None:
        raise Superseded


def _finish(document: Document) -> None:
    """Processing finished (success or failure): clear progress and record the time."""
    document.stage = None
    document.progress = None
    document.claim = None
    document.processed_at = datetime.now(UTC)


def _claim(document_id: uuid.UUID, factory: sessionmaker[Session] | None) -> str | None:
    """Change the status from uploaded to processing and return this run's claim token (None when
    the document was not waiting).

    A single UPDATE both checks and changes, so two workers never claim the same document."""
    claim = uuid.uuid4().hex
    with session_scope(factory) as session:
        claimed = session.scalar(
            update(Document)
            .where(Document.id == document_id, Document.status == "uploaded")
            .values(
                status="processing", error=None, stage=stored("准备处理"), progress=0, claim=claim
            )
            .returning(Document.id)
        )
    return claim if claimed is not None else None


def reset_interrupted(factory: sessionmaker[Session] | None = None) -> int:
    """Called at worker startup: documents left in processing by an interrupted run are set to
    failed and can be reprocessed.

        Only valid with a single worker (personal mode). Multiple workers would require a
    timeout-based check."""
    with session_scope(factory) as session:
        ids = session.scalars(
            update(Document)
            .where(Document.status == "processing")
            .values(
                status="failed",
                error=stored("处理过程中 worker 被关闭，请重新处理"),
                stage=None,
                progress=None,
                claim=None,
                processed_at=func.now(),
            )
            .returning(Document.id)
        ).all()
    return len(ids)


def describe_error(err: Exception) -> str:
    """Turn an exception into a human-readable sentence. Technical details go to the worker log."""
    if isinstance(err, ModelBackendError | ExtractionError | UnsupportedFileError):
        return str(err)  # These errors are already human-readable messages.
    if isinstance(err, StorageError):
        return stored("无法从对象存储读取原始文件")
    return stored("处理时发生意外错误（{kind}），详情请查看 worker 日志", kind=type(err).__name__)


def suffix_for(filename: str, content_type: str | None) -> str:
    guessed = mimetypes.guess_extension(content_type) if content_type else None
    return guessed or Path(filename).suffix
