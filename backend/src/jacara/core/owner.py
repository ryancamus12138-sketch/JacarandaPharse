"""Data ownership and the current actor: which user and team a record belongs to, and who is acting
with which permissions.

Personal mode requires no login; the actor is always PERSONAL_OWNER (administrator). Team mode
resolves the user from the login token; see get_owner() in core/deps.py."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Owner:
    user_id: uuid.UUID
    team_id: uuid.UUID
    role: str = "admin"
    """admin or member."""
    permissions: frozenset[str] = field(default_factory=frozenset)
    """Permissions granted to this member by an administrator; see GRANTABLE in core/permissions.py.
    Ignored for administrators."""


PERSONAL_OWNER = Owner(
    user_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
    team_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
    role="admin",
)
