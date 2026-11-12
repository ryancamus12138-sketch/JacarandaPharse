"""templates can be deleted after use

Revision ID: a7c4e2f9d1b3
Revises: f1a9c3e7b2d5
Create Date: 2026-11-15 13:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7c4e2f9d1b3"
down_revision: str | Sequence[str] | None = "f1a9c3e7b2d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 用过的模板删除时只记下删除时间：版本留着，用过它的文档照常显示
    op.add_column("templates", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("templates", "deleted_at")
