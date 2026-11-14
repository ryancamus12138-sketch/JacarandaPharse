"""Authentication and user management API: /api/v1/auth, /api/v1/users."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from sqlalchemy.orm import Session

from jacara.core.deps import get_owner, get_session, get_storage, get_token
from jacara.core.i18n import render
from jacara.core.owner import Owner
from jacara.core.storage import ObjectStorage
from jacara.modules.auth.profile import ProfileService
from jacara.modules.auth.schemas import (
    CodeReset,
    LoginIn,
    LoginOut,
    MeOut,
    PasswordChange,
    PermissionOut,
    ProfileOut,
    ProfileUpdate,
    ResetCodeOut,
    UserCreate,
    UserOut,
    UserUpdate,
)
from jacara.modules.auth.service import AuthError, AuthService, UserService

auth_router = APIRouter(prefix="/auth", tags=["登录"])
users_router = APIRouter(prefix="/users", tags=["用户管理"])

_STATUS = {
    "bad_credentials": status.HTTP_401_UNAUTHORIZED,
    "bad_code": status.HTTP_400_BAD_REQUEST,
    "not_found": status.HTTP_404_NOT_FOUND,
    "conflict": status.HTTP_409_CONFLICT,
    "invalid": status.HTTP_422_UNPROCESSABLE_CONTENT,
}

DbSession = Annotated[Session, Depends(get_session)]
CurrentOwner = Annotated[Owner, Depends(get_owner)]
Storage = Annotated[ObjectStorage, Depends(get_storage)]


def _http_error(err: AuthError) -> HTTPException:
    return HTTPException(status_code=_STATUS.get(err.code, 400), detail=render(err.message))


@auth_router.post("/login", summary="登录")
def login(body: LoginIn, session: DbSession) -> LoginOut:
    """Exchange user name and password for a token. Send it with subsequent requests:
    `Authorization: Bearer <token>`.

        On the /docs page: copy the returned token, click **Authorize** in the top-right corner
    and paste it."""
    try:
        return AuthService(session).login(body.username, body.password)
    except AuthError as err:
        raise _http_error(err) from err


@auth_router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, summary="退出登录")
def logout(session: DbSession, token: Annotated[str | None, Depends(get_token)]) -> Response:
    """Revoke the current token. Subsequent use returns 401."""
    if token:
        AuthService(session).logout(token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@auth_router.get("/me", summary="当前用户")
def me(session: DbSession, owner: CurrentOwner) -> MeOut:
    """Current user, role, permitted operations, and whether the instance runs in personal or team
    mode."""
    return AuthService(session).me(owner)


@auth_router.post("/password", status_code=status.HTTP_204_NO_CONTENT, summary="修改自己的密码")
def change_password(
    body: PasswordChange,
    session: DbSession,
    owner: CurrentOwner,
    token: Annotated[str | None, Depends(get_token)],
) -> Response:
    """Change one's own password after login; the current password is required. Sessions on other
    devices are logged out; the current one is not."""
    try:
        AuthService(session).change_password(owner, token, body)
    except AuthError as err:
        raise _http_error(err) from err
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@auth_router.post(
    "/reset-password", status_code=status.HTTP_204_NO_CONTENT, summary="用重设码设新密码"
)
def reset_password(body: CodeReset, session: DbSession) -> Response:
    """Forgotten password: user name + reset code from an administrator + new password. No login
    required.

        On success all sessions of the account are logged out; log in again with the new
    password."""
    try:
        AuthService(session).reset_with_code(body)
    except AuthError as err:
        raise _http_error(err) from err
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@auth_router.get("/profile", summary="个人资料")
def get_profile(session: DbSession, owner: CurrentOwner) -> ProfileOut:
    """Own display name, avatar, user name, team, role and permitted operations."""
    return ProfileService(session, owner).get()


@auth_router.patch("/profile", summary="改昵称")
def update_profile(body: ProfileUpdate, session: DbSession, owner: CurrentOwner) -> ProfileOut:
    """Change one's own display name. An empty string removes it (the user name is shown)."""
    try:
        return ProfileService(session, owner).update(body)
    except AuthError as err:
        raise _http_error(err) from err


@auth_router.get(
    "/profile/avatar",
    summary="自己的头像",
    response_class=Response,
    responses={200: {"content": {"image/png": {}}}},
)
def get_avatar(session: DbSession, owner: CurrentOwner, storage: Storage) -> Response:
    """256 × 256 PNG. 404 when there is no avatar."""
    try:
        data = ProfileService(session, owner, storage).avatar()
    except AuthError as err:
        raise _http_error(err) from err
    return Response(content=data, media_type="image/png", headers={"Cache-Control": "private"})


@auth_router.put("/profile/avatar", summary="换头像")
def set_avatar(
    session: DbSession,
    owner: CurrentOwner,
    storage: Storage,
    file: Annotated[UploadFile, File(description="PNG、JPG 或 WEBP，5 MB 以内")],
) -> ProfileOut:
    """Upload an image as the avatar. It is cropped to a square, scaled to 256 × 256, and photo
    metadata is removed."""
    try:
        return ProfileService(session, owner, storage).set_avatar(file.file.read())
    except AuthError as err:
        raise _http_error(err) from err


@auth_router.delete("/profile/avatar", summary="去掉头像")
def remove_avatar(session: DbSession, owner: CurrentOwner) -> ProfileOut:
    """Remove the avatar; the first character of the name is shown instead."""
    return ProfileService(session, owner).remove_avatar()


@users_router.get("/permissions", summary="可开通的权限")
def list_permissions(session: DbSession, owner: CurrentOwner) -> list[PermissionOut]:
    """Permissions administrators can grant to members. Use these names in permissions when creating
    or updating users."""
    return UserService(session, owner).grantable()


@users_router.get("", summary="用户列表")
def list_users(session: DbSession, owner: CurrentOwner) -> list[UserOut]:
    """All users of the team (administrators)."""
    return UserService(session, owner).list()


@users_router.post("", status_code=status.HTTP_201_CREATED, summary="新建用户")
def create_user(body: UserCreate, session: DbSession, owner: CurrentOwner) -> UserOut:
    """Create a user in the team (administrators). Passwords require at least 8 characters.

        Members can view and export by default; uploading, editing fields and so on are granted
    in permissions, for example:
        `{"username": "alice", "password": "...", "permissions": ["document:create"]}`"""
    try:
        return UserService(session, owner).create(body)
    except AuthError as err:
        raise _http_error(err) from err


@users_router.patch("/{user_id}", summary="修改用户")
def update_user(
    user_id: uuid.UUID, body: UserUpdate, session: DbSession, owner: CurrentOwner
) -> UserOut:
    """Change role, grant or revoke permissions, change display name, deactivate or reactivate
    (administrators). Only the given fields change.

        - Grant permissions: `{"permissions": ["document:create", "value:review"]}` (replaces the
          list; does not append)
        - Deactivate: `{"is_active": false}`; the user's existing sessions are revoked
          immediately
        - Changes take effect immediately; the user does not need to log in again."""
    try:
        return UserService(session, owner).update(user_id, body)
    except AuthError as err:
        raise _http_error(err) from err


@users_router.post(
    "/{user_id}/reset-code", status_code=status.HTTP_201_CREATED, summary="生成重设码"
)
def create_reset_code(user_id: uuid.UUID, session: DbSession, owner: CurrentOwner) -> ResetCodeOut:
    """Generate a one-time reset code for a user who forgot the password (administrators). Valid for
    15 minutes, usable once.

        Pass the code to the user in person or by phone; the user selects "Forgot password" on
    the login page, enters the code and sets a new password. The administrator never learns the
    new password. Generating a new code revokes the previous one."""
    try:
        return UserService(session, owner).create_reset_code(user_id)
    except AuthError as err:
        raise _http_error(err) from err
