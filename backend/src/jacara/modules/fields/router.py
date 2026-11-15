"""Field review API: /api/v1/values (single fields) and /api/v1/extractions (adding and removing
table rows)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy.orm import Session

from jacara.core.deps import get_owner, get_session, get_storage
from jacara.core.i18n import render
from jacara.core.owner import Owner
from jacara.core.storage import ObjectStorage
from jacara.modules.fields.schemas import (
    ConfirmManyIn,
    ConfirmManyOut,
    RevisionIn,
    RowChange,
    RowIn,
    ValueHistory,
    ValueOut,
)
from jacara.modules.fields.service import FieldError, FieldService

router = APIRouter(prefix="/values", tags=["字段审核"])
rows_router = APIRouter(prefix="/extractions", tags=["字段审核"])

TableName = Annotated[str, Path(pattern=r"^[a-z][a-z0-9_]*$")]

_STATUS = {
    "not_found": status.HTTP_404_NOT_FOUND,
    "not_ready": status.HTTP_409_CONFLICT,
    "conflict": status.HTTP_409_CONFLICT,
    "invalid": status.HTTP_422_UNPROCESSABLE_CONTENT,
}


def get_service(
    session: Annotated[Session, Depends(get_session)],
    storage: Annotated[ObjectStorage, Depends(get_storage)],
    owner: Annotated[Owner, Depends(get_owner)],
) -> FieldService:
    return FieldService(session, owner, storage)


Service = Annotated[FieldService, Depends(get_service)]


def _http_error(err: FieldError) -> HTTPException:
    return HTTPException(status_code=_STATUS.get(err.code, 400), detail=render(err.message))


@router.post("/confirm", summary="批量确认")
def confirm_many(change: ConfirmManyIn, service: Service) -> ConfirmManyOut:
    """Confirm several fields at once (their values are correct). One confirmation is recorded per
    field; deleted rows are skipped.

        Example: `{"ids": ["…", "…"], "reason": "Checked against the original"}`. Without a
    reason, "Bulk confirmation" is recorded."""
    try:
        return service.confirm_many(change.ids, change.reason)
    except FieldError as err:
        raise _http_error(err) from err


@router.get("/{value_id}/revisions", summary="修改历史")
def get_history(value_id: uuid.UUID, service: Service) -> ValueHistory:
    """The model value of a field and every edit or confirmation: who, when, new value and
    reason."""
    try:
        return service.history(value_id)
    except FieldError as err:
        raise _http_error(err) from err


@router.post("/{value_id}/revisions", status_code=status.HTTP_201_CREATED, summary="审核字段")
def revise_value(value_id: uuid.UUID, change: RevisionIn, service: Service) -> ValueOut:
    """Edit or confirm a field. The original value is kept and a revision is added.

        - Edit: `{"action": "edit", "value": "9.50", "reason": "OCR read 9.50 as 9.00"}`
        - Confirm: `{"action": "confirm"}`
        - Select from source text: `{"action": "edit", "source_lines": [12, 13]}` (block indices
          in this page's OCR result, see `/documents/{id}/pages/{n}/ocr`); the value is parsed
          from these blocks, which become the evidence
        - Transcription: `{"action": "edit", "source_lines": [4], "transcription": "17/05/24"}`;
          the value is parsed from the corrected transcription; when OCR sample collection is
          enabled, an OCR sample is stored as well

        Returns the reviewed field: current_value is the new value and pending_review becomes
    false."""
    try:
        return service.revise(value_id, change)
    except FieldError as err:
        raise _http_error(err) from err


@rows_router.post(
    "/{extraction_id}/tables/{table}/rows",
    status_code=status.HTTP_201_CREATED,
    summary="表格加一行",
)
def add_row(
    extraction_id: uuid.UUID, table: TableName, row: RowIn, service: Service
) -> list[ValueOut]:
    """Add a row to a table (such as line items) after row after (appended when omitted).

    Returns the cells of the new row (empty values); fill them with field review."""
    try:
        return service.add_row(extraction_id, table, row)
    except FieldError as err:
        raise _http_error(err) from err


@rows_router.post("/{extraction_id}/tables/{table}/rows/{index}/delete", summary="删除一行")
def delete_row(
    extraction_id: uuid.UUID, table: TableName, index: int, change: RowChange, service: Service
) -> list[ValueOut]:
    """Delete a table row (reason required). Nothing is deleted: a delete revision is recorded for
    each cell and can be restored."""
    try:
        return service.delete_row(extraction_id, table, index, change)
    except FieldError as err:
        raise _http_error(err) from err


@rows_router.post("/{extraction_id}/tables/{table}/rows/{index}/restore", summary="恢复一行")
def restore_row(
    extraction_id: uuid.UUID, table: TableName, index: int, change: RowChange, service: Service
) -> list[ValueOut]:
    """Restore a deleted row (reason required)."""
    try:
        return service.restore_row(extraction_id, table, index, change)
    except FieldError as err:
        raise _http_error(err) from err
