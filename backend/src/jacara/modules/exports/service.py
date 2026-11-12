"""导出记录：保存每次导出的文件，列出来，重新下载。

导出本身（生成 Excel）还在 documents 模块；生成好之后调用 save_export 存下来。
"""

from __future__ import annotations

import logging
import uuid
from pathlib import PurePath

from sqlalchemy.orm import Session

from jacara.core.owner import Owner
from jacara.core.permissions import require
from jacara.core.storage import ObjectStorage, StorageError
from jacara.models import Document
from jacara.modules.audit.service import record
from jacara.modules.exports.models import Export
from jacara.modules.exports.repository import ExportRepository
from jacara.modules.exports.schemas import ExportOut, ExportPage

log = logging.getLogger(__name__)

CONTENT_TYPES = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "zip": "application/zip",
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
    """记下一次导出，并把文件存进对象存储（exports/<id>.xlsx）。

    存文件失败不影响这次导出：用户照样拿到文件，只是记录里标着"不能重新下载"。
    """
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
                    "user_name": names.get(e.user_id, "本机用户"),
                    "available": e.storage_key is not None,
                }
            )
            for e in exports
        ]
        return ExportPage(items=items, total=total)

    def download(self, export_id: uuid.UUID) -> tuple[Export, bytes]:
        """重新下载以前导出的文件。文件内容是当时导出的样子，不是现在的值。"""
        export = self.repo.get(export_id, self.owner.team_id)
        if export is None:
            raise ExportError("not_found", "找不到这条导出记录")
        require(self.owner, "document:export", export.team_id)
        if export.storage_key is None:
            raise ExportError("no_file", "这次导出的文件当时没能保存，不能重新下载")
        try:
            data = self.storage.get(export.storage_key)
        except StorageError as err:
            raise ExportError("storage", "对象存储里取不到这个文件") from err
        # 重新下载也是"数据离开系统"，留一条记录
        record(self.session, self.owner, "export:download", "export", export.id)
        return export, data
