"""Account persistence."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session, selectinload

from jacara.core.i18n import tr
from jacara.core.owner import PERSONAL_OWNER
from jacara.models import AuthSession, PasswordResetCode, Team, User


class AuthRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def default_team(self) -> Team:
        """The single team of this installation; created if missing.

                Its id reuses the personal-mode team id, so documents created in personal mode
        remain in this team after switching to team mode."""
        team = self.session.scalars(select(Team).order_by(Team.created_at)).first()
        if team is None:
            team = Team(id=PERSONAL_OWNER.team_id, name=tr("默认团队"))
            self.session.add(team)
            self.session.flush()
        return team

    def user_by_username(self, username: str) -> User | None:
        return self.session.scalars(
            select(User).where(User.username == username.lower())
        ).one_or_none()

    def user(self, user_id: uuid.UUID, team_id: uuid.UUID) -> User | None:
        return self.session.scalars(
            select(User).where(User.id == user_id, User.team_id == team_id)
        ).one_or_none()

    def users(self, team_id: uuid.UUID) -> list[User]:
        return list(
            self.session.scalars(
                select(User).where(User.team_id == team_id).order_by(User.username)
            )
        )

    def add(self, row: User | AuthSession | PasswordResetCode) -> None:
        self.session.add(row)
        self.session.flush()

    def live_session(self, token_hash: str, now: datetime) -> AuthSession | None:
        """The session for this token if it has not expired, has not been logged out and its user is
        active."""
        query = (
            select(AuthSession)
            .join(User)
            .where(
                AuthSession.token_hash == token_hash,
                AuthSession.expires_at > now,
                AuthSession.revoked_at.is_(None),
                User.is_active,
            )
            .options(selectinload(AuthSession.user))
        )
        return self.session.scalars(query).one_or_none()

    def revoke_sessions(self, user_id: uuid.UUID, now: datetime, keep: str | None = None) -> None:
        """Revoke all active sessions of this user (on deactivation and password changes).

                keep: the session to keep (token hash). When users change their own password,
        other devices are logged out but the current one is not."""
        query = update(AuthSession).where(
            AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None)
        )
        if keep is not None:
            query = query.where(AuthSession.token_hash != keep)
        self.session.execute(query.values(revoked_at=now))

    def live_reset_code(
        self, user_id: uuid.UUID, code_hash: str, now: datetime
    ) -> PasswordResetCode | None:
        """A matching reset code of this user that has not expired and has not been used."""
        return self.session.scalars(
            select(PasswordResetCode).where(
                PasswordResetCode.user_id == user_id,
                PasswordResetCode.code_hash == code_hash,
                PasswordResetCode.expires_at > now,
                PasswordResetCode.used_at.is_(None),
            )
        ).one_or_none()

    def void_reset_codes(self, user_id: uuid.UUID, now: datetime) -> None:
        """Revoke this user's unused reset codes: once a new one is generated, older ones are no
        longer valid."""
        self.session.execute(
            update(PasswordResetCode)
            .where(PasswordResetCode.user_id == user_id, PasswordResetCode.used_at.is_(None))
            .values(used_at=now)
        )
