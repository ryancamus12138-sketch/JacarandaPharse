"""登录、退出、认出"现在是谁"，以及管理用户。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from jacara.core.config import get_settings
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
from jacara.models import AuthSession, PasswordResetCode, User
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
"""重设码多久内有效。"""


class AuthError(Exception):
    """业务错误。code 让 router 知道该返回哪种 HTTP 状态。"""

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
    """新建用户。API（管理员）和命令行（建第一个管理员）都用这个。"""
    repo = AuthRepository(session)
    username = data.username.lower()
    if repo.user_by_username(username) is not None:
        raise AuthError("conflict", f"用户名 {username} 已经有人用了")
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
            # 不告诉对方是"用户名不存在"还是"密码错"，免得被人用来试出有哪些用户名。
            # 日志里记下来：用户名存在时记在那个用户名下，管理员能看到"有人在试这个账号的密码"
            who = owner_of(user) if user else None
            record(self.session, who, "auth:login_failed", username=username.lower())
            self.session.commit()  # 下面要抛出错误，请求会回滚；先提交，失败记录才能留下
            raise AuthError("bad_credentials", "用户名或密码不对")

        token = new_token()
        now = datetime.now(UTC)
        expires_at = now + timedelta(hours=get_settings().session_hours)
        self.repo.add(
            AuthSession(user_id=user.id, token_hash=token_hash(token), expires_at=expires_at)
        )
        record(self.session, owner_of(user), "auth:login")
        return LoginOut(token=token, expires_at=expires_at, user=UserOut.model_validate(user))

    def authenticate(self, token: str) -> Owner | None:
        """token → 当前用户。token 不对、过期了、退出过、用户被停用，都返回 None。"""
        auth = self.repo.live_session(token_hash(token), datetime.now(UTC))
        return owner_of(auth.user) if auth else None

    def logout(self, token: str) -> None:
        auth = self.repo.live_session(token_hash(token), datetime.now(UTC))
        if auth is None:
            return  # 本来就无效，当作已经退出
        auth.revoked_at = datetime.now(UTC)
        record(self.session, owner_of(auth.user), "auth:logout")

    def me(self, owner: Owner) -> MeOut:
        settings = get_settings()
        can: list[str] = sorted(allowed(owner))
        if settings.jacara_mode == "personal":
            return MeOut(mode="personal", role=PERSONAL_OWNER.role, permissions=can, user=None)
        user = self.repo.user(owner.user_id, owner.team_id)
        return MeOut(
            mode="team",
            role=owner.role,
            permissions=can,
            user=UserOut.model_validate(user) if user else None,
        )

    def change_password(self, owner: Owner, token: str | None, data: PasswordChange) -> None:
        """自己改密码。要先输原密码；改完以后别的设备上的登录都退出，正在用的这个不退出。"""
        if get_settings().jacara_mode == "personal" or token is None:
            raise AuthError("conflict", "个人模式没有账号，不用密码")
        user = self.repo.user(owner.user_id, owner.team_id)
        if user is None or not verify_password(user.password_hash, data.old_password):
            raise AuthError("invalid", "原密码不对")
        if verify_password(user.password_hash, data.new_password):
            raise AuthError("invalid", "新密码不能和原来的一样")
        user.password_hash = hash_password(data.new_password)
        self.repo.revoke_sessions(user.id, datetime.now(UTC), keep=token_hash(token))
        record(self.session, owner, "auth:password_change", "user", user.id)

    def reset_with_code(self, data: CodeReset) -> None:
        """忘记密码：凭管理员给的重设码，自己设新密码。设好后所有登录都退出，用新密码重新登录。"""
        now = datetime.now(UTC)
        user = self.repo.user_by_username(data.username)
        code_hash = token_hash(normalize_reset_code(data.code))
        reset = (
            self.repo.live_reset_code(user.id, code_hash, now) if user and user.is_active else None
        )
        if user is None or reset is None:
            # 和登录一样：不说是用户名不对还是码不对；失败要留记录，先提交再抛错
            who = owner_of(user) if user else None
            record(self.session, who, "auth:reset_failed", username=data.username.lower())
            self.session.commit()
            raise AuthError("bad_code", "用户名或重设码不对，或者重设码已经用过、过期了")
        if verify_password(user.password_hash, data.password):
            raise AuthError("invalid", "新密码不能和原来的一样")  # 码不作废，换一个密码再试
        user.password_hash = hash_password(data.password)
        reset.used_at = now
        self.repo.revoke_sessions(user.id, now)
        record(self.session, owner_of(user), "auth:password_reset", "user", user.id)


class UserService:
    """管理员管理本团队的用户。"""

    def __init__(self, session: Session, owner: Owner) -> None:
        self.session = session
        self.repo = AuthRepository(session)
        self.owner = owner

    def grantable(self) -> list[PermissionOut]:
        """可以开通给使用者的权限，前端用它显示勾选框。"""
        require(self.owner, "user:manage")
        return [PermissionOut(name=name, label=label) for name, label in GRANTABLE.items()]

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
            raise AuthError("not_found", "找不到这个用户")
        if user.id == self.owner.user_id and (
            (change.role is not None and change.role != user.role) or change.is_active is False
        ):
            raise AuthError("conflict", "不能修改自己的角色或停用自己，免得没人能管理")

        changed = change.model_dump(exclude_none=True)
        if "permissions" in changed:
            changed["permissions"] = sorted(set(changed["permissions"]))
        for name, value in changed.items():
            setattr(user, name, value)
        if change.is_active is False:
            self.repo.revoke_sessions(user.id, datetime.now(UTC))  # 停用后已登录的也马上失效
        record(self.session, self.owner, "user:update", "user", user.id, **changed)
        return UserOut.model_validate(user)

    def create_reset_code(self, user_id: uuid.UUID) -> ResetCodeOut:
        """给忘了密码的人生成一次性重设码。管理员只拿到码，不知道对方之后设的新密码。

        同一个人之前没用的码会作废，只有最新的这个能用。
        """
        require(self.owner, "user:manage")
        user = self.repo.user(user_id, self.owner.team_id)
        if user is None:
            raise AuthError("not_found", "找不到这个用户")
        if not user.is_active:
            raise AuthError("conflict", "这个用户已停用，先启用再生成重设码")
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
        # 审计日志里不记码本身，只记"谁给谁生成了"
        record(self.session, self.owner, "user:reset_code", "user", user.id)
        return ResetCodeOut(username=user.username, code=code, expires_at=expires_at)
