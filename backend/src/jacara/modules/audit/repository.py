"""审计日志的数据库读写。只有 add 和查询，没有修改和删除（项目规则 3）。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session

from jacara.models import AuditLog


@dataclass(frozen=True)
class AuditFilter:
    action: str | None = None
    """按开头筛，例如 "auth:" 只看登录相关。"""
    user_id: uuid.UUID | None = None
    """只看这个人做的。"""
    since: datetime | None = None
    until: datetime | None = None
    """时间范围：since ≤ 时间 < until（UTC）。"""


class AuditRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, log: AuditLog) -> None:
        self.session.add(log)

    def page(
        self, team_id: uuid.UUID, limit: int, offset: int, found: AuditFilter | None = None
    ) -> tuple[list[AuditLog], int]:
        """一个团队的审计日志，最新的在前。"""
        found = found or AuditFilter()
        where: list[ColumnElement[bool]] = [AuditLog.team_id == team_id]
        if found.action:
            where.append(AuditLog.action.startswith(found.action))
        if found.user_id:
            where.append(AuditLog.user_id == found.user_id)
        if found.since:
            where.append(AuditLog.created_at >= found.since)
        if found.until:
            where.append(AuditLog.created_at < found.until)
        query = (
            select(AuditLog)
            .where(*where)
            .order_by(AuditLog.created_at.desc(), AuditLog.id)
            .limit(limit)
            .offset(offset)
        )
        total = self.session.scalar(select(func.count()).select_from(AuditLog).where(*where))
        return list(self.session.scalars(query)), total or 0
