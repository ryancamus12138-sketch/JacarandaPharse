"""审计日志接口：/api/v1/audit-logs。只有管理员能看。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from jacara.core.deps import get_owner, get_session
from jacara.core.owner import Owner
from jacara.modules.audit.schemas import AuditPage
from jacara.modules.audit.service import AuditService

router = APIRouter(prefix="/audit-logs", tags=["审计日志"])


@router.get("", summary="审计日志")
def list_audit_logs(
    session: Annotated[Session, Depends(get_session)],
    owner: Annotated[Owner, Depends(get_owner)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    action: Annotated[str | None, Query(description="按开头筛选，例如 auth: 或 document:")] = None,
) -> AuditPage:
    """谁、什么时候、做了什么。最新的在前。"""
    return AuditService(session, owner).page(limit, offset, action)
