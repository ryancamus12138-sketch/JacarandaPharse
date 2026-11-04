"""数据归属：一条数据属于哪个用户、哪个团队。

个人模式（personal）只有一个用户和一个团队，用下面固定的 ID。
以后做团队模式和登录时，Owner 从登录信息里来，存数据的代码不用改。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class Owner:
    user_id: uuid.UUID
    team_id: uuid.UUID


PERSONAL_OWNER = Owner(
    user_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
    team_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
)
