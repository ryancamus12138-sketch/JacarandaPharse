"""Watch folder: images and PDFs placed in this folder (including subfolders) are imported
automatically.

- Read-only: your files are never moved, deleted or modified;
- Each file (by content) is imported once: after a restart the folder is rescanned and files
  already imported are skipped by fingerprint;
- Files still being copied are not read: files modified within the last few seconds wait for the
  next scan;
- The source folder (watch folder name + subfolder) is recorded on import, so the document list
  can filter by folder."""

from __future__ import annotations

import logging
import mimetypes
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from jacara.core.db import session_scope
from jacara.core.owner import Owner
from jacara.core.queue import TaskQueue
from jacara.core.storage import ObjectStorage

log = logging.getLogger(__name__)

SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".pdf"}
SETTLE_SECONDS = 3.0
"""Minimum time since a file's last modification before it is read (files still being copied are
never read half-way)."""


@dataclass
class Watcher:
    """Scan a folder and import new files. scan() scans once; run() scans continuously."""

    root: Path
    factory: sessionmaker[Session] | None
    storage: ObjectStorage
    queue: TaskQueue
    owner: Owner
    template: str | None = None
    """Template to use (id of one of my templates); defaults to the template used last."""
    split: str = "auto"
    seen: dict[Path, tuple[float, int]] = field(default_factory=dict)
    """Processed files → (modification time, size). A changed file (time or size) is examined
    again."""
    now: Callable[[], float] = time.time

    def scan(self) -> list[tuple[Path, str]]:
        """Scan once. Returns [(file, "imported" / "previously imported" / failure reason)]."""
        from jacara.modules.documents.service import DocumentError, DocumentService

        done: list[tuple[Path, str]] = []
        for path in sorted(self.root.rglob("*")):
            if not self._wanted(path):
                continue
            stat = path.stat()
            key = (stat.st_mtime, stat.st_size)
            if self.seen.get(path) == key or self.now() - stat.st_mtime < SETTLE_SECONDS:
                continue
            relative = path.relative_to(self.root).parent
            folder = "/".join([self.root.name, *relative.parts])
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            try:
                with session_scope(self.factory) as session:
                    service = DocumentService(session, self.storage, self.queue, self.owner, None)
                    out = service.upload(
                        path.name,
                        content_type,
                        path.read_bytes(),
                        self.template,
                        folder=folder,
                        split=self.split,
                        skip_duplicate=True,
                    )
                result = "以前导入过" if out.duplicate_skipped else "导入"
            except (DocumentError, OSError) as err:
                result = getattr(err, "message", str(err))
            self.seen[path] = key
            done.append((path, result))
            log.info("%s：%s", result, path)
        return done

    def _wanted(self, path: Path) -> bool:
        if not path.is_file() or path.suffix.lower() not in SUFFIXES:
            return False
        # Skip hidden files (.DS_Store, temporary files starting with ~$).
        parts = path.relative_to(self.root).parts
        return not any(p.startswith((".", "~$")) for p in parts)

    def run(self, every: float = 5.0) -> None:
        """Scan continuously every every seconds. Stop with Ctrl + C."""
        while True:
            for path, result in self.scan():
                print(f"{result}：{path.relative_to(self.root)}")
            time.sleep(every)
