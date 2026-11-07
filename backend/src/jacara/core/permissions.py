"""权限检查（项目规则 5）：每个操作都要调用 require()。

个人模式下所有检查都通过；以后做团队模式时，在这里按角色判断，业务代码不用改。
"""

from __future__ import annotations

import uuid
from typing import Literal

from jacara.core.config import get_settings
from jacara.core.owner import Owner

Action = Literal["document:create", "document:read", "document:download", "document:process"]


class PermissionDenied(Exception):
    """没有权限。API 会把它变成 403。"""


def require(owner: Owner, action: Action, team_id: uuid.UUID | None = None) -> None:
    """检查 owner 能不能做 action。team_id 是被操作的数据属于哪个团队。"""
    if get_settings().jacara_mode == "personal":
        return
    if team_id is not None and team_id != owner.team_id:
        raise PermissionDenied(f"没有权限：{action}")
