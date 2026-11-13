"""换模型接口：/api/v1/models（ADR 0046）。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from jacara.core.config import get_settings
from jacara.core.deps import get_owner, get_session
from jacara.core.owner import Owner
from jacara.modules.model_admin.service import ModelAdminService, ModelError, ModelsIn, ModelsOut

router = APIRouter(prefix="/models", tags=["换模型"])


def get_service(
    session: Annotated[Session, Depends(get_session)],
    owner: Annotated[Owner, Depends(get_owner)],
) -> ModelAdminService:
    return ModelAdminService(session, owner, get_settings())


Service = Annotated[ModelAdminService, Depends(get_service)]


@router.get("", summary="模型文件和现在用的")
def models(service: Service) -> ModelsOut:
    """大模型、视觉模型、OCR、表格模型：models/ 里有哪些文件，现在用的是哪个、是谁定的。"""
    return service.get()


@router.put("", summary="换模型")
def choose(data: ModelsIn, service: Service) -> ModelsOut:
    """选用 models/ 里的文件（只有管理员）。改完要 jacara stop、jacara start 才生效。"""
    try:
        return service.choose(data)
    except ModelError as err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=err.message) from err
