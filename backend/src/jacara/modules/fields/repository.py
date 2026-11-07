"""字段值的数据库读写。只新增审核记录，不提供修改或删除的方法（项目规则 3）。"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from jacara.models import ExtractedValue, Extraction, ValueRevision


class ValueRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, value_id: uuid.UUID, team_id: uuid.UUID) -> ExtractedValue | None:
        """取一个字段，连同它的审核记录和所属文档（要看文档状态）。"""
        query = (
            select(ExtractedValue)
            .where(ExtractedValue.id == value_id, ExtractedValue.team_id == team_id)
            .options(
                selectinload(ExtractedValue.revisions),
                selectinload(ExtractedValue.extraction).selectinload(Extraction.document),
            )
        )
        return self.session.scalars(query).one_or_none()

    def add_revision(self, revision: ValueRevision) -> ValueRevision:
        self.session.add(revision)
        self.session.flush()  # 马上写入：seq 重复（两个人同时改）会在这里报错
        return revision
