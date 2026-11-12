"""auto template choice and vision (ADR 0038)

Revision ID: c5e1a8d3f7b4
Revises: b3d8f1a6c2e9
Create Date: 2026-11-17 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c5e1a8d3f7b4"
down_revision: str | Sequence[str] | None = "b3d8f1a6c2e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    # 上传时选"自动判断"：每张单据从我的模板里挑（ADR 0038）
    op.add_column(
        "documents", sa.Column("auto", sa.Boolean(), server_default="false", nullable=False)
    )
    # 这个文档下一次处理看不看原图：always、off，空的按 JACARA_VISION
    op.add_column("documents", sa.Column("vision", sa.String(length=8), nullable=True))
    # 每张单据：自动判断的经过、看原图的原因
    op.add_column("extractions", sa.Column("classified", JSON, nullable=True))
    op.add_column("extractions", sa.Column("vision", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("extractions", "vision")
    op.drop_column("extractions", "classified")
    op.drop_column("documents", "vision")
    op.drop_column("documents", "auto")
