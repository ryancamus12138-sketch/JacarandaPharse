"""文档的数据库读写。只有这里写 SQL 查询，service 不直接碰 session.execute。

所有查询都按 team_id 过滤：一个团队永远看不到别的团队的数据。
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from jacara.models import Document, ExtractedValue, Extraction


class DocumentRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, document: Document) -> Document:
        self.session.add(document)
        self.session.flush()
        return document

    def get(self, document_id: uuid.UUID, team_id: uuid.UUID) -> Document | None:
        """取一个文档，连同它的抽取记录和字段值（一次查好，不会一个个去查）。"""
        query = (
            select(Document)
            .where(Document.id == document_id, Document.team_id == team_id)
            .options(selectinload(Document.extractions).selectinload(Extraction.values))
        )
        return self.session.scalars(query).one_or_none()

    def page(self, team_id: uuid.UUID, limit: int, offset: int) -> tuple[list[Document], int]:
        """按上传时间倒序，返回一页文档和总数。"""
        query = (
            select(Document)
            .where(Document.team_id == team_id)
            .order_by(Document.created_at.desc(), Document.id)
            .limit(limit)
            .offset(offset)
        )
        total = self.session.scalar(
            select(func.count()).select_from(Document).where(Document.team_id == team_id)
        )
        return list(self.session.scalars(query)), total or 0

    def review_counts(self, document_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
        """每个文档有多少个字段需要人工确认。"""
        if not document_ids:
            return {}
        query = (
            select(Extraction.document_id, func.count(ExtractedValue.id))
            .join(ExtractedValue, ExtractedValue.extraction_id == Extraction.id)
            .where(Extraction.document_id.in_(document_ids), ExtractedValue.needs_review)
            .group_by(Extraction.document_id)
        )
        return {doc_id: count for doc_id, count in self.session.execute(query)}
