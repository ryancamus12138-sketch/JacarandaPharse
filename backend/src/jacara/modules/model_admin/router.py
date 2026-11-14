"""Model selection API: /api/v1/models."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from jacara.core.config import get_settings
from jacara.core.deps import get_owner, get_session
from jacara.core.i18n import render
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
    """LLM, vision projector, OCR and table models: available files in models/, the one in use, and
    what determined it."""
    return service.get()


@router.put("", summary="换模型")
def choose(data: ModelsIn, service: Service) -> ModelsOut:
    """Select files in models/ (administrators only). Takes effect after `jacara stop` and `jacara
    start`."""
    try:
        return service.choose(data)
    except ModelError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=render(err.message)
        ) from err
