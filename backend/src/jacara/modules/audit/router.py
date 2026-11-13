"""审计日志接口：/api/v1/audit-logs。只有管理员能看。"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from jacara.core.deps import get_owner, get_session
from jacara.core.owner import Owner
from jacara.modules.audit.repository import AuditFilter
from jacara.modules.audit.schemas import AuditPage
from jacara.modules.audit.service import AuditService

router = APIRouter(prefix="/audit-logs", tags=["审计日志"])

Action = Annotated[str | None, Query(description="按开头筛选，例如 auth: 或 document:")]
User = Annotated[uuid.UUID | None, Query(description="只看这个人做的")]
DateFrom = Annotated[date | None, Query(description="从这一天（含，本机时区）")]
DateTo = Annotated[date | None, Query(description="到这一天（含，本机时区）")]


def _filter(
    action: str | None, user_id: uuid.UUID | None, date_from: date | None, date_to: date | None
) -> AuditFilter:
    from jacara.modules.documents.service import day_start

    return AuditFilter(
        action=action,
        user_id=user_id,
        since=day_start(date_from) if date_from else None,
        until=day_start(date_to + timedelta(days=1)) if date_to else None,
    )


@router.get("", summary="审计日志")
def list_audit_logs(
    session: Annotated[Session, Depends(get_session)],
    owner: Annotated[Owner, Depends(get_owner)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    action: Action = None,
    user_id: User = None,
    date_from: DateFrom = None,
    date_to: DateTo = None,
) -> AuditPage:
    """谁、什么时候、做了什么。最新的在前。可以按操作、按人、按日期筛。"""
    return AuditService(session, owner).page(
        limit, offset, _filter(action, user_id, date_from, date_to)
    )


@router.get("/export", summary="导出审计日志")
def export_audit_logs(
    session: Annotated[Session, Depends(get_session)],
    owner: Annotated[Owner, Depends(get_owner)],
    action: Action = None,
    user_id: User = None,
    date_from: DateFrom = None,
    date_to: DateTo = None,
) -> AuditPage:
    """按同样的筛选条件取出全部（最多 EXPORT_LIMIT 条），网页把它存成 CSV。导出本身也记一条。"""
    return AuditService(session, owner).export(_filter(action, user_id, date_from, date_to))
