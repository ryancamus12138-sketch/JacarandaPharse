"""API 的"依赖"：每个请求需要的东西（数据库会话、对象存储、任务队列、当前用户）。

router 里写 session: Session = Depends(get_session)，FastAPI 每次请求会自动调用这里的函数。
测试时用 app.dependency_overrides 换成假的，业务代码不用改。
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy.orm import Session

from jacara.core.db import session_scope
from jacara.core.owner import PERSONAL_OWNER, Owner
from jacara.core.queue import CeleryQueue, TaskQueue
from jacara.core.storage import ObjectStorage
from jacara.core.storage import get_storage as _get_storage


def get_session() -> Iterator[Session]:
    """每个请求一个数据库会话：请求正常结束就提交，出错就回滚。"""
    with session_scope() as session:
        yield session


def get_storage() -> ObjectStorage:
    return _get_storage()


def get_queue() -> TaskQueue:
    return CeleryQueue()


def get_owner() -> Owner:
    """当前是谁在操作。个人模式固定是 PERSONAL_OWNER；以后从登录信息里取。"""
    return PERSONAL_OWNER
