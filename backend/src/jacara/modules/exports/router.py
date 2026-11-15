"""Export history API: /api/v1/exports."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from jacara.core.deps import get_owner, get_session, get_storage
from jacara.core.i18n import render
from jacara.core.owner import Owner
from jacara.core.storage import ObjectStorage
from jacara.modules.documents.router import attachment
from jacara.modules.exports.schemas import ExportPage
from jacara.modules.exports.service import CONTENT_TYPES, ExportError, ExportService

router = APIRouter(prefix="/exports", tags=["导出记录"])

_STATUS = {
    "not_found": status.HTTP_404_NOT_FOUND,
    "no_file": status.HTTP_404_NOT_FOUND,
    "storage": status.HTTP_503_SERVICE_UNAVAILABLE,
}


def get_service(
    session: Annotated[Session, Depends(get_session)],
    storage: Annotated[ObjectStorage, Depends(get_storage)],
    owner: Annotated[Owner, Depends(get_owner)],
) -> ExportService:
    return ExportService(session, storage, owner)


Service = Annotated[ExportService, Depends(get_service)]


@router.get("", summary="导出记录")
def list_exports(
    service: Service,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ExportPage:
    """Past exports: who, when and which documents. Newest first."""
    return service.page(limit, offset)


@router.get("/{export_id}/file", summary="重新下载", response_class=Response)
def download_export(export_id: uuid.UUID, service: Service) -> Response:
    """Download the file as exported at the time (values at that time, not current values)."""
    try:
        export, data = service.download(export_id)
    except ExportError as err:
        raise HTTPException(
            status_code=_STATUS.get(err.code, 400), detail=render(err.message)
        ) from err
    return attachment(
        export.filename, data, CONTENT_TYPES.get(export.format, "application/octet-stream")
    )
