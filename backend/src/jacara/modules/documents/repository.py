"""文档的数据库读写。只有这里写 SQL 查询，service 不直接碰 session.execute。

所有查询都按 team_id 过滤：一个团队永远看不到别的团队的数据。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import ColumnElement, and_, exists, func, or_, select
from sqlalchemy.orm import Session, selectinload

from jacara.models import Document, ExtractedValue, Extraction, ValueRevision


@dataclass(frozen=True)
class DocumentFilter:
    """文档列表的筛选条件。都不填 = 全部。"""

    statuses: tuple[str, ...] = ()
    """状态，可以选几个：uploaded、processing、done、failed、reviewed。"""
    template: str | None = None
    """用哪个模板抽取的（任意一页用了就算）。"""
    since: datetime | None = None
    """上传时间不早于（含）。"""
    until: datetime | None = None
    """上传时间早于（不含）。"""
    search: str = ""
    """文件名里包含这些字（不分大小写）。"""
    pending_only: bool = False
    """只要还有字段等人确认的。"""


ORDER = (Document.created_at.desc(), Document.id)
"""列表的顺序：上传时间倒序（最新的在最上面）。上一份 / 下一份也按这个顺序。"""


class DocumentRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, document: Document) -> Document:
        self.session.add(document)
        self.session.flush()
        return document

    def get(self, document_id: uuid.UUID, team_id: uuid.UUID) -> Document | None:
        """取一个文档，连同抽取记录、字段值、审核记录（一次查好，不会一个个去查）。"""
        query = (
            select(Document)
            .where(Document.id == document_id, Document.team_id == team_id)
            .options(
                selectinload(Document.extractions)
                .selectinload(Extraction.values)
                .selectinload(ExtractedValue.revisions)
            )
        )
        return self.session.scalars(query).one_or_none()

    def get_many(self, document_ids: list[uuid.UUID], team_id: uuid.UUID) -> list[Document]:
        """一次取多个文档（导出用），同样连同抽取、字段、审核记录一起查好。顺序不保证。"""
        query = (
            select(Document)
            .where(Document.id.in_(document_ids), Document.team_id == team_id)
            .options(
                selectinload(Document.extractions)
                .selectinload(Extraction.values)
                .selectinload(ExtractedValue.revisions)
            )
        )
        return list(self.session.scalars(query))

    def page(
        self, team_id: uuid.UUID, limit: int, offset: int, filters: DocumentFilter | None = None
    ) -> tuple[list[Document], int]:
        """按上传时间倒序，返回一页文档和总数（符合筛选条件的）。"""
        where = [Document.team_id == team_id, *self._conditions(filters or DocumentFilter())]
        query = select(Document).where(*where).order_by(*ORDER).limit(limit).offset(offset)
        total = self.session.scalar(select(func.count()).select_from(Document).where(*where))
        return list(self.session.scalars(query)), total or 0

    def _conditions(self, f: DocumentFilter) -> list[ColumnElement[bool]]:
        conditions: list[ColumnElement[bool]] = []
        if f.statuses:
            conditions.append(Document.status.in_(f.statuses))
        if f.template:
            conditions.append(
                exists().where(
                    Extraction.document_id == Document.id,
                    Extraction.run == Document.current_run,
                    Extraction.template == f.template,
                )
            )
        if f.since is not None:
            conditions.append(Document.created_at >= f.since)
        if f.until is not None:
            conditions.append(Document.created_at < f.until)
        if f.search.strip():
            # 用户输入里的 % 和 _ 当普通字符，不当通配符
            text = f.search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            conditions.append(Document.filename.ilike(f"%{text}%", escape="\\"))
        if f.pending_only:
            conditions.append(exists().where(*self._pending_value()))
        return conditions

    @staticmethod
    def _pending_value() -> list[ColumnElement[bool]]:
        """ "这个文档有一个字段等人确认"的条件：标了需要确认，并且没有任何审核记录。"""
        reviewed = exists().where(ValueRevision.value_id == ExtractedValue.id)
        return [
            Extraction.document_id == Document.id,
            Extraction.run == Document.current_run,
            ExtractedValue.extraction_id == Extraction.id,
            ExtractedValue.needs_review.is_(True),
            ~reviewed,
        ]

    def neighbors(
        self, document: Document, statuses: tuple[str, ...]
    ) -> tuple[uuid.UUID | None, uuid.UUID | None, int]:
        """按列表顺序：document 的上一份、下一份（只看这些状态），这些状态的共几份（不算自己）。"""
        same = [
            Document.team_id == document.team_id,
            Document.status.in_(statuses),
            Document.id != document.id,
        ]
        # 列表是 created_at 倒序、id 正序：排在后面 = 更早上传，或者同一时间但 id 更大
        later = or_(
            Document.created_at < document.created_at,
            and_(Document.created_at == document.created_at, Document.id > document.id),
        )
        following = self.session.scalar(
            select(Document.id).where(*same, later).order_by(*ORDER).limit(1)
        )
        preceding = self.session.scalar(
            select(Document.id)
            .where(*same, ~later)
            .order_by(Document.created_at.asc(), Document.id.desc())
            .limit(1)
        )
        count = self.session.scalar(select(func.count()).select_from(Document).where(*same))
        return preceding, following, count or 0

    def status_counts(self, team_id: uuid.UUID) -> dict[str, int]:
        query = (
            select(Document.status, func.count())
            .where(Document.team_id == team_id)
            .group_by(Document.status)
        )
        return {status: n for status, n in self.session.execute(query)}

    def processed_since(self, team_id: uuid.UUID, since: datetime) -> int:
        """since 之后处理完的文档数（成功的，不算失败的）。"""
        query = select(func.count()).where(
            Document.team_id == team_id,
            Document.processed_at >= since,
            Document.status.in_(("done", "reviewed")),
        )
        return self.session.scalar(query) or 0

    def reviewed_since(self, team_id: uuid.UUID, since: datetime) -> int:
        """since 之后完成审核的文档数。"""
        query = select(func.count()).where(
            Document.team_id == team_id,
            Document.reviewed_at >= since,
            Document.status == "reviewed",
        )
        return self.session.scalar(query) or 0

    def in_progress(self, team_id: uuid.UUID, limit: int) -> list[Document]:
        """排队中和处理中的文档：处理中的在前，然后按上传先后（先传的先处理）。"""
        query = (
            select(Document)
            .where(Document.team_id == team_id, Document.status.in_(("uploaded", "processing")))
            .order_by((Document.status == "processing").desc(), Document.created_at, Document.id)
            .limit(limit)
        )
        return list(self.session.scalars(query))

    def failed(self, team_id: uuid.UUID, limit: int) -> list[Document]:
        query = (
            select(Document)
            .where(Document.team_id == team_id, Document.status == "failed")
            .order_by(*ORDER)
            .limit(limit)
        )
        return list(self.session.scalars(query))

    def most_pending(self, team_id: uuid.UUID, limit: int) -> list[tuple[Document, int]]:
        """待审核的文档里，等人确认的字段最多的几份（一样多的，先传的在前）。"""
        reviewed = exists().where(ValueRevision.value_id == ExtractedValue.id)
        pending = func.count(ExtractedValue.id)
        query = (
            select(Document, pending)
            .join(Extraction, Extraction.document_id == Document.id)
            .join(ExtractedValue, ExtractedValue.extraction_id == Extraction.id)
            .where(
                Document.team_id == team_id,
                Document.status == "done",
                Extraction.run == Document.current_run,
                ExtractedValue.needs_review,
                ~reviewed,
            )
            .group_by(Document.id)
            .order_by(pending.desc(), Document.created_at, Document.id)
            .limit(limit)
        )
        return [(d, n) for d, n in self.session.execute(query)]

    def pending_fields(self, team_id: uuid.UUID) -> int:
        """待审核的文档里，一共还有多少字段等人确认。"""
        reviewed = exists().where(ValueRevision.value_id == ExtractedValue.id)
        query = (
            select(func.count(ExtractedValue.id))
            .join(Extraction, ExtractedValue.extraction_id == Extraction.id)
            .join(Document, Extraction.document_id == Document.id)
            .where(
                Document.team_id == team_id,
                Document.status == "done",
                Extraction.run == Document.current_run,
                ExtractedValue.needs_review,
                ~reviewed,
            )
        )
        return self.session.scalar(query) or 0

    def average_page_ms(self, team_id: uuid.UUID, since: datetime) -> int | None:
        """最近处理的每页平均用时（抽取一页的时间，不含排队）。"""
        query = select(func.avg(Extraction.duration_ms)).where(
            Extraction.team_id == team_id, Extraction.created_at >= since
        )
        value = self.session.scalar(query)
        return None if value is None else int(value)

    def review_counts(self, document_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
        """每个文档还有多少个字段等人确认：标了需要确认，并且还没有任何审核记录。"""
        if not document_ids:
            return {}
        reviewed = exists().where(ValueRevision.value_id == ExtractedValue.id)
        query = (
            select(Extraction.document_id, func.count(ExtractedValue.id))
            .join(ExtractedValue, ExtractedValue.extraction_id == Extraction.id)
            .join(Document, Extraction.document_id == Document.id)
            .where(
                Extraction.document_id.in_(document_ids),
                Extraction.run == Document.current_run,
                ExtractedValue.needs_review,
                ~reviewed,
            )
            .group_by(Extraction.document_id)
        )
        return {doc_id: count for doc_id, count in self.session.execute(query)}
