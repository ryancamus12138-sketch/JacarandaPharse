"""Export history: store every exported file, list exports and download them again.

The export itself (generating Excel) lives in the documents module, which calls save_export
afterwards."""

from __future__ import annotations

import logging
import uuid
from pathlib import PurePath

from sqlalchemy.orm import Session

from jacara.core.i18n import tr
from jacara.core.owner import Owner
from jacara.core.permissions import require
from jacara.core.storage import ObjectStorage, StorageError
from jacara.models import Document
from jacara.modules.audit.service import record
from jacara.modules.auth.schemas import PERSONAL_NAME
from jacara.modules.exports.models import Export
from jacara.modules.exports.repository import ExportRepository
from jacara.modules.exports.schemas import ExportOut, ExportPage

log = logging.getLogger(__name__)

CONTENT_TYPES = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "zip": "application/zip",
    "csv": "text/csv; charset=utf-8",
    "csv-zip": "application/zip",
    "json": "application/json",
}


class ExportError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def save_export(
    session: Session,
    storage: ObjectStorage,
    owner: Owner,
    filename: str,
    file_format: str,
    data: bytes,
    documents: list[Document],
) -> Export:
    """Record an export and store the file in object storage (exports/<id>.xlsx).

        A storage failure does not fail the export: the user still receives the file; the record
    is marked as not downloadable again."""
    export = Export(
        filename=filename,
        format=file_format,
        size_bytes=len(data),
        document_count=len(documents),
        documents=[{"id": str(d.id), "filename": d.filename} for d in documents],
        user_id=owner.user_id,
        team_id=owner.team_id,
    )
    ExportRepository(session).add(export)
    key = f"exports/{export.id}{PurePath(filename).suffix}"
    try:
        storage.put(key, data, CONTENT_TYPES.get(file_format, "application/octet-stream"))
        export.storage_key = key
    except StorageError:
        log.warning("导出文件 %s 没能存进对象存储，以后不能重新下载", export.id)
    return export


class ExportService:
    def __init__(self, session: Session, storage: ObjectStorage, owner: Owner) -> None:
        self.session = session
        self.storage = storage
        self.owner = owner
        self.repo = ExportRepository(session)

    def page(self, limit: int, offset: int) -> ExportPage:
        require(self.owner, "document:export")
        exports, total = self.repo.page(self.owner.team_id, limit, offset)
        names = self.repo.user_names({e.user_id for e in exports})
        items = [
            ExportOut.model_validate(e).model_copy(
                update={
                    "user_name": names.get(e.user_id, tr(PERSONAL_NAME)),
                    "available": e.storage_key is not None,
                }
            )
            for e in exports
        ]
        return ExportPage(items=items, total=total)

    def download(self, export_id: uuid.UUID) -> tuple[Export, bytes]:
        """Download a past export again. The content is as exported at the time, not the current
        values."""
        export = self.repo.get(export_id, self.owner.team_id)
        if export is None:
            raise ExportError("not_found", tr("导出记录不存在"))
        require(self.owner, "document:export", export.team_id)
        if export.storage_key is None:
            raise ExportError("no_file", tr("此次导出的文件当时未能保存，无法重新下载"))
        try:
            data = self.storage.get(export.storage_key)
        except StorageError as err:
            raise ExportError("storage", tr("无法从对象存储读取此文件")) from err
        # Downloading again is also data leaving the system; record it.
        record(self.session, self.owner, "export:download", "export", export.id)
        return export, data
