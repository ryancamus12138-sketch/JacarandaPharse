"""Audit log: recording (record) and viewing.

Other modules' services call record() after an action that should be traceable: login, logout,
upload, reprocessing, field review, review completion, export, user management. Read-only
operations (lists, details, downloading originals) are not logged, which would drown the log."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from jacara.core.owner import Owner
from jacara.core.permissions import require
from jacara.models import AuditLog
from jacara.modules.audit.repository import AuditFilter, AuditRepository
from jacara.modules.audit.schemas import AuditOut, AuditPage


def record(
    session: Session,
    owner: Owner | None,
    action: str,
    target_type: str | None = None,
    target_id: uuid.UUID | str | None = None,
    **detail: Any,
) -> None:
    """Record an audit log entry in the same transaction as the business change: if the change is
    rolled back, no half-done entry remains."""
    AuditRepository(session).add(
        AuditLog(
            team_id=owner.team_id if owner else None,
            user_id=owner.user_id if owner else None,
            action=action,
            target_type=target_type,
            target_id=str(target_id) if target_id is not None else None,
            detail=detail,
        )
    )


EXPORT_LIMIT = 50_000
"""Maximum number of audit log entries per export."""


class AuditService:
    def __init__(self, session: Session, owner: Owner) -> None:
        self.repo = AuditRepository(session)
        self.owner = owner

    def page(self, limit: int, offset: int, found: AuditFilter | None = None) -> AuditPage:
        require(self.owner, "audit:read")
        logs, total = self.repo.page(self.owner.team_id, limit, offset, found)
        return AuditPage(items=[AuditOut.model_validate(log) for log in logs], total=total)

    def export(self, found: AuditFilter) -> AuditPage:
        """Export all entries matching the filters (at most EXPORT_LIMIT).
        The export itself is also logged (audit data leaving the system)."""
        require(self.owner, "audit:read")
        logs, total = self.repo.page(self.owner.team_id, EXPORT_LIMIT, 0, found)
        record(
            self.repo.session,
            self.owner,
            "audit:export",
            None,
            None,
            count=len(logs),
            filter_action=found.action,
            user=str(found.user_id) if found.user_id else None,
            since=found.since.isoformat() if found.since else None,
            until=found.until.isoformat() if found.until else None,
        )
        return AuditPage(items=[AuditOut.model_validate(log) for log in logs], total=total)
