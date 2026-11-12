"""page structure, rejected values

Revision ID: e6f2c8a1d4b7
Revises: d3e8b1f6a2c4
Create Date: 2026-11-14 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e6f2c8a1d4b7"
down_revision: str | Sequence[str] | None = "d3e8b1f6a2c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    # 每一页的结构：文字块、行、版面区域、表格、标题 → 值（ADR 0034）
    op.add_column("extractions", sa.Column("structure", _JSON, nullable=True))
    # 核对不上、按规则没有填的值：模型给的值记在这里
    op.add_column("extracted_values", sa.Column("rejected_value", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("extracted_values", "rejected_value")
    op.drop_column("extractions", "structure")
