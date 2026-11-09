"""审计日志表：谁、什么时候、做了什么。只新增，不修改、不删除（项目规则 3）。"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from jacara.core.db import Base, TimestampMixin, new_id


class AuditLog(Base, TimestampMixin):
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    team_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    """哪个团队的事。登录失败时可能不知道是谁，就是空的。"""
    user_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    """谁做的。"""
    action: Mapped[str] = mapped_column(String(64), index=True)
    """做了什么，例如 auth:login、document:upload、value:edit。"""
    target_type: Mapped[str | None] = mapped_column(String(32))
    """对什么做的：document、value、user……"""
    target_id: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[dict[str, Any]] = mapped_column(default=dict)
    """补充信息，例如文件名、导出了几个文档、登录失败时输入的用户名。"""
