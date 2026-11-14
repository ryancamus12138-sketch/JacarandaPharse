"""System information API: /api/v1/system. Available to any logged-in user."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from jacara.core.config import get_settings
from jacara.core.deps import get_owner, get_session
from jacara.core.owner import Owner
from jacara.modules.system.schemas import SystemInfo, VisionInfo
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


@router.get("/vision", summary="模型能不能看图")
def vision(owner: Annotated[Owner, Depends(get_owner)]) -> VisionInfo:
    """Whether the model server has a vision projector loaded.

        Used by the review page and reprocessing to decide which options to show. Requires login
    (owner); only queries the model server and is fast."""
    return vision_info(get_settings())
