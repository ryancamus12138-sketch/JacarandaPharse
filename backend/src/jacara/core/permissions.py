"""Permission checks: every operation calls require().

All checks pass in personal mode. Team mode has two roles:
- Administrator (admin): may do everything, including managing users, granting permissions and
  viewing the audit log.
- Member: may view, download and export by default; other permissions are granted individually by
  an administrator (GRANTABLE)."""

from __future__ import annotations

import uuid
from typing import Literal

from jacara.core.config import get_settings
from jacara.core.i18n import N_, tr
from jacara.core.owner import Owner

Action = Literal[
    "document:create",
    "document:read",
    "document:download",
    "document:process",
    "document:delete",
    "document:review",
    "document:export",
    "value:read",
    "value:review",
    "template:manage",
    "user:manage",
    "audit:read",
    "system:manage",
]

BASE: set[Action] = {"document:read", "document:download", "document:export", "value:read"}
"""Granted to every member: view lists and details, download originals, export to Excel, view change
history."""

GRANTABLE: dict[Action, str] = {
    "document:create": N_("上传文件"),
    "document:process": N_("重新处理"),
    "document:delete": N_("删除文档"),
    "value:review": N_("修改和确认字段"),
    "document:review": N_("完成审核"),
    "template:manage": N_("管理模板"),
}
"""Permissions an administrator may grant to individual members (permission name → display name)."""

ADMIN_ONLY: dict[Action, str] = {
    "user:manage": N_("管理用户"),
    "audit:read": N_("查看审计日志"),
    "system:manage": N_("更换模型"),
}
"""Administrator-only; cannot be granted to members."""


class PermissionDenied(Exception):
    """Permission denied. The API maps it to 403."""


def allowed(owner: Owner) -> set[Action]:
    """All operations this actor may currently perform. The frontend uses this to decide which
    buttons to show."""
    if owner.role == "admin":
        return {*BASE, *GRANTABLE, *ADMIN_ONLY}
    return {*BASE, *(a for a in GRANTABLE if a in owner.permissions)}


def require(owner: Owner, action: Action, team_id: uuid.UUID | None = None) -> None:
    """Check whether owner may perform action. team_id is the team that owns the target record."""
    if get_settings().jacara_mode == "personal":
        return
    if team_id is not None and team_id != owner.team_id:
        raise PermissionDenied(tr("此数据属于其他团队"))
    if action not in allowed(owner):
        name = tr(GRANTABLE.get(action) or ADMIN_ONLY.get(action) or action)
        hint = tr("仅管理员可执行") if action in ADMIN_ONLY else tr("请联系管理员开通")
        raise PermissionDenied(tr("没有权限：{name}（{hint}）", name=name, hint=hint))
