"""账号相关的数据库读写。"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session, selectinload

from jacara.core.owner import PERSONAL_OWNER
from jacara.models import AuthSession, Team, User


class AuthRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def default_team(self) -> Team:
        """现在一个安装只有一个团队。还没有就建一个。

        id 沿用个人模式的 team id：从个人模式切换到团队模式，以前的文档都还在这个团队里。
        """
        team = self.session.scalars(select(Team).order_by(Team.created_at)).first()
        if team is None:
            team = Team(id=PERSONAL_OWNER.team_id, name="默认团队")
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

    def add(self, row: User | AuthSession) -> None:
        self.session.add(row)
        self.session.flush()

    def live_session(self, token_hash: str, now: datetime) -> AuthSession | None:
        """token 对应的、没过期、没退出、用户没停用的会话。"""
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

    def revoke_sessions(self, user_id: uuid.UUID, now: datetime) -> None:
        """让这个用户所有还有效的登录都作废（停用用户、改密码时用）。"""
        self.session.execute(
            update(AuthSession)
            .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
            .values(revoked_at=now)
        )
