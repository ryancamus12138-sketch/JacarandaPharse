"""我的模板的数据库查询。"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from jacara.models import Document, Extraction, TemplateRecord, TemplateVersion


class TemplateRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, record: TemplateRecord) -> TemplateRecord:
        self.session.add(record)
        self.session.flush()
        return record

    def add_version(self, version: TemplateVersion) -> TemplateVersion:
        self.session.add(version)
        self.session.flush()
        return version

    def list(self, team_id: uuid.UUID) -> list[TemplateRecord]:
        return list(
            self.session.scalars(
                select(TemplateRecord)
                .where(TemplateRecord.team_id == team_id, TemplateRecord.deleted_at.is_(None))
                .order_by(TemplateRecord.archived, TemplateRecord.updated_at.desc())
                .options(selectinload(TemplateRecord.versions))
            )
        )

    def get(self, template_id: uuid.UUID, team_id: uuid.UUID) -> TemplateRecord | None:
        return self.session.scalar(
            select(TemplateRecord)
            .where(
                TemplateRecord.id == template_id,
                TemplateRecord.team_id == team_id,
                TemplateRecord.deleted_at.is_(None),
            )
            .options(selectinload(TemplateRecord.versions))
        )

    def latest(self, record: TemplateRecord) -> TemplateVersion:
        return next(v for v in record.versions if v.version == record.latest_version)

    def usage_stats(self, team_id: uuid.UUID) -> dict[uuid.UUID, tuple[int, datetime | None]]:
        """每个模板：有几份文档用它、最近一份是什么时候传的（模板页排序、显示用）。

        上传时选了这个模板的，加上自动判断选中它的（ADR 0038，记在每张单据上）。"""
        chosen = select(TemplateVersion.template_id, Document.id, Document.created_at).join(
            Document, Document.template_version_id == TemplateVersion.id
        )
        picked = (
            select(TemplateVersion.template_id, Document.id, Document.created_at)
            .join(Extraction, Extraction.template_version_id == TemplateVersion.id)
            .join(Document, Document.id == Extraction.document_id)
            .where(Document.auto.is_(True), Extraction.run == Document.current_run)
        )
        used: dict[uuid.UUID, dict[uuid.UUID, datetime]] = {}
        for query in (chosen, picked):
            for template_id, document_id, created in self.session.execute(
                query.where(Document.team_id == team_id)
            ):
                used.setdefault(template_id, {})[document_id] = created
        return {
            template_id: (len(documents), max(documents.values(), default=None))
            for template_id, documents in used.items()
        }

    def usage(self, template_id: uuid.UUID) -> int:
        """有几份文档下次处理会用这个模板（任何版本）。"""
        return (
            self.session.scalar(
                select(func.count(Document.id))
                .join(TemplateVersion, Document.template_version_id == TemplateVersion.id)
                .where(TemplateVersion.template_id == template_id)
            )
            or 0
        )

    def documents_using(self, template_id: uuid.UUID) -> int:
        """有几份文档用过这个模板：处理结果（任何一轮）用过，或者正等着用它处理。"""
        versions = select(TemplateVersion.id).where(TemplateVersion.template_id == template_id)
        processed = select(Extraction.document_id).where(
            Extraction.template_version_id.in_(versions)
        )
        waiting = select(Document.id).where(Document.template_version_id.in_(versions))
        ids = set(self.session.scalars(processed)) | set(self.session.scalars(waiting))
        return len(ids)

    def delete(self, record: TemplateRecord) -> None:
        for version in record.versions:
            self.session.delete(version)
        self.session.delete(record)
        self.session.flush()
