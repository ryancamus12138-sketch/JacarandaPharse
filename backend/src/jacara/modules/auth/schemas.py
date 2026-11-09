"""登录和用户管理的对外格式。密码只进不出：任何返回里都没有密码或密码哈希。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from jacara.core.security import MIN_PASSWORD_LENGTH

RoleName = Literal["admin", "member"]
"""admin：管理员；member：使用者。"""

Permission = Literal["document:create", "document:process", "value:review", "document:review"]
"""可以开通给使用者的权限，和 core/permissions.py 的 GRANTABLE 一致。"""


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    username: str
    display_name: str
    role: str
    permissions: list[str]
    """管理员给这个使用者开通的权限。"""
    is_active: bool
    created_at: datetime


class LoginOut(BaseModel):
    token: str
    """之后每个请求都放在请求头里：Authorization: Bearer <token>"""
    expires_at: datetime
    user: UserOut


class UserCreate(BaseModel):
    username: str = Field(min_length=2, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    """只能用英文字母、数字和 _ . -，不区分大小写。"""
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=256)
    display_name: str = Field(default="", max_length=100)
    role: RoleName = "member"
    permissions: list[Permission] = []
    """开通哪些权限（只对使用者有效），例如 ["document:create", "value:review"]。"""


class UserUpdate(BaseModel):
    """只改给出的字段。"""

    role: RoleName | None = None
    permissions: list[Permission] | None = None
    """新的权限清单（整个替换，不是追加）。"""
    is_active: bool | None = None
    display_name: str | None = Field(default=None, max_length=100)


class MeOut(BaseModel):
    """现在是谁。前端靠 mode 决定要不要显示登录页。"""

    mode: Literal["personal", "team"]
    role: str
    permissions: list[str]
    """现在能做的全部操作（包括每人都有的）。前端用它决定显示哪些按钮。"""
    user: UserOut | None
    """个人模式下没有用户账号，是 null。"""


class PermissionOut(BaseModel):
    name: str
    label: str
