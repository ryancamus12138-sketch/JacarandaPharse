"""Settings API: /api/v1/settings."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from jacara.core.deps import get_owner, get_session
from jacara.core.i18n import render
from jacara.core.owner import Owner
from jacara.modules.app_settings.schemas import SettingsIn, SettingsOut
from jacara.modules.app_settings.service import AppSettingsService, SettingsError

router = APIRouter(prefix="/settings", tags=["设置"])


def get_service(
    session: Annotated[Session, Depends(get_session)],
    owner: Annotated[Owner, Depends(get_owner)],
) -> AppSettingsService:
    return AppSettingsService(session, owner)


Service = Annotated[AppSettingsService, Depends(get_service)]


@router.get("", summary="设置页上的设置")
def settings(service: Service) -> SettingsOut:
    """Current value, default value and source (.env / page / default) of each setting."""
    return service.get()


@router.put("", summary="改设置")
def update(data: SettingsIn, service: Service) -> SettingsOut:
    """Administrators only. Settings fixed in .env cannot be changed; a null value restores the
    default. Background workers pick up changes after `jacara stop` and `jacara start`."""
    try:
        return service.update(data)
    except SettingsError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=render(err.message)
        ) from err
