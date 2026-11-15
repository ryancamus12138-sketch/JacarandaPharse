"""Audit log table: who did what, and when. Append-only: rows are never updated or deleted."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from jacara.core.db import Base, TimestampMixin, new_id


class AuditLog(Base, TimestampMixin):
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    team_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    """Team concerned. Empty when the actor is unknown (for example failed logins)."""
    user_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    """Actor."""
    action: Mapped[str] = mapped_column(String(64), index=True)
    """Action, for example auth:login, document:upload, value:edit."""
    target_type: Mapped[str | None] = mapped_column(String(32))
    """Target type: document, value, user, …"""
    target_id: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[dict[str, Any]] = mapped_column(default=dict)
    """Additional details, for example file name, number of exported documents, or the user name
    entered in a failed login."""
