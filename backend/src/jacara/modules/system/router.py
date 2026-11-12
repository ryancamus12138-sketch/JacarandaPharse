"""系统信息接口：/api/v1/system。登录了就能看。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from jacara.core.config import get_settings
from jacara.core.deps import get_owner, get_session
from jacara.core.owner import Owner
from jacara.modules.system.schemas import SystemInfo
from jacara.modules.system.service import SystemService

router = APIRouter(prefix="/system", tags=["系统信息"])


@router.get("", summary="系统信息")
def system_info(
    session: Annotated[Session, Depends(get_session)],
    owner: Annotated[Owner, Depends(get_owner)],
) -> SystemInfo:
    """版本、模型、OCR、模板、数据量，以及数据库、队列、对象存储、模型服务连不连得上。

    要问几个服务，可能要等几秒（连不上的服务最多等 3 秒）。
    """
    return SystemService(session, owner, get_settings()).info()
