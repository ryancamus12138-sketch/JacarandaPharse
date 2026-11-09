"""API 的"依赖"：每个请求需要的东西（数据库会话、对象存储、任务队列、当前用户）。

router 里写 session: Session = Depends(get_session)，FastAPI 每次请求会自动调用这里的函数。
测试时用 app.dependency_overrides 换成假的，业务代码不用改。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from jacara.core.config import get_settings
from jacara.core.db import session_scope
from jacara.core.owner import PERSONAL_OWNER, Owner
from jacara.core.queue import CeleryQueue, TaskQueue
from jacara.core.storage import ObjectStorage
from jacara.core.storage import get_storage as _get_storage
from jacara.modules.auth.service import AuthService

bearer = HTTPBearer(auto_error=False, description="登录接口返回的 token")
"""从请求头 Authorization: Bearer <token> 里取出 token。/docs 页面右上角的 Authorize 按钮就是它。"""


def get_session() -> Iterator[Session]:
    """每个请求一个数据库会话：请求正常结束就提交，出错就回滚。"""
    with session_scope() as session:
        yield session


def get_storage() -> ObjectStorage:
    return _get_storage()


def get_queue() -> TaskQueue:
    return CeleryQueue()


def get_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> str | None:
    return credentials.credentials if credentials else None


def get_owner(
    session: Annotated[Session, Depends(get_session)],
    token: Annotated[str | None, Depends(get_token)],
) -> Owner:
    """当前是谁在操作。

    个人模式：不用登录，固定是 PERSONAL_OWNER。
    团队模式：从 token 查出用户；没带 token 或 token 无效，返回 401（请先登录）。
    """
    if get_settings().jacara_mode == "personal":
        return PERSONAL_OWNER

    owner = AuthService(session).authenticate(token) if token else None
    if owner is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="请先登录" if token is None else "登录已失效，请重新登录",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return owner
