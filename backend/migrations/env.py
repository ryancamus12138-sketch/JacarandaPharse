"""Alembic environment: database connection and target metadata for migrations."""

import logging

from alembic import context
from sqlalchemy import create_engine, pool

import jacara.models  # noqa: F401  Import all models so that autogenerate can see every table.
from jacara.core.config import get_settings
from jacara.core.db import Base

target_metadata = Base.metadata

# Log Alembic activity to the console (for example: Running upgrade -> fb6c95503174).
logging.basicConfig(format="%(message)s", level=logging.WARNING)
logging.getLogger("alembic").setLevel(logging.INFO)


def _url() -> str:
    """Database URL. The restore drill (`jacara restore-drill`) passes its own URL; otherwise the
    URL from .env is used."""
    return context.config.attributes.get("url") or get_settings().database_url


def run_migrations_offline() -> None:
    """Offline mode: emit SQL without connecting to the database (`alembic upgrade head --sql`)."""
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Online mode: connect to the database and run the migrations."""
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
