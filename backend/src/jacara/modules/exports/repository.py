"""导出记录的数据库读写。所有查询都按 team_id 过滤。"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from jacara.models import User
from jacara.modules.exports.models import Export


class ExportRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, export: Export) -> Export:
        self.session.add(export)
        self.session.flush()
        return export

    def get(self, export_id: uuid.UUID, team_id: uuid.UUID) -> Export | None:
        query = select(Export).where(Export.id == export_id, Export.team_id == team_id)
        return self.session.scalars(query).one_or_none()

    def page(self, team_id: uuid.UUID, limit: int, offset: int) -> tuple[list[Export], int]:
        """最新的在前。"""
        query = (
            select(Export)
            .where(Export.team_id == team_id)
            .order_by(Export.created_at.desc(), Export.id)
            .limit(limit)
            .offset(offset)
        )
        total = self.session.scalar(
            select(func.count()).select_from(Export).where(Export.team_id == team_id)
        )
        return list(self.session.scalars(query)), total or 0

    def user_names(self, user_ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
        """用户 id → 显示名（没有显示名就用用户名）。"""
        if not user_ids:
            return {}
        query = select(User.id, User.display_name, User.username).where(User.id.in_(user_ids))
        return {uid: display or username for uid, display, username in self.session.execute(query)}

    def total_bytes(self, team_id: uuid.UUID) -> tuple[int, int]:
        """（导出了几次，存下的文件一共多大）。"""
        query = select(func.count(), func.coalesce(func.sum(Export.size_bytes), 0)).where(
            Export.team_id == team_id, Export.storage_key.is_not(None)
        )
        count, size = self.session.execute(query).one()
        return int(count), int(size)
