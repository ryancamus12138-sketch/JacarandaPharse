"""Forget what this process opened from the data directory, so that the next use opens it again.

Used by the desktop app after it restores a backup or moves the data folder, and when it restarts
its background services: database connections, the file store, the task queue and the models are
all opened once and cached for the life of the process."""

from __future__ import annotations

import contextlib


def reset_caches() -> None:
    """Close the database connections and the task queue, and clear every cache that holds a path
    or a connection into the data directory (settings, database, file store, queue, OCR and
    structure models)."""
    from jacara.core import config, db, deps, queue, storage
    from jacara.structure import engines

    if db.get_engine.cache_info().currsize:
        with contextlib.suppress(Exception):
            db.get_engine().dispose()
    if queue.get_hueys.cache_info().currsize:
        for huey in queue.get_hueys().values():
            with contextlib.suppress(Exception):
                huey.storage.close()
    for cached in (
        db.get_engine,
        db._session_factory,  # The session factory holds the engine.
        storage.get_storage,
        queue.get_hueys,
        queue.huey_tasks,
        deps.get_ocr,
        engines.default_engines,
        config.get_settings,
    ):
        cached.cache_clear()
