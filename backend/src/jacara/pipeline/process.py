"""后台处理一个上传的文档：从对象存储取出文件 → 跑流水线 → 保存结果，并更新状态。

状态变化：uploaded → processing → done / failed

每一步各用一个短事务，而不是一个大事务包住整个处理过程：
处理一张收据要十几秒，期间"processing"状态要能被 API 查到，所以认领之后马上提交。
"""

from __future__ import annotations

import logging
import mimetypes
import tempfile
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from jacara.core.db import session_scope
from jacara.core.storage import ObjectStorage, StorageError
from jacara.models import Document, Extraction, TemplateRecord, TemplateVersion
from jacara.pipeline.classify import AutoChoice, Candidate
from jacara.pipeline.current import template_from_version
from jacara.pipeline.learn import Lesson, from_database
from jacara.pipeline.run import Pipeline, TemplateChoice
from jacara.pipeline.store import add_extractions, page_count
from jacara.pipeline.vision import VisionMode
from jacara.plugins.extractors import ExtractionError
from jacara.plugins.extractors.base import DISCOVER
from jacara.plugins.model_backends import ModelBackendError
from jacara.plugins.ocr.loader import PdfRead, UnsupportedFileError
from jacara.templates import DEFAULT_TEMPLATE

log = logging.getLogger(__name__)

Outcome = Literal["done", "failed", "skipped"]


def _lessons_for(
    pipeline: Pipeline,
    factory: sessionmaker[Session] | None,
    team_id: uuid.UUID,
    document_id: uuid.UUID,
) -> Callable[[str], list[Lesson]] | None:
    """打开了"从人改过的地方学"（ADR 0045）：按模板去数据库找以前人工改过的例子
    （同一个团队、审核完成的、不含这张单据自己）。没打开、抽取器不支持返回 None。"""
    from jacara.core.config import get_settings

    if not get_settings().jacara_learn or not hasattr(pipeline.extractor, "lessons"):
        return None

    def lessons(template: str) -> list[Lesson]:
        try:
            with session_scope(factory) as session:
                return from_database(session, team_id, template, exclude=document_id)
        except Exception:  # 找例子出错不影响处理
            log.exception("找以前改过的例子出错")
            return []

    return lessons


def process_document(
    document_id: uuid.UUID,
    *,
    storage: ObjectStorage,
    pipeline: Pipeline,
    factory: sessionmaker[Session] | None = None,
) -> Outcome:
    """处理一个文档。返回结果，不抛出异常：出错时把原因写进数据库，状态变成 failed。"""
    if not _claim(document_id, factory):
        # 已经被处理过、正在处理、或者文档不存在。同一个任务被重复投递时也会走到这里。
        log.info("跳过 %s：状态不是 uploaded", document_id)
        return "skipped"

    try:
        with session_scope(factory) as session:
            document = session.get_one(Document, document_id)
            key, filename, content_type = (
                document.storage_key,
                document.filename,
                document.content_type,
            )
            template, version_id = choose_template(session, document)
            split = document.split
            vision = cast(VisionMode | None, document.vision)
            pdf_read = cast(PdfRead | None, document.pdf_read)
            history = answer_lengths(session, document.team_id)
            run = next_run(session, document)
            reason = document.run_reason
            team_id = document.team_id
        if key is None:
            raise StorageError("没有原始文件")

        data = storage.get(key)
        with tempfile.TemporaryDirectory() as tmp:
            # 流水线按后缀判断文件类型，所以临时文件的后缀按 content_type 取
            path = Path(tmp) / f"original{suffix_for(filename, content_type)}"
            path.write_bytes(data)
            # 这一次处理用的例子；处理完换回原来的（同一个流水线还要处理别的文档）
            before = pipeline.lessons
            pipeline.lessons = _lessons_for(pipeline, factory, team_id, document_id)
            try:
                reports = pipeline.run(
                    path,
                    name=filename,
                    template=template,
                    progress=lambda stage, percent: _report(document_id, stage, percent, factory),
                    split=split,
                    vision=vision,
                    history=history,
                    pdf_read=pdf_read,
                )
            finally:
                pipeline.lessons = before

        _report(document_id, "保存结果", 98, factory)
        with session_scope(factory) as session:
            document = session.get_one(Document, document_id)
            add_extractions(
                session,
                document,
                reports,
                run=run,
                template_version_id=version_id,
                run_reason=reason,
            )
            document.page_count = page_count(reports)
            document.current_run = run  # 新一轮成功了才换过去；失败时旧结果照常能看
            document.run_reason = None
            document.status = "done"
            _finish(document)
        log.info("完成 %s：%d 张单据", document_id, len(reports))
        return "done"

    except Exception as err:
        log.exception("处理失败 %s", document_id)  # 完整的错误堆栈写进 worker 的日志
        with session_scope(factory) as session:
            document = session.get_one(Document, document_id)
            document.status = "failed"
            document.error = describe_error(err)
            _finish(document)
        return "failed"


def choose_template(
    session: Session, document: Document
) -> tuple[TemplateChoice, uuid.UUID | None]:
    """这次处理用什么模板（ADR 0030）：键值对发现、我的模板的某个版本，或者内置的收据模板。

    返回（给流水线的模板，模板版本的 id）。版本 id 记到抽取结果上，以后模板再改也能还原。
    自动判断（ADR 0038）：交给流水线所有能用的我的模板，每张单据各选各的，版本 id 记在每张上。
    """
    if document.auto:
        return AutoChoice(candidates(session, document.team_id)), None
    if document.discover:
        return DISCOVER, None
    if document.template_version_id is not None:
        version = session.get_one(TemplateVersion, document.template_version_id)
        return template_from_version(version), version.id
    return DEFAULT_TEMPLATE, None


def candidates(session: Session, team_id: uuid.UUID) -> tuple[Candidate, ...]:
    """自动判断能选的模板：这个团队（个人模式就是自己）没归档、没删的我的模板，各取最新版。"""
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
    """每个模板以前模型回答的平均长度（字数）：估这次要写多久，处理进度按它走。"""
    rows = session.execute(
        select(Extraction.template, func.avg(func.length(Extraction.raw_output)))
        .where(Extraction.team_id == team_id)
        .group_by(Extraction.template)
    ).all()
    return {name: int(avg) for name, avg in rows if avg}


def next_run(session: Session, document: Document) -> int:
    """新一轮的编号：已有的最大轮数 + 1。第一次处理是 1。"""
    latest = session.scalar(
        select(func.max(Extraction.run)).where(Extraction.document_id == document.id)
    )
    return (latest or 0) + 1


def _report(
    document_id: uuid.UUID, stage: str, percent: int, factory: sessionmaker[Session] | None
) -> None:
    """把进度写进数据库（单独一个短事务，马上提交，工作台和列表才查得到）。"""
    with session_scope(factory) as session:
        session.execute(
            update(Document)
            .where(Document.id == document_id)
            .values(stage=stage, progress=max(0, min(100, percent)))
        )


def _finish(document: Document) -> None:
    """处理完了（成功或失败）：清掉进度，记下时间。"""
    document.stage = None
    document.progress = None
    document.processed_at = datetime.now(UTC)


def _claim(document_id: uuid.UUID, factory: sessionmaker[Session] | None) -> bool:
    """把状态从 uploaded 改成 processing。

    一条 UPDATE 同时完成"检查 + 修改"，两个 worker 不会抢到同一个文档。
    """
    with session_scope(factory) as session:
        claimed = session.scalar(
            update(Document)
            .where(Document.id == document_id, Document.status == "uploaded")
            .values(status="processing", error=None, stage="准备处理", progress=0)
            .returning(Document.id)
        )
    return claimed is not None


def reset_interrupted(factory: sessionmaker[Session] | None = None) -> int:
    """worker 启动时调用：上次被中途关掉、停在 processing 的文档，改成 failed，可以重新处理。

    只适合只开一个 worker 的情况（个人模式）。多个 worker 时要改成按超时判断。
    """
    with session_scope(factory) as session:
        ids = session.scalars(
            update(Document)
            .where(Document.status == "processing")
            .values(
                status="failed",
                error="处理到一半时 worker 被关闭了，请重新处理",
                stage=None,
                progress=None,
                processed_at=func.now(),
            )
            .returning(Document.id)
        ).all()
    return len(ids)


def describe_error(err: Exception) -> str:
    """把异常变成给人看的一句话。技术细节在 worker 日志里。"""
    if isinstance(err, ModelBackendError | ExtractionError | UnsupportedFileError):
        return str(err)  # 这几种错误本身就是写给人看的说明
    if isinstance(err, StorageError):
        return "对象存储里取不到原始文件"
    return f"处理时出现意外错误（{type(err).__name__}），详情见 worker 日志"


def suffix_for(filename: str, content_type: str | None) -> str:
    guessed = mimetypes.guess_extension(content_type) if content_type else None
    return guessed or Path(filename).suffix
