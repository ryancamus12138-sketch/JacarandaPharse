"""Audit log persistence. Only add and queries; no update or delete."""

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
    """Prefix filter, for example "auth:" for login-related events only."""
    user_id: uuid.UUID | None = None
    """Only events by this actor."""
    since: datetime | None = None
    until: datetime | None = None
    """Time range: since ≤ time < until (UTC)."""


class AuditRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, log: AuditLog) -> None:
        self.session.add(log)

    def page(
        self, team_id: uuid.UUID, limit: int, offset: int, found: AuditFilter | None = None
    ) -> tuple[list[AuditLog], int]:
        """Audit log of one team, newest first."""
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
