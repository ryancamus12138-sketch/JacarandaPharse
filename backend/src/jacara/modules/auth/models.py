"""账号相关的表：团队、用户、登录会话。

teams          团队。现在一个安装只有一个团队；第一个团队沿用个人模式的 team id，以前的数据都还在。
users          用户：用户名、密码哈希、角色、开通的权限、是否启用。
auth_sessions  登录会话：每次登录一条，存 token 的哈希和过期时间。退出登录就标记作废。
password_reset_codes  一次性重设码：忘记密码时管理员生成，用户凭它自己设新密码。
profiles       个人资料：头像，以及个人模式下的昵称（团队模式的昵称就是 users.display_name）。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from jacara.core.db import Base, JsonColumn, TimestampMixin, new_id


class Team(Base, TimestampMixin):
    __tablename__ = "teams"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(100))


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id"), index=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    """登录用的名字，全部小写。"""
    display_name: Mapped[str] = mapped_column(String(100), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    """Argon2id 哈希，不是密码原文。"""
    role: Mapped[str] = mapped_column(String(16))
    """admin（管理员）或 member（使用者）。"""
    permissions: Mapped[list[Any]] = mapped_column(JsonColumn, default=list, server_default="[]")
    """管理员给这个使用者开通的权限，例如 ["document:create", "value:review"]。"""
    is_active: Mapped[bool] = mapped_column(default=True)
    """停用的用户不能登录，已登录的会话也立刻失效。不删除用户：修改记录里还要显示是谁。"""

    team: Mapped[Team] = relationship()


class AuthSession(Base, TimestampMixin):
    __tablename__ = "auth_sessions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    """token 的 SHA-256。数据库里没有 token 原文。"""
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """退出登录的时间；没退出就是空的。"""

    user: Mapped[User] = relationship()


class PasswordResetCode(Base, TimestampMixin):
    """忘记密码时用的一次性重设码。管理员生成，告诉本人；本人凭它自己设新密码。

    和登录 token 一样只存哈希；15 分钟后过期，用过一次就作废。
    """

    __tablename__ = "password_reset_codes"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    """给谁的。"""
    code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    """重设码的 SHA-256。数据库里没有原文。"""
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """用掉或作废的时间；还能用就是空的。"""
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    """哪个管理员生成的。"""


class Profile(Base, TimestampMixin):
    """个人资料，每人一行（没设过头像、昵称的人没有这一行）。

    user_id 不连 users 表：个人模式没有用户账号，用的是固定的 PERSONAL_OWNER.user_id。
    头像图片存在对象存储 avatars/<user_id>.png，这里只记最后一次换头像的时间（也当版本号）。
    """

    __tablename__ = "profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    team_id: Mapped[uuid.UUID] = mapped_column(index=True)
    display_name: Mapped[str | None] = mapped_column(String(100))
    """个人模式下的昵称。团队模式不用这一列，昵称存在 users.display_name（管理员也能改）。"""
    avatar_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """最后一次换头像的时间；没有头像是空的。"""
