"""审计日志：记录（record）和查看。

其他模块的 service 做完一件"值得追查"的事，就调用 record()：
登录、退出、上传、重新处理、审核字段、完成审核、导出、管理用户。
只看、不改的操作（列表、详情、下载原图）不记，否则日志会被淹没。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from jacara.core.owner import Owner
from jacara.core.permissions import require
from jacara.models import AuditLog
from jacara.modules.audit.repository import AuditFilter, AuditRepository
from jacara.modules.audit.schemas import AuditOut, AuditPage


def record(
    session: Session,
    owner: Owner | None,
    action: str,
    target_type: str | None = None,
    target_id: uuid.UUID | str | None = None,
    **detail: Any,
) -> None:
    """记一条审计日志，和业务数据在同一个事务里：业务回滚了，日志也不会留下"做了一半"的记录。"""
    AuditRepository(session).add(
        AuditLog(
            team_id=owner.team_id if owner else None,
            user_id=owner.user_id if owner else None,
            action=action,
            target_type=target_type,
            target_id=str(target_id) if target_id is not None else None,
            detail=detail,
        )
    )


EXPORT_LIMIT = 50_000
"""一次最多导出多少条审计日志。"""


class AuditService:
    def __init__(self, session: Session, owner: Owner) -> None:
        self.repo = AuditRepository(session)
        self.owner = owner

    def page(self, limit: int, offset: int, found: AuditFilter | None = None) -> AuditPage:
        require(self.owner, "audit:read")
        logs, total = self.repo.page(self.owner.team_id, limit, offset, found)
        return AuditPage(items=[AuditOut.model_validate(log) for log in logs], total=total)

    def export(self, found: AuditFilter) -> AuditPage:
        """导出：按筛选条件取出全部（最多 EXPORT_LIMIT 条）。
        导出本身也记一条（审计日志离开系统）。"""
        require(self.owner, "audit:read")
        logs, total = self.repo.page(self.owner.team_id, EXPORT_LIMIT, 0, found)
        record(
            self.repo.session,
            self.owner,
            "audit:export",
            None,
            None,
            count=len(logs),
            filter_action=found.action,
            user=str(found.user_id) if found.user_id else None,
            since=found.since.isoformat() if found.since else None,
            until=found.until.isoformat() if found.until else None,
        )
        return AuditPage(items=[AuditOut.model_validate(log) for log in logs], total=total)
