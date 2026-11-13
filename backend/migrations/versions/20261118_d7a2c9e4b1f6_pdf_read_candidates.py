"""pdf read mode and value candidates (ADR 0042)

Revision ID: d7a2c9e4b1f6
Revises: c5e1a8d3f7b4
Create Date: 2026-11-18 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d7a2c9e4b1f6"
down_revision: str | Sequence[str] | None = "c5e1a8d3f7b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    # 这个文档下一次处理时 PDF 怎么读：image、text，空的按 JACARA_PDF_READ
    op.add_column("documents", sa.Column("pdf_read", sa.String(length=8), nullable=True))
    # 每个值的候选：OCR、PDF 文字层、局部放大再识别、表单字段读出来的不同的值
    op.add_column("extracted_values", sa.Column("candidates", JSON, nullable=True))


def downgrade() -> None:
    op.drop_column("extracted_values", "candidates")
    op.drop_column("documents", "pdf_read")
