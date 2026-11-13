"""document timings (ADR 0051)

Revision ID: f3c7a9e2d5b8
Revises: e9b4d2f7a1c3
Create Date: 2026-11-20 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f3c7a9e2d5b8"
down_revision: str | Sequence[str] | None = "e9b4d2f7a1c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    # 最近一次处理各步用了多久：认字、排队等模型、模型这边
    op.add_column("documents", sa.Column("timings", JSON, nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "timings")
