"""System information API: /api/v1/system. Available to any logged-in user."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from jacara.core.config import get_settings
from jacara.core.deps import get_owner, get_session
from jacara.core.i18n import tr
from jacara.core.owner import Owner
from jacara.modules.system.schemas import Diagnostics, LicenseText, SystemInfo, VisionInfo
from jacara.modules.system.service import SystemService, vision_info

router = APIRouter(prefix="/system", tags=["系统信息"])


@router.get("", summary="系统信息")
def system_info(
    session: Annotated[Session, Depends(get_session)],
    owner: Annotated[Owner, Depends(get_owner)],
) -> SystemInfo:
    """Version, models, OCR, templates, data volume, and reachability of the database, queue, object
    storage and model server.

        Queries several services and may take a few seconds (at most 3 seconds per unreachable
    service)."""
    return SystemService(session, owner, get_settings()).info()


@router.get("/diagnostics", summary="诊断信息")
def diagnostics(
    session: Annotated[Session, Depends(get_session)],
    owner: Annotated[Owner, Depends(get_owner)],
) -> Diagnostics:
    """Plain text to paste into a problem report (administrators): versions, system, models,
    background processes and service checks; no document content."""
    return SystemService(session, owner, get_settings()).diagnostics()


@router.get("/licenses/{which}", summary="许可和署名")
def license_text(
    which: Literal["license", "notice", "third_party"],
    session: Annotated[Session, Depends(get_session)],
    owner: Annotated[Owner, Depends(get_owner)],
) -> LicenseText:
    """LICENSE, NOTICE or THIRD_PARTY_LICENSES as shipped with the program. The third-party list
    is generated when the installer is built and is missing when running from source."""
    try:
        return SystemService(session, owner, get_settings()).license_text(which)
    except FileNotFoundError as err:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, tr("这个版本没有附带 {name}", name=str(err))
        ) from err


@router.get("/vision", summary="模型能不能看图")
def vision(owner: Annotated[Owner, Depends(get_owner)]) -> VisionInfo:
    """Whether the model server has a vision projector loaded.

        Used by the review page and reprocessing to decide which options to show. Requires login
    (owner); only queries the model server and is fast."""
    return vision_info(get_settings())
