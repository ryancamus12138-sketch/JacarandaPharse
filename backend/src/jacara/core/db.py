"""数据库：连接、会话、所有表共用的基类和字段。

- 每张表都继承 Base，并带上 OwnedMixin（归属）和 TimestampMixin（创建时间）。
- 业务代码用 session_scope() 拿一个会话：正常结束自动提交，出错自动回滚。
- 表结构的变化用 Alembic 管理（backend/migrations），不要手动改数据库。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from functools import lru_cache
from typing import Any, ClassVar

from sqlalchemy import JSON, DateTime, Engine, MetaData, Uuid, create_engine, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from jacara.core.config import get_settings

# 约束和索引的命名规则：名字固定，Alembic 以后改表时才能准确找到它们。
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

JsonColumn = JSON().with_variant(JSONB(), "postgresql")
"""JSON 字段：PostgreSQL 上用 JSONB（能建索引、能查询），测试用的 SQLite 上用普通 JSON。"""


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map: ClassVar[dict[Any, Any]] = {dict[str, Any]: JsonColumn}


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    """由数据库填写，不用代码里的时间：多台机器时间不一致也不会乱。"""


class OwnedMixin:
    """数据归属（项目规则 6）：每条业务数据都属于一个用户和一个团队。"""

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    team_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)


def new_id() -> uuid.UUID:
    """主键用 UUID：不会重复，也不会像 1、2、3 那样泄露"一共有多少条"。"""
    return uuid.uuid4()


@lru_cache
def get_engine() -> Engine:
    """数据库连接池。整个程序共用一个。"""
    return create_engine(get_settings().database_url, pool_pre_ping=True)


@lru_cache
def _session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def get_session_factory() -> sessionmaker[Session]:
    """会话工厂：要自己决定提交还是回滚时用（例如只读的回放，ADR 0053）。"""
    return _session_factory()


@contextmanager
def session_scope(factory: sessionmaker[Session] | None = None) -> Iterator[Session]:
    """拿一个数据库会话：with 块正常结束就提交，中途出错就全部回滚。"""
    session = (factory or _session_factory())()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
