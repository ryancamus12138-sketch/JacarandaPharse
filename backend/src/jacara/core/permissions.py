"""权限检查（项目规则 5）：每个操作都要调用 require()。

个人模式下所有检查都通过。团队模式下只有两种角色：
- 管理员（admin）：什么都能做，包括管理用户、给别人开通权限、看审计日志。
- 使用者（member）：默认能看、能下载、能导出；其余的由管理员一项一项开通（GRANTABLE）。
"""

from __future__ import annotations

import uuid
from typing import Literal

from jacara.core.config import get_settings
from jacara.core.owner import Owner

Action = Literal[
    "document:create",
    "document:read",
    "document:download",
    "document:process",
    "document:review",
    "document:export",
    "value:read",
    "value:review",
    "user:manage",
    "audit:read",
]

BASE: set[Action] = {"document:read", "document:download", "document:export", "value:read"}
"""每个使用者都有的：看列表和详情、下载原图、导出 Excel、看修改历史。"""

GRANTABLE: dict[Action, str] = {
    "document:create": "上传文件",
    "document:process": "重新处理",
    "value:review": "修改和确认字段",
    "document:review": "完成审核",
}
"""管理员可以给使用者单独开通的权限（权限名 → 界面上显示的名字）。"""

ADMIN_ONLY: dict[Action, str] = {"user:manage": "管理用户", "audit:read": "查看审计日志"}
"""只有管理员能做，不能开通给使用者。"""


class PermissionDenied(Exception):
    """没有权限。API 会把它变成 403。"""


def allowed(owner: Owner) -> set[Action]:
    """这个人现在能做的全部操作。前端用它决定显示哪些按钮。"""
    if owner.role == "admin":
        return {*BASE, *GRANTABLE, *ADMIN_ONLY}
    return {*BASE, *(a for a in GRANTABLE if a in owner.permissions)}


def require(owner: Owner, action: Action, team_id: uuid.UUID | None = None) -> None:
    """检查 owner 能不能做 action。team_id 是被操作的数据属于哪个团队。"""
    if get_settings().jacara_mode == "personal":
        return
    if team_id is not None and team_id != owner.team_id:
        raise PermissionDenied("这是别的团队的数据")
    if action not in allowed(owner):
        name = GRANTABLE.get(action) or ADMIN_ONLY.get(action) or action
        hint = "只有管理员能做" if action in ADMIN_ONLY else "请找管理员开通"
        raise PermissionDenied(f"没有权限：{name}（{hint}）")
