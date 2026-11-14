"""Where the database migrations are, independent of the current folder.

The migration scripts ship inside the jacara package (jacara/migrations), so the desktop app and any
other installed copy find them next to the code; only `uv run alembic ...` during development goes
through alembic.ini in the project root."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from alembic.config import Config

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"
"""The Alembic script directory (env.py, versions/) inside the package."""


def alembic_config(url: str | None = None) -> Config:
    """Alembic configuration pointing at the packaged migrations. url: database to migrate; without
    it env.py uses the configured database."""
    from alembic.config import Config

    config = Config()
    # Values go through configparser interpolation, where % starts a reference.
    config.set_main_option("script_location", str(MIGRATIONS).replace("%", "%%"))
    config.set_main_option("path_separator", "os")
    if url is not None:
        config.attributes["url"] = url
    return config
