"""several documents in one file, folders

Revision ID: f1a9c3e7b2d5
Revises: e6f2c8a1d4b7
Create Date: 2026-11-15 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f1a9c3e7b2d5"
down_revision: str | Sequence[str] | None = "e6f2c8a1d4b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 一张单据可以占好几页：到第几页结束（ADR 0036）
    op.add_column("extractions", sa.Column("last_page", sa.Integer(), nullable=True))
    # 一个文件里有几张单据：auto、one、pages 或页码范围
    op.add_column(
        "documents",
        sa.Column("split", sa.String(length=200), server_default="auto", nullable=False),
    )
    # 批量导入时文件所在的文件夹
    op.add_column("documents", sa.Column("folder", sa.String(length=255), nullable=True))
    op.create_index("ix_documents_folder", "documents", ["folder"])


def downgrade() -> None:
    op.drop_index("ix_documents_folder", table_name="documents")
    op.drop_column("documents", "folder")
    op.drop_column("documents", "split")
    op.drop_column("extractions", "last_page")
