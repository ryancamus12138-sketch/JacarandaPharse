"""设置页接口：/api/v1/settings（ADR 0063）。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from jacara.core.deps import get_owner, get_session
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
    """每一项现在的值、默认值、谁定的（.env / 页面 / 默认）。"""
    return service.get()


@router.put("", summary="改设置")
def update(data: SettingsIn, service: Service) -> SettingsOut:
    """只有管理员。.env 里写了的改不了；值写 null 改回默认。
    后台要 jacara stop、jacara start 才用新的。"""
    try:
        return service.update(data)
    except SettingsError as err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=err.message) from err
