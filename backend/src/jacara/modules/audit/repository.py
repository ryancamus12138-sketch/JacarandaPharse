"""审计日志的数据库读写。只有 add 和查询，没有修改和删除（项目规则 3）。"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from jacara.models import AuditLog


class AuditRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, log: AuditLog) -> None:
        self.session.add(log)

    def page(
        self, team_id: uuid.UUID, limit: int, offset: int, action: str | None = None
    ) -> tuple[list[AuditLog], int]:
        """一个团队的审计日志，最新的在前。action 可以按开头筛选，例如 "auth:" 只看登录相关。"""
        where = [AuditLog.team_id == team_id]
        if action:
            where.append(AuditLog.action.startswith(action))
        query = (
            select(AuditLog)
            .where(*where)
            .order_by(AuditLog.created_at.desc(), AuditLog.id)
            .limit(limit)
            .offset(offset)
        )
        total = self.session.scalar(select(func.count()).select_from(AuditLog).where(*where))
        return list(self.session.scalars(query)), total or 0
