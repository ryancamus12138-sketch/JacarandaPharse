"""数据归属和当前用户：一条数据属于哪个用户、哪个团队；现在是谁在操作、能做什么。

个人模式（personal）不用登录，固定是 PERSONAL_OWNER（管理员）。
团队模式（team）从登录 token 里查出是谁，见 core/deps.py 的 get_owner()。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Owner:
    user_id: uuid.UUID
    team_id: uuid.UUID
    role: str = "admin"
    """admin（管理员）或 member（使用者）。"""
    permissions: frozenset[str] = field(default_factory=frozenset)
    """管理员给这个使用者开通的权限，见 core/permissions.py 的 GRANTABLE。管理员不看这个。"""


PERSONAL_OWNER = Owner(
    user_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
    team_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
    role="admin",
)
