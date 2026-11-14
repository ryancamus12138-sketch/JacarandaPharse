"""Database: engine, sessions, and the base class and columns shared by all tables.

- Every table inherits Base together with OwnedMixin (ownership) and TimestampMixin (creation
  time).
- Application code obtains a session with session_scope(): it commits on normal exit and rolls
  back on error.
- Schema changes are managed with Alembic (backend/migrations); do not modify the database by
  hand."""

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

# Naming convention for constraints and indexes: stable names let Alembic locate them in later
# migrations.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

JsonColumn = JSON().with_variant(JSONB(), "postgresql")
"""JSON column: JSONB on PostgreSQL (indexable and queryable), plain JSON on SQLite in tests."""


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map: ClassVar[dict[Any, Any]] = {dict[str, Any]: JsonColumn}


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    """Set by the database rather than application code, so clock skew between machines has no
    effect."""


class OwnedMixin:
    """Data ownership: every business record belongs to a user and a team."""

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    team_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)


def new_id() -> uuid.UUID:
    """UUID primary keys: globally unique and, unlike 1, 2, 3, do not reveal the number of rows."""
    return uuid.uuid4()


@lru_cache
def get_engine() -> Engine:
    """Database connection pool, shared by the whole process."""
    return create_engine(get_settings().database_url, pool_pre_ping=True)


@lru_cache
def _session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def get_session_factory() -> sessionmaker[Session]:
    """Session factory, for callers that decide themselves whether to commit or roll back (for
    example read-only replay)."""
    return _session_factory()


@contextmanager
def session_scope(factory: sessionmaker[Session] | None = None) -> Iterator[Session]:
    """Provide a database session: commits when the with block exits normally and rolls back
    everything on error."""
    session = (factory or _session_factory())()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
