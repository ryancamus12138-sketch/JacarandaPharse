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
from pathlib import Path
from typing import Any, ClassVar

from sqlalchemy import JSON, DateTime, Engine, MetaData, Uuid, create_engine, event, func
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


SQLITE_TIMEOUT = 30.0
"""Seconds a SQLite connection waits for another process's write lock before failing (the
`timeout` argument of sqlite3.connect, 5 seconds by default). The API and the two pipeline
workers write to the same file; their writes are short, but a 5-second wait can be exceeded while
a worker saves a large document."""


def sqlite_pragmas(dbapi_connection: Any, _record: Any) -> None:
    """Settings applied to every new SQLite connection.

    - journal_mode=WAL: readers and the writer do not block each other (SQLite documentation,
      "Write-Ahead Logging"). WAL does not work on network file systems, so the data directory must
      be on a local disk.
    - foreign_keys=ON: SQLite does not enforce foreign keys unless enabled per connection
      (SQLAlchemy documentation, "Foreign Key Support"); PostgreSQL always enforces them, so both
      databases behave the same."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def create_db_engine(url: str) -> Engine:
    """Engine for a PostgreSQL or SQLite URL. For SQLite the folder is created and every
    connection gets sqlite_pragmas."""
    if not url.startswith("sqlite"):
        return create_engine(url, pool_pre_ping=True)
    from sqlalchemy.engine import make_url

    database = make_url(url).database
    if database and database != ":memory:":
        Path(database).parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: pooled connections are handed to FastAPI's worker threads; each
    # session still uses its connection from one thread at a time.
    engine = create_engine(
        url, connect_args={"timeout": SQLITE_TIMEOUT, "check_same_thread": False}
    )
    event.listen(engine, "connect", sqlite_pragmas)
    return engine


@lru_cache
def get_engine() -> Engine:
    """Database connection pool, shared by the whole process."""
    return create_db_engine(get_settings().database_url)


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
