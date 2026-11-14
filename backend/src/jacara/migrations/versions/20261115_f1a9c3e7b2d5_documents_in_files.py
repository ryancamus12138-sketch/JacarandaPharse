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
    # A document may span several pages: last page of the document.
    op.add_column("extractions", sa.Column("last_page", sa.Integer(), nullable=True))
    # How a file is split into documents: auto, one, pages, or explicit page ranges.
    op.add_column(
        "documents",
        sa.Column("split", sa.String(length=200), server_default="auto", nullable=False),
    )
    # Source folder of the file in a batch import.
    op.add_column("documents", sa.Column("folder", sa.String(length=255), nullable=True))
    op.create_index("ix_documents_folder", "documents", ["folder"])


def downgrade() -> None:
    op.drop_index("ix_documents_folder", table_name="documents")
    op.drop_column("documents", "folder")
    op.drop_column("documents", "split")
    op.drop_column("extractions", "last_page")
