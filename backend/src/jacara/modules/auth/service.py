"""Login, logout, identifying the current user, and user management."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from jacara.core.config import get_settings
from jacara.core.i18n import tr
from jacara.core.owner import PERSONAL_OWNER, Owner
from jacara.core.permissions import GRANTABLE, allowed, require
from jacara.core.security import (
    hash_password,
    new_reset_code,
    new_token,
    normalize_reset_code,
    token_hash,
    verify_password,
)
from jacara.models import AuthSession, PasswordResetCode, Profile, User
from jacara.modules.audit.service import record
from jacara.modules.auth.repository import AuthRepository
from jacara.modules.auth.schemas import (
    CodeReset,
    LoginOut,
    MeOut,
    PasswordChange,
    PermissionOut,
    ResetCodeOut,
    UserCreate,
    UserOut,
    UserUpdate,
)

RESET_CODE_MINUTES = 15
"""Validity period of reset codes."""


class AuthError(Exception):
    """Business error. code tells the router which HTTP status to return."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def owner_of(user: User) -> Owner:
    return Owner(
        user_id=user.id,
        team_id=user.team_id,
        role=user.role,
        permissions=frozenset(user.permissions),
    )


def add_user(session: Session, team_id: uuid.UUID, data: UserCreate) -> User:
    """Create a user. Used by the API (administrators) and the CLI (first administrator)."""
    repo = AuthRepository(session)
    username = data.username.lower()
    if repo.user_by_username(username) is not None:
        raise AuthError("conflict", tr("用户名 {username} 已经有人用了", username=username))
    user = User(
        team_id=team_id,
        username=username,
        display_name=data.display_name,
        password_hash=hash_password(data.password),
        role=data.role,
        permissions=sorted(set(data.permissions)),
    )
    repo.add(user)
    return user


class AuthService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = AuthRepository(session)

    def login(self, username: str, password: str) -> LoginOut:
        user = self.repo.user_by_username(username)
        ok = verify_password(user.password_hash if user else None, password)
        if user is None or not ok or not user.is_active:
            # Do not reveal whether the user name or the password was wrong, so the endpoint cannot
            # be used to enumerate user names. Log the attempt: when the user name exists, it is
            # logged under that user so administrators can see repeated password attempts on the
            # account.
            who = owner_of(user) if user else None
            record(self.session, who, "auth:login_failed", username=username.lower())
            # The request is rolled back by the error raised below; commit first so that the failure
            # record persists.
            self.session.commit()
            raise AuthError("bad_credentials", tr("用户名或密码不对"))

        token = new_token()
        now = datetime.now(UTC)
        expires_at = now + timedelta(hours=get_settings().session_hours)
        self.repo.add(
            AuthSession(user_id=user.id, token_hash=token_hash(token), expires_at=expires_at)
        )
        record(self.session, owner_of(user), "auth:login")
        return LoginOut(token=token, expires_at=expires_at, user=UserOut.model_validate(user))

    def authenticate(self, token: str) -> Owner | None:
        """Token → current user. None if the token is invalid, expired, logged out, or the user is
        deactivated."""
        auth = self.repo.live_session(token_hash(token), datetime.now(UTC))
        return owner_of(auth.user) if auth else None

    def logout(self, token: str) -> None:
        auth = self.repo.live_session(token_hash(token), datetime.now(UTC))
        if auth is None:
            return  # Already invalid; treat as logged out.
        auth.revoked_at = datetime.now(UTC)
        record(self.session, owner_of(auth.user), "auth:logout")

    def me(self, owner: Owner) -> MeOut:
        settings = get_settings()
        can: list[str] = sorted(allowed(owner))
        profile = self.session.get(Profile, owner.user_id)
        avatar_at = profile.avatar_at if profile else None
        if settings.jacara_mode == "personal":
            name = (profile.display_name if profile else None) or tr("本机用户")
            return MeOut(
                mode="personal",
                role=PERSONAL_OWNER.role,
                permissions=can,
                user=None,
                shown_name=name,
                avatar_at=avatar_at,
            )
        user = self.repo.user(owner.user_id, owner.team_id)
        return MeOut(
            mode="team",
            role=owner.role,
            permissions=can,
            user=UserOut.model_validate(user) if user else None,
            shown_name=(user.display_name or user.username) if user else "",
            avatar_at=avatar_at,
        )

    def change_password(self, owner: Owner, token: str | None, data: PasswordChange) -> None:
        """Change one's own password; the current password is required. Other devices are logged
        out; the current session stays."""
        if get_settings().jacara_mode == "personal" or token is None:
            raise AuthError("conflict", tr("个人模式没有账号，不用密码"))
        user = self.repo.user(owner.user_id, owner.team_id)
        if user is None or not verify_password(user.password_hash, data.old_password):
            raise AuthError("invalid", tr("原密码不对"))
        if verify_password(user.password_hash, data.new_password):
            raise AuthError("invalid", tr("新密码不能和原来的一样"))
        user.password_hash = hash_password(data.new_password)
        self.repo.revoke_sessions(user.id, datetime.now(UTC), keep=token_hash(token))
        record(self.session, owner, "auth:password_change", "user", user.id)

    def reset_with_code(self, data: CodeReset) -> None:
        """Forgotten password: set a new password with the reset code from an administrator. All
        sessions are then logged out; log in again with the new password."""
        now = datetime.now(UTC)
        user = self.repo.user_by_username(data.username)
        code_hash = token_hash(normalize_reset_code(data.code))
        reset = (
            self.repo.live_reset_code(user.id, code_hash, now) if user and user.is_active else None
        )
        if user is None or reset is None:
            # As with login: do not reveal whether the user name or the code was wrong; record the
            # failure and commit before raising.
            who = owner_of(user) if user else None
            record(self.session, who, "auth:reset_failed", username=data.username.lower())
            self.session.commit()
            raise AuthError("bad_code", tr("用户名或重设码不对，或者重设码已经用过、过期了"))
        if verify_password(user.password_hash, data.password):
            # The code is not revoked; the user may retry with a different password.
            raise AuthError("invalid", tr("新密码不能和原来的一样"))
        user.password_hash = hash_password(data.password)
        reset.used_at = now
        self.repo.revoke_sessions(user.id, now)
        record(self.session, owner_of(user), "auth:password_reset", "user", user.id)


class UserService:
    """User management for team administrators."""

    def __init__(self, session: Session, owner: Owner) -> None:
        self.session = session
        self.repo = AuthRepository(session)
        self.owner = owner

    def grantable(self) -> list[PermissionOut]:
        """Permissions grantable to members; the frontend renders them as checkboxes."""
        require(self.owner, "user:manage")
        return [PermissionOut(name=name, label=tr(label)) for name, label in GRANTABLE.items()]

    def list(self) -> list[UserOut]:
        require(self.owner, "user:manage")
        return [UserOut.model_validate(u) for u in self.repo.users(self.owner.team_id)]

    def create(self, data: UserCreate) -> UserOut:
        require(self.owner, "user:manage")
        user = add_user(self.session, self.owner.team_id, data)
        record(
            self.session,
            self.owner,
            "user:create",
            "user",
            user.id,
            role=user.role,
            permissions=user.permissions,
        )
        return UserOut.model_validate(user)

    def update(self, user_id: uuid.UUID, change: UserUpdate) -> UserOut:
        require(self.owner, "user:manage")
        user = self.repo.user(user_id, self.owner.team_id)
        if user is None:
            raise AuthError("not_found", tr("找不到这个用户"))
        if user.id == self.owner.user_id and (
            (change.role is not None and change.role != user.role) or change.is_active is False
        ):
            raise AuthError("conflict", tr("不能修改自己的角色或停用自己，免得没人能管理"))

        changed = change.model_dump(exclude_none=True)
        if "permissions" in changed:
            changed["permissions"] = sorted(set(changed["permissions"]))
        for name, value in changed.items():
            setattr(user, name, value)
        if change.is_active is False:
            # Existing sessions are revoked immediately on deactivation.
            self.repo.revoke_sessions(user.id, datetime.now(UTC))
        record(self.session, self.owner, "user:update", "user", user.id, **changed)
        return UserOut.model_validate(user)

    def create_reset_code(self, user_id: uuid.UUID) -> ResetCodeOut:
        """Generate a one-time reset code for a user who forgot the password. The administrator
        receives only the code and never learns the new password.

                Earlier unused codes of the same user are revoked; only the newest one is valid."""
        require(self.owner, "user:manage")
        user = self.repo.user(user_id, self.owner.team_id)
        if user is None:
            raise AuthError("not_found", tr("找不到这个用户"))
        if not user.is_active:
            raise AuthError("conflict", tr("这个用户已停用，先启用再生成重设码"))
        now = datetime.now(UTC)
        self.repo.void_reset_codes(user.id, now)
        code = new_reset_code()
        expires_at = now + timedelta(minutes=RESET_CODE_MINUTES)
        self.repo.add(
            PasswordResetCode(
                user_id=user.id,
                code_hash=token_hash(normalize_reset_code(code)),
                expires_at=expires_at,
                created_by=self.owner.user_id,
            )
        )
        # The audit log records only who generated a code for whom, never the code itself.
        record(self.session, self.owner, "user:reset_code", "user", user.id)
        return ResetCodeOut(username=user.username, code=code, expires_at=expires_at)
