"""字段审核接口：/api/v1/values（一个字段）和 /api/v1/extractions（表格增删行）。"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy.orm import Session

from jacara.core.deps import get_owner, get_session
from jacara.core.owner import Owner
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
    owner: Annotated[Owner, Depends(get_owner)],
) -> FieldService:
    return FieldService(session, owner)


Service = Annotated[FieldService, Depends(get_service)]


def _http_error(err: FieldError) -> HTTPException:
    return HTTPException(status_code=_STATUS.get(err.code, 400), detail=err.message)


@router.post("/confirm", summary="批量确认")
def confirm_many(change: ConfirmManyIn, service: Service) -> ConfirmManyOut:
    """一次确认好几个字段（值是对的）。每个字段各记一条确认记录；已经删掉的行跳过。

    例如：`{"ids": ["…", "…"], "reason": "核对过原件"}`。原因不写就记"批量确认"。
    """
    try:
        return service.confirm_many(change.ids, change.reason)
    except FieldError as err:
        raise _http_error(err) from err


@router.get("/{value_id}/revisions", summary="修改历史")
def get_history(value_id: uuid.UUID, service: Service) -> ValueHistory:
    """一个字段的模型原值，以及每一次修改或确认：谁、什么时候、改成什么、为什么。"""
    try:
        return service.history(value_id)
    except FieldError as err:
        raise _http_error(err) from err


@router.post("/{value_id}/revisions", status_code=status.HTTP_201_CREATED, summary="审核字段")
def revise_value(value_id: uuid.UUID, change: RevisionIn, service: Service) -> ValueOut:
    """修改（edit）或确认（confirm）一个字段。原值不变，新增一条审核记录。

    - 修改：`{"action": "edit", "value": "9.50", "reason": "OCR 把 9.50 认成了 9.00"}`
    - 确认：`{"action": "confirm"}`
    - 从原文选取：`{"action": "edit", "source_lines": [12, 13]}`（这一页 OCR 结果里的第几块，
      见 `/documents/{id}/pages/{n}/ocr`），值从这几块的文字读出，出处换成这几块

    返回审核之后的字段：current_value 是新值，pending_review 变成 false。
    """
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
    """在表格（如商品明细 items）里加一行，插在第 after 行后面（不写就加在最后）。

    返回新行的每一格（值是空的），再用"审核字段"填值。
    """
    try:
        return service.add_row(extraction_id, table, row)
    except FieldError as err:
        raise _http_error(err) from err


@rows_router.post("/{extraction_id}/tables/{table}/rows/{index}/delete", summary="删除一行")
def delete_row(
    extraction_id: uuid.UUID, table: TableName, index: int, change: RowChange, service: Service
) -> list[ValueOut]:
    """删除表格的一行（原因必填）。不真的删：每一格记一条删除记录，可以恢复。"""
    try:
        return service.delete_row(extraction_id, table, index, change)
    except FieldError as err:
        raise _http_error(err) from err


@rows_router.post("/{extraction_id}/tables/{table}/rows/{index}/restore", summary="恢复一行")
def restore_row(
    extraction_id: uuid.UUID, table: TableName, index: int, change: RowChange, service: Service
) -> list[ValueOut]:
    """恢复删掉的一行（原因必填）。"""
    try:
        return service.restore_row(extraction_id, table, index, change)
    except FieldError as err:
        raise _http_error(err) from err
