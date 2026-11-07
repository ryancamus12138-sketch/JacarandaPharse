"""字段审核接口：/api/v1/values。"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from jacara.core.deps import get_owner, get_session
from jacara.core.owner import Owner
from jacara.modules.fields.schemas import RevisionIn, ValueHistory, ValueOut
from jacara.modules.fields.service import FieldError, FieldService

router = APIRouter(prefix="/values", tags=["字段审核"])

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

    返回审核之后的字段：current_value 是新值，pending_review 变成 false。
    """
    try:
        return service.revise(value_id, change)
    except FieldError as err:
        raise _http_error(err) from err
