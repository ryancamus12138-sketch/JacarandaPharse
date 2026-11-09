"""登录和用户管理接口：/api/v1/auth、/api/v1/users。"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from jacara.core.deps import get_owner, get_session, get_token
from jacara.core.owner import Owner
from jacara.modules.auth.schemas import (
    LoginIn,
    LoginOut,
    MeOut,
    PermissionOut,
    UserCreate,
    UserOut,
    UserUpdate,
)
from jacara.modules.auth.service import AuthError, AuthService, UserService

auth_router = APIRouter(prefix="/auth", tags=["登录"])
users_router = APIRouter(prefix="/users", tags=["用户管理"])

_STATUS = {
    "bad_credentials": status.HTTP_401_UNAUTHORIZED,
    "not_found": status.HTTP_404_NOT_FOUND,
    "conflict": status.HTTP_409_CONFLICT,
}

DbSession = Annotated[Session, Depends(get_session)]
CurrentOwner = Annotated[Owner, Depends(get_owner)]


def _http_error(err: AuthError) -> HTTPException:
    return HTTPException(status_code=_STATUS.get(err.code, 400), detail=err.message)


@auth_router.post("/login", summary="登录")
def login(body: LoginIn, session: DbSession) -> LoginOut:
    """用户名 + 密码，换一个 token。之后的请求在请求头里带上：`Authorization: Bearer <token>`。

    在 /docs 页面：复制返回的 token，点右上角 **Authorize**，粘贴进去。
    """
    try:
        return AuthService(session).login(body.username, body.password)
    except AuthError as err:
        raise _http_error(err) from err


@auth_router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, summary="退出登录")
def logout(session: DbSession, token: Annotated[str | None, Depends(get_token)]) -> Response:
    """让当前 token 作废。之后再用它会得到 401。"""
    if token:
        AuthService(session).logout(token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@auth_router.get("/me", summary="当前用户")
def me(session: DbSession, owner: CurrentOwner) -> MeOut:
    """现在是谁、什么角色、能做哪些操作、是个人模式还是团队模式。"""
    return AuthService(session).me(owner)


@users_router.get("/permissions", summary="可开通的权限")
def list_permissions(session: DbSession, owner: CurrentOwner) -> list[PermissionOut]:
    """管理员可以给使用者开通的权限。新建或修改用户时，permissions 里填这里的 name。"""
    return UserService(session, owner).grantable()


@users_router.get("", summary="用户列表")
def list_users(session: DbSession, owner: CurrentOwner) -> list[UserOut]:
    """本团队所有用户（管理员）。"""
    return UserService(session, owner).list()


@users_router.post("", status_code=status.HTTP_201_CREATED, summary="新建用户")
def create_user(body: UserCreate, session: DbSession, owner: CurrentOwner) -> UserOut:
    """新建一个本团队的用户（管理员）。密码至少 8 个字符。

    使用者默认能看、能导出；要上传、修改字段等，在 permissions 里开通，例如：
    `{"username": "xiaowang", "password": "...", "permissions": ["document:create"]}`
    """
    try:
        return UserService(session, owner).create(body)
    except AuthError as err:
        raise _http_error(err) from err


@users_router.patch("/{user_id}", summary="修改用户")
def update_user(
    user_id: uuid.UUID, body: UserUpdate, session: DbSession, owner: CurrentOwner
) -> UserOut:
    """改角色、开通或取消权限、改显示名、停用或重新启用（管理员）。只改给出的字段。

    - 开通权限：`{"permissions": ["document:create", "value:review"]}`（整个替换，不是追加）
    - 停用：`{"is_active": false}`，这个用户已登录的也马上失效
    - 改了马上生效，对方不用重新登录。
    """
    try:
        return UserService(session, owner).update(user_id, body)
    except AuthError as err:
        raise _http_error(err) from err
