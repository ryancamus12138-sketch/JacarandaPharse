"""auto template choice and vision

Revision ID: c5e1a8d3f7b4
Revises: b3d8f1a6c2e9
Create Date: 2026-11-17 12:00:00.000000"""

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
    # Automatic template selection at upload: each document picks a template from the library.
    op.add_column(
        "documents", sa.Column("auto", sa.Boolean(), server_default="false", nullable=False)
    )
    # Vision mode for the next run of this document: always or off; empty means JACARA_VISION.
    op.add_column("documents", sa.Column("vision", sa.String(length=8), nullable=True))
    # Per document: how the template was chosen and why the image was used.
    op.add_column("extractions", sa.Column("classified", JSON, nullable=True))
    op.add_column("extractions", sa.Column("vision", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("extractions", "vision")
    op.drop_column("extractions", "classified")
    op.drop_column("documents", "vision")
    op.drop_column("documents", "auto")
